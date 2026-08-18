# Copyright (c) Opendatalab. All rights reserved.
import asyncio
import time
import weakref
from collections.abc import Iterable
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
):
    semaphore = get_vlm_page_semaphore()
    timeout_seconds = get_vlm_page_timeout_seconds(default=600.0)
    connect_max_retries = get_vlm_connect_max_retries(default=1)
    task_progress_registry.queue_pages(
        task_id,
        source_file_name,
        page_start_index,
        len(images),
    )

    async def extract_page(offset, image):
        page_index = page_start_index + offset
        extraction_started_at = time.monotonic()
        vlm_request_seconds = 0.0
        retry_wait_seconds = 0.0
        attempts = 0
        while True:
            attempts += 1
            try:
                async with semaphore:
                    task_progress_registry.page_started(
                        task_id,
                        source_file_name,
                        page_index,
                        attempts,
                    )
                    request_started_at = time.monotonic()
                    try:
                        page_results = await asyncio.wait_for(
                            predictor.aio_batch_two_step_extract(
                                images=[image],
                                image_analysis=image_analysis,
                            ),
                            timeout=timeout_seconds,
                        )
                    finally:
                        vlm_request_seconds += (
                            time.monotonic() - request_started_at
                        )
                if len(page_results) != 1:
                    raise RuntimeError(
                        "VLM page extraction returned an unexpected result count: "
                        f"expected 1, got {len(page_results)}"
                    )
                elapsed_seconds = time.monotonic() - extraction_started_at
                page = task_progress_registry.page_completed(
                    task_id,
                    source_file_name,
                    page_index,
                    attempts,
                    vlm_request_seconds=vlm_request_seconds,
                    retry_wait_seconds=retry_wait_seconds,
                    elapsed_seconds=elapsed_seconds,
                )
                timing = page or {
                    "vlm_request_seconds": round(vlm_request_seconds, 2),
                    "retry_wait_seconds": round(retry_wait_seconds, 2),
                    "elapsed_seconds": round(elapsed_seconds, 2),
                    "total_seconds": round(elapsed_seconds, 2),
                }
                log_page_timing(
                    timing,
                    task_id=task_id,
                    source_file_name=source_file_name,
                    page_index=page_index,
                    status="completed",
                    attempts=attempts,
                    warning=vlm_request_seconds
                    >= min(120.0, max(1.0, timeout_seconds * 0.5)),
                )
                return page_results[0], None, None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                elapsed_seconds = time.monotonic() - extraction_started_at
                if not is_skippable_vlm_page_error(exc):
                    page = task_progress_registry.page_failed(
                        task_id,
                        source_file_name,
                        page_index,
                        exc,
                        attempts,
                        vlm_request_seconds=vlm_request_seconds,
                        retry_wait_seconds=retry_wait_seconds,
                        elapsed_seconds=elapsed_seconds,
                    )
                    timing = page or {
                        "vlm_request_seconds": round(vlm_request_seconds, 2),
                        "retry_wait_seconds": round(retry_wait_seconds, 2),
                        "elapsed_seconds": round(elapsed_seconds, 2),
                        "total_seconds": round(elapsed_seconds, 2),
                    }
                    log_page_timing(
                        timing,
                        task_id=task_id,
                        source_file_name=source_file_name,
                        page_index=page_index,
                        status="failed",
                        attempts=attempts,
                        error_type=type(exc).__name__,
                        action="fail_task",
                        failed=True,
                    )
                    return None, None, exc
                if (
                    is_retryable_vlm_page_error(exc)
                    and attempts <= connect_max_retries
                ):
                    retry_started_at = time.monotonic()
                    await asyncio.sleep(min(2 ** (attempts - 1), 5))
                    retry_wait_seconds += time.monotonic() - retry_started_at
                    continue
                failure = build_page_failure(
                    page_index,
                    exc,
                    elapsed_seconds,
                    attempts,
                    vlm_request_seconds=vlm_request_seconds,
                    retry_wait_seconds=retry_wait_seconds,
                )
                page = task_progress_registry.page_skipped(
                    task_id,
                    source_file_name,
                    failure,
                )
                timing = page or {
                    **failure,
                    "total_seconds": round(elapsed_seconds, 2),
                }
                log_page_timing(
                    timing,
                    task_id=task_id,
                    source_file_name=source_file_name,
                    page_index=page_index,
                    status="skipped",
                    attempts=attempts,
                    error_type=failure["error_type"],
                    action="skip_and_continue",
                    warning=True,
                )
                return [], failure, None

    outcomes = await asyncio.gather(
        *(extract_page(offset, image) for offset, image in enumerate(images))
    )
    fatal_errors = [fatal for _, _, fatal in outcomes if fatal is not None]
    if fatal_errors:
        raise fatal_errors[0]
    page_results = [page_result for page_result, _, _ in outcomes]
    page_failures = [failure for _, failure, _ in outcomes if failure is not None]
    return page_results, page_failures
