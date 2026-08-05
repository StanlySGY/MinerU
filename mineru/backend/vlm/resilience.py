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
) -> dict:
    return {
        "page_idx": page_index,
        "page_number": page_index + 1,
        "status": "skipped",
        "error_type": type(exc).__name__,
        "error": str(exc) or repr(exc),
        "elapsed_seconds": round(elapsed, 2),
        "attempts": attempts,
    }


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

    async def extract_page(offset, image):
        page_index = page_start_index + offset
        started_at = time.monotonic()
        attempts = 0
        while True:
            attempts += 1
            try:
                async with semaphore:
                    page_results = await asyncio.wait_for(
                        predictor.aio_batch_two_step_extract(
                            images=[image],
                            image_analysis=image_analysis,
                        ),
                        timeout=timeout_seconds,
                    )
                if len(page_results) != 1:
                    raise RuntimeError(
                        "VLM page extraction returned an unexpected result count: "
                        f"expected 1, got {len(page_results)}"
                    )
                return page_results[0], None, None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not is_skippable_vlm_page_error(exc):
                    return None, None, exc
                if (
                    is_retryable_vlm_page_error(exc)
                    and attempts <= connect_max_retries
                ):
                    await asyncio.sleep(min(2 ** (attempts - 1), 5))
                    continue
                elapsed = time.monotonic() - started_at
                failure = build_page_failure(page_index, exc, elapsed, attempts)
                logger.warning(
                    "VLM page skipped: task_id={}, file_name={}, page_number={}, "
                    "page_idx={}, error_type={}, attempts={}, elapsed_seconds={}, "
                    "action=skip_and_continue",
                    task_id or "-",
                    source_file_name or "-",
                    failure["page_number"],
                    page_index,
                    failure["error_type"],
                    attempts,
                    failure["elapsed_seconds"],
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
