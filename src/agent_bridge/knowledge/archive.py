from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArchivedFile:
    content_hash: str
    file_size: int
    archive_path: Path


class ArchiveStorage:
    def __init__(self, archive_dir: Path) -> None:
        self.archive_dir = archive_dir

    def store(self, source: Path) -> ArchivedFile:
        content_hash = self._sha256(source)
        suffix = source.suffix.lower()
        target_dir = self.archive_dir / content_hash[:2] / content_hash[2:4]
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{content_hash}{suffix}"
        if not target.exists():
            shutil.copy2(source, target)
        return ArchivedFile(
            content_hash=content_hash,
            file_size=source.stat().st_size,
            archive_path=target,
        )

    def remove(self, archive_path: Path) -> None:
        archive_path.unlink(missing_ok=True)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
