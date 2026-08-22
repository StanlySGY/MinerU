#!/usr/bin/env python3
"""Batch-test PDFs through mineru-router and write a Markdown diagnostic report."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote


TERMINAL_STATUSES = {"completed", "failed"}
RETRYABLE_HTTP_STATUSES = {429, 502, 503, 504}


class CurlRequestError(RuntimeError):
    pass


class TaskWaitTimeout(TimeoutError):
    """The diagnostic client stopped waiting; the remote task may still be running."""

    def __init__(
        self,
        message: str,
        *,
        last_payload: dict[str, Any] | None = None,
        raw_paths: list[Path] | None = None,
    ) -> None:
        super().__init__(message)
        self.last_payload = last_payload or {}
        self.raw_paths = list(raw_paths or [])


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    payload: dict[str, Any] | None
    body: str


@dataclass(frozen=True)
class RunConfig:
    input_dir: Path
    router_url: str
    backend: str
    effort: str
    parse_method: str
    lang: str
    server_url: str | None
    image_analysis: bool
    formula_enable: bool
    table_enable: bool
    start_page_id: int
    end_page_id: int
    recursive: bool
    poll_interval: float
    task_timeout: float
    page_timeout_seconds: float
    page_connect_max_retries: int
    vlm_batch_size: int
    pause_seconds: float
    submit_retries: int
    curl_bin: str
    output_path: Path
    raw_dir: Path
    preview_dir: Path | None
    save_result_images: bool
    collect_diagnostics: bool
    router_container: str
    api_container: str
    vlm_container: str | None
    vlm_ssh: str | None
    vlm_metrics_url: str | None
    log_tail: int


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def utc_docker_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "-"
    seconds = max(0, int(round(float(seconds))))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_size(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def markdown_escape(value: Any, limit: int = 500) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned[:100] or "pdf"


def bool_form(value: bool) -> str:
    return "true" if value else "false"


def discover_pdfs(input_dir: Path, recursive: bool) -> list[Path]:
    iterator = input_dir.rglob("*") if recursive else input_dir.iterdir()
    return sorted(
        (path for path in iterator if path.is_file() and path.suffix.lower() == ".pdf"),
        key=lambda path: str(path.relative_to(input_dir)).lower(),
    )


def curl_request(
    config: RunConfig,
    method: str,
    url: str,
    *,
    forms: list[tuple[str, str]] | None = None,
    file_path: Path | None = None,
    max_time: int = 300,
) -> HttpResponse:
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="mineru-diagnose-", delete=False) as handle:
            temp_path = Path(handle.name)

        command = [
            config.curl_bin,
            "--silent",
            "--show-error",
            "--location",
            "--request",
            method,
            "--connect-timeout",
            "10",
            "--max-time",
            str(max_time),
            "--output",
            str(temp_path),
            "--write-out",
            "%{http_code}",
        ]
        for key, value in forms or []:
            command.extend(["--form-string", f"{key}={value}"])
        if file_path is not None:
            command.extend(["--form", f"files=@{file_path};type=application/pdf"])
        command.append(url)

        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        body = temp_path.read_text(encoding="utf-8", errors="replace")
        if completed.returncode != 0:
            message = completed.stderr.strip() or f"curl exited with {completed.returncode}"
            raise CurlRequestError(message)
        try:
            status_code = int(completed.stdout.strip())
        except ValueError as exc:
            raise CurlRequestError(f"curl returned an invalid HTTP status: {completed.stdout!r}") from exc

        payload: dict[str, Any] | None = None
        try:
            decoded = json.loads(body)
            if isinstance(decoded, dict):
                payload = decoded
        except json.JSONDecodeError:
            pass
        return HttpResponse(status_code=status_code, payload=payload, body=body)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def write_raw_response(
    raw_dir: Path,
    prefix: str,
    label: str,
    response: HttpResponse,
) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".json" if response.payload is not None else ".txt"
    path = raw_dir / f"{prefix}-{label}{suffix}"
    if response.payload is not None:
        content = json.dumps(response.payload, ensure_ascii=False, indent=2)
    else:
        content = response.body
    path.write_text(content + ("\n" if content and not content.endswith("\n") else ""), encoding="utf-8")
    return path


def write_raw_text(raw_dir: Path, prefix: str, label: str, content: str) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{prefix}-{label}.txt"
    path.write_text(
        content + ("\n" if content and not content.endswith("\n") else ""),
        encoding="utf-8",
    )
    return path


def download_file(
    config: RunConfig,
    url: str,
    destination: Path,
    *,
    max_time: int = 600,
) -> tuple[int, str, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial_path = destination.with_suffix(destination.suffix + ".part")
    command = [
        config.curl_bin,
        "--silent",
        "--show-error",
        "--location",
        "--connect-timeout",
        "10",
        "--max-time",
        str(max_time),
        "--output",
        str(partial_path),
        "--write-out",
        "%{http_code}\n%{content_type}",
        url,
    ]
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        partial_path.unlink(missing_ok=True)
        message = completed.stderr.strip() or f"curl exited with {completed.returncode}"
        raise CurlRequestError(message)
    output_lines = completed.stdout.splitlines()
    try:
        status_code = int(output_lines[0].strip())
    except (IndexError, ValueError) as exc:
        partial_path.unlink(missing_ok=True)
        raise CurlRequestError(f"curl returned invalid download metadata: {completed.stdout!r}") from exc
    content_type = output_lines[1].strip() if len(output_lines) > 1 else ""
    if status_code != 200:
        body = partial_path.read_text(encoding="utf-8", errors="replace")[:4000]
        partial_path.unlink(missing_ok=True)
        return status_code, content_type, body
    partial_path.replace(destination)
    return status_code, content_type, ""


def safe_extract_zip(archive_path: Path, destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    total_size = 0
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if len(entries) > 20000:
            raise CurlRequestError("result archive contains too many files")
        for entry in entries:
            normalized_name = entry.filename.replace("\\", "/")
            relative = PurePosixPath(normalized_name)
            if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
                raise CurlRequestError(f"unsafe path in result archive: {entry.filename}")
            file_type = (entry.external_attr >> 16) & 0o170000
            if file_type == 0o120000:
                raise CurlRequestError(f"symbolic link is not allowed in result archive: {entry.filename}")
            total_size += max(0, entry.file_size)
            if total_size > 4 * 1024 * 1024 * 1024:
                raise CurlRequestError("result archive expands beyond 4 GB")
            target = (destination / Path(*relative.parts)).resolve()
            try:
                target.relative_to(destination.resolve())
            except ValueError as exc:
                raise CurlRequestError(f"unsafe path in result archive: {entry.filename}") from exc
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            extracted.append(target)
    return extracted


def download_result_preview(
    config: RunConfig,
    result_url: str,
    pdf_path: Path,
    index: int,
) -> tuple[dict[str, Any], dict[str, Any] | None, list[Path]]:
    if config.preview_dir is None:
        raise CurlRequestError("preview directory is not configured")
    preview_root = config.preview_dir / f"{index:04d}-{safe_name(pdf_path.stem)}"
    archive_path = preview_root / "result.zip"
    status_code, content_type, error_body = download_file(
        config,
        result_url,
        archive_path,
    )
    if status_code != 200:
        raise CurlRequestError(
            f"result download failed: HTTP {status_code} {content_type}: {markdown_escape(error_body, 1000)}"
        )
    extract_dir = preview_root / "extracted"
    extracted_paths = safe_extract_zip(archive_path, extract_dir)
    markdown_paths = sorted(path for path in extracted_paths if path.suffix.lower() == ".md")
    image_paths = sorted(path for path in extracted_paths if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"})
    report_payload: dict[str, Any] | None = None
    parse_report = next((path for path in extracted_paths if path.name == "_parse_report.json"), None)
    if parse_report is not None:
        try:
            decoded = json.loads(parse_report.read_text(encoding="utf-8"))
            if isinstance(decoded, dict):
                report_payload = decoded
        except (OSError, json.JSONDecodeError):
            pass
    preview = {
        "archive_path": archive_path.relative_to(config.preview_dir).as_posix(),
        "markdown_path": (markdown_paths[0].relative_to(config.preview_dir).as_posix() if markdown_paths else None),
        "image_paths": [path.relative_to(config.preview_dir).as_posix() for path in image_paths],
        "image_count": len(image_paths),
        "extracted_file_count": len(extracted_paths),
    }
    return preview, report_payload, [archive_path]


def write_preview_metadata(config: RunConfig, result: dict[str, Any]) -> None:
    if config.preview_dir is None:
        return
    index = int(result["index"])
    file_name = str(result["file_name"])
    preview_root = config.preview_dir / f"{index:04d}-{safe_name(Path(file_name).stem)}"
    preview_root.mkdir(parents=True, exist_ok=True)
    metadata = {
        "index": index,
        "file_name": file_name,
        "relative_path": result.get("relative_path"),
        "task_id": result.get("task_id"),
        "task_status": result.get("task_status"),
        "classification": result.get("classification"),
        "partial_success": result.get("partial_success", False),
        "failed_pages": result.get("failed_pages") or [],
        "error": result.get("error"),
        "started_at": result.get("started_at"),
        "completed_at": result.get("completed_at"),
        "elapsed_seconds": result.get("elapsed_seconds"),
        "progress": result.get("progress") or {},
        "preview": result.get("preview") or {},
    }
    (preview_root / "preview.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_command(command: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, f"{type(exc).__name__}: {exc}"
    output_parts = [part.strip() for part in (completed.stdout, completed.stderr) if part.strip()]
    return completed.returncode, "\n".join(output_parts)


def run_vlm_host_command(config: RunConfig, command: list[str]) -> tuple[int, str]:
    if not config.vlm_ssh:
        return run_command(command)
    remote_command = " ".join(shlex.quote(part) for part in command)
    return run_command(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            config.vlm_ssh,
            remote_command,
        ],
        timeout=90,
    )


def log_excerpt(text: str, task_id: str | None, pdf_name: str) -> str:
    if not text:
        return ""
    keywords = (
        "error",
        "exception",
        "traceback",
        "timeout",
        "timed out",
        "failed",
        "skip",
        "warning",
        "warn",
        "queue",
        "waiting",
        "running",
        "token",
        "page",
        "/v1/chat/completions",
    )
    identifiers = [value.lower() for value in (task_id, pdf_name, Path(pdf_name).stem) if value]
    selected = []
    for line in text.splitlines():
        lowered = line.lower()
        if any(identifier in lowered for identifier in identifiers) or any(keyword in lowered for keyword in keywords):
            selected.append(line)
    if not selected:
        selected = text.splitlines()[-40:]
    return "\n".join(selected[-120:])


def capture_container_logs(
    config: RunConfig,
    container_name: str,
    started_at_utc: str,
    completed_at_utc: str,
    *,
    remote_vlm: bool = False,
) -> tuple[int, str]:
    command = [
        "docker",
        "logs",
        "--since",
        started_at_utc,
        "--until",
        completed_at_utc,
        "--timestamps",
        "--tail",
        str(config.log_tail),
        container_name,
    ]
    if remote_vlm:
        return run_vlm_host_command(config, command)
    return run_command(command, timeout=90)


def capture_metrics(config: RunConfig) -> tuple[int, str]:
    if not config.vlm_metrics_url:
        return 0, ""
    try:
        response = curl_request(
            config,
            "GET",
            config.vlm_metrics_url,
            max_time=20,
        )
    except CurlRequestError as exc:
        return 1, str(exc)
    if response.status_code != 200:
        return response.status_code, response.body
    metric_names = (
        "vllm:num_requests_running",
        "vllm:num_requests_waiting",
        "vllm:gpu_cache_usage_perc",
        "vllm:request_success_total",
        "vllm:time_to_first_token_seconds",
        "vllm:e2e_request_latency_seconds",
        "vllm:request_queue_time_seconds",
        "vllm:request_prefill_time_seconds",
        "vllm:request_decode_time_seconds",
    )
    selected = [
        line for line in response.body.splitlines() if not line.startswith("#") and any(name in line for name in metric_names)
    ]
    return 0, "\n".join(selected[-200:])


def capture_task_diagnostics(
    config: RunConfig,
    raw_prefix: str,
    started_at_utc: str,
    completed_at_utc: str,
    task_id: str | None,
    pdf_name: str,
) -> tuple[dict[str, str], list[Path]]:
    if not config.collect_diagnostics:
        return {}, []

    diagnostics: dict[str, str] = {}
    raw_paths: list[Path] = []
    log_targets = [
        ("router_logs", config.router_container, False),
        ("api_logs", config.api_container, False),
    ]
    if config.vlm_container:
        log_targets.append(("vlm_logs", config.vlm_container, bool(config.vlm_ssh)))

    for label, container_name, remote_vlm in log_targets:
        return_code, output = capture_container_logs(
            config,
            container_name,
            started_at_utc,
            completed_at_utc,
            remote_vlm=remote_vlm,
        )
        if return_code != 0:
            output = f"UNAVAILABLE (exit={return_code}): {output}"
        raw_paths.append(write_raw_text(config.raw_dir, raw_prefix, label, output))
        diagnostics[label] = log_excerpt(output, task_id, pdf_name)

    npu_return_code, npu_output = run_vlm_host_command(config, ["npu-smi", "info"])
    if npu_return_code != 0:
        npu_output = f"UNAVAILABLE (exit={npu_return_code}): {npu_output}"
    raw_paths.append(write_raw_text(config.raw_dir, raw_prefix, "npu-smi", npu_output))
    diagnostics["npu_smi"] = "\n".join(npu_output.splitlines()[:120])

    metrics_return_code, metrics_output = capture_metrics(config)
    if config.vlm_metrics_url:
        if metrics_return_code != 0:
            metrics_output = f"UNAVAILABLE (status={metrics_return_code}): {metrics_output}"
        raw_paths.append(write_raw_text(config.raw_dir, raw_prefix, "vllm-metrics", metrics_output))
        diagnostics["vllm_metrics"] = metrics_output

    if not config.vlm_container:
        diagnostics["vlm_logs"] = (
            "UNAVAILABLE: no VLM container was configured. Use --vlm-container and, for a remote host, --vlm-ssh."
        )
    return diagnostics, raw_paths


def relative_link(path: Path, report_path: Path) -> str:
    return Path(os.path.relpath(path, report_path.parent)).as_posix()


def extract_file_results(*payloads: dict[str, Any] | None) -> list[dict[str, Any]]:
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        file_results = payload.get("file_results")
        if isinstance(file_results, list) and all(isinstance(item, dict) for item in file_results):
            return list(file_results)
    return []


def collect_failed_pages(file_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failed_pages: list[dict[str, Any]] = []
    for file_result in file_results:
        pages = file_result.get("failed_pages")
        if not isinstance(pages, list):
            continue
        for page in pages:
            if not isinstance(page, dict):
                continue
            item = dict(page)
            item.setdefault("file_name", file_result.get("file_name"))
            failed_pages.append(item)
    return failed_pages


def collect_all_pages(progress: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten progress.files[].pages[] (from the Router status payload) into
    a per-page list, tagging each row with its file name and sorted by
    file name then page number. Returns [] when the server response has no
    page-level progress (older deployments, or a task that never queued
    per-page timing)."""
    rows: list[dict[str, Any]] = []
    files = progress.get("files")
    if not isinstance(files, list):
        return rows
    for file_state in files:
        if not isinstance(file_state, dict):
            continue
        file_name = file_state.get("file_name")
        pages = file_state.get("pages")
        if not isinstance(pages, list):
            continue
        for page in pages:
            if not isinstance(page, dict):
                continue
            status = page.get("status")
            if status not in {"completed", "skipped", "failed"}:
                continue
            item = dict(page)
            item.setdefault("file_name", file_name)
            rows.append(item)
    rows.sort(
        key=lambda row: (
            str(row.get("file_name") or ""),
            int(row.get("page_number") or 0),
        )
    )
    return rows


