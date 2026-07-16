"""Helpers for safely expanding document upload archives."""
from __future__ import annotations

import hashlib
import io
import stat
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal
from zipfile import BadZipFile, ZipFile, ZipInfo

from agent_bridge.app.document_paths import normalize_relative_document_path
from agent_bridge.core.domain import ValidationError


MAX_NESTED_ZIP_DEPTH = 8
_ENCRYPTED_FLAG = 0x1
_UTF8_FILENAME_FLAG = 0x800
_FILENAME_ENCODINGS = ("utf-8", "gb18030", "big5", "shift_jis", "cp437")
_ZIP_ERRORS = (BadZipFile, EOFError, OSError, RuntimeError, KeyError, ValueError, zlib.error)


@dataclass(frozen=True)
class ExtractedDocument:
    path: Path
    relative_path: str
    content_hash: str
    file_size: int


@dataclass(frozen=True)
class ExtractedArchiveEntry:
    kind: Literal["zip", "folder", "document"]
    relative_path: str
    name: str
    parent_path: str | None
    content_hash: str | None = None
    file_size: int | None = None


@dataclass(frozen=True)
class ExtractedZip:
    entries: list[ExtractedArchiveEntry]
    documents: list[ExtractedDocument]

    def __iter__(self) -> Iterator[ExtractedDocument]:
        """Keep the old ``for document in extract_zip_documents(...)`` behavior."""
        return iter(self.documents)

    def __len__(self) -> int:
        return len(self.documents)

    def __getitem__(self, index: int) -> ExtractedDocument:
        return self.documents[index]


@dataclass(frozen=True)
class _SafeMember:
    info: ZipInfo
    relative_path: str
    is_directory: bool
    name: str


class _ExtractionCollector:
    def __init__(self, destination: Path, allowed_extensions: set[str]) -> None:
        self.destination = destination
        self.allowed_extensions = {extension.lower() for extension in allowed_extensions}
        self.entries: dict[str, ExtractedArchiveEntry] = {}
        self.documents: list[ExtractedDocument] = []
        self._claimed_member_paths: dict[str, str] = {}

    def claim_member_path(self, relative_path: str, kind: str, chain: tuple[str, ...]) -> None:
        previous_kind = self._claimed_member_paths.get(relative_path)
        if previous_kind is not None:
            raise _validation(
                chain,
                "发现规范化后的重复 ZIP 成员路径",
                relative_path,
                f"（已有成员类型：{previous_kind}，当前类型：{kind}）",
            )
        self._claimed_member_paths[relative_path] = kind

    def add_folder(self, relative_path: str, chain: tuple[str, ...]) -> None:
        self._ensure_parent_folders(relative_path, chain)
        existing = self.entries.get(relative_path)
        if existing is not None:
            if existing.kind != "folder":
                raise _validation(chain, "ZIP 路径类型冲突", relative_path)
            return
        self.entries[relative_path] = _entry("folder", relative_path)

    def add_zip(self, relative_path: str, chain: tuple[str, ...]) -> None:
        self._ensure_parent_folders(relative_path, chain)
        existing = self.entries.get(relative_path)
        if existing is not None:
            raise _validation(chain, "ZIP 路径类型冲突", relative_path)
        self.entries[relative_path] = _entry("zip", relative_path)

    def add_document(self, document: ExtractedDocument, chain: tuple[str, ...]) -> None:
        self._ensure_parent_folders(document.relative_path, chain)
        existing = self.entries.get(document.relative_path)
        if existing is not None:
            raise _validation(chain, "ZIP 路径类型冲突", document.relative_path)
        self.documents.append(document)
        self.entries[document.relative_path] = _entry(
            "document",
            document.relative_path,
            content_hash=document.content_hash,
            file_size=document.file_size,
        )

    def _ensure_parent_folders(self, relative_path: str, chain: tuple[str, ...]) -> None:
        parts = relative_path.split("/")
        for index in range(1, len(parts)):
            folder_path = "/".join(parts[:index])
            existing = self.entries.get(folder_path)
            if existing is not None:
                if existing.kind not in {"folder", "zip"}:
                    raise _validation(chain, "ZIP 路径类型冲突", folder_path)
                continue
            self.entries[folder_path] = _entry("folder", folder_path)

    def copy_document(
        self,
        archive: ZipFile,
        member: _SafeMember,
        relative_path: str,
        chain: tuple[str, ...],
    ) -> ExtractedDocument:
        target = self.destination.joinpath(*relative_path.split("/"))
        digest = hashlib.sha256()
        file_size = 0
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with archive.open(member.info) as input_file, target.open("wb") as output_file:
                for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
                    digest.update(chunk)
                    file_size += len(chunk)
                    output_file.write(chunk)
        except _ZIP_ERRORS as exc:
            target.unlink(missing_ok=True)
            raise _validation(chain, "文档解压失败", relative_path, str(exc)) from exc
        return ExtractedDocument(
            path=target,
            relative_path=relative_path,
            content_hash=digest.hexdigest(),
            file_size=file_size,
        )


