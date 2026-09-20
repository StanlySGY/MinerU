import asyncio
import json
import signal
import struct
import zipfile
from pathlib import Path
from tempfile import SpooledTemporaryFile

import httpx
import pytest
import yaml
from fastapi import HTTPException, UploadFile

from mineru.cli import ops as ops_module
from mineru.cli.ops import (
    BatchRunRequest,
    OpsRuntime,
    OpsStore,
    ProblemPageSelection,
    ProblemPagesRetryRequest,
    apply_service_runtime_health,
    build_smoke_test_pdf,
    create_app,
    slim_task_for_list,
    strip_pages_from_progress,
)


class FakeBatchProcess:
    def __init__(self, *, wait_timeout: bool = False):
        self.returncode = None
        self.signals = []
        self.events = []
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_timeout = wait_timeout
        self.stdout = None
        self._exit_event = asyncio.Event()

    def send_signal(self, sig):
        self.signals.append(sig)
        self.events.append(("signal", sig))

    def terminate(self):
        self.terminate_calls += 1
        self.events.append(("terminate", None))
        if not self.wait_timeout:
            self.returncode = -15
            self._exit_event.set()

    def kill(self):
        self.kill_calls += 1
        self.events.append(("kill", None))
        self.returncode = -9
        self._exit_event.set()

    async def wait(self):
        if self.returncode is None:
            await self._exit_event.wait()
        return self.returncode


def _create_batch_record(
    runtime: OpsRuntime,
    tmp_path: Path,
    run_id: str,
    status: str,
) -> dict:
    run_dir = runtime.report_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    runtime.store.create_batch_run(
        run_id,
        tmp_path,
        {
            "input_path": ".",
            "pdf_count": 1,
        },
        run_dir / "BATCH_DIAGNOSIS.md",
        run_dir / "raw",
        run_dir / "batch.log",
    )
    runtime.store.update_batch_run(run_id, status=status)
    record = runtime.store.get_batch_run(run_id)
    assert record is not None
    return record


def test_ops_store_persists_task_snapshots(tmp_path: Path):
    store = OpsStore(tmp_path / "ops.db")
    payload = {"task_id": "task-1", "status": "processing", "file_names": ["a.pdf"]}

    store.upsert_tasks([payload])

    assert store.cached_task("task-1") == payload
    assert store.cached_tasks() == [payload]


def test_ops_store_task_delete_cascades_page_details(tmp_path: Path):
    store = OpsStore(tmp_path / "ops.db")
    payload = {
        "task_id": "stale-task",
        "status": "failed",
        "progress": {"files": [{"file_name": "a.pdf", "pages": [{"page_idx": 0, "page_number": 1, "status": "failed"}]}]},
    }
    store.upsert_tasks([payload])

    assert store.delete_task_snapshots(["stale-task"]) == 1
    assert store.cached_task("stale-task") is None
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM task_page_timings WHERE task_id = ?", ("stale-task",)).fetchone()[0] == 0


def test_ops_store_preserves_batch_source_when_live_snapshot_updates(tmp_path: Path):
    store = OpsStore(tmp_path / "ops.db")
    store.upsert_tasks([{"task_id": "batch-task", "status": "processing", "source_batch_run_id": "run-1"}])
    store.upsert_tasks([{"task_id": "batch-task", "status": "completed"}])

    assert store.cached_task("batch-task")["source_batch_run_id"] == "run-1"
    assert store.cached_task("batch-task")["status"] == "completed"


def test_ops_store_deletes_all_snapshots_for_batch(tmp_path: Path):
    store = OpsStore(tmp_path / "ops.db")
    store.upsert_tasks([
        {"task_id": "batch-a", "status": "completed", "source_batch_run_id": "run-1"},
        {"task_id": "batch-b", "status": "failed", "source_batch_run_id": "run-1"},
        {"task_id": "other", "status": "completed", "source_batch_run_id": "run-2"},
    ])

    assert store.delete_task_snapshots_for_batch("run-1") == 2
    assert store.cached_task("batch-a") is None
    assert store.cached_task("batch-b") is None
    assert store.cached_task("other") is not None


def test_ops_store_cleanup_keeps_explicitly_retained_tasks(tmp_path: Path):
    store = OpsStore(tmp_path / "ops.db")
    store.upsert_tasks([
        {"task_id": "old-task", "status": "failed"},
        {"task_id": "kept-task", "status": "failed"},
    ])
    with store.connect() as connection:
        connection.execute("UPDATE task_snapshots SET updated_at = ?", ("2020-01-01T00:00:00+00:00",))

    assert store.cleanup_stale_task_snapshots(
        keep_task_ids={"kept-task"},
        updated_before="2021-01-01T00:00:00+00:00",
    ) == 1
    assert store.cached_task("old-task") is None
    assert store.cached_task("kept-task") is not None


def test_batch_reaper_marks_orphaned_active_record_interrupted(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    runtime = OpsRuntime()
    runtime.batch_orphan_grace_seconds = 0
    _create_batch_record(runtime, tmp_path, "orphan-run", "running")

    repaired = asyncio.run(runtime.reap_batch_runs())

    assert repaired == ["orphan-run"]
    record = runtime.store.get_batch_run("orphan-run")
    assert record["status"] == "interrupted"
    assert "没有对应的运行进程" in record["error"]
    asyncio.run(runtime.close())


def test_ops_store_lists_audit_logs(tmp_path: Path):
    store = OpsStore(tmp_path / "ops.db")
    store.audit("first", "target-a", True, "{}")
    store.audit("second", "target-b", False, "failed")

    payload = store.list_audit_logs(limit=1, offset=0)

    assert payload["total"] == 2
    assert payload["limit"] == 1
    assert payload["offset"] == 0
    assert payload["items"][0]["action"] == "second"
    assert payload["items"][0]["target"] == "target-b"
    assert payload["items"][0]["success"] is False

    next_page = store.list_audit_logs(limit=1, offset=1)
    assert next_page["items"][0]["action"] == "first"
    assert next_page["items"][0]["success"] is True


def test_runtime_diagnostics_is_offline_and_degrades_without_devices(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("MINERU_MODEL_SOURCE", "local")
    monkeypatch.setenv("MODELSCOPE_OFFLINE", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "true")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "yes")
    monkeypatch.setattr(
        OpsRuntime,
        "_safe_command",
        staticmethod(
            lambda command, timeout=3.0: {
                "available": False,
                "ok": False,
                "error": "command not found",
            }
        ),
    )

    runtime = OpsRuntime()
    diagnostics = runtime.runtime_diagnostics()

    assert diagnostics["offline"] == {
        "configured": True,
        "model_source": "local",
        "modelscope_disabled": True,
        "huggingface_disabled": True,
        "transformers_disabled": True,
        "network_access": "not_checked",
    }
    assert diagnostics["commands"]["nvidia_smi"]["available"] is False
    assert diagnostics["commands"]["npu_smi"]["available"] is False
    assert diagnostics["devices"] == []
    assert diagnostics["models"][0]["path"] == "/models/pipeline"
    assert diagnostics["warnings"]
    asyncio.run(runtime.close())


def test_ops_store_persists_and_summarizes_page_timings(tmp_path: Path):
    store = OpsStore(tmp_path / "ops.db")
    pages = [
        {
            "page_idx": index,
            "page_number": index + 1,
            "status": "completed",
            "queue_seconds": 1.0,
            "vlm_request_seconds": duration,
            "retry_wait_seconds": 0.0,
            "elapsed_seconds": duration,
            "total_seconds": duration + 1.0,
            "attempts": 1,
        }
        for index, duration in enumerate([10.0, 20.0, 30.0, 40.0])
    ]
    pages.append(
        {
            "page_idx": 4,
            "page_number": 5,
            "status": "skipped",
            "queue_seconds": 2.0,
            "inference_seconds": 600.0,
            "elapsed_seconds": 602.0,
            "attempts": 1,
            "error_type": "TimeoutError",
            "error": "page timed out",
        }
    )
    pages[0]["attempts"] = 2
    pages[0]["retry_wait_seconds"] = 1.25
    pages[0]["attempt_details"] = [
        {
            "attempt_no": 1,
            "started_at": "2026-08-19T00:00:00+00:00",
            "completed_at": "2026-08-19T00:00:03+00:00",
            "outcome": "retry",
            "error_type": "ConnectError",
            "error": "temporary connection failure",
            "request_seconds": 3.0,
            "retry_wait_before_seconds": 0.0,
            "timeout_seconds": 600.0,
            "batch_size": 1,
        },
        {
            "attempt_no": 2,
            "started_at": "2026-08-19T00:00:04+00:00",
            "completed_at": "2026-08-19T00:00:11+00:00",
            "outcome": "completed",
            "request_seconds": 7.0,
            "retry_wait_before_seconds": 1.25,
            "timeout_seconds": 600.0,
            "batch_size": 1,
        },
    ]
    payload = {
        "task_id": "timing-task",
        "status": "completed",
        "progress": {
            "files": [
                {"file_name": "sample.pdf", "total_pages": 5, "pages": pages}
            ]
        },
    }

    store.upsert_tasks([payload])
    store.upsert_tasks([payload])

    result = store.page_timings(
        "timing-task",
        sort="vlm_request_seconds",
        descending=True,
    )
    summary = store.page_timing_summary("timing-task", slow_page_seconds=30.0)
    assert result["total"] == 5
    assert result["items"][0]["page_number"] == 5
    assert result["items"][0]["vlm_request_seconds"] == 600.0
    first_page = next(item for item in result["items"] if item["page_number"] == 1)
    assert [item["outcome"] for item in first_page["attempt_details"]] == [
        "retry",
        "completed",
    ]
    assert first_page["successful_attempt_seconds"] == 7.0
    assert first_page["failed_attempt_seconds"] == 3.0
    assert first_page["retry_overhead_seconds"] == 1.25
    with store.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM task_page_attempts WHERE task_id = ?",
            ("timing-task",),
        ).fetchone()[0] == 2
    assert summary["recorded_pages"] == 5
    assert summary["completed_pages"] == 4
    assert summary["skipped_pages"] == 1
    assert summary["completed_average_vlm_request_seconds"] == 25.0
    assert summary["completed_p50_vlm_request_seconds"] == 25.0
    assert summary["completed_p95_vlm_request_seconds"] == 38.5
    assert summary["completed_p99_vlm_request_seconds"] == 39.7
    assert summary["completed_max_vlm_request_seconds"] == 40.0
    assert summary["slow_pages"] == 3
    assert summary["timeout_pages"] == 1
    assert summary["timeout_rate"] == pytest.approx(0.2)
    assert summary["retry_pages"] == 1
    assert summary["retry_rate"] == pytest.approx(0.2)
    assert summary["successful_attempt_seconds"] == pytest.approx(7.0)
    assert summary["failed_attempt_seconds"] == pytest.approx(3.0)
    assert summary["retry_overhead_seconds"] == pytest.approx(1.25)
    assert summary["with_retry_wall_seconds"] == pytest.approx(706.0)
    skipped_summary = store.page_timing_summary("timing-task", status="skipped")
    assert skipped_summary["recorded_pages"] == 1
    assert skipped_summary["completed_pages"] == 0
    assert skipped_summary["completed_p95_vlm_request_seconds"] is None


