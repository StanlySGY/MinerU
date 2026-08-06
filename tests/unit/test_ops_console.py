import asyncio
from pathlib import Path
from tempfile import SpooledTemporaryFile

import pytest
import yaml
from fastapi import HTTPException, UploadFile

from mineru.cli.ops import BatchRunRequest, OpsRuntime, OpsStore, apply_service_runtime_health, create_app


def test_ops_store_persists_task_snapshots(tmp_path: Path):
    store = OpsStore(tmp_path / "ops.db")
    payload = {"task_id": "task-1", "status": "processing", "file_names": ["a.pdf"]}

    store.upsert_tasks([payload])

    assert store.cached_task("task-1") == payload
    assert store.cached_tasks() == [payload]


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
    assert "/api/batch-runs" in paths
    assert "/api/batch-runs/upload" in paths
    assert "/api/batch-runs/{run_id}/artifacts/{kind}/{artifact_path:path}" in paths
    assert "/" in paths
    dashboard_html = (static_dir / "index.html").read_text(encoding="utf-8")
    dashboard_js = (static_dir / "ops.js").read_text(encoding="utf-8")
    assert "MinerU 运维控制台" in dashboard_html
    assert "拖拽 PDF 文件或文件夹到这里" in dashboard_html
    assert "batch-detail-dialog" in dashboard_html
    assert "log-live" in dashboard_html
    assert "log-follow" in dashboard_html
    assert "markdown-table-wrap" in dashboard_js


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
    input_pdf.write_bytes(b"%PDF-preview")
    result_dir = run_dir / "results/0001-a"
    result_dir.mkdir(parents=True)
    markdown_path = result_dir / "result.md"
    markdown_path.write_text("# result", encoding="utf-8")
    (result_dir / "preview.json").write_text(
        '{"file_name":"a.pdf","relative_path":"folder/a.pdf","preview":{"markdown_path":"0001-a/result.md"}}',
        encoding="utf-8",
    )
    record = {
        "run_id": "run-1",
        "report_path": str(run_dir / "BATCH_DIAGNOSIS.md"),
        "log_path": str(run_dir / "batch.log"),
        "settings": {"input_path": "browser upload", "pdf_count": 1},
    }

    artifacts = runtime.batch_artifacts(record)

    assert artifacts["originals"][0]["path"] == "folder/a.pdf"
    assert artifacts["originals"][0]["kind"] == "input"
    assert artifacts["previews"][0]["preview"]["markdown_path"] == "0001-a/result.md"
    assert runtime.resolve_artifact(record, "input", "folder/a.pdf") == input_pdf
    assert runtime.resolve_artifact(record, "results", "0001-a/result.md") == markdown_path
    with pytest.raises(HTTPException):
        runtime.resolve_artifact(record, "input", "../outside.pdf")
    asyncio.run(runtime.close())


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
