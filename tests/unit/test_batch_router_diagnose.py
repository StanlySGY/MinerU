import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "docker" / "multi" / "batch-router-diagnose.py"
SPEC = importlib.util.spec_from_file_location("mineru_batch_router_diagnose", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_safe_extract_zip_preserves_preview_files(tmp_path: Path) -> None:
    archive_path = tmp_path / "result.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("document/auto/document.md", "# extracted")
        archive.writestr("document/auto/images/page.jpg", b"image")

    extracted = MODULE.safe_extract_zip(archive_path, tmp_path / "extracted")

    relative_paths = {path.relative_to(tmp_path / "extracted").as_posix() for path in extracted}
    assert relative_paths == {
        "document/auto/document.md",
        "document/auto/images/page.jpg",
    }


def test_safe_extract_zip_rejects_path_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../outside.txt", "unsafe")

    with pytest.raises(MODULE.CurlRequestError):
        MODULE.safe_extract_zip(archive_path, tmp_path / "extracted")

    assert not (tmp_path / "outside.txt").exists()


def _page(number: int, status: str = "completed", **overrides) -> dict:
    page = {
        "page_idx": number - 1,
        "page_number": number,
        "status": status,
        "queue_seconds": 1.0,
        "vlm_request_seconds": 10.0 + number,
        "retry_wait_seconds": 0.0,
        "total_seconds": 11.0 + number,
        "attempts": 1,
    }
    page.update(overrides)
    return page


def test_collect_all_pages_flattens_sorts_and_tags_file_name() -> None:
    progress = {
        "files": [
            {"file_name": "b.pdf", "pages": [_page(2), _page(1)]},
            {"file_name": "a.pdf", "pages": [_page(3, "failed", error="boom"), _page(1)]},
        ]
    }

    rows = MODULE.collect_all_pages(progress)

    assert [(row["file_name"], row["page_number"]) for row in rows] == [
        ("a.pdf", 1),
        ("a.pdf", 3),
        ("b.pdf", 1),
        ("b.pdf", 2),
    ]
    assert rows[1]["status"] == "failed"


def test_collect_all_pages_skips_non_terminal_and_malformed_entries() -> None:
    progress = {
        "files": [
            {
                "file_name": "a.pdf",
                "pages": [
                    _page(1, "processing"),
                    _page(2, "queued"),
                    _page(3, "completed"),
                    _page(4, "skipped"),
                    "not-a-dict",
                ],
            },
            {"file_name": "b.pdf", "pages": "not-a-list"},
            "not-a-dict",
        ]
    }

    rows = MODULE.collect_all_pages(progress)

    assert [row["page_number"] for row in rows] == [3, 4]


def test_collect_all_pages_returns_empty_without_page_progress() -> None:
    assert MODULE.collect_all_pages({}) == []
    assert MODULE.collect_all_pages({"files": None}) == []
    assert MODULE.collect_all_pages({"files": []}) == []


def test_format_page_seconds_handles_missing_and_invalid_values() -> None:
    assert MODULE.format_page_seconds(None) == "-"
    assert MODULE.format_page_seconds("abc") == "-"
    assert MODULE.format_page_seconds(3) == "3.00"
    assert MODULE.format_page_seconds(12.345) == "12.35"


def _run_config(tmp_path: Path):
    return MODULE.RunConfig(
        input_dir=tmp_path,
        router_url="http://router:8000",
        backend="vlm-http-client",
        effort="standard",
        parse_method="auto",
        lang="ch",
        server_url=None,
        image_analysis=False,
        formula_enable=True,
        table_enable=True,
        start_page_id=0,
        end_page_id=-1,
        recursive=False,
        poll_interval=5.0,
        task_timeout=3600.0,
        pause_seconds=0.0,
        submit_retries=1,
        curl_bin="curl",
        output_path=tmp_path / "BATCH_DIAGNOSIS.md",
        raw_dir=tmp_path / "raw",
        preview_dir=None,
        save_result_images=False,
        collect_diagnostics=False,
        router_container="router",
        api_container="api",
        vlm_container=None,
        vlm_ssh=None,
        vlm_metrics_url=None,
        log_tail=100,
    )


def test_render_report_lists_every_page_timing(tmp_path: Path) -> None:
    results = [
        {
            "index": 1,
            "relative_path": "a.pdf",
            "size_bytes": 1024,
            "elapsed_seconds": 30.0,
            "started_at": "2026-08-19T00:00:00+00:00",
            "completed_at": "2026-08-19T00:00:30+00:00",
            "error": None,
            "failed_pages": [],
            "classification": "partial",
            "task_status": "completed",
            "task_id": "task-1",
            "partial_success": True,
            "progress": {
                "files": [
                    {
                        "file_name": "a.pdf",
                        "pages": [
                            _page(1),
                            _page(2),
                            _page(3, "failed", error="pipe | and\nnewline"),
                        ],
                    }
                ]
            },
        }
    ]

    report = MODULE.render_report(_run_config(tmp_path), 1, results, None, "2026-08-19T00:00:00+00:00")

    assert "#### 逐页耗时" in report
    assert "| 文件 | 页码 | 状态 | 排队(秒) | VLM请求(秒) | 重试等待(秒) | 总耗时(秒) | 尝试次数 | 错误 |" in report
    for page_number in (1, 2, 3):
        assert f"| a.pdf | {page_number} |" in report
    # escaped so the pipe/newline cannot break the table
    assert "pipe \\| and newline" in report


def test_render_report_omits_page_table_without_page_progress(tmp_path: Path) -> None:
    results = [
        {
            "index": 1,
            "relative_path": "a.pdf",
            "size_bytes": 1024,
            "elapsed_seconds": 5.0,
            "started_at": "2026-08-19T00:00:00+00:00",
            "completed_at": "2026-08-19T00:00:05+00:00",
            "error": "boom",
            "failed_pages": [],
            "classification": "failed",
            "task_status": "failed",
            "task_id": "task-1",
            "partial_success": False,
            "progress": {},
        }
    ]

    report = MODULE.render_report(_run_config(tmp_path), 1, results, None, "2026-08-19T00:00:00+00:00")

    assert "#### 逐页耗时" not in report
