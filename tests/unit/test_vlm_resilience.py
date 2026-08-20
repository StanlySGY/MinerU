import asyncio

import httpx
import pytest

from mineru.backend.vlm import resilience
from mineru.utils.task_progress import task_progress_registry


@pytest.fixture
def page_timing_logs():
    messages = []
    sink_id = resilience.logger.add(
        lambda message: messages.append(str(message).strip()),
        format="{message}",
    )
    try:
        yield messages
    finally:
        resilience.logger.remove(sink_id)


@pytest.mark.asyncio
async def test_page_timeout_is_skipped_and_other_pages_are_preserved(monkeypatch, page_timing_logs):
    monkeypatch.setenv("MINERU_VLM_GLOBAL_PAGE_CONCURRENCY", "2")

    class Predictor:
        async def aio_batch_two_step_extract(self, images, image_analysis):
            page = images[0]
            if page == "slow":
                raise httpx.ReadTimeout("page timed out")
            await asyncio.sleep(0)
            return [[{"page": page}]]

    results, failures = await resilience.aio_extract_pages_with_failure_isolation(
        Predictor(),
        ["first", "slow", "third"],
        page_start_index=5,
        image_analysis=True,
        task_id="task-1",
        source_file_name="sample.pdf",
    )

    assert results == [[{"page": "first"}], [], [{"page": "third"}]]
    assert len(failures) == 1
    assert failures[0]["page_idx"] == 6
    assert failures[0]["page_number"] == 7
    assert failures[0]["status"] == "skipped"
    assert failures[0]["error_type"] == "ReadTimeout"
    assert failures[0]["error"] == "page timed out"
    assert failures[0]["attempts"] == 1
    assert failures[0]["elapsed_seconds"] >= 0
    assert failures[0]["vlm_request_seconds"] >= 0
    assert failures[0]["retry_wait_seconds"] >= 0
    assert any(
        "event=vlm_page_timing" in message
        and "page_number=7" in message
        and "status=skipped" in message
        and "action=skip_and_continue" in message
        for message in page_timing_logs
    )


@pytest.mark.asyncio
async def test_non_transient_page_error_remains_fatal(page_timing_logs):
    class Predictor:
        async def aio_batch_two_step_extract(self, images, image_analysis):
            raise ValueError("invalid model response")

    with pytest.raises(ValueError, match="invalid model response"):
        await resilience.aio_extract_pages_with_failure_isolation(
            Predictor(),
            ["page"],
            page_start_index=0,
            image_analysis=True,
            task_id=None,
            source_file_name=None,
        )
    assert any(
        "event=vlm_page_timing" in message
        and "status=failed" in message
        and "error_type=ValueError" in message
        and "action=fail_task" in message
        for message in page_timing_logs
    )


@pytest.mark.asyncio
async def test_connect_error_is_retried_once(monkeypatch, page_timing_logs):
    monkeypatch.setenv("MINERU_VLM_CONNECT_MAX_RETRIES", "1")

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(resilience.asyncio, "sleep", no_sleep)

    class Predictor:
        attempts = 0

        async def aio_batch_two_step_extract(self, images, image_analysis):
            self.attempts += 1
            if self.attempts == 1:
                raise httpx.ConnectError("temporary connection failure")
            return [[{"page": images[0]}]]

    predictor = Predictor()
    task_id = "retry-timing-task"
    task_progress_registry.initialize(task_id, ["sample.pdf"])
    try:
        results, failures = await resilience.aio_extract_pages_with_failure_isolation(
            predictor,
            ["page"],
            page_start_index=0,
            image_analysis=True,
            task_id=task_id,
            source_file_name="sample.pdf",
        )
        snapshot = task_progress_registry.snapshot(task_id)
    finally:
        task_progress_registry.remove(task_id)

    assert predictor.attempts == 2
    assert results == [[{"page": "page"}]]
    assert failures == []
    page = snapshot["files"][0]["pages"][0]
    assert page["attempts"] == 2
    assert page["vlm_request_seconds"] >= 0
    assert page["retry_wait_seconds"] >= 0
    assert page["elapsed_seconds"] >= page["vlm_request_seconds"]
    assert [item["outcome"] for item in page["attempt_details"]] == [
        "retry",
        "completed",
    ]
    assert page["successful_attempt_seconds"] >= 0
    assert page["failed_attempt_seconds"] >= 0
    assert page["retry_overhead_seconds"] >= 0
    assert page["with_retry_wall_seconds"] == page["elapsed_seconds"]
    assert any(
        "event=vlm_page_timing" in message
        and "status=completed" in message
        and "attempts=2" in message
        for message in page_timing_logs
    )


@pytest.mark.asyncio
async def test_wait_for_timeout_skips_only_the_slow_page(monkeypatch):
    monkeypatch.setattr(
        resilience,
        "get_vlm_page_timeout_seconds",
        lambda default: 0.01,
    )

    class Predictor:
        async def aio_batch_two_step_extract(self, images, image_analysis):
            if images[0] == "slow":
                await asyncio.Event().wait()
            return [[{"page": images[0]}]]

    results, failures = await resilience.aio_extract_pages_with_failure_isolation(
        Predictor(),
        ["fast", "slow"],
        page_start_index=0,
        image_analysis=True,
        task_id=None,
        source_file_name=None,
    )

    assert results == [[{"page": "fast"}], []]
    assert failures[0]["page_number"] == 2
    assert failures[0]["error_type"] == "TimeoutError"
    assert failures[0]["attempts"] == 1
    assert failures[0]["vlm_request_seconds"] >= 0.01
    assert failures[0]["elapsed_seconds"] >= failures[0]["vlm_request_seconds"]


