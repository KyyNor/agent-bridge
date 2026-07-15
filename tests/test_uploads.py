from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import pytest

from agent_bridge.app.document_paths import (
    join_backend_path,
    normalize_relative_document_path,
    split_document_path,
)
from agent_bridge.core.domain import ValidationError
from agent_bridge.knowledge_management.docs_knowledge.uploads import extract_zip_documents


def test_document_paths_normalize_and_join_without_virtual_root() -> None:
    assert normalize_relative_document_path(r"docs\\api//guide.md") == "docs/api/guide.md"
    assert split_document_path("docs/api/guide.md") == (["docs", "api"], "guide.md")
    assert join_backend_path("", r"docs\\api/guide.md") == "docs/api/guide.md"
    assert join_backend_path("Guides", "api/guide.md") == "Guides/api/guide.md"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "/guide.md",
        r"\\server\\guide.md",
        "C:/guide.md",
        "docs/",
        "./guide.md",
        "docs/../guide.md",
        "docs/./guide.md",
        "docs/guide\x00.md",
        "docs/bad:name.md",
    ],
)
def test_document_paths_reject_unsafe_names(value: str) -> None:
    with pytest.raises(ValidationError):
        normalize_relative_document_path(value)


def test_extract_zip_documents_returns_temp_paths_and_relative_paths(tmp_path: Path) -> None:
    archive = tmp_path / "docs.zip"
    destination = tmp_path / "extracted"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("A/guide.md", b"guide")
        zf.writestr("A/B/spec.txt", b"spec")

    records = extract_zip_documents(archive, destination, {".md", ".txt"})

    assert [(item.path, item.relative_path) for item in records] == [
        (destination / "A" / "B" / "spec.txt", "A/B/spec.txt"),
        (destination / "A" / "guide.md", "A/guide.md"),
    ]
    assert all(item.path.exists() for item in records)
    assert (destination / "A" / "B" / "spec.txt").read_bytes() == b"spec"


def test_extract_zip_documents_rejects_nested_archives_and_symlinks(tmp_path: Path) -> None:
    nested = tmp_path / "nested.zip"
    with zipfile.ZipFile(nested, "w") as zf:
        zf.writestr("inner.md", b"inner")

    archive = tmp_path / "docs.zip"
    symlink = zipfile.ZipInfo("link.md")
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("nested.zip", nested.read_bytes())
        zf.writestr(symlink, "../outside.md")

    with pytest.raises(ValidationError, match="nested|symbolic"):
        extract_zip_documents(archive, tmp_path / "extracted", {".md"})