def extract_zip_documents(
    source: Path,
    destination: Path,
    allowed_extensions: set[str],
    archive_name: str | None = None,
) -> ExtractedZip:
    """Safely validate and recursively extract supported ZIP documents."""
    collector = _ExtractionCollector(destination, allowed_extensions)
    chain = (archive_name or source.name,)
    try:
        _extract_archive(
            source,
            prefix="",
            chain=chain,
            nested_depth=0,
            collector=collector,
        )
    except ValidationError:
        raise
    except _ZIP_ERRORS as exc:
        raise _validation(chain, "ZIP 解压失败", None, str(exc)) from exc

    entries = [collector.entries[path] for path in sorted(collector.entries)]
    documents = sorted(collector.documents, key=lambda document: document.relative_path)
    return ExtractedZip(entries=entries, documents=documents)


def _extract_archive(
    source: Path | io.BytesIO,
    *,
    prefix: str,
    chain: tuple[str, ...],
    nested_depth: int,
    collector: _ExtractionCollector,
) -> int:
    if nested_depth > MAX_NESTED_ZIP_DEPTH:
        raise _validation(chain, "嵌套 ZIP 深度超过 8", prefix or None)

    try:
        archive = ZipFile(source)
    except _ZIP_ERRORS as exc:
        raise _validation(chain, "内层 ZIP 解压失败" if len(chain) > 1 else "ZIP 解压失败", prefix or None, str(exc)) from exc

    with archive:
        members = _read_safe_members(archive, prefix=prefix, chain=chain, collector=collector)
        _validate_crc(archive, members, prefix=prefix, chain=chain)

        document_count = 0
        for member in sorted(members, key=lambda item: item.relative_path):
            relative_path = _join_relative(prefix, member.relative_path)
            if member.is_directory:
                collector.add_folder(relative_path, chain)
                continue

            if Path(member.relative_path).suffix.lower() == ".zip":
                collector.add_zip(relative_path, chain)
                try:
                    nested_payload = archive.read(member.info)
                except _ZIP_ERRORS as exc:
                    raise _validation(chain + (member.name,), "内层 ZIP 解压失败", relative_path, str(exc)) from exc
                document_count += _extract_archive(
                    io.BytesIO(nested_payload),
                    prefix=relative_path,
                    chain=chain + (member.name,),
                    nested_depth=nested_depth + 1,
                    collector=collector,
                )
                continue

            if Path(member.relative_path).suffix.lower() not in collector.allowed_extensions:
                continue

            document = collector.copy_document(archive, member, relative_path, chain)
            collector.add_document(document, chain)
            document_count += 1

        if document_count == 0:
            raise _validation(
                chain,
                "ZIP 压缩层没有支持的后代文档",
                prefix or None,
            )
        return document_count


def _read_safe_members(
    archive: ZipFile,
    *,
    prefix: str,
    chain: tuple[str, ...],
    collector: _ExtractionCollector,
) -> list[_SafeMember]:
    _reject_nul_in_central_directory(archive, prefix=prefix, chain=chain)
    members: list[_SafeMember] = []
    for info in archive.infolist():
        try:
            name, is_directory = _normalise_member_name(info)
        except ValidationError as exc:
            raise _validation(chain, "ZIP 成员路径不安全", prefix or None, str(exc)) from exc

        relative_path = _join_relative(prefix, name)
        if info.flag_bits & _ENCRYPTED_FLAG:
            raise _validation(chain, "ZIP 成员已加密，拒绝解压", relative_path)
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(mode):
            raise _validation(chain, "ZIP 成员符号链接不允许解压", relative_path)

        kind = "folder" if is_directory else "file"
        collector.claim_member_path(relative_path, kind, chain)
        members.append(
            _SafeMember(
                info=info,
                relative_path=name,
                is_directory=is_directory,
                name=name.rsplit("/", 1)[-1],
            )
        )
    return members


