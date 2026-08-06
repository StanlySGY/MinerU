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
