from __future__ import annotations

import binascii
import hashlib
import io
import stat
import struct
import zipfile
from pathlib import Path

import pytest

from agent_bridge.app.document_paths import (
    join_backend_path,
    normalize_relative_document_path,
    split_document_path,
)
from agent_bridge.core.domain import ValidationError
from agent_bridge.knowledge_management.docs_knowledge.archive import ArchiveStorage
from agent_bridge.knowledge_management.docs_knowledge import uploads
from agent_bridge.knowledge_management.docs_knowledge.uploads import extract_zip_documents


def _zip_bytes(members: list[tuple[str | zipfile.ZipInfo, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members:
            archive.writestr(name, content)
    return buffer.getvalue()


def _legacy_zip_bytes(
    filename: bytes,
    content: bytes,
    *,
    flag_bits: int = 0,
    crc: int | None = None,
) -> bytes:
    """Build one stored member with an explicitly legacy filename encoding."""
    checksum = binascii.crc32(content) & 0xFFFFFFFF if crc is None else crc
    local_header = struct.pack(
        "<4s5H3L2H",
        b"PK\x03\x04",
        20,
        flag_bits,
        0,
        0,
        0,
        checksum,
        len(content),
        len(content),
        len(filename),
        0,
    )
    central_header = struct.pack(
        "<4s6H3L5H2L",
        b"PK\x01\x02",
        20,
        20,
        flag_bits,
        0,
        0,
        0,
        checksum,
        len(content),
        len(content),
        len(filename),
        0,
        0,
        0,
        0,
        0,
        0,
    )
    local = local_header + filename + content
    central = central_header + filename
    end = struct.pack(
        "<4s4H2LH",
        b"PK\x05\x06",
        0,
        0,
        1,
        1,
        len(central),
        len(local),
        0,
    )
    return local + central + end


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


def test_extract_zip_documents_returns_tree_and_document_metadata(tmp_path: Path) -> None:
    archive = tmp_path / "docs.zip"
    destination = tmp_path / "extracted"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("A/guide.md", b"guide")
        zf.writestr("A/B/spec.txt", b"spec")

    result = extract_zip_documents(archive, destination, {".md", ".txt"})

    assert isinstance(result, uploads.ExtractedZip)
    assert [(item.relative_path, item.file_size) for item in result.documents] == [
        ("A/B/spec.txt", 4),
        ("A/guide.md", 5),
    ]
    assert [item.content_hash for item in result.documents] == [
        hashlib.sha256(b"spec").hexdigest(),
        hashlib.sha256(b"guide").hexdigest(),
    ]
    assert list(result) == result.documents
    assert {
        (entry.kind, entry.relative_path, entry.name, entry.parent_path)
        for entry in result.entries
    } == {
        ("folder", "A", "A", None),
        ("folder", "A/B", "B", "A"),
        ("document", "A/guide.md", "guide.md", "A"),
        ("document", "A/B/spec.txt", "spec.txt", "A/B"),
    }
    assert all(item.path.exists() for item in result.documents)
    assert (destination / "A" / "B" / "spec.txt").read_bytes() == b"spec"


def test_extract_zip_documents_recurses_into_nested_archives_with_full_paths(tmp_path: Path) -> None:
    nested = _zip_bytes([("手册/api.md", b"api")])
    archive = tmp_path / "release.zip"
    archive.write_bytes(_zip_bytes([("release/manuals.zip", nested)]))

    result = extract_zip_documents(archive, tmp_path / "extracted", {".md"})

    assert [document.relative_path for document in result.documents] == [
        "release/manuals.zip/手册/api.md"
    ]
    assert {
        (entry.kind, entry.relative_path, entry.parent_path)
        for entry in result.entries
    } == {
        ("folder", "release", None),
        ("zip", "release/manuals.zip", "release"),
        ("folder", "release/manuals.zip/手册", "release/manuals.zip"),
        ("document", "release/manuals.zip/手册/api.md", "release/manuals.zip/手册"),
    }
    assert (
        tmp_path / "extracted" / "release" / "manuals.zip" / "手册" / "api.md"
    ).read_bytes() == b"api"


def test_extract_zip_documents_decodes_gb18030_legacy_filenames(tmp_path: Path) -> None:
    archive = tmp_path / "legacy.zip"
    archive.write_bytes(_legacy_zip_bytes("手册/api.md".encode("gb18030"), b"api"))

    result = extract_zip_documents(archive, tmp_path / "extracted", {".md"})

    assert result.documents[0].relative_path == "手册/api.md"
    assert (tmp_path / "extracted" / "手册" / "api.md").read_bytes() == b"api"


def test_extract_zip_documents_reports_complete_chain_for_broken_inner_zip(tmp_path: Path) -> None:
    inner = _zip_bytes([("broken.zip", b"not a zip")])
    archive = tmp_path / "release.zip"
    archive.write_bytes(_zip_bytes([("manuals.zip", inner)]))

    with pytest.raises(ValidationError) as error:
        extract_zip_documents(archive, tmp_path / "extracted", {".md"})

    message = str(error.value)
    assert "release.zip -> manuals.zip -> broken.zip" in message
    assert "内层 ZIP" in message
    assert "解压失败" in message
    assert "manuals.zip/broken.zip" in message


def test_extract_zip_documents_uses_display_archive_name_for_error_chain(tmp_path: Path) -> None:
    source = tmp_path / "tmp-upload.zip"
    source.write_bytes(_zip_bytes([("broken.zip", b"not a zip")]))

    with pytest.raises(ValidationError) as error:
        extract_zip_documents(
            source,
            tmp_path / "extracted",
            {".md"},
            archive_name="release.zip",
        )

    assert "release.zip -> broken.zip" in str(error.value)
    assert "tmp-upload.zip" not in str(error.value)


@pytest.mark.parametrize(
    "member_name",
    ["../outside.md", "/absolute.md", r"C:\\absolute.md", "docs/guide\x00.md", ""],
)
def test_extract_zip_documents_rejects_unsafe_member_paths(
    tmp_path: Path,
    member_name: str,
) -> None:
    archive = tmp_path / "unsafe.zip"
    if "\x00" in member_name:
        archive.write_bytes(_legacy_zip_bytes(member_name.encode(), b"unsafe"))
    else:
        info = zipfile.ZipInfo(member_name)
        archive.write_bytes(_zip_bytes([(info, b"unsafe")]))

    with pytest.raises(ValidationError, match="路径|成员|非法|安全"):
        extract_zip_documents(archive, tmp_path / "extracted", {".md"})
    assert not (tmp_path / "outside.md").exists()


def test_extract_zip_documents_rejects_symlink_member(tmp_path: Path) -> None:
    symlink = zipfile.ZipInfo("link.md")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    archive = tmp_path / "symlink.zip"
    archive.write_bytes(_zip_bytes([(symlink, b"../outside.md")]))

    with pytest.raises(ValidationError, match="符号链接|symlink"):
        extract_zip_documents(archive, tmp_path / "extracted", {".md"})


def test_extract_zip_documents_rejects_encrypted_member(tmp_path: Path) -> None:
    archive = tmp_path / "encrypted.zip"
    archive.write_bytes(_legacy_zip_bytes(b"secret.md", b"secret", flag_bits=0x1))

    with pytest.raises(ValidationError, match="加密"):
        extract_zip_documents(archive, tmp_path / "extracted", {".md"})


def test_extract_zip_documents_rejects_normalized_duplicate_paths(tmp_path: Path) -> None:
    archive = tmp_path / "duplicate.zip"
    archive.write_bytes(_zip_bytes([("docs\\guide.md", b"one"), ("docs/guide.md", b"two")]))

    with pytest.raises(ValidationError, match="重复"):
        extract_zip_documents(archive, tmp_path / "extracted", {".md"})


def test_extract_zip_documents_rejects_crc_error_before_writing(tmp_path: Path) -> None:
    archive = tmp_path / "crc.zip"
    valid_crc = binascii.crc32(b"guide") & 0xFFFFFFFF
    archive.write_bytes(_legacy_zip_bytes(b"guide.md", b"guide", crc=valid_crc ^ 1))
    destination = tmp_path / "extracted"

    with pytest.raises(ValidationError, match="校验|CRC|损坏"):
        extract_zip_documents(archive, destination, {".md"})

    assert not destination.exists()


def test_extract_zip_documents_allows_eight_nested_zip_levels(tmp_path: Path) -> None:
    payload = _zip_bytes([("guide.md", b"guide")])
    for level in range(8, 0, -1):
        payload = _zip_bytes([(f"level-{level}.zip", payload)])
    archive = tmp_path / "outer.zip"
    archive.write_bytes(payload)

    result = extract_zip_documents(archive, tmp_path / "extracted", {".md"})

    assert len(result.documents) == 1
    assert result.documents[0].relative_path.endswith("/guide.md")


def test_extract_zip_documents_rejects_ninth_nested_zip_level(tmp_path: Path) -> None:
    payload = _zip_bytes([("guide.md", b"guide")])
    for level in range(9, 0, -1):
        payload = _zip_bytes([(f"level-{level}.zip", payload)])
    archive = tmp_path / "outer.zip"
    archive.write_bytes(payload)

    with pytest.raises(ValidationError, match="深度|8"):
        extract_zip_documents(archive, tmp_path / "extracted", {".md"})


def test_extract_zip_documents_rejects_archive_without_supported_descendants(tmp_path: Path) -> None:
    archive = tmp_path / "unsupported.zip"
    archive.write_bytes(_zip_bytes([("readme.bin", b"binary")]))

    with pytest.raises(ValidationError, match="支持|文档"):
        extract_zip_documents(archive, tmp_path / "extracted", {".md"})


def test_extract_zip_documents_rejects_nested_archive_without_supported_descendants(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "release.zip"
    archive.write_bytes(_zip_bytes([("manuals.zip", _zip_bytes([("readme.bin", b"binary")]))]))

    with pytest.raises(ValidationError, match="release.zip -> manuals.zip.*支持|文档"):
        extract_zip_documents(archive, tmp_path / "extracted", {".md"})


def test_archive_store_uses_precomputed_metadata_without_rehashing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "guide.md"
    source.write_bytes(b"same content")
    content_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    storage = ArchiveStorage(tmp_path / "archive")

    def fail_rehash(_: Path) -> str:
        raise AssertionError("precomputed hash must not be recomputed")

    monkeypatch.setattr(storage, "content_hash", fail_rehash)

    result = storage.store(source, content_hash=content_hash, file_size=12)

    assert result.content_hash == content_hash
    assert result.file_size == 12
    assert result.archive_path == tmp_path / "archive" / content_hash[:2] / content_hash[2:4] / f"{content_hash}.md"
    assert result.archive_path.read_bytes() == b"same content"
