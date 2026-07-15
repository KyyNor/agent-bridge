"""Helpers for safely expanding document upload archives."""
from __future__ import annotations

import shutil
import stat
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

from agent_bridge.core.domain import ValidationError


def extract_zip_documents(
    source: Path,
    destination: Path,
    allowed_extensions: set[str],
) -> list[Path]:
    """Validate and extract supported document members from a ZIP archive."""
    try:
        with ZipFile(source) as archive:
            members = archive.infolist()
            safe_members = []
            for info in members:
                normalized_name = info.filename.replace("\\", "/")
                member_path = PurePosixPath(normalized_name)
                if "\x00" in normalized_name or member_path.is_absolute() or any(
                    part == ".." for part in member_path.parts
                ) or (member_path.parts and member_path.parts[0].endswith(":")):
                    raise ValidationError(f"unsafe zip member path: {info.filename}")
                if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
                    continue
                safe_members.append((info, member_path))

            broken_member = archive.testzip()
            if broken_member is not None:
                raise ValidationError(f"invalid zip archive: {broken_member}")

            supported_members = [
                (info, member_path)
                for info, member_path in safe_members
                if member_path.suffix.lower() in allowed_extensions
                and member_path.suffix.lower() != ".zip"
            ]
            if not supported_members:
                raise ValidationError("zip archive contains no supported documents")

            extracted: list[Path] = []
            for info, member_path in sorted(supported_members, key=lambda item: item[1].as_posix()):
                target = destination.joinpath(*member_path.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as input_file, target.open("wb") as output_file:
                    shutil.copyfileobj(input_file, output_file)
                extracted.append(target)
            return extracted
    except ValidationError:
        raise
    except (BadZipFile, EOFError, OSError, RuntimeError, KeyError) as exc:
        raise ValidationError("invalid zip archive") from exc
