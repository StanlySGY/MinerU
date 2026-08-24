# Copyright (c) Opendatalab. All rights reserved.
import asyncio
import time
import weakref
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

import httpx
from loguru import logger

from mineru.utils.config_reader import (
    get_vlm_connect_max_retries,
    get_vlm_global_page_concurrency,
    get_vlm_page_timeout_seconds,
)
from mineru.utils.task_progress import task_progress_registry

_vlm_page_semaphores = weakref.WeakKeyDictionary()


def get_vlm_page_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    concurrency = get_vlm_global_page_concurrency(default=2)
    cached = _vlm_page_semaphores.get(loop)
    if cached is None or cached[0] != concurrency:
        cached = (concurrency, asyncio.Semaphore(concurrency))
        _vlm_page_semaphores[loop] = cached
    return cached[1]


def iter_exception_chain(exc: BaseException) -> Iterable[BaseException]:
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def is_skippable_vlm_page_error(exc: BaseException) -> bool:
    transient_status_codes = {429, 502, 503, 504}
    transient_types = (
        TimeoutError,
        httpx.TimeoutException,
        httpx.RemoteProtocolError,
        httpx.NetworkError,
    )
    for current in iter_exception_chain(exc):
        if isinstance(current, transient_types):
            return True
        if isinstance(current, httpx.HTTPStatusError):
            response = current.response
            if response is not None and response.status_code in transient_status_codes:
                return True
    return False


def is_retryable_vlm_page_error(exc: BaseException) -> bool:
    retryable_status_codes = {429, 502, 503, 504}
    retryable_types = (
        httpx.ConnectTimeout,
        httpx.ConnectError,
        httpx.RemoteProtocolError,
        httpx.ReadError,
        httpx.WriteError,
    )
    for current in iter_exception_chain(exc):
        if isinstance(current, retryable_types):
            return True
        if isinstance(current, httpx.HTTPStatusError):
            response = current.response
            if response is not None and response.status_code in retryable_status_codes:
                return True
    return False


def build_page_failure(
    page_index: int,
    exc: BaseException,
    elapsed: float,
    attempts: int,
    *,
    vlm_request_seconds: float = 0.0,
    retry_wait_seconds: float = 0.0,
) -> dict:
    return {
        "page_idx": page_index,
        "page_number": page_index + 1,
        "status": "skipped",
        "error_type": type(exc).__name__,
        "error": str(exc) or repr(exc),
        "elapsed_seconds": round(elapsed, 2),
        "vlm_request_seconds": round(vlm_request_seconds, 2),
        "retry_wait_seconds": round(retry_wait_seconds, 2),
        "attempts": attempts,
    }


def log_page_timing(
    page: dict[str, Any] | None,
    *,
    task_id: str | None,
    source_file_name: str | None,
    page_index: int,
    status: str,
    attempts: int,
    error_type: str = "-",
    action: str = "continue",
    warning: bool = False,
    failed: bool = False,
) -> None:
    payload = page or {}
    message = (
        "event=vlm_page_timing task_id={} file_name={!r} page_number={} "
        "page_idx={} status={} attempts={} queue_seconds={} "
        "vlm_request_seconds={} retry_wait_seconds={} elapsed_seconds={} "
        "total_seconds={} error_type={} action={}"
    )
    values = (
        task_id or "-",
        source_file_name or "-",
        page_index + 1,
        page_index,
        status,
        attempts,
        payload.get("queue_seconds", 0.0),
        payload.get("vlm_request_seconds", 0.0),
        payload.get("retry_wait_seconds", 0.0),
        payload.get("elapsed_seconds", 0.0),
        payload.get("total_seconds", 0.0),
        error_type,
        action,
    )
    if failed:
        logger.error(message, *values)
    elif warning:
        logger.warning(message, *values)
    else:
        logger.info(message, *values)


