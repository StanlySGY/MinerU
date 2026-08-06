# Copyright (c) Opendatalab. All rights reserved.
"""MinerU operations dashboard and batch diagnosis service."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import struct
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import click
import httpx
import uvicorn
import yaml
from fastapi import File, Form, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


DEFAULT_DATA_DIR = "/tmp/mineru-ops"
DEFAULT_TEST_ROOT = "./test-pdfs"
TERMINAL_BATCH_STATES = {
    "completed",
    "completed_with_failures",
    "failed",
    "cancelled",
    "interrupted",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def apply_service_runtime_health(service: dict[str, Any], state: dict[str, Any]) -> None:
    service["runtime"] = state
    if service.get("role") != "code-sync":
        return
    runtime_state = str(state.get("state") or "").lower()
    runtime_status = str(state.get("status") or "")
    exit_code = state.get("exit_code")
    successful_exit = exit_code == 0 or str(exit_code) == "0" or re.search(r"\(0\)", runtime_status)
    if runtime_state == "exited" and successful_exit:
        service.update(
            {
                "health": "healthy",
                "health_error": None,
                "health_message": "代码同步完成（exit 0）",
            }
        )
    elif runtime_state == "exited":
        service.update(
            {
                "health": "unhealthy",
                "health_error": runtime_status or "code sync exited unsuccessfully",
                "health_message": "代码同步失败",
            }
        )
    elif runtime_state in {"running", "restarting"}:
        service.update(
            {
                "health": "processing",
                "health_error": None,
                "health_message": "正在同步代码",
            }
        )


def json_loads_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def build_smoke_test_pdf() -> bytes:
    content_stream = b"BT /F1 18 Tf 72 720 Td (MinerU operations smoke test) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        (f"<< /Length {len(content_stream)} >>\nstream\n".encode("ascii") + content_stream + b"\nendstream"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, object_body in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode("ascii"))
        payload.extend(object_body)
        payload.extend(b"\nendobj\n")
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend((f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n").encode("ascii"))
    return bytes(payload)


class OpsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS task_snapshots (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS batch_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    exit_code INTEGER,
                    settings_json TEXT NOT NULL,
                    report_path TEXT NOT NULL,
                    raw_dir TEXT NOT NULL,
                    log_path TEXT NOT NULL,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    detail TEXT
                );
                """
            )
            connection.execute(
                """
                UPDATE batch_runs
                SET status = 'interrupted', completed_at = ?,
                    error = COALESCE(error, 'mineru-ops restarted while the run was active')
                WHERE status IN ('pending', 'running', 'paused', 'cancelling')
                """,
                (utc_now_iso(),),
            )

    def upsert_tasks(self, tasks: list[dict[str, Any]]) -> None:
        now = utc_now_iso()
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO task_snapshots(task_id, status, updated_at, payload_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    payload_json = excluded.payload_json
                """,
                [
                    (
                        str(task.get("task_id")),
                        str(task.get("status", "unknown")),
                        now,
                        json.dumps(task, ensure_ascii=False),
                    )
                    for task in tasks
                    if task.get("task_id")
                ],
            )

    def cached_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM task_snapshots ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [json_loads_object(row["payload_json"]) for row in rows]

    def cached_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM task_snapshots WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return json_loads_object(row["payload_json"]) if row else None

    def create_batch_run(
        self,
        run_id: str,
        input_path: Path,
        settings: dict[str, Any],
        report_path: Path,
        raw_dir: Path,
        log_path: Path,
    ) -> dict[str, Any]:
        created_at = utc_now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO batch_runs(
                    run_id, status, input_path, created_at, settings_json,
                    report_path, raw_dir, log_path
                ) VALUES (?, 'pending', ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(input_path),
                    created_at,
                    json.dumps(settings, ensure_ascii=False),
                    str(report_path),
                    str(raw_dir),
                    str(log_path),
                ),
            )
        return self.get_batch_run(run_id) or {}

    def update_batch_run(self, run_id: str, **values: Any) -> None:
        allowed = {
            "status",
            "started_at",
            "completed_at",
            "exit_code",
            "error",
        }
        filtered = {key: value for key, value in values.items() if key in allowed}
        if not filtered:
            return
        assignments = ", ".join(f"{key} = ?" for key in filtered)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE batch_runs SET {assignments} WHERE run_id = ?",
                (*filtered.values(), run_id),
            )

    def list_batch_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM batch_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._batch_row(row) for row in rows]

    def get_batch_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM batch_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return self._batch_row(row) if row else None

    def audit(self, action: str, target: str, success: bool, detail: str = "") -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO audit_log(created_at, action, target, success, detail) VALUES (?, ?, ?, ?, ?)",
                (utc_now_iso(), action, target, 1 if success else 0, detail[:4000]),
            )

    def delete_batch_run(self, run_id: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM batch_runs WHERE run_id = ?", (run_id,))

    @staticmethod
    def _batch_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["settings"] = json_loads_object(payload.pop("settings_json", None))
        log_path = Path(payload["log_path"])
        payload["log_tail"] = ""
        if log_path.is_file():
            payload["log_tail"] = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:])
        payload["report_ready"] = Path(payload["report_path"]).is_file()
        run_dir = Path(payload["report_path"]).parent
        payload["input_preview_ready"] = (run_dir / "input").is_dir()
        payload["result_preview_ready"] = (run_dir / "results").is_dir()
        return payload


class BatchRunRequest(BaseModel):
    input_path: str = "."
    backend: str = Field(default="vlm-http-client", min_length=1, max_length=80)
    effort: str = Field(default="medium", min_length=1, max_length=30)
    parse_method: str = Field(default="auto", min_length=1, max_length=30)
    lang: str = Field(default="ch", min_length=1, max_length=30)
    server_url: str | None = None
    recursive: bool = True
    task_timeout: int = Field(default=7200, ge=60, le=86400)
    pause_seconds: float = Field(default=2.0, ge=0, le=300)


class OpsRuntime:
    def __init__(self) -> None:
        self.data_dir = Path(os.getenv("MINERU_OPS_DATA_DIR", DEFAULT_DATA_DIR)).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir = self.data_dir / "reports"
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir = self.data_dir / "uploads"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir = self.data_dir / "tmp"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.store = OpsStore(self.data_dir / "ops.db")
        self.compose_config_path = Path(os.getenv("MINERU_OPS_COMPOSE_CONFIG", "/config/compose-config.yaml"))
        self.test_root = Path(os.getenv("MINERU_OPS_TEST_ROOT", DEFAULT_TEST_ROOT)).resolve()
        self.router_url = os.getenv("MINERU_OPS_ROUTER_URL", "http://mineru-router:8002").rstrip("/")
        self.agent_socket = os.getenv("MINERU_OPS_AGENT_SOCKET", "/run/mineru-ops/agent.sock")
        self.auth_token = os.getenv("MINERU_OPS_AUTH_TOKEN", "").strip()
        self.batch_script = Path(
            os.getenv(
                "MINERU_OPS_BATCH_SCRIPT",
                "/opt/mineru-ops/batch-router-diagnose.py",
            )
        )
        self.batch_processes: dict[str, asyncio.subprocess.Process] = {}
        self.smoke_backend = os.getenv("MINERU_OPS_SMOKE_BACKEND", "vlm-http-client")
        self.smoke_timeout = max(
            60,
            int(os.getenv("MINERU_OPS_SMOKE_TIMEOUT_SECONDS", "900")),
        )
        self.max_upload_bytes = (
            max(
                1,
                int(os.getenv("MINERU_OPS_MAX_UPLOAD_MB", "2048")),
            )
            * 1024
            * 1024
        )
        self.artifact_retention_days = max(
            0,
            int(os.getenv("MINERU_OPS_ARTIFACT_RETENTION_DAYS", "7")),
        )
        self.artifact_max_bytes = max(
            1,
            int(float(os.getenv("MINERU_OPS_ARTIFACT_MAX_GB", "50")) * 1024 * 1024 * 1024),
        )
        self.save_result_images = env_bool("MINERU_OPS_SAVE_RESULT_IMAGES", True)
        self.cleanup_expired_artifacts()
        self.http_client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)

    async def close(self) -> None:
        for process in list(self.batch_processes.values()):
            if process.returncode is None:
                process.terminate()
        await self.http_client.aclose()

    def compose_config(self) -> dict[str, Any]:
        try:
            content = self.compose_config_path.read_text(encoding="utf-8")
            payload = yaml.safe_load(content)
        except (OSError, yaml.YAMLError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def discover_services(self) -> list[dict[str, Any]]:
        services = self.compose_config().get("services")
        if not isinstance(services, dict):
            return []
        discovered = []
        vlm_urls: set[str] = set()
        for name, spec_value in services.items():
            spec = spec_value if isinstance(spec_value, dict) else {}
            labels = spec.get("labels") if isinstance(spec.get("labels"), dict) else {}
            role = labels.get("com.mineru.role") or self._infer_role(name)
            environment = spec.get("environment") if isinstance(spec.get("environment"), dict) else {}
            endpoint = self._service_endpoint(name, role)
            discovered.append(
                {
                    "name": name,
                    "role": role,
                    "image": spec.get("image"),
                    "endpoint": endpoint,
                    "control_enabled": role not in {"ops", "code-sync"},
                }
            )
            if role == "api":
                raw_url = environment.get("MINERU_VL_SERVER") or environment.get("MINERU_VLM_SERVER_URL")
                if isinstance(raw_url, str) and raw_url.startswith("http"):
                    host_part = raw_url.split("//", 1)[-1].split("/", 1)[0]
                    if not host_part.startswith(":") and not host_part.startswith("127.0.0.1"):
                        vlm_urls.add(raw_url.rstrip("/"))
        for index, url in enumerate(sorted(vlm_urls), start=1):
            models_url = url if url.endswith("/models") else f"{url}/models"
            discovered.append(
                {
                    "name": f"external-vlm-{index}",
                    "role": "vlm",
                    "image": None,
                    "endpoint": models_url,
                    "control_enabled": False,
                }
            )
        return discovered

    @staticmethod
    def _infer_role(name: str) -> str:
        lowered = name.lower()
        if "router" in lowered:
            return "router"
        if "api" in lowered:
            return "api"
        if "vlm" in lowered or "vllm" in lowered:
            return "vlm"
        if "ops" in lowered:
            return "ops"
        if "code-sync" in lowered:
            return "code-sync"
        return "service"

    @staticmethod
    def _service_endpoint(name: str, role: str) -> str | None:
        if role == "router":
            return f"http://{name}:8002/health"
        if role == "api":
            return f"http://{name}:8000/health"
        if role == "ops":
            return "http://127.0.0.1:19000/api/health"
        return None

    async def agent_call(self, payload: dict[str, Any], timeout: float = 60.0) -> dict[str, Any]:
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(self.agent_socket), timeout=3.0)
            writer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            await writer.drain()
            raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
            writer.close()
            await writer.wait_closed()
            response = json.loads(raw.decode("utf-8"))
            if not isinstance(response, dict):
                raise ValueError("agent response is not an object")
            return response
        except Exception as exc:
            return {"ok": False, "error": f"ops agent unavailable: {exc}"}

    def resolve_input_path(self, relative_path: str) -> Path:
        candidate = (self.test_root / relative_path).resolve()
        try:
            candidate.relative_to(self.test_root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="input_path is outside the configured test root") from exc
        if not candidate.is_dir():
            raise HTTPException(status_code=404, detail=f"input directory not found: {relative_path}")
        return candidate

    @staticmethod
    def directory_size(path: Path) -> int:
        total = 0
        if not path.exists():
            return total
        for item in path.rglob("*"):
            if not item.is_file():
                continue
            try:
                total += item.stat().st_size
            except OSError:
                continue
        return total

    def artifact_storage_bytes(self) -> int:
        total = self.directory_size(self.upload_dir)
        for run_dir in self.report_dir.iterdir():
            if not run_dir.is_dir():
                continue
            total += self.directory_size(run_dir / "input")
            total += self.directory_size(run_dir / "results")
        return total

    def artifact_storage_status(self) -> dict[str, Any]:
        used_bytes = self.artifact_storage_bytes()
        return {
            "used_bytes": used_bytes,
            "max_bytes": self.artifact_max_bytes,
            "usage_percent": round(min(100, used_bytes * 100 / self.artifact_max_bytes), 1),
            "retention_days": self.artifact_retention_days,
            "save_result_images": self.save_result_images,
        }

    def cleanup_expired_artifacts(self) -> None:
        now = datetime.now(timezone.utc)
        for upload_path in self.upload_dir.iterdir():
            try:
                age_seconds = now.timestamp() - upload_path.stat().st_mtime
            except OSError:
                continue
            if age_seconds > 86400:
                shutil.rmtree(upload_path, ignore_errors=True)
        if self.artifact_retention_days <= 0:
            return
        retention_seconds = self.artifact_retention_days * 86400
        for record in self.store.list_batch_runs(limit=10000):
            completed_at = record.get("completed_at")
            if not completed_at:
                continue
            try:
                completed = datetime.fromisoformat(str(completed_at))
            except ValueError:
                continue
            if completed.tzinfo is None:
                completed = completed.replace(tzinfo=timezone.utc)
            if (now - completed).total_seconds() < retention_seconds:
                continue
            try:
                run_dir = self.run_dir_for_record(record)
            except HTTPException:
                continue
            shutil.rmtree(run_dir / "input", ignore_errors=True)
            shutil.rmtree(run_dir / "results", ignore_errors=True)
            for zip_path in run_dir.glob("mineru-batch-*.zip"):
                zip_path.unlink(missing_ok=True)

    def run_dir_for_record(self, record: dict[str, Any]) -> Path:
        run_dir = Path(record["report_path"]).parent.resolve()
        try:
            run_dir.relative_to(self.report_dir)
        except ValueError as exc:
            raise HTTPException(status_code=500, detail="batch artifact path is invalid") from exc
        return run_dir

    def batch_artifacts(self, record: dict[str, Any]) -> dict[str, Any]:
        run_dir = self.run_dir_for_record(record)
        originals = []
        input_root = run_dir / "input"
        original_kind = "input"
        original_root: Path | None = input_root if input_root.is_dir() else None
        if original_root is None:
            source_root: Path | None = Path(str(record.get("input_path") or "")).resolve()
            try:
                source_root.relative_to(self.test_root)
            except ValueError:
                source_root = None
            if source_root is not None and source_root.is_dir():
                original_root = source_root
                original_kind = "source"
        if original_root is not None:
            for path in sorted(original_root.rglob("*")):
                if path.is_file() and path.suffix.lower() == ".pdf":
                    originals.append(
                        {
                            "path": path.relative_to(original_root).as_posix(),
                            "name": path.name,
                            "size_bytes": path.stat().st_size,
                            "kind": original_kind,
                        }
                    )
        previews = []
        results_root = run_dir / "results"
        if results_root.is_dir():
            for metadata_path in sorted(results_root.glob("*/preview.json")):
                try:
                    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict):
                    previews.append(payload)
        return {
            "originals": originals,
            "previews": previews,
            "storage_bytes": self.directory_size(input_root) + self.directory_size(results_root),
            "retention_days": self.artifact_retention_days,
        }

    def task_preview_artifacts(self, task_id: str) -> dict[str, Any]:
        for record in self.store.list_batch_runs(limit=10000):
            artifacts = self.batch_artifacts(record)
            preview = next(
                (
                    item
                    for item in artifacts["previews"]
                    if str(item.get("task_id") or "") == task_id
                ),
                None,
            )
            if preview is None:
                continue
            relative_path = str(preview.get("relative_path") or preview.get("file_name") or "")
            original = next(
                (
                    item
                    for item in artifacts["originals"]
                    if str(item.get("path") or "") == relative_path
                ),
                None,
            )
            if original is None:
                raise HTTPException(
                    status_code=404,
                    detail="the task result exists, but its original PDF is unavailable",
                )
            original_path = self.resolve_artifact(
                record,
                str(original.get("kind") or "input"),
                str(original["path"]),
            )
            return {
                "task_id": task_id,
                "run_id": record["run_id"],
                "file_name": preview.get("file_name") or original.get("name"),
                "relative_path": relative_path,
                "classification": preview.get("classification"),
                "failed_pages": preview.get("failed_pages") or [],
                "original": {
                    **original,
                    "page_count": self.pdf_page_count(original_path),
                },
                "preview": preview.get("preview") or {},
            }
        raise HTTPException(
            status_code=404,
            detail="preview is only available for completed tasks with retained batch artifacts",
        )

    @staticmethod
    def pdf_page_count(path: Path) -> int:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(str(path))
        try:
            return len(document)
        finally:
            document.close()

    @staticmethod
    def render_pdf_page(path: Path, page_number: int) -> bytes:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(str(path))
        try:
            if page_number < 1 or page_number > len(document):
                raise HTTPException(status_code=404, detail="PDF page not found")
            page = document[page_number - 1]
            try:
                bitmap = page.render(scale=1.5)
                try:
                    if bitmap.mode not in {"BGR", "BGRA", "BGRX"}:
                        raise HTTPException(
                            status_code=500,
                            detail=f"unsupported PDF bitmap mode: {bitmap.mode}",
                        )
                    bits_per_pixel = bitmap.n_channels * 8
                    image_size = bitmap.stride * bitmap.height
                    pixel_offset = 14 + 40
                    file_header = struct.pack(
                        "<2sIHHI",
                        b"BM",
                        pixel_offset + image_size,
                        0,
                        0,
                        pixel_offset,
                    )
                    dib_header = struct.pack(
                        "<IiiHHIIiiII",
                        40,
                        bitmap.width,
                        -bitmap.height,
                        1,
                        bits_per_pixel,
                        0,
                        image_size,
                        2835,
                        2835,
                        0,
                        0,
                    )
                    return file_header + dib_header + bytes(bitmap.buffer)
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            document.close()

    def resolve_artifact(self, record: dict[str, Any], kind: str, artifact_path: str) -> Path:
        run_dir = self.run_dir_for_record(record)
        roots = {"input": run_dir / "input", "results": run_dir / "results"}
        if kind == "source":
            source_root = Path(str(record.get("input_path") or "")).resolve()
            try:
                source_root.relative_to(self.test_root)
            except ValueError as exc:
                raise HTTPException(status_code=403, detail="source artifact path is outside the test root") from exc
            roots["source"] = source_root
        root = roots.get(kind)
        if root is None:
            raise HTTPException(status_code=400, detail="unsupported artifact kind")
        relative = self.normalize_artifact_path(artifact_path)
        target = (root / relative).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="artifact path is outside the task") from exc
        if not target.is_file():
            raise HTTPException(status_code=404, detail="artifact not found")
        allowed_suffixes = {
            "input": {".pdf"},
            "source": {".pdf"},
            "results": {".md", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".json", ".zip"},
        }
        if target.suffix.lower() not in allowed_suffixes[kind]:
            raise HTTPException(status_code=403, detail="artifact type is not previewable")
        return target

    @staticmethod
    def normalize_artifact_path(value: str) -> Path:
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise HTTPException(status_code=400, detail="invalid artifact path")
        return Path(*path.parts)

    @staticmethod
    def process_log_markdown(record: dict[str, Any]) -> str:
        settings = record.get("settings") or {}
        log_path = Path(record["log_path"])
        log_content = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else "尚无过程日志。"
        lines = [
            "# MinerU 批量任务过程日志",
            "",
            f"- Run ID：`{record['run_id']}`",
            f"- 输入：`{settings.get('input_path', record.get('input_path', '-'))}`",
            f"- 状态：`{record.get('status', '-')}`",
            f"- 创建时间：`{record.get('created_at', '-')}`",
            f"- 开始时间：`{record.get('started_at') or '-'}`",
            f"- 完成时间：`{record.get('completed_at') or '-'}`",
            f"- Backend：`{settings.get('backend', '-')}`",
            f"- PDF 数量：`{settings.get('pdf_count', '-')}`",
            "",
            "## 完整过程输出",
            "",
            "```text",
            log_content.replace("```", "'''"),
            "```",
            "",
        ]
        return "\n".join(lines)

    async def start_batch(self, request: BatchRunRequest) -> dict[str, Any]:
        if not self.batch_script.is_file():
            raise HTTPException(status_code=503, detail="batch diagnosis script is unavailable")
        input_path = self.resolve_input_path(request.input_path)
        return await self.start_batch_path(
            input_path,
            request,
            request.input_path,
            source_type="server_directory",
        )

    async def start_batch_path(
        self,
        input_path: Path,
        request: BatchRunRequest,
        display_path: str,
        *,
        source_type: str,
        preserve_input: str | None = None,
    ) -> dict[str, Any]:
        if not self.batch_script.is_file():
            raise HTTPException(status_code=503, detail="batch diagnosis script is unavailable")
        pdf_count = sum(1 for path in input_path.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf")
        if pdf_count == 0:
            raise HTTPException(status_code=400, detail="no PDF files found in the selected directory")

        run_id = str(uuid.uuid4())
        run_dir = self.report_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        effective_input_path = input_path
        if preserve_input is not None:
            preserved_input = run_dir / "input"
            try:
                if preserve_input == "move":
                    shutil.move(str(input_path), str(preserved_input))
                elif preserve_input == "copy":
                    shutil.copytree(input_path, preserved_input)
                else:
                    raise ValueError(f"unsupported input preservation mode: {preserve_input}")
            except Exception:
                shutil.rmtree(run_dir, ignore_errors=True)
                raise
            effective_input_path = preserved_input
        report_path = run_dir / "BATCH_DIAGNOSIS.md"
        raw_dir = run_dir / "raw"
        preview_dir = run_dir / "results"
        log_path = run_dir / "batch.log"
        settings = request.model_dump()
        settings["input_path"] = display_path
        settings["pdf_count"] = pdf_count
        settings["source_type"] = source_type
        record = self.store.create_batch_run(
            run_id,
            effective_input_path,
            settings,
            report_path,
            raw_dir,
            log_path,
        )
        asyncio.create_task(
            self._run_batch_process(
                run_id,
                effective_input_path,
                request,
                report_path,
                raw_dir,
                preview_dir,
                log_path,
            ),
            name=f"mineru-ops-batch-{run_id}",
        )
        return record

    @staticmethod
    def normalize_upload_name(filename: str | None) -> Path:
        raw_name = (filename or "").replace("\\", "/")
        path = PurePosixPath(raw_name)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise HTTPException(status_code=400, detail="uploaded file name contains an invalid path")
        relative = Path(*path.parts)
        if relative.suffix.lower() != ".pdf":
            raise HTTPException(status_code=400, detail=f"only PDF files are supported: {filename}")
        return relative

    async def save_uploaded_files(self, files: list[UploadFile], destination: Path) -> int:
        if not files:
            raise HTTPException(status_code=400, detail="no files were uploaded")
        destination.mkdir(parents=True, exist_ok=False)
        total_bytes = 0
        saved_count = 0
        self.cleanup_expired_artifacts()
        stored_bytes = self.artifact_storage_bytes()
        try:
            for upload in files:
                relative_path = self.normalize_upload_name(upload.filename)
                target = destination / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    target = target.with_name(f"{target.stem}-{saved_count + 1}{target.suffix}")
                with target.open("wb") as output:
                    while True:
                        chunk = await upload.read(1024 * 1024)
                        if not chunk:
                            break
                        total_bytes += len(chunk)
                        if total_bytes > self.max_upload_bytes:
                            raise HTTPException(
                                status_code=413,
                                detail=f"uploaded files exceed {self.max_upload_bytes // 1024 // 1024} MB",
                            )
                        if stored_bytes + total_bytes > self.artifact_max_bytes:
                            raise HTTPException(
                                status_code=507,
                                detail=(
                                    "operations artifact storage limit exceeded; "
                                    "delete old tasks or raise MINERU_OPS_ARTIFACT_MAX_GB"
                                ),
                            )
                        output.write(chunk)
                saved_count += 1
            if saved_count == 0:
                raise HTTPException(status_code=400, detail="no PDF files were uploaded")
            return saved_count
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise

    async def run_smoke_test(self) -> dict[str, Any]:
        form = {
            "backend": self.smoke_backend,
            "effort": "medium",
            "parse_method": "auto",
            "lang_list": "ch",
            "formula_enable": "true",
            "table_enable": "true",
            "image_analysis": "false",
            "return_md": "false",
            "return_middle_json": "false",
            "return_model_output": "false",
            "return_content_list": "false",
            "return_images": "false",
            "response_format_zip": "false",
            "return_original_file": "false",
            "client_side_output_generation": "false",
            "start_page_id": "0",
            "end_page_id": "0",
        }
        response = await self.http_client.post(
            f"{self.router_url}/tasks",
            data=form,
            files={
                "files": (
                    "mineru-ops-smoke-test.pdf",
                    build_smoke_test_pdf(),
                    "application/pdf",
                )
            },
            timeout=60.0,
        )
        if response.status_code != 202:
            raise HTTPException(
                status_code=502,
                detail=f"smoke task submission failed: HTTP {response.status_code}: {response.text[:1000]}",
            )
        payload = response.json()
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise HTTPException(status_code=502, detail="smoke task response has no task_id")
        deadline = asyncio.get_running_loop().time() + self.smoke_timeout
        while asyncio.get_running_loop().time() < deadline:
            status_response = await self.http_client.get(f"{self.router_url}/tasks/{task_id}", timeout=30.0)
            if status_response.status_code == 200:
                payload = status_response.json()
                if payload.get("status") in {"completed", "failed"}:
                    return payload
            await asyncio.sleep(2.0)
        raise HTTPException(
            status_code=504,
            detail=f"smoke task {task_id} did not finish within {self.smoke_timeout} seconds",
        )

    async def _run_batch_process(
        self,
        run_id: str,
        input_path: Path,
        request: BatchRunRequest,
        report_path: Path,
        raw_dir: Path,
        preview_dir: Path,
        log_path: Path,
    ) -> None:
        command = [
            "python",
            str(self.batch_script),
            str(input_path),
            "--router-url",
            self.router_url,
            "--backend",
            request.backend,
            "--effort",
            request.effort,
            "--parse-method",
            request.parse_method,
            "--lang",
            request.lang,
            "--task-timeout",
            str(request.task_timeout),
            "--pause-seconds",
            str(request.pause_seconds),
            "--output",
            str(report_path),
            "--raw-dir",
            str(raw_dir),
            "--preview-dir",
            str(preview_dir),
            "--no-collect-diagnostics",
        ]
        command.append("--save-result-images" if self.save_result_images else "--no-save-result-images")
        if request.recursive:
            command.append("--recursive")
        if request.server_url:
            command.extend(["--server-url", request.server_url])
        self.store.update_batch_run(run_id, status="running", started_at=utc_now_iso())
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self.batch_processes[run_id] = process
            with log_path.open("a", encoding="utf-8") as log_file:
                if process.stdout is not None:
                    while True:
                        line = await process.stdout.readline()
                        if not line:
                            break
                        log_file.write(line.decode("utf-8", errors="replace"))
                        log_file.flush()
            exit_code = await process.wait()
            await self._collect_batch_diagnostics(report_path.parent, report_path)
            current = self.store.get_batch_run(run_id) or {}
            if current.get("status") == "cancelling":
                status = "cancelled"
            elif exit_code == 0:
                status = "completed"
            elif exit_code == 1 and report_path.is_file():
                status = "completed_with_failures"
            else:
                status = "failed"
            self.store.update_batch_run(
                run_id,
                status=status,
                completed_at=utc_now_iso(),
                exit_code=exit_code,
            )
        except Exception as exc:
            self.store.update_batch_run(
                run_id,
                status="failed",
                completed_at=utc_now_iso(),
                error=str(exc),
            )
        finally:
            self.batch_processes.pop(run_id, None)

    async def _collect_batch_diagnostics(self, run_dir: Path, report_path: Path) -> None:
        diagnostics = await self.agent_call(
            {"action": "diagnostics", "tail": 800},
            timeout=90,
        )
        diagnostics_path = run_dir / "host-diagnostics.json"
        diagnostics_path.write_text(
            json.dumps(diagnostics, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if not report_path.is_file():
            return
        npu_output = str((diagnostics.get("npu_smi") or {}).get("output", ""))
        log_targets = diagnostics.get("logs") if isinstance(diagnostics.get("logs"), dict) else {}
        lines = [
            "",
            "## 控制台附加诊断",
            "",
            "- 完整主机诊断：`host-diagnostics.json`（包含在 ZIP 导出中）",
            f"- 控制代理状态：`{'available' if diagnostics.get('ok') else 'unavailable'}`",
            "",
        ]
        if npu_output:
            lines.extend(
                [
                    "### npu-smi 摘要",
                    "",
                    "```text",
                    "\n".join(npu_output.splitlines()[:100]).replace("```", "'''"),
                    "```",
                    "",
                ]
            )
        for service_name, log_result in log_targets.items():
            if not isinstance(log_result, dict):
                continue
            output = str(log_result.get("logs", ""))
            if not output:
                continue
            lines.extend(
                [
                    f"### {service_name} 日志尾部",
                    "",
                    "```text",
                    "\n".join(output.splitlines()[-120:]).replace("```", "'''"),
                    "```",
                    "",
                ]
            )
        with report_path.open("a", encoding="utf-8") as report_file:
            report_file.write("\n".join(lines))


def create_app() -> FastAPI:
    runtime = OpsRuntime()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime = runtime
        try:
            yield
        finally:
            await runtime.close()

    app = FastAPI(title="MinerU Operations Console", lifespan=lifespan)
    static_dir = Path(__file__).resolve().parents[1] / "ops" / "static"

    def authorize(request: Request, *, write: bool = False) -> None:
        configured_token = runtime.auth_token
        supplied_token = request.headers.get("X-MinerU-Ops-Token") or request.query_params.get("token", "")
        if configured_token and not secrets.compare_digest(configured_token, supplied_token):
            raise HTTPException(status_code=401, detail="invalid operations token")
        if write and not configured_token:
            raise HTTPException(
                status_code=403,
                detail="service control is disabled until MINERU_OPS_AUTH_TOKEN is configured",
            )

    async def query_service(service: dict[str, Any]) -> dict[str, Any]:
        result = dict(service)
        endpoint = service.get("endpoint")
        if not endpoint:
            result.update({"health": "unknown", "health_error": "no health endpoint"})
            return result
        try:
            response = await runtime.http_client.get(endpoint)
            payload = response.json() if "json" in response.headers.get("content-type", "") else None
            result.update(
                {
                    "health": "healthy" if response.status_code == 200 else "unhealthy",
                    "health_status_code": response.status_code,
                    "health_payload": payload,
                    "health_error": None if response.status_code == 200 else response.text[:1000],
                }
            )
        except Exception as exc:
            result.update({"health": "unavailable", "health_error": str(exc)})
        return result

    @app.get("/api/health")
    async def ops_health():
        return {"status": "healthy", "service": "mineru-ops", "time": utc_now_iso()}

    @app.get("/api/overview")
    async def overview(request: Request):
        authorize(request)
        services = await services_view(request)
        tasks_payload = await tasks_view(request, limit=100, offset=0, status=None)
        tasks = tasks_payload.get("items", [])
        counts: dict[str, int] = {}
        skipped_pages = 0
        failed_pages = 0
        for task in tasks:
            status = str(task.get("status", "unknown"))
            counts[status] = counts.get(status, 0) + 1
            progress = task.get("progress") or {}
            skipped_pages += int(progress.get("skipped_pages", 0) or 0)
            failed_pages += int(progress.get("failed_pages", 0) or 0)
        return {
            "services": services,
            "task_counts": counts,
            "skipped_pages": skipped_pages,
            "failed_pages": failed_pages,
            "batch_runs": runtime.store.list_batch_runs(limit=10),
            "updated_at": utc_now_iso(),
        }

    @app.get("/api/services")
    async def services_view(request: Request):
        authorize(request)
        definitions = runtime.discover_services()
        health_results = await asyncio.gather(*(query_service(service) for service in definitions))
        agent_result = await runtime.agent_call({"action": "services"}, timeout=15)
        runtime_states = agent_result.get("services", {}) if agent_result.get("ok") else {}
        for service in health_results:
            state = runtime_states.get(service["name"], {}) if isinstance(runtime_states, dict) else {}
            apply_service_runtime_health(service, state)
            if not agent_result.get("ok"):
                service["agent_error"] = agent_result.get("error")
        return health_results

    @app.post("/api/services/{service_name}/actions/{action}")
    async def service_action(service_name: str, action: str, request: Request):
        authorize(request, write=True)
        allowed_actions = {"check", "test", "start", "stop", "restart"}
        if action not in allowed_actions:
            raise HTTPException(status_code=400, detail="unsupported service action")
        definition = next(
            (item for item in runtime.discover_services() if item["name"] == service_name),
            None,
        )
        if definition is None:
            raise HTTPException(status_code=404, detail="service not found")
        if action in {"check", "test"}:
            result = await query_service(definition)
            if definition.get("role") == "code-sync":
                agent_result = await runtime.agent_call({"action": "services"}, timeout=15)
                runtime_states = agent_result.get("services", {}) if agent_result.get("ok") else {}
                state = runtime_states.get(service_name, {}) if isinstance(runtime_states, dict) else {}
                apply_service_runtime_health(result, state)
            success = result.get("health") == "healthy"
            runtime.store.audit(action, service_name, success, json.dumps(result, ensure_ascii=False))
            return result
        if not definition.get("control_enabled"):
            raise HTTPException(status_code=403, detail="this service cannot be controlled from the console")
        result = await runtime.agent_call(
            {"action": "service_action", "service": service_name, "operation": action},
            timeout=180,
        )
        runtime.store.audit(action, service_name, bool(result.get("ok")), json.dumps(result, ensure_ascii=False))
        if not result.get("ok"):
            raise HTTPException(status_code=502, detail=result.get("error", "service action failed"))
        return result

    @app.post("/api/system/actions/{action}")
    async def system_action(action: str, request: Request):
        authorize(request, write=True)
        if action == "test":
            try:
                result = await runtime.run_smoke_test()
                success = result.get("status") == "completed"
                runtime.store.audit("system_test", "mineru-stack", success, json.dumps(result, ensure_ascii=False))
                if not success:
                    raise HTTPException(status_code=502, detail=result)
                return result
            except HTTPException:
                raise
            except Exception as exc:
                runtime.store.audit("system_test", "mineru-stack", False, str(exc))
                raise HTTPException(status_code=502, detail=str(exc)) from exc
        if action not in {"start", "stop", "restart"}:
            raise HTTPException(status_code=400, detail="unsupported system action")
        result = await runtime.agent_call(
            {"action": "stack_action", "operation": action},
            timeout=240,
        )
        runtime.store.audit(f"system_{action}", "mineru-stack", bool(result.get("ok")), json.dumps(result, ensure_ascii=False))
        if not result.get("ok"):
            raise HTTPException(status_code=502, detail=result.get("error", "system action failed"))
        return result

    @app.get("/api/services/{service_name}/logs")
    async def service_logs(service_name: str, request: Request, tail: int = 300):
        authorize(request)
        result = await runtime.agent_call(
            {"action": "logs", "service": service_name, "tail": min(max(tail, 20), 2000)},
            timeout=30,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=502, detail=result.get("error", "logs unavailable"))
        return result

    @app.get("/api/tasks")
    async def tasks_view(
        request: Request,
        limit: int = 100,
        offset: int = 0,
        status: str | None = None,
    ):
        authorize(request)
        params: dict[str, Any] = {"limit": min(max(limit, 1), 500), "offset": max(offset, 0)}
        if status:
            params["status"] = status
        try:
            response = await runtime.http_client.get(f"{runtime.router_url}/tasks", params=params)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("router task list is not an object")
            items = payload.get("items") if isinstance(payload.get("items"), list) else []
            runtime.store.upsert_tasks([item for item in items if isinstance(item, dict)])
            payload["source"] = "live"
            return payload
        except Exception as exc:
            cached = runtime.store.cached_tasks(limit=params["limit"])
            return {
                "items": cached,
                "total": len(cached),
                "limit": params["limit"],
                "offset": 0,
                "source": "cache",
                "error": str(exc),
            }

    @app.get("/api/tasks/{task_id}")
    async def task_detail(task_id: str, request: Request):
        authorize(request)
        try:
            response = await runtime.http_client.get(f"{runtime.router_url}/tasks/{task_id}")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("router task payload is not an object")
            runtime.store.upsert_tasks([payload])
            payload["source"] = "live"
            return payload
        except Exception as exc:
            cached = runtime.store.cached_task(task_id)
            if cached is None:
                raise HTTPException(status_code=404, detail=f"task unavailable: {exc}") from exc
            cached["source"] = "cache"
            cached["cache_error"] = str(exc)
            return cached

    @app.get("/api/tasks/{task_id}/preview")
    async def task_preview(task_id: str, request: Request):
        authorize(request)
        return runtime.task_preview_artifacts(task_id)

    @app.get("/api/tasks/{task_id}/preview/pages/{page_number}")
    async def task_preview_page(
        task_id: str,
        page_number: int,
        request: Request,
    ):
        authorize(request)
        preview = runtime.task_preview_artifacts(task_id)
        record = runtime.store.get_batch_run(str(preview["run_id"]))
        if record is None:
            raise HTTPException(status_code=404, detail="batch run not found")
        original = preview["original"]
        path = runtime.resolve_artifact(
            record,
            str(original.get("kind") or "input"),
            str(original["path"]),
        )
        content = await asyncio.to_thread(runtime.render_pdf_page, path, page_number)
        return Response(content=content, media_type="image/bmp")

    @app.get("/api/tasks/{task_id}/events")
    async def task_events(task_id: str, request: Request):
        authorize(request)

        async def event_stream():
            last_signature: tuple[str, int] | None = None
            while True:
                if await request.is_disconnected():
                    break
                try:
                    response = await runtime.http_client.get(f"{runtime.router_url}/tasks/{task_id}")
                    response.raise_for_status()
                    payload = response.json()
                except Exception as exc:
                    payload = {"task_id": task_id, "status": "unavailable", "error": str(exc)}
                progress = payload.get("progress") or {}
                signature = (str(payload.get("status")), int(progress.get("version", 0)))
                if signature != last_signature:
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    last_signature = signature
                if payload.get("status") in {"completed", "failed"}:
                    break
                await asyncio.sleep(1.0)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/batch-runs")
    async def batch_runs(request: Request):
        authorize(request)
        return {
            "items": runtime.store.list_batch_runs(),
            "storage": runtime.artifact_storage_status(),
        }

    @app.post("/api/batch-runs", status_code=202)
    async def create_batch_run(payload: BatchRunRequest, request: Request):
        authorize(request, write=True)
        result = await runtime.start_batch(payload)
        runtime.store.audit("batch_start", result.get("run_id", "unknown"), True, payload.model_dump_json())
        return result

    @app.post("/api/batch-runs/upload", status_code=202)
    async def upload_batch_run(
        request: Request,
        files: list[UploadFile] = File(...),
        backend: str = Form("vlm-http-client"),
        lang: str = Form("ch"),
        task_timeout: int = Form(7200),
        server_url: str | None = Form(None),
        recursive: bool = Form(True),
    ):
        authorize(request, write=True)
        upload_id = str(uuid.uuid4())
        upload_path = runtime.upload_dir / upload_id
        try:
            uploaded_count = await runtime.save_uploaded_files(files, upload_path)
            payload = BatchRunRequest(
                input_path=f"browser-upload/{upload_id}",
                backend=backend,
                lang=lang,
                task_timeout=task_timeout,
                server_url=server_url or None,
                recursive=recursive,
            )
            display_path = f"浏览器上传（{uploaded_count} 个 PDF）"
            result = await runtime.start_batch_path(
                upload_path,
                payload,
                display_path,
                source_type="browser_upload",
                preserve_input="move",
            )
        except Exception:
            shutil.rmtree(upload_path, ignore_errors=True)
            raise
        audit_payload = payload.model_dump()
        audit_payload["uploaded_files"] = uploaded_count
        runtime.store.audit(
            "batch_upload_start",
            result.get("run_id", "unknown"),
            True,
            json.dumps(audit_payload, ensure_ascii=False),
        )
        return result

    @app.get("/api/batch-runs/{run_id}")
    async def batch_run(run_id: str, request: Request):
        authorize(request)
        result = runtime.store.get_batch_run(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="batch run not found")
        result["artifacts"] = runtime.batch_artifacts(result)
        return result

    @app.get("/api/batch-runs/{run_id}/artifacts/{kind}/{artifact_path:path}")
    async def batch_artifact(
        run_id: str,
        kind: str,
        artifact_path: str,
        request: Request,
    ):
        authorize(request)
        record = runtime.store.get_batch_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="batch run not found")
        path = runtime.resolve_artifact(record, kind, artifact_path)
        return FileResponse(path)

    @app.post("/api/batch-runs/{run_id}/{action}")
    async def batch_action(run_id: str, action: str, request: Request):
        authorize(request, write=True)
        record = runtime.store.get_batch_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="batch run not found")
        process = runtime.batch_processes.get(run_id)
        if action == "cancel":
            if process is None or process.returncode is not None:
                raise HTTPException(status_code=409, detail="batch run is not active")
            runtime.store.update_batch_run(run_id, status="cancelling")
            process.terminate()
        elif action == "pause":
            if process is None or process.returncode is not None:
                raise HTTPException(status_code=409, detail="batch run is not active")
            process.send_signal(signal.SIGSTOP)
            runtime.store.update_batch_run(run_id, status="paused")
        elif action == "resume":
            if process is None or process.returncode is not None:
                raise HTTPException(status_code=409, detail="batch run is not active")
            process.send_signal(signal.SIGCONT)
            runtime.store.update_batch_run(run_id, status="running")
        elif action == "retry":
            settings = dict(record["settings"])
            source_type = str(settings.pop("source_type", "server_directory"))
            settings.pop("pdf_count", None)
            display_path = str(settings.get("input_path") or ".")
            if source_type == "browser_upload":
                input_path = runtime.run_dir_for_record(record) / "input"
                if not input_path.is_dir():
                    raise HTTPException(
                        status_code=409,
                        detail="uploaded files have expired; upload them again to retry",
                    )
                settings["input_path"] = "."
                retry_payload = BatchRunRequest(**settings)
                return await runtime.start_batch_path(
                    input_path,
                    retry_payload,
                    f"重试：{display_path}",
                    source_type="browser_upload",
                    preserve_input="copy",
                )
            retry_payload = BatchRunRequest(**settings)
            return await runtime.start_batch(retry_payload)
        elif action == "delete":
            if process is not None and process.returncode is None:
                raise HTTPException(status_code=409, detail="cancel the active batch run before deleting it")
            run_dir = runtime.run_dir_for_record(record)
            shutil.rmtree(run_dir)
            runtime.store.delete_batch_run(run_id)
            runtime.store.audit("batch_delete", run_id, True)
            return {"ok": True, "run_id": run_id}
        else:
            raise HTTPException(status_code=400, detail="unsupported batch action")
        runtime.store.audit(f"batch_{action}", run_id, True)
        return runtime.store.get_batch_run(run_id)

    @app.get("/api/batch-runs/{run_id}/export")
    async def export_batch_run(run_id: str, request: Request, format: str = "markdown"):
        authorize(request)
        record = runtime.store.get_batch_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="batch run not found")
        if format == "json":
            return JSONResponse(record)
        report_path = Path(record["report_path"])
        if format == "markdown":
            if not report_path.is_file():
                raise HTTPException(status_code=404, detail="report is not ready")
            return FileResponse(report_path, filename=f"BATCH_DIAGNOSIS-{run_id}.md")
        if format == "process_markdown":
            content = runtime.process_log_markdown(record)
            return Response(
                content=content,
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="PROCESS_LOG-{run_id}.md"'},
            )
        if format != "zip":
            raise HTTPException(
                status_code=400,
                detail="format must be markdown, process_markdown, json, or zip",
            )
        zip_path = report_path.parent / f"mineru-batch-{run_id}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in report_path.parent.rglob("*"):
                if path.is_file() and path != zip_path:
                    relative_path = path.relative_to(report_path.parent)
                    if relative_path.parts and relative_path.parts[0] == "input":
                        continue
                    archive.write(path, relative_path)
        return FileResponse(zip_path, filename=zip_path.name)

    if static_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=static_dir), name="ops-assets")

        @app.get("/")
        async def index():
            return FileResponse(static_dir / "index.html")

    return app


app = create_app()


@click.command()
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=19000, type=int)
def main(host: str, port: int) -> None:
    uvicorn.run(app, host=host, port=port, access_log=False)


if __name__ == "__main__":
    main()
