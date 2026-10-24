"""降权后执行的文件操作 helper；输入/输出有界，拒绝目录逃逸和软链。"""
from __future__ import annotations

import errno
import json
import os
import secrets
import stat
import sys
from pathlib import PurePosixPath

from agent_bridge.agent_workspace.directory import (
    WorkspaceDirectory, open_selected_directory, snapshot_directory,
)

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_ENTRIES = 2000


def valid_filename(name: str) -> str:
    if not name or name in {'.', '..'} or '/' in name or '\\' in name or len(name.encode()) > 255:
        raise ValueError('文件名不合法')
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError('文件名不允许控制字符')
    return name


def list_directory(root: WorkspaceDirectory, relative: str, *, directories_only=False) -> dict:
    fd = open_selected_directory(root, relative)
    try:
        entries = []
        truncated = False
        with os.scandir(fd) as items:
            for entry in items:
                try:
                    info = entry.stat(follow_symlinks=False)
                    kind = ('directory' if stat.S_ISDIR(info.st_mode) else
                            'file' if stat.S_ISREG(info.st_mode) else
                            'symlink' if stat.S_ISLNK(info.st_mode) else 'other')
                    size = info.st_size
                except FileNotFoundError:
                    # 目录正被 Agent 更新：展示不可用条目，下一次刷新再读取。
                    kind, size = 'other', 0
                if directories_only and kind != 'directory':
                    continue
                if len(entries) >= MAX_ENTRIES:
                    truncated = True
                    break
                entries.append({'name': entry.name, 'kind': kind, 'size': size})
        entries.sort(key=lambda item: (item['kind'] != 'directory', item['name'].casefold()))
        return {'root': root.path, 'path': relative, 'entries': entries, 'truncated': truncated}
    finally:
        os.close(fd)


def download_file(root: WorkspaceDirectory, relative: str) -> bytes:
    path = PurePosixPath(relative)
    name = valid_filename(path.name)
    parent = open_selected_directory(root, str(path.parent))
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('只允许下载普通文件')
            if info.st_size > MAX_FILE_BYTES:
                raise ValueError('单个文件不能超过 32 MiB')
            data = source.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise ValueError('单个文件不能超过 32 MiB')
            return data
    finally:
        os.close(parent)


def upload_file(root: WorkspaceDirectory, relative: str, name: str, data: bytes) -> dict:
    valid_filename(name)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError('单个文件不能超过 32 MiB')
    parent = open_selected_directory(root, relative)
    temporary = '.agent-bridge-upload-' + secrets.token_hex(16)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, 'wb') as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        # 同名文件（含软链）存在时失败，不覆盖；完全写好后才发布。
        os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        return {'name': name, 'size': len(data)}
    finally:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


def execute(payload: dict, data: bytes = b'') -> dict | bytes:
    action = payload['action']
    if action == 'browse':
        root = snapshot_directory(payload['directory'])
        return {**list_directory(root, '', directories_only=True), 'directory': root.payload()}
    root = WorkspaceDirectory.parse(payload['root'])
    relative = payload.get('path', '')
    if action == 'list':
        return list_directory(root, relative)
    if action == 'download':
        # PurePosixPath 会规范化开头，因此在分拆文件名之前先拒绝绝对路径。
        if relative.startswith('/') or '..' in relative.split('/') or '\\' in relative:
            raise ValueError('文件路径不允许越过选定目录')
        return download_file(root, relative)
    if action == 'upload':
        return upload_file(root, relative, payload['name'], data)
    raise ValueError('未知文件操作')


def main():
    try:
        payload = json.loads(sys.argv[1])
        data = sys.stdin.buffer.read(MAX_FILE_BYTES + 1) if payload['action'] == 'upload' else b''
        result = execute(payload, data)
        if isinstance(result, bytes):
            sys.stdout.buffer.write(result)
        else:
            print(json.dumps(result, ensure_ascii=True))
    except (OSError, ValueError, KeyError) as exc:
        kind = 'invalid'
        if isinstance(exc, PermissionError):
            kind = 'denied'
        elif isinstance(exc, FileNotFoundError):
            kind = 'not_found'
        elif isinstance(exc, FileExistsError):
            kind = 'conflict'
        message = str(exc)
        if isinstance(exc, OSError) and exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            message = '目录或文件是软链或已变化，请重新选择'
        print(json.dumps({'kind': kind, 'error': message}, ensure_ascii=True), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