def _reject_nul_in_central_directory(
    archive: ZipFile,
    *,
    prefix: str,
    chain: tuple[str, ...],
) -> None:
    """ZipInfo truncates NUL-containing names, so inspect raw central names first."""
    file_handle = archive.fp
    if file_handle is None:
        return
    position = file_handle.tell()
    try:
        file_handle.seek(archive.start_dir)
        for _ in archive.infolist():
            header = file_handle.read(46)
            if len(header) != 46 or header[:4] != b"PK\x01\x02":
                break
            filename_size, extra_size, comment_size = struct.unpack_from("<HHH", header, 28)
            raw_name = file_handle.read(filename_size)
            if b"\x00" in raw_name:
                raise _validation(
                    chain,
                    "ZIP 成员文件名包含 NUL，路径不安全",
                    prefix or None,
                )
            file_handle.seek(extra_size + comment_size, 1)
    except OSError as exc:
        raise _validation(chain, "ZIP 中心目录读取失败", prefix or None, str(exc)) from exc
    finally:
        file_handle.seek(position)


def _validate_crc(
    archive: ZipFile,
    members: list[_SafeMember],
    *,
    prefix: str,
    chain: tuple[str, ...],
) -> None:
    try:
        broken_member = archive.testzip()
    except _ZIP_ERRORS as exc:
        raise _validation(chain, "ZIP CRC 校验/解压失败", prefix or None, str(exc)) from exc
    if broken_member is None:
        return

    matching_member = next(
        (member for member in members if member.info.filename == broken_member),
        None,
    )
    broken_path = (
        _join_relative(prefix, matching_member.relative_path)
        if matching_member is not None
        else _join_relative(prefix, str(broken_member).replace("\\", "/"))
    )
    raise _validation(chain, "ZIP CRC 校验失败，压缩数据可能已损坏", broken_path)


def _normalise_member_name(info: ZipInfo) -> tuple[str, bool]:
    decoded_name = _decode_member_filename(info)
    normalized_name = decoded_name.replace("\\", "/")
    is_directory = info.is_dir() or normalized_name.endswith("/")
    validation_name = normalized_name.rstrip("/")
    if not validation_name:
        raise ValidationError("ZIP 成员路径为空")
    return normalize_relative_document_path(validation_name), is_directory


def _decode_member_filename(info: ZipInfo) -> str:
    if info.flag_bits & _UTF8_FILENAME_FLAG:
        return info.filename

    try:
        raw_name = info.filename.encode("cp437")
    except UnicodeEncodeError:
        raw_name = info.filename.encode("utf-8")
    if all(byte < 0x80 for byte in raw_name):
        return raw_name.decode("ascii")

    candidates = [
        raw_name.decode(encoding, errors="replace")
        for encoding in _FILENAME_ENCODINGS
    ]
    return max(candidates, key=_filename_score)


def _filename_score(value: str) -> int:
    score = 0
    for character in value:
        codepoint = ord(character)
        if character == "\ufffd":
            score -= 100
        elif codepoint < 32 or 0x7F <= codepoint < 0xA0:
            score -= 40
        elif 0x2500 <= codepoint <= 0x259F:
            score -= 12
        elif _is_cjk(character):
            score += 12
        elif character.isprintable():
            score += 3
        else:
            score -= 4
    return score


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3040 <= codepoint <= 0x30FF  # Japanese kana
        or 0x3400 <= codepoint <= 0x4DBF  # CJK extension A
        or 0x4E00 <= codepoint <= 0x9FFF  # CJK unified ideographs
        or 0xAC00 <= codepoint <= 0xD7AF  # Korean syllables
    )


def _join_relative(prefix: str, relative_path: str) -> str:
    return f"{prefix}/{relative_path}" if prefix else relative_path


def _entry(
    kind: Literal["zip", "folder", "document"],
    relative_path: str,
    *,
    content_hash: str | None = None,
    file_size: int | None = None,
) -> ExtractedArchiveEntry:
    parent_path, _, name = relative_path.rpartition("/")
    return ExtractedArchiveEntry(
        kind=kind,
        relative_path=relative_path,
        name=name or relative_path,
        parent_path=parent_path or None,
        content_hash=content_hash,
        file_size=file_size,
    )


def _validation(
    chain: tuple[str, ...],
    reason: str,
    relative_path: str | None,
    detail: str | None = None,
) -> ValidationError:
    chain_label = " -> ".join(chain)
    path_detail = f"，相对路径 {relative_path}" if relative_path else ""
    error_detail = f"：{detail}" if detail else ""
    return ValidationError(f"压缩链 {chain_label}：{reason}{path_detail}{error_detail}")
