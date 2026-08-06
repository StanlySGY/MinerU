import asyncio
from pathlib import Path
from tempfile import SpooledTemporaryFile

import pytest
import yaml
from fastapi import HTTPException, UploadFile

from mineru.cli.ops import OpsRuntime, OpsStore, create_app


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
    assert "/" in paths
    assert "MinerU 运维控制台" in (static_dir / "index.html").read_text(encoding="utf-8")


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
