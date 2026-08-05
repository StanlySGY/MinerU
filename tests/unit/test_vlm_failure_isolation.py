import asyncio
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest


pytest.importorskip("PIL")
pytest.importorskip("cv2")
pytest.importorskip("bs4")
pytest.importorskip("mineru_vl_utils")

from mineru.cli import common, fast_api


@pytest.mark.asyncio
async def test_failed_file_does_not_abort_remaining_files(monkeypatch, tmp_path):
    monkeypatch.setattr(
        common,
        "prepare_env",
        lambda output_dir, file_name, parse_method: (
            str(tmp_path / file_name / "images"),
            str(tmp_path / file_name),
        ),
    )
    monkeypatch.setattr(common, "_process_output", lambda *args, **kwargs: None)

    async def fake_analyze(pdf_bytes, **kwargs):
        if pdf_bytes == b"bad":
            raise httpx.ReadTimeout("document timed out")
        return {"pdf_info": [{"page_idx": 0}]}, [[]]

    monkeypatch.setattr(common, "aio_vlm_doc_analyze", fake_analyze)

    results = await common._async_process_vlm(
        str(tmp_path),
        ["bad", "good"],
        [b"bad", b"good"],
        "http-client",
        False,
        False,
        True,
        True,
        True,
        False,
        True,
        "mm_markdown",
        server_url="http://vlm/v1",
    )

    assert [result["status"] for result in results] == ["failed", "completed"]
    assert results[0]["error"]["type"] == "ReadTimeout"


@pytest.mark.asyncio
async def test_run_parse_job_returns_partial_batch_report(monkeypatch, tmp_path):
    monkeypatch.setattr(
        fast_api,
        "load_parse_inputs",
        lambda uploads: (["first", "second"], [b"one", b"two"]),
    )

    from mineru.utils import pdf_precheck

    monkeypatch.setattr(
        pdf_precheck,
        "check_pdf_quality",
        lambda pdf_bytes: SimpleNamespace(safe=True, reason=None),
    )
    monkeypatch.setattr(
        fast_api,
        "aio_do_parse",
        lambda **kwargs: asyncio.sleep(
            0,
            result=[
                {
                    "file_name": "first",
                    "status": "partial",
                    "failed_pages": [{"page_number": 3}],
                    "error": None,
                },
                {
                    "file_name": "second",
                    "status": "completed",
                    "failed_pages": [],
                    "error": None,
                },
            ],
        ),
    )

    request_options = SimpleNamespace(
        task_id="task-1",
        lang_list=["ch"],
        backend="vlm-http-client",
        parse_method="auto",
        effort="medium",
        formula_enable=True,
        table_enable=True,
        image_analysis=True,
        server_url="http://vlm/v1",
        return_md=True,
        return_middle_json=True,
        return_model_output=True,
        return_content_list=True,
        return_original_file=False,
        response_format_zip=False,
        client_side_output_generation=False,
        start_page_id=0,
        end_page_id=999,
    )

    result = await fast_api.run_parse_job(
        output_dir=str(tmp_path),
        uploads=[],
        request_options=request_options,
        config={},
    )

    assert result["partial_success"] is True
    assert [item["status"] for item in result["file_results"]] == [
        "partial",
        "completed",
    ]


@pytest.mark.asyncio
async def test_run_parse_job_preserves_all_failed_file_report(monkeypatch, tmp_path):
    monkeypatch.setattr(
        fast_api,
        "load_parse_inputs",
        lambda uploads: (["first", "second"], [b"one", b"two"]),
    )

    from mineru.utils import pdf_precheck

    monkeypatch.setattr(
        pdf_precheck,
        "check_pdf_quality",
        lambda pdf_bytes: SimpleNamespace(safe=True, reason=None),
    )
    monkeypatch.setattr(
        fast_api,
        "aio_do_parse",
        lambda **kwargs: asyncio.sleep(
            0,
            result=[
                {
                    "file_name": name,
                    "status": "failed",
                    "failed_pages": [],
                    "error": {"type": "RuntimeError", "message": "failed"},
                }
                for name in ("first", "second")
            ],
        ),
    )

    request_options = SimpleNamespace(
        task_id="task-2",
        lang_list=["ch"],
        backend="vlm-http-client",
        parse_method="auto",
        effort="medium",
        formula_enable=True,
        table_enable=True,
        image_analysis=True,
        server_url="http://vlm/v1",
        return_md=True,
        return_middle_json=True,
        return_model_output=True,
        return_content_list=True,
        return_original_file=False,
        response_format_zip=False,
        client_side_output_generation=False,
        start_page_id=0,
        end_page_id=999,
    )

    with pytest.raises(fast_api.ParseBatchFailedError) as exc_info:
        await fast_api.run_parse_job(
            output_dir=str(tmp_path),
            uploads=[],
            request_options=request_options,
            config={},
        )

    assert [item["file_name"] for item in exc_info.value.file_results] == [
        "first",
        "second",
    ]


def test_partial_result_zip_contains_parse_report(tmp_path):
    zip_path = fast_api.create_result_zip(
        output_dir=str(tmp_path),
        pdf_file_names=["sample"],
        backend="vlm-http-client",
        parse_method="auto",
        return_md=False,
        return_middle_json=False,
        return_model_output=False,
        return_content_list=False,
        return_images=False,
        return_original_file=False,
        partial_success=True,
        file_results=[
            {
                "file_name": "sample",
                "status": "partial",
                "failed_pages": [{"page_number": 2}],
            }
        ],
    )
    try:
        with zipfile.ZipFile(zip_path) as zf:
            report = json.loads(zf.read("_parse_report.json"))
        assert report["partial_success"] is True
        assert report["file_results"][0]["failed_pages"] == [{"page_number": 2}]
    finally:
        Path(zip_path).unlink(missing_ok=True)