def _seed_report_task(store: OpsStore, *, page_count: int = 3) -> dict:
    pages = [
        {
            "page_idx": index,
            "page_number": index + 1,
            "status": "completed",
            "queue_seconds": 1.5,
            "vlm_request_seconds": 10.0 + index,
            "retry_wait_seconds": 0.0,
            "elapsed_seconds": 10.0 + index,
            "total_seconds": 11.5 + index,
            "attempts": 1,
        }
        for index in range(page_count)
    ]
    pages.append(
        {
            "page_idx": page_count,
            "page_number": page_count + 1,
            "status": "failed",
            "queue_seconds": 2.0,
            "vlm_request_seconds": 300.0,
            "retry_wait_seconds": 5.0,
            "total_seconds": 307.0,
            "attempts": 3,
            "error_type": "TimeoutError",
            "error": "page | timed out\nafter retries",
            "attempt_details": [
                {
                    "attempt_no": 1,
                    "outcome": "retry",
                    "request_seconds": 100.0,
                    "retry_wait_before_seconds": 0.0,
                    "timeout_seconds": 600.0,
                    "batch_size": 1,
                },
                {
                    "attempt_no": 2,
                    "outcome": "retry",
                    "request_seconds": 100.0,
                    "retry_wait_before_seconds": 2.0,
                    "timeout_seconds": 600.0,
                    "batch_size": 1,
                },
                {
                    "attempt_no": 3,
                    "outcome": "failed",
                    "request_seconds": 100.0,
                    "retry_wait_before_seconds": 3.0,
                    "timeout_seconds": 600.0,
                    "batch_size": 1,
                },
            ],
        }
    )
    payload = {
        "task_id": "report-task",
        "status": "completed",
        "backend": "vlm-http-client",
        "file_names": ["report.pdf"],
        "created_at": "2026-08-19T00:00:00+00:00",
        "started_at": "2026-08-19T00:00:01+00:00",
        "completed_at": "2026-08-19T00:05:00+00:00",
        "progress": {
            "total_pages": page_count + 1,
            "completed_pages": page_count,
            "skipped_pages": 0,
            "failed_pages": 1,
            "files": [
                {"file_name": "report.pdf", "total_pages": page_count + 1, "pages": pages}
            ],
        },
    }
    store.upsert_tasks([payload])
    return payload


def test_task_report_markdown_lists_every_page(tmp_path: Path) -> None:
    store = OpsStore(tmp_path / "ops.db")
    task = _seed_report_task(store, page_count=3)

    items = store.all_page_timings("report-task")
    summary = store.page_timing_summary("report-task", slow_page_seconds=30.0)
    content = OpsRuntime.task_report_markdown(task, {"items": items, "summary": summary})

    assert len(items) == 4
    assert [row["page_number"] for row in items] == [1, 2, 3, 4]
    assert "# MinerU 任务耗时报告" in content
    assert "report-task" in content
    assert "report.pdf" in content
    assert "## 耗时摘要" in content
    assert "## 逐页耗时" in content
    # the Ops-side display threshold says nothing about the runtime page timeout,
    # so it must not appear in a report meant for diagnosing per-page duration
    assert "慢页阈值" not in content
    assert "慢页数量" not in content
    assert "最慢请求" in content
    # the four aggregate stats cover completed pages only; say so, or a 600s
    # skipped page silently hides behind a small "最慢请求"
    assert "仅基于已完成页面" in content
    # every page has its own row, none truncated away
    for page_number in (1, 2, 3, 4):
        assert f"| {page_number} |" in content
    # pipe and newline in the error must not break the table layout
    assert "page \\| timed out after retries" in content
    assert content.count("\n|") >= 6


def test_task_report_csv_contains_full_page_rows(tmp_path: Path) -> None:
    import csv as csv_module
    import io as io_module

    store = OpsStore(tmp_path / "ops.db")
    _seed_report_task(store, page_count=3)

    items = store.all_page_timings("report-task")
    content = OpsRuntime.task_report_csv({"items": items})
    rows = list(csv_module.reader(io_module.StringIO(content)))

    assert rows[0] == [
        "file_name",
        "page_number",
        "page_idx",
        "status",
        "queue_seconds",
        "successful_attempt_seconds",
        "failed_attempt_seconds",
        "vlm_request_seconds",
        "retry_overhead_seconds",
        "total_seconds",
        "attempts",
        "error_type",
        "error",
    ]
    assert len(rows) == 5  # header + 4 pages
    assert [row[1] for row in rows[1:]] == ["1", "2", "3", "4"]
    failed_row = rows[-1]
    assert failed_row[3] == "failed"
    assert failed_row[5] == "0"
    assert failed_row[6] == "300.0"
    assert failed_row[8] == "5.0"
    assert failed_row[10] == "3"
    assert failed_row[11] == "TimeoutError"


def test_all_page_timings_filters_by_status(tmp_path: Path) -> None:
    store = OpsStore(tmp_path / "ops.db")
    _seed_report_task(store, page_count=3)

    assert len(store.all_page_timings("report-task", status="completed")) == 3
    assert len(store.all_page_timings("report-task", status="failed")) == 1
    with pytest.raises(ValueError):
        store.all_page_timings("report-task", status="bogus")


def test_synthetic_timeout_progress_does_not_create_failed_page_timings(tmp_path: Path) -> None:
    store = OpsStore(tmp_path / "ops.db")
    store.upsert_tasks(
        [
            {
                "task_id": "synthetic-timeout-task",
                "status": "wait_timeout",
                "progress": {
                    "synthetic": True,
                    "total_pages": 2,
                    "unknown_pages": 2,
                    "failed_pages": 0,
                    "files": [
                        {
                            "file_name": "sample.pdf",
                            "pages": [
                                {"page_idx": 0, "page_number": 1, "status": "unknown"},
                                {"page_idx": 1, "page_number": 2, "status": "unknown"},
                            ],
                        }
                    ],
                },
            }
        ]
    )

    assert store.page_timings("synthetic-timeout-task")["items"] == []
    with store.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM task_page_attempts WHERE task_id = ?",
            ("synthetic-timeout-task",),
        ).fetchone()[0] == 0


