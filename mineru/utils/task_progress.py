# Copyright (c) Opendatalab. All rights reserved.
"""Best-effort in-process progress tracking for asynchronous parse tasks."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

MAX_EVENTS_PER_TASK = 500


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskProgressRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tasks: dict[str, dict[str, Any]] = {}

    def initialize(self, task_id: str, file_names: list[str]) -> None:
        with self._lock:
            self._tasks[task_id] = {
                "task_id": task_id,
                "phase": "queued",
                "file_names": list(file_names),
                "files": {},
                "events": [],
                "version": 0,
                "updated_at": _utc_now_iso(),
            }
            self._append_event_locked(task_id, "task_queued")

    def remove(self, task_id: str) -> None:
        with self._lock:
            self._tasks.pop(task_id, None)

    def set_phase(self, task_id: str | None, phase: str, **details: Any) -> None:
        if not task_id:
            return
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task["phase"] = phase
            self._append_event_locked(task_id, "task_phase_changed", phase=phase, **details)

    def register_file_pages(
        self,
        task_id: str | None,
        file_name: str | None,
        page_count: int,
    ) -> None:
        if not task_id or page_count <= 0:
            return
        normalized_file_name = file_name or "unknown"
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            file_state = task["files"].setdefault(
                normalized_file_name,
                {"file_name": normalized_file_name, "pages": {}},
            )
            file_state["total_pages"] = page_count
            queued_at = _utc_now_iso()
            queued_monotonic = time.monotonic()
            for page_index in range(page_count):
                page_number = page_index + 1
                file_state["pages"].setdefault(
                    str(page_number),
                    {
                        "page_idx": page_index,
                        "page_number": page_number,
                        "status": "queued",
                        "queued_at": queued_at,
                        "_queued_monotonic": queued_monotonic,
                    },
                )
            task["phase"] = "vlm_queue"
            self._append_event_locked(
                task_id,
                "file_pages_registered",
                file_name=normalized_file_name,
                page_count=page_count,
            )

    def queue_pages(
        self,
        task_id: str | None,
        file_name: str | None,
        page_start_index: int,
        page_count: int,
    ) -> None:
        if not task_id or page_count <= 0:
            return
        normalized_file_name = file_name or "unknown"
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            file_state = task["files"].setdefault(
                normalized_file_name,
                {"file_name": normalized_file_name, "pages": {}},
            )
            for offset in range(page_count):
                page_index = page_start_index + offset
                page_number = page_index + 1
                page = file_state["pages"].setdefault(
                    str(page_number),
                    {"page_idx": page_index, "page_number": page_number},
                )
                if "status" not in page:
                    page.update(
                        {
                            "status": "queued",
                            "queued_at": _utc_now_iso(),
                            "_queued_monotonic": time.monotonic(),
                        }
                    )
                    self._append_event_locked(
                        task_id,
                        "page_queued",
                        file_name=normalized_file_name,
                        page_idx=page_index,
                        page_number=page_number,
                    )
            task["phase"] = "vlm_queue"

    def page_started(
        self,
        task_id: str | None,
        file_name: str | None,
        page_index: int,
        attempt: int,
    ) -> None:
        page = self._get_page(task_id, file_name, page_index)
        if page is None or not task_id:
            return
        with self._lock:
            started_monotonic = time.monotonic()
            queued_monotonic = page.get("_queued_monotonic", started_monotonic)
            if "_started_monotonic" not in page:
                page.update(
                    {
                        "started_at": _utc_now_iso(),
                        "queue_seconds": round(
                            started_monotonic - queued_monotonic,
                            2,
                        ),
                        "_started_monotonic": started_monotonic,
                    }
                )
            page.update(
                {
                    "status": "processing",
                    "attempts": attempt,
                }
            )
            self._tasks[task_id]["phase"] = "vlm_inference"
            self._append_event_locked(
                task_id,
                "page_inference_started",
                file_name=file_name or "unknown",
                page_idx=page_index,
                page_number=page_index + 1,
                attempt=attempt,
                queue_seconds=page["queue_seconds"],
            )

    def page_completed(
        self,
        task_id: str | None,
        file_name: str | None,
        page_index: int,
        attempt: int,
        **timing: Any,
    ) -> dict[str, Any] | None:
        return self._finish_page(
            task_id,
            file_name,
            page_index,
            "completed",
            attempts=attempt,
            **timing,
        )

    def page_skipped(
        self,
        task_id: str | None,
        file_name: str | None,
        failure: dict[str, Any],
    ) -> dict[str, Any] | None:
        page_index = int(failure.get("page_idx", 0))
        return self._finish_page(
            task_id,
            file_name,
            page_index,
            "skipped",
            **{
                key: value
                for key, value in failure.items()
                if key not in {"status", "page_idx", "page_number"}
            },
        )

    def page_failed(
        self,
        task_id: str | None,
        file_name: str | None,
        page_index: int,
        exc: BaseException,
        attempt: int,
        **timing: Any,
    ) -> dict[str, Any] | None:
        return self._finish_page(
            task_id,
            file_name,
            page_index,
            "failed",
            error_type=type(exc).__name__,
            error=str(exc) or repr(exc),
            attempts=attempt,
            **timing,
        )

    def snapshot(self, task_id: str | None) -> dict[str, Any]:
        if not task_id:
            return self.empty_snapshot()
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return self.empty_snapshot()
            files = []
            totals = {"queued": 0, "processing": 0, "completed": 0, "skipped": 0, "failed": 0}
            for file_state in task["files"].values():
                pages = []
                for page in file_state["pages"].values():
                    public_page = {key: value for key, value in page.items() if not key.startswith("_")}
                    pages.append(public_page)
                    status = str(public_page.get("status", "queued"))
                    if status in totals:
                        totals[status] += 1
                pages.sort(key=lambda item: int(item.get("page_number", 0)))
                files.append(
                    {
                        "file_name": file_state["file_name"],
                        "total_pages": int(file_state.get("total_pages", len(pages))),
                        "pages": pages,
                    }
                )
            files.sort(key=lambda item: item["file_name"])
            total_pages = sum(totals.values())
            return {
                "phase": task["phase"],
                "total_pages": total_pages,
                "queued_pages": totals["queued"],
                "processing_pages": totals["processing"],
                "completed_pages": totals["completed"],
                "skipped_pages": totals["skipped"],
                "failed_pages": totals["failed"],
                "inflight_page_numbers": self._page_numbers(files, "processing"),
                "skipped_page_numbers": self._page_numbers(files, "skipped"),
                "failed_page_numbers": self._page_numbers(files, "failed"),
                "files": files,
                "events": list(task["events"]),
                "version": task["version"],
                "updated_at": task["updated_at"],
            }

    @staticmethod
    def empty_snapshot() -> dict[str, Any]:
        return {
            "phase": "unknown",
            "total_pages": 0,
            "queued_pages": 0,
            "processing_pages": 0,
            "completed_pages": 0,
            "skipped_pages": 0,
            "failed_pages": 0,
            "inflight_page_numbers": [],
            "skipped_page_numbers": [],
            "failed_page_numbers": [],
            "files": [],
            "events": [],
            "version": 0,
            "updated_at": None,
        }

    def _get_page(
        self,
        task_id: str | None,
        file_name: str | None,
        page_index: int,
    ) -> dict[str, Any] | None:
        if not task_id:
            return None
        normalized_file_name = file_name or "unknown"
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            file_state = task["files"].setdefault(
                normalized_file_name,
                {"file_name": normalized_file_name, "pages": {}},
            )
            page_number = page_index + 1
            return file_state["pages"].setdefault(
                str(page_number),
                {
                    "page_idx": page_index,
                    "page_number": page_number,
                    "status": "queued",
                    "queued_at": _utc_now_iso(),
                    "_queued_monotonic": time.monotonic(),
                },
            )

    def _finish_page(
        self,
        task_id: str | None,
        file_name: str | None,
        page_index: int,
        status: str,
        **details: Any,
    ) -> dict[str, Any] | None:
        page = self._get_page(task_id, file_name, page_index)
        if page is None or not task_id:
            return None
        with self._lock:
            now_monotonic = time.monotonic()
            queued_monotonic = page.get("_queued_monotonic", now_monotonic)
            started_monotonic = page.get("_started_monotonic", queued_monotonic)
            completed_at = _utc_now_iso()
            vlm_request_seconds = round(
                float(
                    details.pop(
                        "vlm_request_seconds",
                        now_monotonic - started_monotonic,
                    )
                ),
                2,
            )
            retry_wait_seconds = round(
                float(details.pop("retry_wait_seconds", 0.0)),
                2,
            )
            elapsed_seconds = round(
                float(
                    details.pop(
                        "elapsed_seconds",
                        now_monotonic - started_monotonic,
                    )
                ),
                2,
            )
            timing = {
                "completed_at": completed_at,
                "inference_seconds": vlm_request_seconds,
                "vlm_request_seconds": vlm_request_seconds,
                "retry_wait_seconds": retry_wait_seconds,
                "elapsed_seconds": elapsed_seconds,
                "total_seconds": round(now_monotonic - queued_monotonic, 2),
            }
            page.update(
                {
                    "status": status,
                    **timing,
                    **details,
                }
            )
            event_details = {
                "file_name": file_name or "unknown",
                "page_idx": page_index,
                "page_number": page_index + 1,
                "status": status,
                **timing,
                **details,
            }
            self._append_event_locked(
                task_id,
                f"page_{status}",
                **event_details,
            )
            return {
                key: value
                for key, value in page.items()
                if not key.startswith("_")
            }

    def _append_event_locked(self, task_id: str, event_type: str, **details: Any) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        task["version"] += 1
        task["updated_at"] = _utc_now_iso()
        task["events"].append(
            {
                "id": task["version"],
                "type": event_type,
                "timestamp": task["updated_at"],
                **details,
            }
        )
        del task["events"][:-MAX_EVENTS_PER_TASK]

    @staticmethod
    def _page_numbers(files: list[dict[str, Any]], status: str) -> list[int]:
        return [
            int(page["page_number"]) for file_state in files for page in file_state["pages"] if page.get("status") == status
        ]


task_progress_registry = TaskProgressRegistry()
