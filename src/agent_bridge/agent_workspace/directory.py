"""选定工作目录的身份快照与基于目录 fd 的路径约束。"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkspaceDirectory:
    path: str
    device: int
    inode: int

    def payload(self) -> dict:
        return asdict(self)

    @classmethod
    def parse(cls, payload: dict) -> WorkspaceDirectory:
        return cls(path=str(payload['path']), device=int(payload['device']), inode=int(payload['inode']))


def open_absolute_directory(path: str) -> int:
    """逐段拒绝软链，避免校验后目录被替换为软链。调用方负责关闭 fd。"""
    if not path.startswith('/') or '\x00' in path:
        raise ValueError('目录必须为绝对路径')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for segment in path.split('/'):
            if not segment or segment == '.':
                continue
            if segment == '..':
                raise ValueError('目录不允许包含上级路径')
            child = os.open(segment, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_selected_directory(root: WorkspaceDirectory, relative: str = '') -> int:
    fd = open_absolute_directory(root.path)
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != (root.device, root.inode):
            raise ValueError('启动目录已被替换，请重新选择目录')
        if relative.startswith('/') or '\\' in relative or '\x00' in relative:
            raise ValueError('文件路径必须在选定目录内')
        for segment in relative.split('/'):
            if not segment or segment == '.':
                continue
            if segment == '..':
                raise ValueError('文件路径不允许越过选定目录')
            child = os.open(segment, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def snapshot_directory(path: str) -> WorkspaceDirectory:
    canonical = str(Path(path).resolve(strict=True))
    fd = open_absolute_directory(canonical)
    try:
        info = os.fstat(fd)
        return WorkspaceDirectory(canonical, info.st_dev, info.st_ino)
    finally:
        os.close(fd)
