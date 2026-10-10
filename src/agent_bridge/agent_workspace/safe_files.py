"""工作台配置文件的安全读写工具。

Agent Bridge 以 root 身份在目标 Linux 用户的 home 下读写配置（DSH 的
``--patch`` 覆盖文件、Pi 的 ``models.json`` / ``settings.json`` / ``mcp.json``）。
该目录由用户控制，用户（或 Agent 的 bash 工具）可以预先放置软链，因此：

- **写入**必须用 ``O_NOFOLLOW | O_CREAT | O_EXCL`` 打开一个**随机名**临时文件，
  再 ``os.replace`` 原子改名。固定名临时文件可被预置软链，导致 root 覆盖任意
  文件；``O_NOFOLLOW`` 额外保证最终落点本身不是软链。
- **读取**必须用 ``os.open(..., O_NOFOLLOW)``：否则软链会让 root 读取并合并
  任意文件内容，再写入一个用户可读的文件，造成数据外泄。
- 写入后立即 ``fchmod(0600)``，避免文件在 ``replace`` 之前短暂以默认权限存在。

软链一律视为非法输入并拒绝（记 warning），不静默跟随，也不删除用户的软链。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 托管配置文件的权限：仅归属用户可读写。
CONFIG_FILE_MODE = 0o600


def is_symlink(path: Path) -> bool:
    """路径是否为软链（含指向不存在目标的悬空软链）。"""
    try:
        return path.is_symlink()
    except OSError:
        return False


def read_managed_json(path: Path, *, label: str = "配置") -> dict[str, Any]:
    """安全读取 JSON 对象。

    文件缺失返回空字典（正常首次启动）；软链、权限不足或解析失败都记 warning
    并返回空字典——调用方只会重写托管条目，不会因此丢失用户配置以外的语义。
    """
    if not path.exists():
        return {}
    if is_symlink(path):
        logger.warning(
            "拒绝读取软链形式的%s（可能是攻击或误配置），按空配置处理 path=%s",
            label,
            path,
        )
        return {}
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        logger.warning("%s读取失败 path=%s 原因=%s", label, path, exc)
        return {}
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        logger.warning("%s解析失败，按空配置处理 path=%s", label, path)
        return {}
    return payload if isinstance(payload, dict) else {}


def write_managed_json_atomic(
    path: Path,
    payload: dict[str, Any],
    *,
    uid: int,
    gid: int,
    label: str = "配置",
) -> None:
    """原子写入 JSON（0600，归属目标 Linux 用户），不跟随软链。

    先写随机名临时文件（``O_EXCL`` 保证不覆盖已存在文件——攻击者无法预置抢占），
    ``fchmod`` 收紧权限后再 ``os.replace`` 改名。落点若是软链则直接拒绝：
    ``os.replace`` 会替换软链本身而非跟随它，因此不会写到软链指向的文件。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if is_symlink(path):
        logger.warning(
            "拒绝写入软链形式的%s（可能是攻击或误配置），已跳过 path=%s", label, path
        )
        return

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = path.with_name(f".{path.name}.agent-bridge-{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, CONFIG_FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _chown_quietly(tmp, uid, gid)
        os.replace(tmp, path)
    except Exception:
        with_suppressed_unlink(tmp)
        raise


def _chown_quietly(path: Path, uid: int, gid: int) -> None:
    """尽力归属；非 root 或已归属时静默跳过（开发/测试环境无权限属正常）。"""
    if os.geteuid() != 0 or uid == os.geteuid():
        return
    try:
        os.chown(path, uid, gid)
    except OSError as exc:
        logger.warning("配置 chown 失败 path=%s uid=%s 原因=%s", path, uid, exc)


def with_suppressed_unlink(path: Path) -> None:
    """删除临时文件；失败只记 warning（不掩盖原始异常）。"""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("临时文件删除失败 path=%s 原因=%s", path, exc)


def ensure_owner(path: Path, *, uid: int, gid: int) -> None:
    """root 下把文件归属目标 Linux 用户（非 root 或同用户时跳过）。"""
    if os.geteuid() != 0 or uid == os.geteuid():
        return
    try:
        os.chown(path, uid, gid)
    except OSError as exc:
        logger.warning("配置 chown 失败 path=%s uid=%s 原因=%s", path, uid, exc)


def write_text_secure(
    path: Path, text: str, *, uid: int, gid: int, label: str = "配置"
) -> bool:
    """安全写入文本文件（DSH 的 ``--patch`` 覆盖文件使用）。

    语义与 :func:`write_managed_json_atomic` 相同，只是内容是任意文本。
    返回是否实际写入。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if is_symlink(path):
        logger.warning(
            "拒绝写入软链形式的%s（可能是攻击或误配置），已跳过 path=%s", label, path
        )
        return False
    tmp = path.with_name(f".{path.name}.agent-bridge-{secrets.token_hex(8)}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, CONFIG_FILE_MODE)
    except FileExistsError:
        with_suppressed_unlink(tmp)
        return write_text_secure(path, text, uid=uid, gid=gid, label=label)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _chown_quietly(tmp, uid, gid)
        os.replace(tmp, path)
    except Exception:
        with_suppressed_unlink(tmp)
        raise
    return True


def read_text_secure(path: Path, *, label: str = "配置") -> str | None:
    """安全读取文本文件；软链或读取失败返回 None。"""
    if not path.exists():
        return None
    if is_symlink(path):
        logger.warning(
            "拒绝读取软链形式的%s（可能是攻击或误配置）path=%s", label, path
        )
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        logger.warning("%s读取失败 path=%s 原因=%s", label, path, exc)
        return None
    try:
        with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except Exception:
        logger.warning("%s读取失败 path=%s", label, path)
        return None


def unlink_if_exists(path: Path, *, label: str = "配置") -> None:
    """删除托管文件；软链只删除链接本身，不触碰其目标。"""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("%s删除失败 path=%s 原因=%s", label, path, exc)