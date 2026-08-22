import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from starlette.requests import Request

from mineru.cli.api_request import (
    apply_effective_vlm_request_controls,
    parse_request_form,
    resolve_vlm_request_controls,
)


def test_vlm_request_controls_use_environment_when_request_omits_values(monkeypatch):
    monkeypatch.setenv("MINERU_VLM_PAGE_TIMEOUT_SECONDS", "1200")
    monkeypatch.setenv("MINERU_VLM_CONNECT_MAX_RETRIES", "2")

    assert resolve_vlm_request_controls(None, None) == (1200.0, 2)


def test_explicit_vlm_request_controls_override_environment(monkeypatch):
    monkeypatch.setenv("MINERU_VLM_PAGE_TIMEOUT_SECONDS", "1200")
    monkeypatch.setenv("MINERU_VLM_CONNECT_MAX_RETRIES", "2")

    assert resolve_vlm_request_controls(30, 0) == (30.0, 0)


def test_vlm_request_controls_use_builtin_defaults_without_environment(monkeypatch):
    monkeypatch.delenv("MINERU_VLM_PAGE_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("MINERU_VLM_CONNECT_MAX_RETRIES", raising=False)

    assert resolve_vlm_request_controls(None, None) == (600.0, 1)


def test_invalid_vlm_request_control_environment_uses_builtin_defaults(monkeypatch):
    monkeypatch.setenv("MINERU_VLM_PAGE_TIMEOUT_SECONDS", "invalid")
    monkeypatch.setenv("MINERU_VLM_CONNECT_MAX_RETRIES", "invalid")

    assert resolve_vlm_request_controls(None, None) == (600.0, 1)


@pytest.mark.parametrize(
    ("page_timeout_seconds", "page_connect_max_retries", "expected_detail"),
    [
        (7201, 1, "page_timeout_seconds must be between 1 and 7200"),
        (600, 4, "page_connect_max_retries must be between 0 and 3"),
    ],
)
def test_vlm_request_controls_reject_out_of_range_values(
    page_timeout_seconds,
    page_connect_max_retries,
    expected_detail,
):
    with pytest.raises(HTTPException) as exc_info:
        resolve_vlm_request_controls(
            page_timeout_seconds,
            page_connect_max_retries,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == expected_detail


def test_router_adds_effective_vlm_controls_when_fields_are_missing():
    fields = []
    request_options = SimpleNamespace(
        page_timeout_seconds=1200.0,
        page_connect_max_retries=2,
    )

    apply_effective_vlm_request_controls(fields, request_options)

    assert fields == [
        ("page_timeout_seconds", "1200.0"),
        ("page_connect_max_retries", "2"),
    ]


def test_router_preserves_explicit_vlm_control_fields():
    fields = [
        ("page_timeout_seconds", "30"),
        ("page_connect_max_retries", "0"),
    ]
    request_options = SimpleNamespace(
        page_timeout_seconds=1200.0,
        page_connect_max_retries=2,
    )

    apply_effective_vlm_request_controls(fields, request_options)

    assert fields == [
        ("page_timeout_seconds", "30"),
        ("page_connect_max_retries", "0"),
    ]


def _request_for_parse_form() -> Request:
    app = FastAPI()
    app.state.public_bind_exposed = False
    app.state.allow_public_http_client = False
    return Request(
        {
            "type": "http",
            "app": app,
            "method": "POST",
            "scheme": "http",
            "path": "/tasks",
            "raw_path": b"/tasks",
            "query_string": b"",
            "headers": [],
            "client": ("test", 1234),
            "server": ("test", 80),
        }
    )


def _parse_request_form_with_batch_size(batch_size: int):
    return asyncio.run(
        parse_request_form(
            _request_for_parse_form(),
            [UploadFile(filename="sample.pdf", file=BytesIO(b"%PDF-test"))],
            backend="pipeline",
            vlm_batch_size=batch_size,
        )
    )


def test_parse_request_form_accepts_vlm_batch_size_32():
    options = _parse_request_form_with_batch_size(32)

    assert options.vlm_batch_size == 32


def test_parse_request_form_rejects_vlm_batch_size_above_32():
    with pytest.raises(HTTPException) as exc_info:
        _parse_request_form_with_batch_size(33)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "vlm_batch_size must be between 1 and 32"
