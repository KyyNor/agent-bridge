"""Helpers for safely expanding document upload archives."""
from __future__ import annotations

import shutil
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

from agent_bridge.app.document_paths import normalize_relative_document_path
from agent_bridge.core.domain import ValidationError


@dataclass(frozen=True)
class ExtractedDocument:
    path: Path
    relative_path: str


def extract_zip_documents(
    source: Path,
    destination: Path,
    allowed_extensions: set[str],
) -> list[ExtractedDocument]:
    """Validate and extract supported document members from a ZIP archive."""
    try:
        with ZipFile(source) as archive:
            members = archive.infolist()
            safe_members = []
            for info in members:
                normalized_name = info.filename.replace("\\", "/")
                validation_name = normalized_name.rstrip("/")
                if not validation_name:
                    if info.is_dir():
                        continue
                    raise ValidationError(f"unsafe zip member path: {info.filename}")
                try:
                    relative_path = normalize_relative_document_path(validation_name)
                except ValidationError as exc:
                    raise ValidationError(f"unsafe zip member path: {info.filename}") from exc
                member_path = PurePosixPath(relative_path)
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise ValidationError(f"symbolic link zip member is not allowed: {info.filename}")
                if not info.is_dir() and member_path.suffix.lower() == ".zip":
                    raise ValidationError(f"nested zip member is not allowed: {info.filename}")
                if info.is_dir():
                    continue
                safe_members.append((info, member_path, relative_path))

            broken_member = archive.testzip()
            if broken_member is not None:
                raise ValidationError(f"invalid zip archive: {broken_member}")

            supported_members = [
                (info, member_path, relative_path)
                for info, member_path, relative_path in safe_members
                if member_path.suffix.lower() in allowed_extensions
            ]
            if not supported_members:
                raise ValidationError("zip archive contains no supported documents")

            extracted: list[ExtractedDocument] = []
            for info, member_path, relative_path in sorted(supported_members, key=lambda item: item[2]):
                target = destination.joinpath(*member_path.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as input_file, target.open("wb") as output_file:
                    shutil.copyfileobj(input_file, output_file)
                extracted.append(ExtractedDocument(path=target, relative_path=relative_path))
            return extracted
    except ValidationError:
        raise
    except (BadZipFile, EOFError, OSError, RuntimeError, KeyError) as exc:
        raise ValidationError("invalid zip archive") from exc