@pytest.mark.asyncio
async def test_explicit_timeout_overrides_environment_default(monkeypatch):
    def unexpected_default(*_args, **_kwargs):
        raise AssertionError("environment timeout getter should not be used")

    monkeypatch.setattr(resilience, "get_vlm_page_timeout_seconds", unexpected_default)
    task_id = "explicit-timeout-task"
    task_progress_registry.initialize(task_id, ["sample.pdf"])

    class Predictor:
        async def aio_batch_two_step_extract(self, images, image_analysis):
            return [[{"page": images[0]}]]

    try:
        results, failures = await resilience.aio_extract_pages_with_failure_isolation(
            Predictor(),
            ["page"],
            page_start_index=0,
            image_analysis=True,
            task_id=task_id,
            source_file_name="sample.pdf",
            timeout_seconds=123.0,
        )
        page = task_progress_registry.snapshot(task_id)["files"][0]["pages"][0]
    finally:
        task_progress_registry.remove(task_id)

    assert results == [[{"page": "page"}]]
    assert failures == []
    assert page["attempt_details"][0]["timeout_seconds"] == 123.0


@pytest.mark.asyncio
async def test_explicit_zero_retries_disables_connect_retry(monkeypatch):
    monkeypatch.setenv("MINERU_VLM_CONNECT_MAX_RETRIES", "3")

    class Predictor:
        attempts = 0

        async def aio_batch_two_step_extract(self, images, image_analysis):
            self.attempts += 1
            raise httpx.ConnectError("connection refused")

    predictor = Predictor()
    results, failures = await resilience.aio_extract_pages_with_failure_isolation(
        predictor,
        ["page"],
        page_start_index=0,
        image_analysis=True,
        task_id=None,
        source_file_name=None,
        connect_max_retries=0,
    )

    assert predictor.attempts == 1
    assert results == [[]]
    assert failures[0]["attempts"] == 1
    assert failures[0]["attempt_details"][0]["outcome"] == "skipped"


@pytest.mark.asyncio
async def test_micro_batch_preserves_page_order():
    class Predictor:
        def __init__(self):
            self.calls = []

        async def aio_batch_two_step_extract(self, images, image_analysis):
            self.calls.append(list(images))
            return [{"page": image} for image in images]

    predictor = Predictor()
    results, failures = await resilience.aio_extract_pages_with_failure_isolation(
        predictor,
        ["one", "two", "three", "four"],
        page_start_index=0,
        image_analysis=False,
        task_id=None,
        source_file_name=None,
        batch_size=2,
    )

    assert predictor.calls == [["one", "two"], ["three", "four"]]
    assert results == [
        {"page": "one"},
        {"page": "two"},
        {"page": "three"},
        {"page": "four"},
    ]
    assert failures == []


@pytest.mark.asyncio
async def test_micro_batch_failure_falls_back_to_isolated_pages():
    class Predictor:
        def __init__(self):
            self.calls = []

        async def aio_batch_two_step_extract(self, images, image_analysis):
            self.calls.append(list(images))
            if len(images) > 1:
                raise httpx.ReadTimeout("batch timed out")
            if images[0] == "slow":
                raise httpx.ReadTimeout("page timed out")
            return [{"page": images[0]}]

    predictor = Predictor()
    task_id = "micro-batch-fallback-task"
    task_progress_registry.initialize(task_id, ["sample.pdf"])
    try:
        results, failures = await resilience.aio_extract_pages_with_failure_isolation(
            predictor,
            ["first", "slow"],
            page_start_index=0,
            image_analysis=True,
            task_id=task_id,
            source_file_name="sample.pdf",
            connect_max_retries=0,
            batch_size=2,
        )
        pages = task_progress_registry.snapshot(task_id)["files"][0]["pages"]
    finally:
        task_progress_registry.remove(task_id)

    assert predictor.calls == [["first", "slow"], ["first"], ["slow"]]
    assert results == [{"page": "first"}, []]
    assert failures[0]["page_number"] == 2
    assert [item["outcome"] for item in pages[0]["attempt_details"]] == [
        "batch_fallback",
        "completed",
    ]
    assert [item["outcome"] for item in pages[1]["attempt_details"]] == [
        "batch_fallback",
        "skipped",
    ]
    assert pages[0]["attempts"] == 2
    assert pages[1]["attempts"] == 2


@pytest.mark.asyncio
async def test_global_page_concurrency_is_enforced(monkeypatch):
    monkeypatch.setenv("MINERU_VLM_GLOBAL_PAGE_CONCURRENCY", "2")
    resilience._vlm_page_semaphores.clear()
    active = 0
    max_active = 0
    release = asyncio.Event()

    class Predictor:
        async def aio_batch_two_step_extract(self, images, image_analysis):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            try:
                if active == 2:
                    release.set()
                await release.wait()
                await asyncio.sleep(0)
                return [[{"page": images[0]}]]
            finally:
                active -= 1

    results, failures = await resilience.aio_extract_pages_with_failure_isolation(
        Predictor(),
        ["one", "two", "three", "four"],
        page_start_index=0,
        image_analysis=True,
        task_id=None,
        source_file_name=None,
    )

    assert max_active == 2
    assert len(results) == 4
    assert failures == []