async def aio_extract_pages_with_failure_isolation(
    predictor: Any,
    images,
    *,
    page_start_index: int,
    image_analysis: bool,
    task_id: str | None,
    source_file_name: str | None,
    timeout_seconds: float | None = None,
    connect_max_retries: int | None = None,
    batch_size: int = 1,
):
    """Extract pages with per-request controls and page-level failure isolation.

    ``batch_size`` enables a conservative micro-batch fast path. If a batch fails
    or returns an unexpected result count, its pages fall back to isolated calls
    so one problematic page does not fail the rest of the document.
    """
    images = list(images)
    semaphore = get_vlm_page_semaphore()
    effective_timeout = (
        get_vlm_page_timeout_seconds(default=600.0)
        if timeout_seconds is None
        else min(7200.0, max(1.0, float(timeout_seconds)))
    )
    effective_connect_retries = (
        get_vlm_connect_max_retries(default=1)
        if connect_max_retries is None
        else min(3, max(0, int(connect_max_retries)))
    )
    effective_batch_size = min(32, max(1, int(batch_size)))
    logger.info(
        "event=vlm_micro_batch requested={} effective={} window_pages={}",
        batch_size,
        min(effective_batch_size, len(images)) if images else 0,
        len(images),
    )
    task_progress_registry.queue_pages(
        task_id,
        source_file_name,
        page_start_index,
        len(images),
    )

    def utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    def timing_fields(
        attempt_details: list[dict[str, Any]],
        *,
        vlm_request_seconds: float,
        retry_wait_seconds: float,
        elapsed_seconds: float,
    ) -> dict[str, Any]:
        successful_attempt_seconds = sum(
            float(item.get("request_seconds") or 0.0)
            for item in attempt_details
            if item.get("outcome") == "completed"
        )
        failed_attempt_seconds = sum(
            float(item.get("request_seconds") or 0.0)
            for item in attempt_details
            if item.get("outcome") != "completed"
        )
        return {
            "vlm_request_seconds": round(vlm_request_seconds, 2),
            "retry_wait_seconds": round(retry_wait_seconds, 2),
            "elapsed_seconds": round(elapsed_seconds, 2),
            "successful_attempt_seconds": round(successful_attempt_seconds, 2),
            "failed_attempt_seconds": round(failed_attempt_seconds, 2),
            "retry_overhead_seconds": round(retry_wait_seconds, 2),
            "with_retry_wall_seconds": round(elapsed_seconds, 2),
            "attempt_details": attempt_details,
        }

    async def extract_page(
        offset: int,
        image: Any,
        *,
        extraction_started_at: float | None = None,
        attempt_details: list[dict[str, Any]] | None = None,
        initial_request_seconds: float = 0.0,
    ):
        page_index = page_start_index + offset
        if extraction_started_at is None:
            extraction_started_at = time.monotonic()
        details = list(attempt_details or [])
        vlm_request_seconds = initial_request_seconds
        retry_wait_seconds = 0.0
        retries_used = 0
        retry_wait_before_seconds = 0.0
        while True:
            attempt_no = len(details) + 1
            attempt_started_at: str | None = None
            attempt_request_seconds = 0.0
            try:
                async with semaphore:
                    task_progress_registry.page_started(
                        task_id,
                        source_file_name,
                        page_index,
                        attempt_no,
                    )
                    attempt_started_at = utc_now_iso()
                    request_started_at = time.monotonic()
                    try:
                        page_results = await asyncio.wait_for(
                            predictor.aio_batch_two_step_extract(
                                images=[image],
                                image_analysis=image_analysis,
                            ),
                            timeout=effective_timeout,
                        )
                    finally:
                        attempt_request_seconds = time.monotonic() - request_started_at
                        vlm_request_seconds += attempt_request_seconds
                if len(page_results) != 1:
                    raise RuntimeError(
                        "VLM page extraction returned an unexpected result count: "
                        f"expected 1, got {len(page_results)}"
                    )
                details.append(
                    {
                        "attempt_no": attempt_no,
                        "started_at": attempt_started_at,
                        "completed_at": utc_now_iso(),
                        "outcome": "completed",
                        "request_seconds": round(attempt_request_seconds, 3),
                        "retry_wait_before_seconds": round(retry_wait_before_seconds, 3),
                        "timeout_seconds": effective_timeout,
                        "batch_size": 1,
                    }
                )
                elapsed_seconds = time.monotonic() - extraction_started_at
                fields = timing_fields(
                    details,
                    vlm_request_seconds=vlm_request_seconds,
                    retry_wait_seconds=retry_wait_seconds,
                    elapsed_seconds=elapsed_seconds,
                )
                page = task_progress_registry.page_completed(
                    task_id,
                    source_file_name,
                    page_index,
                    attempt_no,
                    **fields,
                )
                timing = page or {**fields, "total_seconds": round(elapsed_seconds, 2)}
                log_page_timing(
                    timing,
                    task_id=task_id,
                    source_file_name=source_file_name,
                    page_index=page_index,
                    status="completed",
                    attempts=attempt_no,
                    warning=vlm_request_seconds
                    >= min(120.0, max(1.0, effective_timeout * 0.5)),
                )
                return page_results[0], None, None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                elapsed_seconds = time.monotonic() - extraction_started_at
                retryable = (
                    is_retryable_vlm_page_error(exc)
                    and retries_used < effective_connect_retries
                )
                skippable = is_skippable_vlm_page_error(exc)
                outcome = "retry" if retryable else ("skipped" if skippable else "failed")
                details.append(
                    {
                        "attempt_no": attempt_no,
                        "started_at": attempt_started_at or utc_now_iso(),
                        "completed_at": utc_now_iso(),
                        "outcome": outcome,
                        "error_type": type(exc).__name__,
                        "error": str(exc) or repr(exc),
                        "request_seconds": round(attempt_request_seconds, 3),
                        "retry_wait_before_seconds": round(retry_wait_before_seconds, 3),
                        "timeout_seconds": effective_timeout,
                        "batch_size": 1,
                    }
                )
                if retryable:
                    retries_used += 1
                    retry_started_at = time.monotonic()
                    await asyncio.sleep(min(2 ** (retries_used - 1), 5))
                    retry_wait_before_seconds = time.monotonic() - retry_started_at
                    retry_wait_seconds += retry_wait_before_seconds
                    continue

                fields = timing_fields(
                    details,
                    vlm_request_seconds=vlm_request_seconds,
                    retry_wait_seconds=retry_wait_seconds,
                    elapsed_seconds=elapsed_seconds,
                )
                if not skippable:
                    page = task_progress_registry.page_failed(
                        task_id,
                        source_file_name,
                        page_index,
                        exc,
                        attempt_no,
                        **fields,
                    )
                    timing = page or {**fields, "total_seconds": round(elapsed_seconds, 2)}
                    log_page_timing(
                        timing,
                        task_id=task_id,
                        source_file_name=source_file_name,
                        page_index=page_index,
                        status="failed",
                        attempts=attempt_no,
                        error_type=type(exc).__name__,
                        action="fail_task",
                        failed=True,
                    )
                    return None, None, exc

                failure = build_page_failure(
                    page_index,
                    exc,
                    elapsed_seconds,
                    attempt_no,
                    vlm_request_seconds=vlm_request_seconds,
                    retry_wait_seconds=retry_wait_seconds,
                )
                failure.update(fields)
                page = task_progress_registry.page_skipped(
                    task_id,
                    source_file_name,
                    failure,
                )
                timing = page or {**failure, "total_seconds": round(elapsed_seconds, 2)}
                log_page_timing(
                    timing,
                    task_id=task_id,
                    source_file_name=source_file_name,
                    page_index=page_index,
                    status="skipped",
                    attempts=attempt_no,
                    error_type=failure["error_type"],
                    action="skip_and_continue",
                    warning=True,
                )
                return [], failure, None

    async def extract_batch(batch_start: int, batch_images: list[Any]):
        if len(batch_images) == 1:
            return [await extract_page(batch_start, batch_images[0])]

        extraction_started_at = time.monotonic()
        started_at: str | None = None
        request_seconds = 0.0
        try:
            async with semaphore:
                for batch_offset in range(len(batch_images)):
                    task_progress_registry.page_started(
                        task_id,
                        source_file_name,
                        page_start_index + batch_start + batch_offset,
                        1,
                    )
                started_at = utc_now_iso()
                request_started_at = time.monotonic()
                try:
                    batch_results = await asyncio.wait_for(
                        predictor.aio_batch_two_step_extract(
                            images=batch_images,
                            image_analysis=image_analysis,
                        ),
                        timeout=effective_timeout,
                    )
                finally:
                    request_seconds = time.monotonic() - request_started_at
            if len(batch_results) != len(batch_images):
                raise RuntimeError(
                    "VLM micro-batch returned an unexpected result count: "
                    f"expected {len(batch_images)}, got {len(batch_results)}"
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            completed_at = utc_now_iso()
            fallback_details = [
                [{
                    "attempt_no": 1,
                    "started_at": started_at or completed_at,
                    "completed_at": completed_at,
                    "outcome": "batch_fallback",
                    "error_type": type(exc).__name__,
                    "error": str(exc) or repr(exc),
                    "request_seconds": round(request_seconds, 3),
                    "retry_wait_before_seconds": 0.0,
                    "timeout_seconds": effective_timeout,
                    "batch_size": len(batch_images),
                }]
                for _ in batch_images
            ]
            return await asyncio.gather(
                *(
                    extract_page(
                        batch_start + offset,
                        image,
                        extraction_started_at=extraction_started_at,
                        attempt_details=fallback_details[offset],
                        initial_request_seconds=request_seconds,
                    )
                    for offset, image in enumerate(batch_images)
                )
            )

        completed_at = utc_now_iso()
        elapsed_seconds = time.monotonic() - extraction_started_at
        outcomes = []
        for offset, result in enumerate(batch_results):
            page_index = page_start_index + batch_start + offset
            details = [{
                "attempt_no": 1,
                "started_at": started_at,
                "completed_at": completed_at,
                "outcome": "completed",
                "request_seconds": round(request_seconds, 3),
                "retry_wait_before_seconds": 0.0,
                "timeout_seconds": effective_timeout,
                "batch_size": len(batch_images),
            }]
            fields = timing_fields(
                details,
                vlm_request_seconds=request_seconds,
                retry_wait_seconds=0.0,
                elapsed_seconds=elapsed_seconds,
            )
            page = task_progress_registry.page_completed(
                task_id,
                source_file_name,
                page_index,
                1,
                **fields,
            )
            log_page_timing(
                page or fields,
                task_id=task_id,
                source_file_name=source_file_name,
                page_index=page_index,
                status="completed",
                attempts=1,
            )
            outcomes.append((result, None, None))
        return outcomes

    batches = [
        (start, images[start : start + effective_batch_size])
        for start in range(0, len(images), effective_batch_size)
    ]
    batch_outcomes = await asyncio.gather(
        *(extract_batch(start, batch_images) for start, batch_images in batches)
    )
    outcomes = [outcome for batch in batch_outcomes for outcome in batch]
    fatal_errors = [fatal for _, _, fatal in outcomes if fatal is not None]
    if fatal_errors:
        raise fatal_errors[0]
    page_results = [page_result for page_result, _, _ in outcomes]
    page_failures = [failure for _, failure, _ in outcomes if failure is not None]
    return page_results, page_failures
