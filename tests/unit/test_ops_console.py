import asyncio
import struct
from pathlib import Path
from tempfile import SpooledTemporaryFile

import httpx
import pytest
import yaml
from fastapi import HTTPException, UploadFile

from mineru.cli.ops import (
    BatchRunRequest,
    OpsRuntime,
    OpsStore,
    apply_service_runtime_health,
    build_smoke_test_pdf,
    create_app,
)


def test_ops_store_persists_task_snapshots(tmp_path: Path):
    store = OpsStore(tmp_path / "ops.db")
    payload = {"task_id": "task-1", "status": "processing", "file_names": ["a.pdf"]}

    store.upsert_tasks([payload])

    assert store.cached_task("task-1") == payload
    assert store.cached_tasks() == [payload]


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
    assert summary["recorded_pages"] == 5
    assert summary["completed_pages"] == 4
    assert summary["skipped_pages"] == 1
    assert summary["completed_average_vlm_request_seconds"] == 25.0
    assert summary["completed_p50_vlm_request_seconds"] == 25.0
    assert summary["completed_p95_vlm_request_seconds"] == 38.5
    assert summary["completed_max_vlm_request_seconds"] == 40.0
    assert summary["slow_pages"] == 3
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
        "vlm_request_seconds",
        "retry_wait_seconds",
        "total_seconds",
        "attempts",
        "error_type",
        "error",
    ]
    assert len(rows) == 5  # header + 4 pages
    assert [row[1] for row in rows[1:]] == ["1", "2", "3", "4"]
    failed_row = rows[-1]
    assert failed_row[3] == "failed"
    assert failed_row[8] == "3"
    assert failed_row[9] == "TimeoutError"


def test_all_page_timings_filters_by_status(tmp_path: Path) -> None:
    store = OpsStore(tmp_path / "ops.db")
    _seed_report_task(store, page_count=3)

    assert len(store.all_page_timings("report-task", status="completed")) == 3
    assert len(store.all_page_timings("report-task", status="failed")) == 1
    with pytest.raises(ValueError):
        store.all_page_timings("report-task", status="bogus")


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
    assert "/api/batch-runs/upload" in paths
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
    assert "task-timing-table" in dashboard_css
    assert "width: calc(100vw - 24px)" in dashboard_css


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
