import asyncio

from mineru.backend.vlm.resilience import aio_extract_pages_with_failure_isolation
from mineru.utils.task_progress import TaskProgressRegistry
from mineru.utils.task_progress import task_progress_registry


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