def format_page_seconds(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "-"


def classify_result(
    task_status: str,
    partial_success: bool,
    file_results: list[dict[str, Any]],
    failed_pages: list[dict[str, Any]],
) -> str:
    if task_status != "completed":
        return "failed"
    if partial_success or failed_pages:
        return "partial"
    if any(item.get("status") != "completed" for item in file_results):
        return "partial"
    return "success"


def build_submit_forms(config: RunConfig) -> list[tuple[str, str]]:
    preview_enabled = config.preview_dir is not None
    forms = [
        ("lang_list", config.lang),
        ("backend", config.backend),
        ("effort", config.effort),
        ("parse_method", config.parse_method),
        ("formula_enable", bool_form(config.formula_enable)),
        ("table_enable", bool_form(config.table_enable)),
        ("image_analysis", bool_form(config.image_analysis)),
        ("return_md", bool_form(preview_enabled)),
        ("return_middle_json", "false"),
        ("return_model_output", "false"),
        ("return_content_list", "false"),
        ("return_images", bool_form(preview_enabled and config.save_result_images)),
        ("response_format_zip", bool_form(preview_enabled)),
        ("return_original_file", "false"),
        ("client_side_output_generation", "false"),
        ("start_page_id", str(config.start_page_id)),
        ("end_page_id", str(config.end_page_id)),
        ("page_timeout_seconds", str(config.page_timeout_seconds)),
        ("page_connect_max_retries", str(config.page_connect_max_retries)),
        ("vlm_batch_size", str(config.vlm_batch_size)),
    ]
    if config.server_url:
        forms.append(("server_url", config.server_url))
    return forms


def submit_pdf(config: RunConfig, pdf_path: Path) -> HttpResponse:
    url = f"{config.router_url}/tasks"
    last_error: Exception | None = None
    for attempt in range(config.submit_retries + 1):
        try:
            response = curl_request(
                config,
                "POST",
                url,
                forms=build_submit_forms(config),
                file_path=pdf_path,
                max_time=300,
            )
            if response.status_code not in RETRYABLE_HTTP_STATUSES or attempt >= config.submit_retries:
                return response
        except CurlRequestError as exc:
            last_error = exc
            if attempt >= config.submit_retries:
                raise
        time.sleep(min(2**attempt, 5))
    raise CurlRequestError(str(last_error or "task submission failed"))


def poll_task(
    config: RunConfig,
    task_id: str,
    raw_prefix: str,
) -> tuple[dict[str, Any], list[Path]]:
    deadline = time.monotonic() + config.task_timeout
    raw_paths: list[Path] = []
    last_payload: dict[str, Any] | None = None
    status_url = f"{config.router_url}/tasks/{quote(task_id, safe='')}"
    poll_count = 0

    while time.monotonic() < deadline:
        poll_count += 1
        response = curl_request(config, "GET", status_url, max_time=60)
        if response.status_code != 200 or response.payload is None:
            if response.status_code in RETRYABLE_HTTP_STATUSES:
                time.sleep(config.poll_interval)
                continue
            raise CurlRequestError(
                f"status request failed: HTTP {response.status_code}: {markdown_escape(response.body, 1000)}"
            )
        last_payload = response.payload
        status = str(last_payload.get("status", "unknown"))
        queued = last_payload.get("queued_ahead")
        queued_text = f", queued_ahead={queued}" if queued is not None else ""
        print(f"    status={status}{queued_text}", flush=True)
        if status in TERMINAL_STATUSES:
            raw_paths.append(write_raw_response(config.raw_dir, raw_prefix, "status", response))
            return last_payload, raw_paths
        time.sleep(config.poll_interval)

    if last_payload is not None:
        timeout_response = HttpResponse(200, last_payload, json.dumps(last_payload))
        raw_paths.append(write_raw_response(config.raw_dir, raw_prefix, "status-timeout", timeout_response))
    raise TaskWaitTimeout(
        f"diagnostic client stopped waiting after {format_duration(config.task_timeout)}; "
        "the remote task was not cancelled and may still be running",
        last_payload=last_payload,
        raw_paths=raw_paths,
    )


def diagnose_pdf(config: RunConfig, pdf_path: Path, index: int) -> dict[str, Any]:
    relative_path = pdf_path.relative_to(config.input_dir).as_posix()
    raw_prefix = f"{index:04d}-{safe_name(pdf_path.stem)}"
    started_at = utc_now_iso()
    started_at_utc = utc_docker_timestamp()
    started = time.monotonic()
    raw_paths: list[Path] = []
    task_id: str | None = None
    preview: dict[str, Any] | None = None

    try:
        submit_response = submit_pdf(config, pdf_path)
        raw_paths.append(write_raw_response(config.raw_dir, raw_prefix, "submit", submit_response))
        if submit_response.status_code != 202 or submit_response.payload is None:
            raise CurlRequestError(
                f"submission failed: HTTP {submit_response.status_code}: {markdown_escape(submit_response.body, 1000)}"
            )
        task_id_value = submit_response.payload.get("task_id")
        if not isinstance(task_id_value, str) or not task_id_value:
            raise CurlRequestError("submission response does not contain task_id")
        task_id = task_id_value
        print(f"    task_id={task_id}", flush=True)

        status_payload, status_raw_paths = poll_task(config, task_id, raw_prefix)
        raw_paths.extend(status_raw_paths)
        task_status = str(status_payload.get("status", "unknown"))
        result_payload: dict[str, Any] | None = None

        if task_status == "completed":
            result_url = f"{config.router_url}/tasks/{quote(task_id, safe='')}/result"
            if config.preview_dir is not None:
                preview, result_payload, preview_paths = download_result_preview(
                    config,
                    result_url,
                    pdf_path,
                    index,
                )
                raw_paths.extend(preview_paths)
            else:
                result_response = curl_request(config, "GET", result_url, max_time=300)
                raw_paths.append(write_raw_response(config.raw_dir, raw_prefix, "result", result_response))
                if result_response.status_code == 200 and result_response.payload is not None:
                    result_payload = result_response.payload
                elif result_response.status_code != 200:
                    raise CurlRequestError(
                        f"result request failed: HTTP {result_response.status_code}: "
                        f"{markdown_escape(result_response.body, 1000)}"
                    )

        file_results = extract_file_results(result_payload, status_payload)
        failed_pages = collect_failed_pages(file_results)
        partial_success = bool((result_payload or {}).get("partial_success", status_payload.get("partial_success", False)))
        classification = classify_result(
            task_status,
            partial_success,
            file_results,
            failed_pages,
        )
        error = status_payload.get("error")
        result = {
            "index": index,
            "file_name": pdf_path.name,
            "relative_path": relative_path,
            "size_bytes": pdf_path.stat().st_size,
            "task_id": task_id,
            "task_status": task_status,
            "classification": classification,
            "partial_success": partial_success,
            "progress": status_payload.get("progress") or {},
            "file_results": file_results,
            "failed_pages": failed_pages,
            "error": error,
            "started_at": started_at,
            "completed_at": utc_now_iso(),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "raw_paths": raw_paths,
            "page_report_available": bool(file_results),
            "preview": preview,
        }
    except TaskWaitTimeout as exc:
        raw_paths.extend(path for path in exc.raw_paths if path not in raw_paths)
        progress = exc.last_payload.get("progress")
        result = {
            "index": index,
            "file_name": pdf_path.name,
            "relative_path": relative_path,
            "size_bytes": pdf_path.stat().st_size,
            "task_id": task_id,
            "task_status": "wait_timeout",
            "classification": "monitoring_stopped",
            "partial_success": bool(exc.last_payload.get("partial_success", False)),
            "progress": progress if isinstance(progress, dict) else {},
            "file_results": [],
            "failed_pages": [],
            "error": f"{type(exc).__name__}: {exc}",
            "started_at": started_at,
            "completed_at": utc_now_iso(),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "raw_paths": raw_paths,
            "page_report_available": False,
            "preview": preview,
        }
    except (CurlRequestError, TimeoutError, OSError) as exc:
        result = {
            "index": index,
            "file_name": pdf_path.name,
            "relative_path": relative_path,
            "size_bytes": pdf_path.stat().st_size,
            "task_id": task_id,
            "task_status": "client_error",
            "classification": "failed",
            "partial_success": False,
            "progress": {},
            "file_results": [],
            "failed_pages": [],
            "error": f"{type(exc).__name__}: {exc}",
            "started_at": started_at,
            "completed_at": utc_now_iso(),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "raw_paths": raw_paths,
            "page_report_available": False,
            "preview": preview,
        }
    completed_at_utc = utc_docker_timestamp()
    diagnostics, diagnostic_raw_paths = capture_task_diagnostics(
        config,
        raw_prefix,
        started_at_utc,
        completed_at_utc,
        task_id,
        pdf_path.name,
    )
    result["diagnostics"] = diagnostics
    result["raw_paths"].extend(diagnostic_raw_paths)
    write_preview_metadata(config, result)
    return result


def result_label(classification: str) -> str:
    return {
        "success": "成功",
        "partial": "部分成功",
        "failed": "失败",
    }.get(classification, classification)


def page_number_list(failed_pages: list[dict[str, Any]]) -> str:
    values: list[str] = []
    for page in failed_pages:
        page_number = page.get("page_number")
        if isinstance(page_number, int):
            values.append(str(page_number))
        elif isinstance(page.get("page_idx"), int):
            values.append(str(int(page["page_idx"]) + 1))
    return ", ".join(values) if values else "-"


def render_report(
    config: RunConfig,
    pdf_count: int,
    results: list[dict[str, Any]],
    health_payload: dict[str, Any] | None,
    run_started_at: str,
) -> str:
    counts = {key: sum(item["classification"] == key for item in results) for key in ("success", "partial", "failed")}
    lines = [
        "# MinerU Router PDF 批量诊断报告",
        "",
        "## 运行信息",
        "",
        f"- 开始时间：{run_started_at}",
        f"- 报告更新时间：{utc_now_iso()}",
        f"- 输入目录：`{config.input_dir}`",
        f"- Router：`{config.router_url}`",
        f"- Backend：`{config.backend}`",
        f"- 自动采集日志/NPU 信息：`{config.collect_diagnostics}`",
        f"- Router 容器：`{config.router_container}`",
        f"- API 容器：`{config.api_container}`",
        f"- VLM 容器：`{config.vlm_container or 'not configured'}`",
        f"- VLM SSH：`{config.vlm_ssh or 'local/not configured'}`",
        f"- VLM Metrics：`{config.vlm_metrics_url or 'not configured'}`",
        f"- 页面范围：{config.start_page_id + 1} - {config.end_page_id + 1}（报告页码从 1 开始）",
        f"- 扫描 PDF 数：{pdf_count}",
        f"- 已完成：{len(results)}",
    ]
    if health_payload:
        lines.extend(
            [
                f"- Router 健康状态：`{health_payload.get('status', 'unknown')}`",
                f"- Router/MinerU 版本：`{health_payload.get('version', 'unknown')}`",
            ]
        )
    lines.extend(
        [
            "",
            "## 结果汇总",
            "",
            f"- 成功：{counts['success']}",
            f"- 部分成功：{counts['partial']}",
            f"- 失败：{counts['failed']}",
            "",
            "| # | PDF | 大小 | 结果 | 耗时 | 跳过/失败页 | Task ID | 错误摘要 |",
            "| ---: | --- | ---: | --- | ---: | --- | --- | --- |",
        ]
    )
    for item in results:
        lines.append(
            "| {index} | `{path}` | {size} | {result} | {elapsed} | {pages} | `{task_id}` | {error} |".format(
                index=item["index"],
                path=markdown_escape(item["relative_path"], 200),
                size=format_size(item["size_bytes"]),
                result=result_label(item["classification"]),
                elapsed=format_duration(item["elapsed_seconds"]),
                pages=page_number_list(item["failed_pages"]),
                task_id=markdown_escape(item.get("task_id") or "-", 100),
                error=markdown_escape(item.get("error") or "", 160),
            )
        )

    lines.extend(["", "## 逐文件详情", ""])
    for item in results:
        lines.extend(
            [
                f"### {item['index']}. `{item['relative_path']}`",
                "",
                f"- 结果：**{result_label(item['classification'])}**",
                f"- 文件大小：{format_size(item['size_bytes'])}",
                f"- Task ID：`{item.get('task_id') or '-'}`",
                f"- Task 状态：`{item['task_status']}`",
                f"- 开始时间：{item['started_at']}",
                f"- 完成时间：{item['completed_at']}",
                f"- 耗时：{format_duration(item['elapsed_seconds'])}",
            ]
        )
        if item.get("error"):
            lines.extend([f"- 错误：`{markdown_escape(item['error'], 1200)}`"])

        file_results = item.get("file_results") or []
        if file_results:
            lines.extend(
                [
                    "",
                    "#### 文件结果",
                    "",
                    "| 服务端文件名 | 状态 | 总页数 | 成功页数 | 失败页数 | 错误 |",
                    "| --- | --- | ---: | ---: | ---: | --- |",
                ]
            )
            for file_result in file_results:
                failed_pages = file_result.get("failed_pages")
                failed_count = len(failed_pages) if isinstance(failed_pages, list) else 0
                error = file_result.get("error")
                if isinstance(error, dict):
                    error_text = f"{error.get('type', '')}: {error.get('message', '')}".strip(": ")
                else:
                    error_text = error or ""
                lines.append(
                    "| {name} | {status} | {total} | {success} | {failed} | {error} |".format(
                        name=markdown_escape(file_result.get("file_name") or "", 160),
                        status=markdown_escape(file_result.get("status") or "", 50),
                        total=file_result.get("total_pages") if file_result.get("total_pages") is not None else "-",
                        success=file_result.get("successful_pages") if file_result.get("successful_pages") is not None else "-",
                        failed=failed_count,
                        error=markdown_escape(error_text, 300),
                    )
                )
        elif item["task_status"] in TERMINAL_STATUSES:
            lines.extend(
                [
                    "",
                    "> 该服务端响应没有返回 `file_results`。请确认现场已部署包含页级容错报告的新代码，并且 `compose-multi.yaml` 已传入 `MINERU_VLM_FAILURE_POLICY=skip_page`。",
                ]
            )

        failed_pages = item.get("failed_pages") or []
        if failed_pages:
            lines.extend(
                [
                    "",
                    "#### 跳过/失败页面",
                    "",
                    "| 页码 | Page Index | 错误类型 | 耗时 | 尝试次数 | 错误信息 |",
                    "| ---: | ---: | --- | ---: | ---: | --- |",
                ]
            )
            for page in failed_pages:
                lines.append(
                    "| {number} | {index} | {error_type} | {elapsed} | {attempts} | {error} |".format(
                        number=page.get("page_number", "-"),
                        index=page.get("page_idx", "-"),
                        error_type=markdown_escape(page.get("error_type") or "", 100),
                        elapsed=format_duration(page.get("elapsed_seconds")),
                        attempts=page.get("attempts", "-"),
                        error=markdown_escape(page.get("error") or "", 500),
                    )
                )

        page_rows = collect_all_pages(item.get("progress") or {})
        if page_rows:
            lines.extend(
                [
                    "",
                    "#### 逐页耗时",
                    "",
                    "| 文件 | 页码 | 状态 | 排队(秒) | 成功尝试(秒) | 失败尝试(秒) | VLM请求累计(秒) | 重试等待(秒) | 含重试总耗时(秒) | 尝试次数 | 错误 |",
                    "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
                ]
            )
            for page in page_rows:
                lines.append(
                    "| {file_name} | {number} | {status} | {queue} | {successful} | {failed} | {vlm} | {retry} | {total} | {attempts} | {error} |".format(
                        file_name=markdown_escape(page.get("file_name") or "", 160),
                        number=page.get("page_number", "-"),
                        status=markdown_escape(page.get("status") or "", 30),
                        queue=format_page_seconds(page.get("queue_seconds")),
                        successful=format_page_seconds(page.get("successful_attempt_seconds")),
                        failed=format_page_seconds(page.get("failed_attempt_seconds")),
                        vlm=format_page_seconds(page.get("vlm_request_seconds")),
                        retry=format_page_seconds(
                            page.get("retry_overhead_seconds", page.get("retry_wait_seconds"))
                        ),
                        total=format_page_seconds(
                            page.get("with_retry_wall_seconds", page.get("total_seconds"))
                        ),
                        attempts=page.get("attempts", "-"),
                        error=markdown_escape(page.get("error") or "", 300),
                    )
                )

        diagnostics = item.get("diagnostics") or {}
        if diagnostics:
            lines.extend(["", "#### 自动采集的现场诊断信息", ""])
            diagnostic_labels = {
                "router_logs": "Router 日志摘要",
                "api_logs": "API 日志摘要",
                "vlm_logs": "VLM 日志摘要",
                "npu_smi": "npu-smi 快照",
                "vllm_metrics": "vLLM Metrics 快照",
            }
            for diagnostic_key in (
                "router_logs",
                "api_logs",
                "vlm_logs",
                "npu_smi",
                "vllm_metrics",
            ):
                diagnostic_text = diagnostics.get(diagnostic_key)
                if not diagnostic_text:
                    continue
                lines.extend(
                    [
                        f"##### {diagnostic_labels[diagnostic_key]}",
                        "",
                        "```text",
                        str(diagnostic_text).replace("```", "'''"),
                        "```",
                        "",
                    ]
                )

        raw_paths = item.get("raw_paths") or []
        if raw_paths:
            links = [f"[{path.name}]({relative_link(path, config.output_path)})" for path in raw_paths]
            lines.extend(["", "原始响应：" + "、".join(links)])
        lines.append("")

    lines.extend(
        [
            "## 判读说明",
            "",
            "- `成功`：任务完成，服务端未报告跳过页或文件错误。",
            "- `部分成功`：任务完成，但存在跳过页或文件级失败。",
            "- `失败`：Router/API 任务失败，或诊断客户端未能完成提交/轮询。",
            "- 页码使用服务端返回的 `page_number`，从 1 开始；`page_idx` 从 0 开始。",
            "- 如果客户端超时，服务端任务可能仍在继续运行，可使用报告中的 Task ID 继续查询。",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(
    config: RunConfig,
    pdf_count: int,
    results: list[dict[str, Any]],
    health_payload: dict[str, Any] | None,
    run_started_at: str,
) -> None:
    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    content = render_report(config, pdf_count, results, health_payload, run_started_at)
    temp_path = config.output_path.with_suffix(config.output_path.suffix + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(config.output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan a directory for PDFs, submit them one-by-one to mineru-router, "
            "and write a Markdown report containing failed files and page numbers."
        )
    )
    parser.add_argument("input_dir", type=Path, help="Directory containing PDF files")
    parser.add_argument(
        "--router-url",
        default=os.getenv("MINERU_ROUTER_URL", "http://127.0.0.1:8002"),
        help="mineru-router base URL (default: %(default)s)",
    )
    parser.add_argument("--backend", default="vlm-http-client")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--parse-method", default="auto")
    parser.add_argument("--lang", default="ch")
    parser.add_argument("--server-url", default=None)
    parser.add_argument(
        "--image-analysis",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--formula-enable",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--table-enable",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--start-page-id", type=int, default=0)
    parser.add_argument("--end-page-id", type=int, default=99999)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument(
        "--task-timeout",
        type=float,
        default=7200.0,
        help="Maximum seconds to wait for each PDF task (default: %(default)s)",
    )
    parser.add_argument(
        "--page-timeout-seconds",
        type=float,
        default=600.0,
        help="Soft timeout in seconds for one VLM request (default: %(default)s)",
    )
    parser.add_argument("--page-connect-max-retries", type=int, default=0)
    parser.add_argument("--vlm-batch-size", type=int, default=1)
    parser.add_argument("--pause-seconds", type=float, default=2.0)
    parser.add_argument("--submit-retries", type=int, default=2)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--raw-dir", type=Path, default=None)
    parser.add_argument(
        "--preview-dir",
        type=Path,
        default=None,
        help="Optional directory used to retain result ZIPs and extracted Markdown previews",
    )
    parser.add_argument(
        "--save-result-images",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include extracted images in preview result ZIPs (default: enabled)",
    )
    parser.add_argument("--curl-bin", default="curl")
    parser.add_argument("--skip-health-check", action="store_true")
    parser.add_argument(
        "--collect-diagnostics",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Collect Router/API/VLM logs and npu-smi output (default: enabled)",
    )
    parser.add_argument("--router-container", default="mineru-router")
    parser.add_argument("--api-container", default="mineru-api-1")
    parser.add_argument(
        "--vlm-container",
        default=os.getenv("MINERU_VLM_CONTAINER"),
        help="VLM container name; omit if the VLM is remote and logs are unavailable",
    )
    parser.add_argument(
        "--vlm-ssh",
        default=os.getenv("MINERU_VLM_SSH"),
        help="Optional SSH target for remote VLM logs/npu-smi, e.g. maas@10.0.0.8",
    )
    parser.add_argument(
        "--vlm-metrics-url",
        default=os.getenv("MINERU_VLM_METRICS_URL"),
        help="Optional vLLM Prometheus endpoint, e.g. http://10.0.0.8:30000/metrics",
    )
    parser.add_argument("--log-tail", type=int, default=800)
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> RunConfig:
    input_dir = args.input_dir.expanduser().resolve()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_path = args.output.expanduser().resolve() if args.output else input_dir / f"mineru-router-diagnostic-{timestamp}.md"
    raw_dir = args.raw_dir.expanduser().resolve() if args.raw_dir else output_path.parent / f"{output_path.stem}-raw"
    preview_dir = args.preview_dir.expanduser().resolve() if args.preview_dir else None
    vlm_metrics_url = args.vlm_metrics_url
    if not vlm_metrics_url and args.server_url:
        server_root = args.server_url.rstrip("/")
        if server_root.endswith("/v1"):
            server_root = server_root[:-3]
        vlm_metrics_url = f"{server_root}/metrics"
    return RunConfig(
        input_dir=input_dir,
        router_url=args.router_url.rstrip("/"),
        backend=args.backend,
        effort=args.effort,
        parse_method=args.parse_method,
        lang=args.lang,
        server_url=args.server_url,
        image_analysis=args.image_analysis,
        formula_enable=args.formula_enable,
        table_enable=args.table_enable,
        start_page_id=args.start_page_id,
        end_page_id=args.end_page_id,
        recursive=args.recursive,
        poll_interval=max(0.5, args.poll_interval),
        task_timeout=max(1.0, args.task_timeout),
        page_timeout_seconds=min(7200.0, max(1.0, args.page_timeout_seconds)),
        page_connect_max_retries=min(3, max(0, args.page_connect_max_retries)),
        vlm_batch_size=min(32, max(1, args.vlm_batch_size)),
        pause_seconds=max(0.0, args.pause_seconds),
        submit_retries=max(0, args.submit_retries),
        curl_bin=args.curl_bin,
        output_path=output_path,
        raw_dir=raw_dir,
        preview_dir=preview_dir,
        save_result_images=args.save_result_images,
        collect_diagnostics=args.collect_diagnostics,
        router_container=args.router_container,
        api_container=args.api_container,
        vlm_container=args.vlm_container,
        vlm_ssh=args.vlm_ssh,
        vlm_metrics_url=vlm_metrics_url,
        log_tail=max(50, args.log_tail),
    )


def validate_config(config: RunConfig) -> None:
    if not config.input_dir.is_dir():
        raise SystemExit(f"Input directory does not exist: {config.input_dir}")
    if shutil.which(config.curl_bin) is None:
        raise SystemExit(f"curl executable not found: {config.curl_bin}")
    if config.start_page_id < 0 or config.end_page_id < config.start_page_id:
        raise SystemExit("Invalid page range")
    valid_container_name = re.compile(r"^[A-Za-z0-9_.-]+$")
    for label, container_name in (
        ("router", config.router_container),
        ("api", config.api_container),
        ("vlm", config.vlm_container),
    ):
        if container_name and not valid_container_name.fullmatch(container_name):
            raise SystemExit(f"Invalid {label} container name: {container_name}")
    if config.vlm_ssh and shutil.which("ssh") is None:
        raise SystemExit("ssh executable is required by --vlm-ssh")


def main() -> int:
    args = parse_args()
    config = build_config(args)
    validate_config(config)
    pdf_paths = discover_pdfs(config.input_dir, config.recursive)
    if args.limit > 0:
        pdf_paths = pdf_paths[: args.limit]
    if not pdf_paths:
        print(f"No PDF files found in {config.input_dir}", file=sys.stderr)
        return 2

    run_started_at = utc_now_iso()
    health_payload: dict[str, Any] | None = None
    if not args.skip_health_check:
        health_response = curl_request(
            config,
            "GET",
            f"{config.router_url}/health",
            max_time=30,
        )
        if health_response.status_code != 200:
            print(
                f"Router health check failed: HTTP {health_response.status_code}: "
                f"{markdown_escape(health_response.body, 1000)}",
                file=sys.stderr,
            )
            return 3
        health_payload = health_response.payload

    print(f"Found {len(pdf_paths)} PDF file(s). Report: {config.output_path}")
    results: list[dict[str, Any]] = []
    write_report(config, len(pdf_paths), results, health_payload, run_started_at)

    try:
        for index, pdf_path in enumerate(pdf_paths, start=1):
            print(
                f"[{index}/{len(pdf_paths)}] {pdf_path.relative_to(config.input_dir)} ({format_size(pdf_path.stat().st_size)})",
                flush=True,
            )
            result = diagnose_pdf(config, pdf_path, index)
            results.append(result)
            write_report(config, len(pdf_paths), results, health_payload, run_started_at)
            print(
                f"    result={result_label(result['classification'])}, "
                f"elapsed={format_duration(result['elapsed_seconds'])}, "
                f"pages={page_number_list(result['failed_pages'])}",
                flush=True,
            )
            if index < len(pdf_paths) and config.pause_seconds:
                time.sleep(config.pause_seconds)
    except KeyboardInterrupt:
        print("\nInterrupted. Completed results have been saved.", file=sys.stderr)
        write_report(config, len(pdf_paths), results, health_payload, run_started_at)
        return 130

    counts = {key: sum(item["classification"] == key for item in results) for key in ("success", "partial", "failed")}
    print(f"Completed: success={counts['success']}, partial={counts['partial']}, failed={counts['failed']}")
    print(f"Markdown report: {config.output_path}")
    print(f"Raw responses: {config.raw_dir}")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
