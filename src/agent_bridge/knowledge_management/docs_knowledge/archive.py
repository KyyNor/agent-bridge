"""文档上传的内容寻址归档存储。

本模块是上传热路径(每次文档上传都会调用一次 ``store``),因此这里只用 DEBUG,
不逐次打 INFO,避免在批量同步时产生日志噪音。
"""
from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArchivedFile:
    content_hash: str
    file_size: int
    archive_path: Path


class ArchiveStorage:
    """基于 sha256 内容寻址的归档存储。

    目录布局按 hash 前两段分桶(``<ab>/<cd>/<fullhash><suffix>``),相同内容只落盘一份。
    """

    def __init__(self, archive_dir: Path) -> None:
        self.archive_dir = archive_dir

    def store(self, source: Path) -> ArchivedFile:
        """把源文件拷进归档,返回内容寻址结果。

        命中已存在目标(同 hash)时跳过拷贝实现去重。拷贝失败由调用方捕获;
        本函数不吞异常,以保留上层统一的错误处理。
        """
        content_hash = self._sha256(source)
        suffix = source.suffix.lower()
        target_dir = self.archive_dir / content_hash[:2] / content_hash[2:4]
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{content_hash}{suffix}"
        if not target.exists():
            # 热路径:用 DEBUG 避免批量同步时刷屏
            logger.debug("归档写入 sha256=%s size=%s", content_hash, source.stat().st_size)
            shutil.copy2(source, target)
        else:
            logger.debug("归档命中已存在 sha256=%s,跳过拷贝", content_hash)
        return ArchivedFile(
            content_hash=content_hash,
            file_size=source.stat().st_size,
            archive_path=target,
        )

    def remove(self, archive_path: Path) -> None:
        archive_path.unlink(missing_ok=True)

    @staticmethod
    def _sha256(path: Path) -> str:
        """流式计算文件 sha256,作为内容寻址的唯一键(支持大文件)。"""
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
