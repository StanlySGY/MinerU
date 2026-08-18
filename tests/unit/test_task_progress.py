import asyncio

from mineru.backend.vlm.resilience import aio_extract_pages_with_failure_isolation
from mineru.utils import task_progress as task_progress_module
from mineru.utils.task_progress import TaskProgressRegistry, task_progress_registry


def test_task_progress_preserves_first_start_and_terminal_timing(monkeypatch):
    class Clock:
        def __init__(self):
            self.value = 10.0

        def monotonic(self):
            return self.value

    clock = Clock()
    monkeypatch.setattr(task_progress_module, "time", clock)
    registry = TaskProgressRegistry()
    registry.initialize("timing-task", ["sample.pdf"])
    registry.queue_pages("timing-task", "sample.pdf", 0, 1)

    clock.value = 12.345
    registry.page_started("timing-task", "sample.pdf", 0, 1)
    first_started_at = registry.snapshot("timing-task")["files"][0]["pages"][0]["started_at"]
    clock.value = 13.0
    registry.page_started("timing-task", "sample.pdf", 0, 2)
    clock.value = 18.678
    registry.page_completed("timing-task", "sample.pdf", 0, 2)

    page = registry.snapshot("timing-task")["files"][0]["pages"][0]
    terminal_event = registry.snapshot("timing-task")["events"][-1]
    assert page["started_at"] == first_started_at
    assert page["attempts"] == 2
    assert page["queue_seconds"] == 2.35
    assert page["inference_seconds"] == 6.33
    assert page["vlm_request_seconds"] == 6.33
    assert page["total_seconds"] == 8.68
    assert terminal_event["inference_seconds"] == 6.33
    assert terminal_event["total_seconds"] == 8.68


def test_task_progress_tracks_concurrent_page_outcomes():
    registry = TaskProgressRegistry()
    registry.initialize("task-1", ["sample.pdf"])
    registry.queue_pages("task-1", "sample.pdf", 0, 3)

    registry.page_started("task-1", "sample.pdf", 0, 1)
    registry.page_started("task-1", "sample.pdf", 1, 1)
    registry.page_completed("task-1", "sample.pdf", 0, 1)
    registry.page_skipped(
        "task-1",
        "sample.pdf",
        {
            "page_idx": 1,
            "page_number": 2,
            "error_type": "TimeoutError",
            "error": "page timed out",
            "attempts": 1,
        },
    )

    snapshot = registry.snapshot("task-1")

    assert snapshot["total_pages"] == 3
    assert snapshot["completed_pages"] == 1
    assert snapshot["skipped_pages"] == 1
    assert snapshot["queued_pages"] == 1
    assert snapshot["skipped_page_numbers"] == [2]
    assert snapshot["events"][-1]["type"] == "page_skipped"


def test_task_progress_registers_total_pages_before_window_processing():
    registry = TaskProgressRegistry()
    registry.initialize("task-total", ["sample.pdf"])
    registry.register_file_pages("task-total", "sample.pdf", 20)

    initial = registry.snapshot("task-total")
    registry.queue_pages("task-total", "sample.pdf", 0, 8)
    first_window = registry.snapshot("task-total")

    assert initial["total_pages"] == 20
    assert initial["queued_pages"] == 20
    assert first_window["total_pages"] == 20
    assert first_window["queued_pages"] == 20
    assert first_window["files"][0]["total_pages"] == 20


def test_task_progress_is_best_effort_for_unknown_tasks():
    registry = TaskProgressRegistry()

    registry.queue_pages("missing", "sample.pdf", 0, 2)
    registry.page_started("missing", "sample.pdf", 0, 1)
    registry.page_completed("missing", "sample.pdf", 0, 1)

    assert registry.snapshot("missing") == registry.empty_snapshot()


def test_vlm_resilience_emits_page_progress(monkeypatch) -> None:
    class Predictor:
        async def aio_batch_two_step_extract(self, images, image_analysis):
            assert image_analysis is False
            return [{"page": "ok"} for _ in images]

    task_id = "progress-integration-task"
    monkeypatch.setenv("MINERU_VLM_GLOBAL_PAGE_CONCURRENCY", "1")
    task_progress_registry.initialize(task_id, ["sample.pdf"])
    try:
        results, failures = asyncio.run(
            aio_extract_pages_with_failure_isolation(
                Predictor(),
                [object(), object()],
                page_start_index=0,
                image_analysis=False,
                task_id=task_id,
                source_file_name="sample.pdf",
            )
        )
        snapshot = task_progress_registry.snapshot(task_id)
    finally:
        task_progress_registry.remove(task_id)

    assert results == [{"page": "ok"}, {"page": "ok"}]
    assert failures == []
    assert snapshot["completed_pages"] == 2
    assert snapshot["processing_pages"] == 0
    pages = snapshot["files"][0]["pages"]
    assert all(page["status"] == "completed" for page in pages)
    assert all(page["attempts"] == 1 for page in pages)
    assert all(page["queue_seconds"] >= 0 for page in pages)
    assert all(page["vlm_request_seconds"] >= 0 for page in pages)
    assert all(page["total_seconds"] >= 0 for page in pages)
    assert all(not any(key.startswith("_") for key in page) for page in pages)