def test_fallback_batch_progress_keeps_wait_timeout_pages_unknown(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    runtime = OpsRuntime()
    run_dir = runtime.report_dir / "run-timeout"
    input_pdf = run_dir / "input/sample.pdf"
    input_pdf.parent.mkdir(parents=True)
    input_pdf.write_bytes(build_smoke_test_pdf())
    record = runtime.store.create_batch_run(
        "run-timeout",
        run_dir / "input",
        {"input_path": "browser upload", "pdf_count": 1},
        run_dir / "BATCH_DIAGNOSIS.md",
        run_dir / "raw",
        run_dir / "batch.log",
    )
    preview = {
        "file_name": "sample.pdf",
        "relative_path": "sample.pdf",
        "task_status": "wait_timeout",
        "classification": "monitoring_stopped",
        "failed_pages": [],
    }

    progress = runtime._fallback_batch_progress(
        record,
        preview,
        runtime.batch_artifacts(record),
    )

    assert progress["phase"] == "monitoring_stopped"
    assert progress["synthetic"] is True
    assert progress["total_pages"] == 1
    assert progress["unknown_pages"] == 1
    assert progress["failed_pages"] == 0
    assert progress["files"][0]["pages"][0]["status"] == "unknown"
    asyncio.run(runtime.close())


def test_problem_pages_archive_exports_original_page_numbers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    runtime = OpsRuntime()
    run_dir = runtime.report_dir / "run-problem-pages"
    input_pdf = run_dir / "input/folder/sample.pdf"
    input_pdf.parent.mkdir(parents=True)
    input_pdf.write_bytes(b"%PDF-original")
    result_dir = run_dir / "results/0001-sample"
    result_dir.mkdir(parents=True)
    (result_dir / "preview.json").write_text(
        json.dumps(
            {
                "task_id": "task-problem-pages",
                "file_name": "sample.pdf",
                "relative_path": "folder/sample.pdf",
                "task_status": "completed",
                "classification": "partial",
                "progress": {
                    "files": [
                        {
                            "file_name": "sample.pdf",
                            "pages": [
                                {"page_idx": 0, "page_number": 1, "status": "completed"},
                                {
                                    "page_idx": 1,
                                    "page_number": 2,
                                    "status": "skipped",
                                    "error_type": "TimeoutError",
                                    "error": "page timed out",
                                },
                                {
                                    "page_idx": 3,
                                    "page_number": 4,
                                    "status": "failed",
                                    "error_type": "RuntimeError",
                                    "error": "model failed",
                                },
                            ],
                        }
                    ]
                },
                "failed_pages": [
                    {
                        "page_idx": 1,
                        "page_number": 2,
                        "status": "skipped",
                        "error_type": "TimeoutError",
                        "error": "page timed out",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    record = runtime.store.create_batch_run(
        "run-problem-pages",
        run_dir / "input",
        {"input_path": "browser upload", "pdf_count": 1},
        run_dir / "BATCH_DIAGNOSIS.md",
        run_dir / "raw",
        run_dir / "batch.log",
    )
    selected_indices = []

    def fake_rewrite(source_bytes: bytes, *, page_indices: list[int]) -> bytes:
        assert source_bytes == b"%PDF-original"
        selected_indices.append(page_indices)
        return b"%PDF-problem-pages"

    monkeypatch.setattr(
        "mineru.cli.ops.rewrite_pdf_bytes_with_pdfium",
        fake_rewrite,
    )

    archive_path = runtime.build_problem_pages_archive(record)

    assert selected_indices == [[1, 3]]
    with zipfile.ZipFile(archive_path) as archive:
        assert set(archive.namelist()) == {
            "problem-pages/0001-sample-problem-pages.pdf",
            "manifest.json",
            "manifest.csv",
        }
        assert archive.read("problem-pages/0001-sample-problem-pages.pdf") == b"%PDF-problem-pages"
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["files"][0]["original_page_numbers"] == [2, 4]
        assert manifest["files"][0]["exported_page_count"] == 2
        assert "2,4" in archive.read("manifest.csv").decode("utf-8")
    asyncio.run(runtime.close())


def test_cancel_pending_batch_marks_it_cancelled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    runtime = OpsRuntime()
    _create_batch_record(runtime, tmp_path, "pending-run", "pending")

    result = asyncio.run(runtime.cancel_batch("pending-run"))

    assert result["status"] == "cancelled"
    assert result["completed_at"] is not None
    assert "pending-run" in runtime.batch_cancel_requested
    asyncio.run(runtime.close())


def test_cancel_running_batch_terminates_process(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    runtime = OpsRuntime()
    _create_batch_record(runtime, tmp_path, "running-run", "running")
    process = FakeBatchProcess()
    runtime.batch_processes["running-run"] = process

    result = asyncio.run(runtime.cancel_batch("running-run"))

    assert result["status"] == "cancelled"
    assert result["exit_code"] == -15
    assert process.terminate_calls == 1
    assert process.kill_calls == 0
    runtime.batch_processes.pop("running-run")
    asyncio.run(runtime.close())


def test_cancel_paused_batch_resumes_before_terminate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    runtime = OpsRuntime()
    _create_batch_record(runtime, tmp_path, "paused-run", "paused")
    process = FakeBatchProcess()
    runtime.batch_processes["paused-run"] = process

    result = asyncio.run(runtime.cancel_batch("paused-run"))

    assert result["status"] == "cancelled"
    assert process.events[:2] == [
        ("signal", signal.SIGCONT),
        ("terminate", None),
    ]
    runtime.batch_processes.pop("paused-run")
    asyncio.run(runtime.close())


def test_terminate_batch_process_kills_after_timeout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    runtime = OpsRuntime()
    process = FakeBatchProcess(wait_timeout=True)

    returncode = asyncio.run(
        runtime._terminate_batch_process(
            "timeout-run",
            process,
            timeout=0.01,
        )
    )

    assert returncode == -9
    assert process.events == [
        ("terminate", None),
        ("kill", None),
    ]
    asyncio.run(runtime.close())


def test_batch_runner_does_not_overwrite_cancelled_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    runtime = OpsRuntime()
    _create_batch_record(runtime, tmp_path, "runner-cancelled", "pending")
    run_dir = runtime.report_dir / "runner-cancelled"
    process = FakeBatchProcess()
    process.returncode = 0
    diagnostics_collected = False

    async def fake_create_subprocess_exec(*args, **kwargs):
        runtime.batch_cancel_requested.add("runner-cancelled")
        return process

    async def fake_collect_batch_diagnostics(*args, **kwargs):
        nonlocal diagnostics_collected
        diagnostics_collected = True

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(runtime, "_collect_batch_diagnostics", fake_collect_batch_diagnostics)

    asyncio.run(
        runtime._run_batch_process(
            "runner-cancelled",
            tmp_path,
            BatchRunRequest(),
            run_dir / "BATCH_DIAGNOSIS.md",
            run_dir / "raw",
            run_dir / "results",
            run_dir / "batch.log",
        )
    )

    result = runtime.store.get_batch_run("runner-cancelled")
    assert result is not None
    assert result["status"] == "cancelled"
    assert result["exit_code"] == 0
    assert diagnostics_collected is False
    asyncio.run(runtime.close())


def test_cancel_batch_rejects_duplicate_stopping_request(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    runtime = OpsRuntime()
    _create_batch_record(runtime, tmp_path, "stopping-run", "cancelling")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(runtime.cancel_batch("stopping-run"))

    assert exc_info.value.status_code == 409
    assert "already stopping" in str(exc_info.value.detail)
    asyncio.run(runtime.close())


def test_close_terminates_paused_batch_and_kills_after_timeout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    monkeypatch.setattr(ops_module, "BATCH_PROCESS_TERMINATE_TIMEOUT_SECONDS", 0.01)
    runtime = OpsRuntime()
    _create_batch_record(runtime, tmp_path, "close-run", "paused")
    process = FakeBatchProcess(wait_timeout=True)
    runtime.batch_processes["close-run"] = process

    asyncio.run(runtime.close())

    result = runtime.store.get_batch_run("close-run")
    assert result is not None
    assert result["status"] == "cancelled"
    assert process.events == [
        ("signal", signal.SIGCONT),
        ("terminate", None),
        ("kill", None),
    ]


@pytest.mark.parametrize("source_status", ["running", "cancelling"])
def test_retry_problem_pages_requires_terminal_source_batch(
    tmp_path: Path,
    monkeypatch,
    source_status: str,
) -> None:
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    runtime = OpsRuntime()
    source_record = {
        "run_id": "active-source",
        "status": source_status,
        "settings": {},
    }

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            runtime.retry_problem_pages(
                source_record,
                ProblemPagesRetryRequest(),
            )
        )

    assert exc_info.value.status_code == 409
    assert "terminal state" in str(exc_info.value.detail)
    asyncio.run(runtime.close())


def test_retry_problem_pages_creates_linked_batch_with_timeout_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    runtime = OpsRuntime()
    runtime.batch_script = tmp_path / "batch.py"
    runtime.batch_script.write_text("pass\n", encoding="utf-8")
    source_run_dir = runtime.report_dir / "source-run"
    input_pdf = source_run_dir / "input/sample.pdf"
    input_pdf.parent.mkdir(parents=True)
    input_pdf.write_bytes(b"%PDF-original")
    result_dir = source_run_dir / "results/0001-sample"
    result_dir.mkdir(parents=True)
    (result_dir / "preview.json").write_text(
        json.dumps(
            {
                "task_id": "source-task",
                "file_name": "sample.pdf",
                "relative_path": "sample.pdf",
                "progress": {
                    "files": [
                        {
                            "file_name": "sample.pdf",
                            "pages": [
                                {"page_number": 1, "status": "completed"},
                                {
                                    "page_number": 2,
                                    "status": "skipped",
                                    "error_type": "TimeoutError",
                                    "error": "page timed out",
                                },
                            ],
                        }
                    ]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    source_record = runtime.store.create_batch_run(
        "source-run",
        source_run_dir / "input",
        {
            "input_path": "浏览器上传（1 个 PDF）",
            "source_type": "browser_upload",
            "pdf_count": 1,
            "backend": "vlm-http-client",
            "effort": "medium",
            "parse_method": "auto",
            "lang": "ch",
            "server_url": "http://vlm:30000",
            "recursive": True,
            "task_timeout": 7200,
            "page_timeout_seconds": 600,
            "page_connect_max_retries": 1,
            "vlm_batch_size": 4,
            "pause_seconds": 2,
        },
        source_run_dir / "BATCH_DIAGNOSIS.md",
        source_run_dir / "raw",
        source_run_dir / "batch.log",
    )
    runtime.store.update_batch_run("source-run", status="completed_with_failures")
    source_record = runtime.store.get_batch_run("source-run")
    assert source_record is not None

    def fake_rewrite(source_bytes: bytes, *, page_indices: list[int]) -> bytes:
        assert source_bytes == b"%PDF-original"
        assert page_indices == [1]
        return b"%PDF-retry-page"

    async def fake_batch_process(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "mineru.cli.ops.rewrite_pdf_bytes_with_pdfium",
        fake_rewrite,
    )
    monkeypatch.setattr(runtime, "_run_batch_process", fake_batch_process)

    async def run_test():
        result = await runtime.retry_problem_pages(
            source_record,
            ProblemPagesRetryRequest(
                page_timeout_seconds=1200,
                task_timeout=900,
                page_connect_max_retries=0,
                vlm_batch_size=1,
            ),
        )
        await asyncio.sleep(0)
        return result

    retry_record = asyncio.run(run_test())

    assert retry_record["settings"]["source_type"] == "problem_page_retry"
    assert retry_record["settings"]["retry_of_run_id"] == "source-run"
    assert retry_record["settings"]["page_timeout_seconds"] == 1200
    assert retry_record["settings"]["task_timeout"] == 1260
    assert retry_record["settings"]["page_connect_max_retries"] == 0
    assert retry_record["settings"]["vlm_batch_size"] == 1
    assert retry_record["settings"]["problem_page_count"] == 1
    assert retry_record["settings"]["problem_pages_manifest_path"] == "manifest.json"
    assert "problem_pages_manifest" not in retry_record["settings"]
    retry_input = Path(retry_record["input_path"])
    assert (retry_input / "0001-sample-problem-pages.pdf").read_bytes() == b"%PDF-retry-page"
    assert json.loads((retry_input / "manifest.json").read_text(encoding="utf-8"))[
        "files"
    ][0]["original_page_numbers"] == [2]
    assert runtime.resolve_artifact(
        retry_record,
        "input",
        "manifest.json",
    ) == retry_input / "manifest.json"
    assert runtime.resolve_artifact(
        retry_record,
        "input",
        "manifest.csv",
    ) == retry_input / "manifest.csv"
    asyncio.run(runtime.close())


def test_write_problem_pages_supports_per_page_selection_and_source_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    runtime = OpsRuntime()
    run_dir = runtime.report_dir / "source-run"
    input_dir = run_dir / "input"
    source_a = input_dir / "folder-a/shared.pdf"
    source_b = input_dir / "folder-b/shared.pdf"
    source_a.parent.mkdir(parents=True)
    source_b.parent.mkdir(parents=True)
    source_a.write_bytes(b"%PDF-source-a")
    source_b.write_bytes(b"%PDF-source-b")

    preview_payloads = [
        (
            "0001-shared-a",
            {
                "task_id": "task-a",
                "file_name": "shared.pdf",
                "relative_path": "folder-a/shared.pdf",
                "progress": {
                    "files": [
                        {
                            "file_name": "shared.pdf",
                            "relative_path": "folder-a/shared.pdf",
                            "pages": [
                                {"page_number": 1, "status": "completed"},
                                {
                                    "page_number": 2,
                                    "status": "skipped",
                                    "error_type": "TimeoutError",
                                    "error": "timed out",
                                },
                                {
                                    "page_number": 3,
                                    "status": "failed",
                                    "error_type": "RuntimeError",
                                    "error": "inference failed",
                                },
                            ],
                        }
                    ]
                },
            },
        ),
        (
            "0002-shared-b",
            {
                "task_id": "task-b",
                "file_name": "shared.pdf",
                "relative_path": "folder-b/shared.pdf",
                "progress": {
                    "files": [
                        {
                            "file_name": "shared.pdf",
                            "relative_path": "folder-b/shared.pdf",
                            "pages": [
                                {
                                    "page_number": 1,
                                    "status": "failed",
                                    "error_type": "ValueError",
                                    "error": "bad page",
                                },
                                {"page_number": 2, "status": "completed"},
                            ],
                        }
                    ]
                },
            },
        ),
    ]
    for directory_name, payload in preview_payloads:
        result_dir = run_dir / "results" / directory_name
        result_dir.mkdir(parents=True)
        (result_dir / "preview.json").write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

    runtime.store.create_batch_run(
        "source-run",
        input_dir,
        {"input_path": "browser upload", "source_type": "browser_upload", "pdf_count": 2},
        run_dir / "BATCH_DIAGNOSIS.md",
        run_dir / "raw",
        run_dir / "batch.log",
    )
    record = runtime.store.get_batch_run("source-run")
    assert record is not None

    rewrite_calls: list[tuple[bytes, list[int]]] = []

    def fake_rewrite(source_bytes: bytes, *, page_indices: list[int]) -> bytes:
        rewrite_calls.append((source_bytes, page_indices))
        return b"%PDF-selected-" + bytes(str(page_indices), encoding="ascii")

    monkeypatch.setattr(
        "mineru.cli.ops.rewrite_pdf_bytes_with_pdfium",
        fake_rewrite,
    )

    all_result = runtime._write_problem_pages(
        record,
        tmp_path / "all-problem-pages",
        output_prefix="",
    )

    assert all_result["exported_pdf_count"] == 2
    assert all_result["exported_page_count"] == 3
    assert rewrite_calls == [
        (b"%PDF-source-a", [1, 2]),
        (b"%PDF-source-b", [0]),
    ]
    assert [row["source_path"] for row in all_result["manifest"]["files"]] == [
        "folder-a/shared.pdf",
        "folder-b/shared.pdf",
    ]

    rewrite_calls.clear()
    selected_result = runtime._write_problem_pages(
        record,
        tmp_path / "selected-problem-pages",
        output_prefix="retry-input",
        selected_pages=[
            ProblemPageSelection(source_path="folder-b/shared.pdf", page_number=1),
            ProblemPageSelection(source_path="folder-b/shared.pdf", page_number=1),
        ],
    )

    assert selected_result["exported_pdf_count"] == 1
    assert selected_result["exported_page_count"] == 1
    assert rewrite_calls == [(b"%PDF-source-b", [0])]
    selected_row = selected_result["manifest"]["files"][0]
    assert selected_row["source_path"] == "folder-b/shared.pdf"
    assert selected_row["original_page_numbers"] == [1]
    assert selected_row["output_file"].startswith("retry-input/")

    invalid_selections = [
        ([], "select at least one problem page"),
        (
            [ProblemPageSelection(source_path="folder-a/shared.pdf", page_number=1)],
            "not skipped or failed",
        ),
        (
            [ProblemPageSelection(source_path="folder-a/shared.pdf", page_number=99)],
            "not skipped or failed",
        ),
    ]
    for selected_pages, expected_detail in invalid_selections:
        with pytest.raises(HTTPException) as exc_info:
            runtime._write_problem_pages(
                record,
                tmp_path / "invalid-problem-pages",
                output_prefix="",
                selected_pages=selected_pages,
            )
        assert exc_info.value.status_code == 400
        assert expected_detail in str(exc_info.value.detail)

    asyncio.run(runtime.close())


def test_ops_runtime_background_sync_persists_router_tasks(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("MINERU_OPS_TASK_SYNC_INTERVAL_SECONDS", "0.01")
    runtime = OpsRuntime()
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        assert request.url.path == "/tasks"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "task_id": "background-task",
                        "status": "completed",
                        "progress": {
                            "files": [
                                {
                                    "file_name": "sample.pdf",
                                    "pages": [
                                        {
                                            "page_idx": 0,
                                            "page_number": 1,
                                            "status": "completed",
                                            "vlm_request_seconds": 3.5,
                                            "total_seconds": 4.0,
                                        }
                                    ],
                                }
                            ]
                        },
                    }
                ],
                "total": 1,
            },
        )

    async def scenario():
        await runtime.http_client.aclose()
        runtime.http_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://router",
        )
        runtime.router_url = "http://router"
        runtime.task_sync_interval_seconds = 0.01
        await runtime.start()
        await asyncio.sleep(0.03)
        await runtime.close()

    asyncio.run(scenario())

    assert requests >= 2
    assert runtime.store.cached_task("background-task")["status"] == "completed"
    assert runtime.store.page_timings("background-task")["items"][0][
        "vlm_request_seconds"
    ] == 3.5


def test_ops_runtime_discovers_compose_roles_and_external_vlm(tmp_path: Path, monkeypatch):
    compose_path = tmp_path / "compose-config.yaml"
    compose_path.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "mineru-router": {
                        "image": "mineru-env:test",
                        "labels": {"com.mineru.role": "router"},
                    },
                    "mineru-api-1": {
                        "image": "mineru-env:test",
                        "labels": {"com.mineru.role": "api"},
                        "environment": {"MINERU_VL_SERVER": "http://10.0.0.8:30000/v1"},
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("MINERU_OPS_COMPOSE_CONFIG", str(compose_path))
    runtime = OpsRuntime()

    services = runtime.discover_services()

    assert [service["role"] for service in services] == ["api", "router", "vlm"] or [
        service["role"] for service in services
    ] == ["router", "api", "vlm"]
    assert next(service for service in services if service["role"] == "router")["endpoint"] == (
        "http://mineru-router:8002/health"
    )
    assert next(service for service in services if service["role"] == "vlm")["endpoint"] == ("http://10.0.0.8:30000/v1/models")

    asyncio.run(runtime.close())


def test_ops_runtime_shows_remote_router_upstreams(tmp_path: Path, monkeypatch):
    compose_path = tmp_path / "compose-config.yaml"
    compose_path.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "mineru-code-sync": {"labels": {"com.mineru.role": "code-sync"}},
                    "mineru-router": {"labels": {"com.mineru.role": "router"}},
                    "mineru-ops": {"labels": {"com.mineru.role": "ops"}},
                    **{
                        f"mineru-api-{index}": {
                            "labels": {"com.mineru.role": "api"},
                            "environment": {"MINERU_VL_SERVER": f"http://10.0.0.{index}:30000/v1"},
                        }
                        for index in range(1, 5)
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("MINERU_OPS_COMPOSE_CONFIG", str(compose_path))
    monkeypatch.setenv("MINERU_DEPLOY_ROLE", "router")
    monkeypatch.setenv(
        "MINERU_ROUTER_UPSTREAM_URLS_JSON",
        '["http://32.15.75.232:6002", "http://32.15.75.232:6003", "http://32.15.84.31:8001", "http://32.15.84.31:8002"]',
    )
    runtime = OpsRuntime()

    services = runtime.discover_services()

    api_services = [service for service in services if service["role"] == "api"]
    assert [service["address"] for service in api_services] == [
        "http://32.15.75.232:6002",
        "http://32.15.75.232:6003",
        "http://32.15.84.31:8001",
        "http://32.15.84.31:8002",
    ]
    assert all(service["remote"] and not service["control_enabled"] for service in api_services)
    assert not any(service["name"] == "mineru-api-1" and not service["remote"] for service in services)
    assert [service["endpoint"] for service in services if service["role"] == "vlm"] == [
        "http://10.0.0.1:30000/v1/models",
        "http://10.0.0.2:30000/v1/models",
        "http://10.0.0.3:30000/v1/models",
        "http://10.0.0.4:30000/v1/models",
    ]

    asyncio.run(runtime.close())


def test_ops_runtime_filters_api_nodes_for_api_role(tmp_path: Path, monkeypatch):
    compose_path = tmp_path / "compose-config.yaml"
    compose_path.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "mineru-api-1": {"labels": {"com.mineru.role": "api"}},
                    "mineru-api-2": {"labels": {"com.mineru.role": "api"}},
                    "mineru-api-3": {"labels": {"com.mineru.role": "api"}},
                    "mineru-api-4": {"labels": {"com.mineru.role": "api"}},
                    "mineru-router": {"labels": {"com.mineru.role": "router"}},
                    "mineru-ops": {"labels": {"com.mineru.role": "ops"}},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("MINERU_OPS_COMPOSE_CONFIG", str(compose_path))
    monkeypatch.setenv("MINERU_DEPLOY_ROLE", "api")
    monkeypatch.setenv("MINERU_API_NODE_IDS", "3,4")
    runtime = OpsRuntime()

    services = runtime.discover_services()

    assert [service["name"] for service in services if service["role"] == "api"] == [
        "mineru-api-3",
        "mineru-api-4",
    ]
    assert not any(service["role"] == "router" for service in services)
    assert not any(service["role"] == "ops" for service in services)

    asyncio.run(runtime.close())


def test_ops_runtime_combines_local_and_remote_api_nodes_for_all_role(tmp_path: Path, monkeypatch):
    compose_path = tmp_path / "compose-config.yaml"
    compose_path.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "mineru-api-1": {"labels": {"com.mineru.role": "api"}},
                    "mineru-api-2": {"labels": {"com.mineru.role": "api"}},
                    "mineru-api-3": {"labels": {"com.mineru.role": "api"}},
                    "mineru-api-4": {"labels": {"com.mineru.role": "api"}},
                    "mineru-router": {"labels": {"com.mineru.role": "router"}},
                    "mineru-ops": {"labels": {"com.mineru.role": "ops"}},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("MINERU_OPS_COMPOSE_CONFIG", str(compose_path))
    monkeypatch.setenv("MINERU_DEPLOY_ROLE", "all")
    monkeypatch.setenv("MINERU_API_NODE_IDS", "1,2")
    monkeypatch.setenv(
        "MINERU_ROUTER_UPSTREAM_URLS_JSON",
        '["http://32.15.75.232:6002", "http://32.15.75.232:6003", "http://32.15.84.31:8001", "http://32.15.84.31:8002"]',
    )
    runtime = OpsRuntime()

    services = runtime.discover_services()

    api_services = [service for service in services if service["role"] == "api"]
    assert [service["name"] for service in api_services] == [
        "mineru-api-1",
        "mineru-api-2",
        "mineru-api-3",
        "mineru-api-4",
    ]
    assert [service["address"] for service in api_services if service["remote"]] == [
        "http://32.15.84.31:8001",
        "http://32.15.84.31:8002",
    ]
    assert all(not service["remote"] for service in api_services[:2])
    assert all(service["remote"] for service in api_services[2:])

    asyncio.run(runtime.close())


def test_batch_run_request_supports_performance_lab_and_batch_32() -> None:
    assert BatchRunRequest().experiment_type == "batch_test"
    payload = BatchRunRequest(experiment_type="performance_lab", vlm_batch_size=32)
    assert payload.experiment_type == "performance_lab"
    assert payload.vlm_batch_size == 32
    with pytest.raises(ValueError):
        BatchRunRequest(vlm_batch_size=33)


def test_batch_run_metrics_counts_pages_and_throughput(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    runtime = OpsRuntime()
    run_dir = runtime.report_dir / "metrics-run"
    results_dir = run_dir / "results" / "0001-sample"
    results_dir.mkdir(parents=True)
    preview = {
        "file_name": "sample.pdf",
        "progress": {
            "files": [{
                "file_name": "sample.pdf",
                "pages": [
                    {"page_number": 1, "status": "completed"},
                    {"page_number": 2, "status": "completed"},
                    {"page_number": 3, "status": "completed"},
                    {"page_number": 4, "status": "skipped"},
                ],
            }]
        },
    }
    (results_dir / "preview.json").write_text(json.dumps(preview), encoding="utf-8")
    record = runtime.store.create_batch_run(
        "metrics-run", run_dir / "input", {"experiment_type": "performance_lab"},
        run_dir / "BATCH_DIAGNOSIS.md", run_dir / "raw", run_dir / "batch.log",
    )
    runtime.store.update_batch_run(
        "metrics-run", status="completed_with_failures",
        started_at="2026-08-22T00:00:00+00:00",
        completed_at="2026-08-22T00:02:00+00:00",
    )
    record = runtime.store.get_batch_run("metrics-run")
    assert record is not None
    metrics = runtime.batch_run_metrics(record)
    assert metrics["total_pages"] == 4
    assert metrics["successful_pages"] == 3
    assert metrics["failed_pages"] == 1
    assert metrics["processed_pages"] == 4
    assert metrics["elapsed_seconds"] == 120.0
    assert metrics["pages_per_minute"] == 2.0
    assert metrics["successful_pages_per_minute"] == 1.5
    assert metrics["average_page_seconds"] == 30.0
    assert metrics["complete"] is True
    asyncio.run(runtime.close())


def test_performance_snapshot_fingerprints_dataset_redacts_config_and_degrades(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    pdf = input_dir / "sample.pdf"
    pdf.write_bytes(b"%PDF-first")
    runtime = OpsRuntime()
    monkeypatch.setattr(runtime, "pdf_page_count", lambda path: 3)

    first = runtime.performance_dataset_snapshot(input_dir)
    second = runtime.performance_dataset_snapshot(input_dir)
    assert first["dataset_sha256"] == second["dataset_sha256"]
    assert first["files"] == [{
        "path": "sample.pdf",
        "size_bytes": len(b"%PDF-first"),
        "sha256": runtime.file_sha256(pdf),
        "page_count": 3,
    }]

    pdf.write_bytes(b"%PDF-changed")
    changed = runtime.performance_dataset_snapshot(input_dir)
    assert changed["dataset_sha256"] != first["dataset_sha256"]

    safe = runtime._safe_experiment_config({
        "MINERU_OPS_AUTH_TOKEN": "do-not-store",
        "nested": {"api_key": "also-secret", "safe": "kept"},
    })
    assert safe == {
        "MINERU_OPS_AUTH_TOKEN": "<redacted>",
        "nested": {"api_key": "<redacted>", "safe": "kept"},
    }

    async def unavailable_agent(*args, **kwargs):
        raise RuntimeError("agent socket unavailable")

    monkeypatch.setattr(runtime, "agent_call", unavailable_agent)
    snapshot = asyncio.run(
        runtime.performance_snapshot(input_dir, BatchRunRequest(experiment_type="performance_lab"))
    )
    assert snapshot["dataset_sha256"] == changed["dataset_sha256"]
    assert snapshot["effective_config"] == {}
    assert "effective config unavailable: RuntimeError" in snapshot["warnings"][0]
    asyncio.run(runtime.close())


def test_performance_metrics_account_for_timeouts_retries_and_percentiles(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    runtime = OpsRuntime()
    pages = [
        {
            "status": "completed", "page_number": 1, "vlm_request_seconds": 10,
            "attempts": 1,
            "attempt_details": [{"outcome": "completed", "request_seconds": 10}],
        },
        {
            "status": "completed", "page_number": 2, "vlm_request_seconds": 20,
            "attempts": 2,
            "attempt_details": [
                {"outcome": "timeout", "error": "timed out", "request_seconds": 600, "retry_wait_before_seconds": 5},
                {"outcome": "completed", "request_seconds": 20},
            ],
        },
        {
            "status": "failed", "page_number": 3, "vlm_request_seconds": 600,
            "attempts": 1,
            "error_type": "TimeoutError",
            "attempt_details": [{"outcome": "timeout", "request_seconds": 600}],
        },
    ]
    monkeypatch.setattr(runtime, "batch_page_records", lambda record, artifacts: pages)
    metrics = runtime.batch_run_metrics(
        {
            "status": "completed_with_failures",
            "created_at": "2026-08-23T00:00:00+00:00",
            "started_at": "2026-08-23T00:00:00+00:00",
            "completed_at": "2026-08-23T00:02:00+00:00",
        },
        {"previews": [], "originals": []},
    )

    assert metrics["total_pages"] == 3
    assert metrics["successful_pages"] == 2
    assert metrics["failed_pages"] == 1
    assert metrics["timeout_pages"] == 2
    assert metrics["retry_pages"] == 2
    assert metrics["p50_page_seconds"] == 15.0
    assert metrics["p95_page_seconds"] == 19.5
    assert metrics["max_page_seconds"] == 20.0
    assert metrics["successful_attempt_seconds"] == 30.0
    assert metrics["failed_attempt_seconds"] == 1200.0
    assert metrics["retry_overhead_seconds"] == 5.0
    assert metrics["without_retry_seconds"] == 30.0
    assert metrics["with_retry_seconds"] == 1235.0
    assert metrics["complete"] is True
    asyncio.run(runtime.close())


def test_performance_experiment_comparison_and_exports_choose_batch_one_baseline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    runtime = OpsRuntime()
    run_dir = runtime.report_dir / "compare"
    run_dir.mkdir(parents=True)
    for run_id in ("batch-8", "batch-1"):
        runtime.store.create_batch_run(
            run_id,
            run_dir / run_id,
            {"experiment_type": "performance_lab"},
            run_dir / f"{run_id}.md",
            run_dir / f"{run_id}-raw",
            run_dir / f"{run_id}.log",
        )
        runtime.store.update_batch_run(
            run_id,
            status="completed",
            started_at="2026-08-23T00:00:00+00:00",
            completed_at="2026-08-23T00:01:00+00:00",
        )

    def experiment(run_id: str, batch_size: int, dataset_sha256: str, elapsed: float) -> dict:
        return {
            "run_id": run_id,
            "status": "completed",
            "created_at": "2026-08-23T00:00:00+00:00" if batch_size == 1 else "2026-08-23T00:01:00+00:00",
            "experiment_name": run_id,
            "environment_name": "lab-a",
            "hardware_type": "ascend-npu",
            "engine": "mineru",
            "settings": {
                "experiment_type": "performance_lab", "backend": "vlm-http-client",
                "engine": "mineru", "vlm_batch_size": batch_size,
                "page_timeout_seconds": 600.0, "page_connect_max_retries": 0,
            },
            "snapshot": {"dataset_sha256": dataset_sha256},
            "metrics": {
                "complete": True, "total_pages": 2, "failed_pages": 0,
                "page_samples": [{}, {}], "elapsed_seconds": elapsed,
                "pages_per_minute": round(120 / elapsed, 3), "successful_pages": 2,
                "timeout_pages": 0, "p50_page_seconds": 20.0,
                "p95_page_seconds": 30.0, "max_page_seconds": 30.0,
                "retry_pages": 0, "retry_overhead_seconds": 0.0,
                "without_retry_seconds": 40.0, "with_retry_seconds": 40.0,
            },
        }

    records = {
        "batch-1": experiment("batch-1", 1, "same-dataset", 100.0),
        "batch-8": experiment("batch-8", 8, "other-dataset", 50.0),
    }
    monkeypatch.setattr(runtime, "performance_experiment_record", lambda record: records[record["run_id"]])
    comparison = runtime.compare_performance_experiments(["batch-8", "batch-1"])

    assert comparison["baseline_run_id"] == "batch-1"
    batch_eight = next(item for item in comparison["items"] if item["run_id"] == "batch-8")
    assert batch_eight["speedup"] == 2.0
    assert "dataset hash 不同或缺失，结果不宜直接比较" in batch_eight["warnings"]
    csv_content = runtime.performance_experiments_csv(comparison)
    markdown_content = runtime.performance_experiments_markdown(comparison)
    assert "run_id,experiment_name" in csv_content
    assert "batch-8" in csv_content
    assert "# MinerU 性能实验对比" in markdown_content
    assert "batch-1" in markdown_content
    asyncio.run(runtime.close())


def test_ops_app_serves_dashboard_and_health(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("MINERU_OPS_COMPOSE_CONFIG", str(tmp_path / "missing.yaml"))
    monkeypatch.setenv("MINERU_OPS_AGENT_SOCKET", str(tmp_path / "missing.sock"))

    app = create_app()
    paths = {route.path for route in app.routes}
    static_dir = Path(__file__).resolve().parents[2] / "mineru" / "ops" / "static"

    assert "/api/health" in paths
    assert "/api/services" in paths
    assert "/api/tasks" in paths
    assert "/api/tasks/{task_id}/page-timings" in paths
    assert "/api/tasks/{task_id}/report" in paths
    assert "/api/tasks/{task_id}/preview" in paths
    assert "/api/tasks/{task_id}/preview/pages/{page_number}" in paths
    assert "/api/batch-runs" in paths
    assert "/api/batch-runs/bulk-delete" in paths
    assert "/api/tasks/cleanup" in paths
    assert "/api/tasks/{task_id}" in paths
    assert "/api/tasks/{task_id}/cancel" in paths
    assert "/api/batch-runs/upload" in paths
    assert "/api/batch-runs/{run_id}/retry-problem-pages" in paths
    assert "/api/config" in paths
    assert "/api/config/schema" in paths
    assert "/api/config/validate" in paths
    assert "/api/config/plan" in paths
    assert "/api/config/apply" in paths
    assert "/api/config/restore" in paths
    assert "/api/config/history" in paths
    assert "/api/audit" in paths
    assert "/api/diagnostics/runtime" in paths
    assert "/api/config/status" in paths
    assert "/api/config/effective" in paths
    assert "/api/diagnostics/deep" in paths
    assert "/api/batch-runs/{run_id}/artifacts/{kind}/{artifact_path:path}" in paths
    assert "/" in paths
    dashboard_html = (static_dir / "index.html").read_text(encoding="utf-8")
    dashboard_js = (static_dir / "ops.js").read_text(encoding="utf-8")
    dashboard_css = (static_dir / "ops.css").read_text(encoding="utf-8")
    assert "MinerU 运维控制台" in dashboard_html
    assert "拖拽 PDF 文件或文件夹到这里" in dashboard_html
    assert "batch-detail-dialog" in dashboard_html
    assert "task-preview-dialog" in dashboard_html
    assert "同步滚动" in dashboard_html
    assert "导出当前内容" in dashboard_html
    assert "problem-pages-retry-dialog" in dashboard_html
    assert "重试异常页" in dashboard_html
    assert 'data-view="lab"' in dashboard_html
    assert 'data-view="config"' in dashboard_html
    assert 'id="service-inventory"' in dashboard_html
    assert "性能实验室" in dashboard_html
    assert "吞吐" in dashboard_html or "吞吐" in dashboard_js
    assert "experiment_type" in dashboard_js
    assert "32" in dashboard_html
    assert "配置中心" in dashboard_html
    assert "{{OPS_VERSION}}" in dashboard_html
    assert "预览变更" in dashboard_html
    assert "保存并应用" in dashboard_html
    assert "data-config-group-apply" in dashboard_js
    assert "configDraftValues" in dashboard_js
    assert "最终有效配置" in dashboard_html
    assert "config-apply-dialog" in dashboard_html
    assert "config-apply-result" in dashboard_html
    assert "ops.js?v={{OPS_VERSION}}" in dashboard_html
    assert "ops.css?v={{OPS_VERSION}}" in dashboard_html
    assert dashboard_html.count('name="vlm_batch_sizes"') == 6
    assert 'type="checkbox" name="vlm_batch_sizes"' in dashboard_html
    assert "waitForBatchRun" in dashboard_js
    assert "LAB_TERMINAL_STATES" in dashboard_js
    assert "for (let index = 0; index < batchSizes.length; index += 1)" in dashboard_js
    assert 'data-batch-action="delete"' in dashboard_js
    assert "页面请求累计" in dashboard_js
    assert "problem-pages-list" in dashboard_html
    assert "problem-pages-select-all" in dashboard_html
    assert "runtime-diagnostics" in dashboard_html
    assert "runtime-diagnostics-refresh" in dashboard_html
    assert "config-application-status" in dashboard_html
    assert "config-status-refresh" in dashboard_html
    assert "deep-diagnostics" in dashboard_html
    assert "deep-diagnostics-refresh" in dashboard_html
    assert "audit-log" in dashboard_html
    assert "audit-refresh" in dashboard_html
    assert "log-live" in dashboard_html
    assert "log-follow" in dashboard_html
    assert "markdown-table-wrap" in dashboard_js
    assert "sanitizeMarkdownHtmlTable" in dashboard_js
    assert "当前处理位置" in dashboard_js
    assert "page-legend" in dashboard_js
    assert "loadTaskDetail(state.activeTaskId" in dashboard_js
    assert "data-task-preview" in dashboard_js
    assert "task.preview_available" in dashboard_js
    assert "refreshTasksView" in dashboard_js
    assert "task-timing-summary" in dashboard_js
    assert "vlm_request_seconds" in dashboard_js
    assert "页面耗时" in dashboard_js
    assert "data-task-report" in dashboard_js
    assert "导出 Markdown 报告" in dashboard_js
    assert "导出 CSV" in dashboard_js
    assert "retry-problem-pages" in dashboard_js
    assert "configStatus" in dashboard_js
    assert "deepDiagnostics" in dashboard_js
    assert "pending_restart" in dashboard_js
    assert "/api/config/status" in dashboard_js
    assert "/api/config/effective" in dashboard_js
    assert "configApplyStepLabel" in dashboard_js
    assert "config-apply-step" in dashboard_js
    assert "data-copy-command-index" in dashboard_js
    assert "/api/diagnostics/deep" in dashboard_js
    assert "data-problem-pages-retry" in dashboard_js
    assert "selected_pages" in dashboard_js
    assert "/api/audit" in dashboard_js
    assert "/api/diagnostics/runtime" in dashboard_js
    assert 'cancelling: "正在停止"' in dashboard_js
    assert "cancel-pending" in dashboard_js
    assert "button.disabled" in dashboard_js
    assert "batch-router-diagnose.py" in dashboard_js
    assert "远程任务不会被取消" in dashboard_js
    assert "回滚到此版本" in dashboard_js
    assert "task-timing-table" in dashboard_css
    assert "runtime-diagnostics-grid" in dashboard_css
    assert "audit-table" in dashboard_css
    assert "problem-page-row" in dashboard_css
    assert "action-button.cancel-pending" in dashboard_css
    assert "service-card-metrics" in dashboard_js
    assert "runtime-health" in dashboard_js
    assert "service-endpoint" in dashboard_js
    assert "service-card-metrics" in dashboard_css
    assert "config-effective-item" in dashboard_css
    assert "config-apply-result" in dashboard_css
    assert "manual-action-command" in dashboard_css
    assert "max-width: 1400px" in dashboard_css
    assert "overflow-wrap: anywhere" in dashboard_css
    assert "width: 100vw" in dashboard_css
    # 列表页的列宽保护和表头吸顶依赖这些约定,改名会让样式静默失效。
    assert dashboard_html.count("table-wrap table-scroll") == 4
    assert ".table-scroll thead th { position: sticky" in dashboard_css
    assert "col-time" in dashboard_js
    assert "clearTaskSelection" in dashboard_js
    assert "data-task-cancel" in dashboard_js
    assert "/cancel`" in dashboard_js
    # 取消按钮只对未结束的任务出现;终态判据必须和 SSE 收尾共用一套,
    # 否则失败任务也会显示「终止任务」,点下去 Router 报 404 并顺手清掉本地记录。
    assert "const cancelAction = isTaskTerminal(task.status)" in dashboard_js
    assert "isTaskTerminal(payload.status) || payload.status === \"unavailable\"" in dashboard_js
    assert "const finished = task.partial_success || isTaskTerminal(task.status)" in dashboard_js

    index_route = next(route for route in app.routes if route.path == "/")
    response = asyncio.run(index_route.endpoint())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("a.pdf", Path("a.pdf")),
        ("folder/sub/a.PDF", Path("folder/sub/a.PDF")),
        (r"folder\a.pdf", Path("folder/a.pdf")),
    ],
)
def test_normalize_upload_name_preserves_safe_relative_paths(filename: str, expected: Path) -> None:
    assert OpsRuntime.normalize_upload_name(filename) == expected


@pytest.mark.parametrize("filename", ["../a.pdf", "/a.pdf", "folder/a.txt", "folder/../a.pdf"])
def test_normalize_upload_name_rejects_unsafe_or_non_pdf_paths(filename: str) -> None:
    with pytest.raises(HTTPException):
        OpsRuntime.normalize_upload_name(filename)


def test_save_uploaded_files_streams_pdf_tree(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    runtime = OpsRuntime()
    uploads = []
    for filename, content in [("folder/a.pdf", b"%PDF-a"), ("folder/b.pdf", b"%PDF-b")]:
        temporary_file = SpooledTemporaryFile(max_size=1024)
        temporary_file.write(content)
        temporary_file.seek(0)
        uploads.append(UploadFile(filename=filename, file=temporary_file))

    saved_count = asyncio.run(runtime.save_uploaded_files(uploads, tmp_path / "uploaded"))

    assert saved_count == 2
    assert (tmp_path / "uploaded/folder/a.pdf").read_bytes() == b"%PDF-a"
    assert (tmp_path / "uploaded/folder/b.pdf").read_bytes() == b"%PDF-b"
    asyncio.run(runtime.close())


def test_code_sync_exit_zero_is_reported_as_healthy() -> None:
    service = {"name": "mineru-code-sync", "role": "code-sync", "health": "unknown"}

    apply_service_runtime_health(
        service,
        {"state": "exited", "status": "Exited (0) 2 minutes ago"},
    )

    assert service["health"] == "healthy"
    assert "同步完成" in service["health_message"]


def test_batch_artifacts_list_originals_and_result_previews(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    runtime = OpsRuntime()
    run_dir = runtime.report_dir / "run-1"
    input_pdf = run_dir / "input/folder/a.pdf"
    input_pdf.parent.mkdir(parents=True)
    input_pdf.write_bytes(build_smoke_test_pdf())
    result_dir = run_dir / "results/0001-a"
    result_dir.mkdir(parents=True)
    markdown_path = result_dir / "result.md"
    markdown_path.write_text("# result", encoding="utf-8")
    (result_dir / "preview.json").write_text(
        '{"task_id":"task-preview","file_name":"a.pdf","relative_path":"folder/a.pdf",'
        '"task_status":"completed","classification":"success","elapsed_seconds":12.5,'
        '"preview":{"markdown_path":"0001-a/result.md"}}',
        encoding="utf-8",
    )
    record = runtime.store.create_batch_run(
        "run-1",
        run_dir / "input",
        {"input_path": "browser upload", "pdf_count": 1},
        run_dir / "BATCH_DIAGNOSIS.md",
        run_dir / "raw",
        run_dir / "batch.log",
    )

    artifacts = runtime.batch_artifacts(record)

    assert artifacts["originals"][0]["path"] == "folder/a.pdf"
    assert artifacts["originals"][0]["kind"] == "input"
    assert artifacts["previews"][0]["preview"]["markdown_path"] == "0001-a/result.md"
    assert runtime.resolve_artifact(record, "input", "folder/a.pdf") == input_pdf
    assert runtime.resolve_artifact(record, "results", "0001-a/result.md") == markdown_path
    task_preview = runtime.task_preview_artifacts("task-preview")
    assert task_preview["original"]["page_count"] == 1
    assert task_preview["preview"]["markdown_path"] == "0001-a/result.md"
    page_content, page_media_type, page_suffix = runtime.render_pdf_page(input_pdf, 1)
    assert page_media_type in {"image/jpeg", "image/png", "image/bmp"}
    assert page_suffix in {".jpg", ".png", ".bmp"}
    assert page_content.startswith((b"\xff\xd8", b"\x89PNG\r\n\x1a\n", b"BM"))
    cached_content, cached_media_type = runtime.render_pdf_page_cached(input_pdf, 1)
    assert cached_content == page_content
    assert cached_media_type == page_media_type
    asyncio.run(runtime.sync_batch_task_snapshots(force=True))
    task_snapshot = runtime.store.cached_task("task-preview")
    assert task_snapshot is not None
    assert task_snapshot["status"] == "completed"
    assert task_snapshot["file_names"] == ["a.pdf"]
    assert task_snapshot["progress"]["total_pages"] == 1
    assert task_snapshot["progress"]["completed_pages"] == 1
    assert task_snapshot["source_batch_run_id"] == "run-1"
    assert task_snapshot["preview_available"] is True
    with pytest.raises(HTTPException):
        runtime.resolve_artifact(record, "input", "../outside.pdf")
    with pytest.raises(HTTPException):
        runtime.render_pdf_page(input_pdf, 2)
    asyncio.run(runtime.close())


def test_pdf_preview_bmp_fallback_pads_each_scanline() -> None:
    class Bitmap:
        mode = "BGR"
        width = 3
        height = 2
        n_channels = 3
        stride = 9
        buffer = bytes(range(18))

    content = OpsRuntime._bitmap_to_bmp(Bitmap())

    assert content.startswith(b"BM")
    assert len(content) == 54 + 24
    assert struct.unpack_from("<i", content, 22)[0] == -2
    assert content[54 + 9 : 54 + 12] == b"\0\0\0"
    assert content[54 + 21 : 54 + 24] == b"\0\0\0"


def test_batch_artifacts_preview_server_directory_source_pdf(tmp_path: Path, monkeypatch) -> None:
    test_root = tmp_path / "test-pdfs"
    source_pdf = test_root / "folder/a.pdf"
    source_pdf.parent.mkdir(parents=True)
    source_pdf.write_bytes(b"%PDF-source-preview")
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(test_root))
    runtime = OpsRuntime()
    run_dir = runtime.report_dir / "run-source"
    run_dir.mkdir(parents=True)
    record = {
        "run_id": "run-source",
        "input_path": str(test_root),
        "report_path": str(run_dir / "BATCH_DIAGNOSIS.md"),
        "log_path": str(run_dir / "batch.log"),
        "settings": {"input_path": ".", "source_type": "server_directory", "pdf_count": 1},
    }

    artifacts = runtime.batch_artifacts(record)

    assert artifacts["originals"] == [
        {
            "path": "folder/a.pdf",
            "name": "a.pdf",
            "size_bytes": len(b"%PDF-source-preview"),
            "kind": "source",
        }
    ]
    assert runtime.resolve_artifact(record, "source", "folder/a.pdf") == source_pdf
    asyncio.run(runtime.close())


def test_process_log_markdown_contains_run_context(tmp_path: Path) -> None:
    log_path = tmp_path / "batch.log"
    log_path.write_text("Found 1 PDF\nstatus=processing\n", encoding="utf-8")
    record = {
        "run_id": "run-2",
        "status": "running",
        "input_path": str(tmp_path),
        "created_at": "2026-08-06T00:00:00+00:00",
        "started_at": "2026-08-06T00:00:01+00:00",
        "completed_at": None,
        "log_path": str(log_path),
        "settings": {"input_path": "浏览器上传", "backend": "vlm-http-client", "pdf_count": 1},
    }

    content = OpsRuntime.process_log_markdown(record)

    assert "# MinerU 批量任务过程日志" in content
    assert "run-2" in content
    assert "status=processing" in content


def test_start_batch_preserves_browser_upload_for_preview(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    runtime = OpsRuntime()
    batch_script = tmp_path / "batch.py"
    batch_script.write_text("pass\n", encoding="utf-8")
    runtime.batch_script = batch_script
    upload_dir = runtime.upload_dir / "upload-1"
    upload_dir.mkdir()
    (upload_dir / "a.pdf").write_bytes(b"%PDF-preview")

    async def fake_batch_process(*args, **kwargs):
        return None

    monkeypatch.setattr(runtime, "_run_batch_process", fake_batch_process)

    async def run_test():
        record = await runtime.start_batch_path(
            upload_dir,
            BatchRunRequest(),
            "浏览器上传（1 个 PDF）",
            source_type="browser_upload",
            preserve_input="move",
        )
        await asyncio.sleep(0)
        return record

    record = asyncio.run(run_test())

    preserved_input = Path(record["input_path"])
    assert not upload_dir.exists()
    assert (preserved_input / "a.pdf").is_file()
    assert record["settings"]["source_type"] == "browser_upload"
    asyncio.run(runtime.close())


def test_strip_pages_removes_per_page_arrays_only():
    progress = {
        "total_pages": 3,
        "completed_pages": 2,
        "files": [
            {
                "file_name": "a.pdf",
                "total_pages": 3,
                "pages": [{"page_idx": 0, "page_number": 1, "status": "completed"}],
            }
        ],
    }

    trimmed = strip_pages_from_progress(progress)

    # 计数保留,逐页数组去掉,其余字段原样。
    assert trimmed["total_pages"] == 3
    assert trimmed["completed_pages"] == 2
    assert trimmed["files"] == [{"file_name": "a.pdf", "total_pages": 3}]
    assert "pages" in progress["files"][0], "原始数据不能被就地修改"


def test_strip_pages_tolerates_missing_or_odd_progress():
    assert strip_pages_from_progress(None) == {}
    assert strip_pages_from_progress({"phase": "queued"}) == {"phase": "queued"}
    assert strip_pages_from_progress({"files": "not-a-list"}) == {"files": "not-a-list"}


def test_slim_task_for_list_drops_pages_and_keeps_row_fields():
    task = {
        "task_id": "task-1",
        "status": "completed",
        "backend": "vlm-http-client",
        "file_names": ["a.pdf"],
        "progress": {
            "total_pages": 2,
            "completed_pages": 2,
            "files": [{"file_name": "a.pdf", "total_pages": 2, "pages": [{"page_idx": 0}]}],
        },
    }

    slim = slim_task_for_list(task)

    assert slim["task_id"] == "task-1"
    assert slim["file_names"] == ["a.pdf"]
    assert slim["progress"]["completed_pages"] == 2
    assert "pages" not in slim["progress"]["files"][0]
    assert "pages" in task["progress"]["files"][0]


def test_count_tasks_by_status_and_failed_pages(tmp_path: Path):
    store = OpsStore(tmp_path / "ops.db")
    store.upsert_tasks([
        {"task_id": "t1", "status": "completed", "progress": {"files": []}},
        {"task_id": "t2", "status": "completed", "progress": {"files": []}},
        {"task_id": "t3", "status": "processing", "progress": {"files": []}},
    ])
    store.upsert_tasks([
        {
            "task_id": "t4",
            "status": "completed",
            "progress": {
                "files": [
                    {
                        "file_name": "a.pdf",
                        "total_pages": 2,
                        "pages": [
                            {"page_idx": 0, "page_number": 1, "status": "skipped"},
                            {"page_idx": 1, "page_number": 2, "status": "failed"},
                        ],
                    }
                ]
            },
        },
    ])

    assert store.count_tasks_by_status() == {
        "completed": 3,
        "processing": 1,
    }
    assert store.count_failed_pages() == (1, 1)


def test_batch_metrics_drops_page_samples_and_slims_slowest_pages(tmp_path: Path):
    store = OpsStore(tmp_path / "ops.db")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    try:
        runtime = OpsRuntime()
        run_id = "run-metrics"
        run_dir = runtime.report_dir / run_id
        results_dir = run_dir / "results" / "task-metrics"
        results_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "input").mkdir(parents=True, exist_ok=True)
        (results_dir / "preview.json").write_text(
            json.dumps({
                "task_id": "task-metrics",
                "file_name": "a.pdf",
                "relative_path": "a.pdf",
                "task_status": "completed",
                "progress": {
                    "total_pages": 2,
                    "files": [
                        {
                            "file_name": "a.pdf",
                            "total_pages": 2,
                            "pages": [
                                {"page_idx": 0, "page_number": 1, "status": "completed",
                                 "vlm_request_seconds": 9.0, "total_seconds": 10.0},
                                {"page_idx": 1, "page_number": 2, "status": "completed",
                                 "vlm_request_seconds": 1.0, "total_seconds": 2.0},
                            ],
                        }
                    ],
                },
            }),
            encoding="utf-8",
        )
        store.create_batch_run(
            run_id,
            tmp_path,
            {"input_path": "."},
            run_dir / "BATCH_DIAGNOSIS.md",
            run_dir / "raw",
            run_dir / "batch.log",
        )
        store.update_batch_run(run_id, status="completed")

        metrics = runtime.batch_run_metrics(store.get_batch_run(run_id))

        assert "page_samples" not in metrics
        assert metrics["page_sample_count"] == 2
        assert metrics["slowest_pages"][0]["page_number"] == 1
        assert metrics["slowest_pages"][0]["seconds"] == 9.0
        # 逐页大字段不再随 metrics 一起传输。
        assert set(metrics["slowest_pages"][0]) == {"page_number", "file_name", "seconds", "timeout"}
        asyncio.run(runtime.close())
    finally:
        monkeypatch.undo()


def test_cancel_task_stops_pending_task_and_clears_cache(tmp_path: Path, monkeypatch) -> None:
    """#4:取消必须真的让任务停下来,并把控制台那一行也清掉。"""
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("MINERU_OPS_AUTH_TOKEN", "tok")
    runtime = OpsRuntime()
    runtime.store.upsert_tasks([
        {
            "task_id": "cancel-me",
            "status": "processing",
            "source_batch_run_id": "run-1",
            "progress": {"total_pages": 1, "files": []},
        }
    ])

    async def fake_delete(url, *args, **kwargs):
        return httpx.Response(200, json={"task_id": "cancel-me", "cancelled": True})

    monkeypatch.setattr(runtime.http_client, "delete", fake_delete)
    runtime.router_url = "http://router:8002"
    monkeypatch.setattr(ops_module, "OpsRuntime", lambda: runtime)
    app = create_app()

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        response = client.post(
            "/api/tasks/cancel-me/cancel",
            headers={"X-MinerU-Ops-Token": "tok"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["cancelled"] is True
    # 即使任务是批次产生的,取消也要把它从列表里移走。
    assert runtime.store.cached_task("cancel-me") is None
    asyncio.run(runtime.close())


def test_cancel_task_reports_router_failure_without_clearing_cache(tmp_path: Path, monkeypatch) -> None:
    """Router 没确认取消时,不能把缓存删掉:否则页面显示的"已取消"是假的。"""
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("MINERU_OPS_AUTH_TOKEN", "tok")
    runtime = OpsRuntime()
    runtime.store.upsert_tasks([{"task_id": "still-there", "status": "processing", "progress": {"files": []}}])

    async def fake_delete(url, *args, **kwargs):
        raise httpx.ConnectError("router down")

    monkeypatch.setattr(runtime.http_client, "delete", fake_delete)
    monkeypatch.setattr(ops_module, "OpsRuntime", lambda: runtime)
    app = create_app()

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        response = client.post(
            "/api/tasks/still-there/cancel",
            headers={"X-MinerU-Ops-Token": "tok"},
        )
    assert response.status_code == 502
    assert runtime.store.cached_task("still-there") is not None
    asyncio.run(runtime.close())


def test_overview_tiles_count_every_task_not_just_the_first_page(tmp_path: Path, monkeypatch) -> None:
    """#6:总览的计数曾经来自列表前 100 条,任务一多就会少算;现在走服务端聚合。"""
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("MINERU_OPS_AUTH_TOKEN", "tok")
    runtime = OpsRuntime()
    runtime.store.upsert_tasks(
        [
            {"task_id": f"done-{index}", "status": "completed", "progress": {"files": []}}
            for index in range(150)
        ]
    )
    runtime.store.upsert_tasks(
        [
            {"task_id": "running-1", "status": "processing", "progress": {"files": []}},
            {"task_id": "queued-1", "status": "pending", "progress": {"files": []}},
            {"task_id": "broken-1", "status": "failed", "progress": {"files": []}},
        ]
    )

    async def no_router(*args, **kwargs):
        raise httpx.ConnectError("router down")

    monkeypatch.setattr(runtime.http_client, "get", no_router)
    monkeypatch.setattr(ops_module, "OpsRuntime", lambda: runtime)
    app = create_app()

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        response = client.get("/api/overview", headers={"X-MinerU-Ops-Token": "tok"})

    assert response.status_code == 200, response.text
    counts = response.json()["task_counts"]
    # Router 不可达时仍要给出缓存里的真实计数,而不是退回第一页。
    assert counts["completed"] == 150
    assert counts["processing"] == 1
    assert counts["pending"] == 1
    assert counts["failed"] == 1
    asyncio.run(runtime.close())


def test_task_events_stops_immediately_when_task_is_gone(tmp_path: Path, monkeypatch) -> None:
    """任务已被终止:SSE 要报一次就收尾,不能每秒重新问一个不存在的任务。"""
    monkeypatch.setenv("MINERU_OPS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MINERU_OPS_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("MINERU_OPS_AUTH_TOKEN", "tok")
    runtime = OpsRuntime()
    router_calls: list[str] = []

    async def gone(url, *args, **kwargs):
        if "/tasks/gone" in str(url):
            router_calls.append("call")
        return httpx.Response(404, text="not found")

    monkeypatch.setattr(runtime.http_client, "get", gone)
    monkeypatch.setattr(ops_module, "OpsRuntime", lambda: runtime)
    app = create_app()

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        with client.stream(
            "GET",
            "/api/tasks/gone/events",
            headers={"X-MinerU-Ops-Token": "tok"},
        ) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "task not found" in body
    assert body.count("data:") == 1, "只应推送一次"
    assert len(router_calls) == 1, "404 之后不能再轮询 Router"
    asyncio.run(runtime.close())
