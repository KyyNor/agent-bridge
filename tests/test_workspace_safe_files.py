"""工作台配置文件的软链防护测试。

Agent Bridge 以 root 身份在目标 Linux 用户的 home 下读写配置，该目录由用户
控制。固定名临时文件与软链都可能造成**任意文件覆盖**或**数据外泄**，因此
写入必须用随机名 + ``O_NOFOLLOW|O_EXCL``，读取必须拒绝软链。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_bridge.agent_workspace import safe_files


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """构造含软链的配置目录，模拟用户预先布置的陷阱。"""
    secret = tmp_path / "target.json"
    secret.write_text("ORIGINAL-SECRET", encoding="utf-8")
    (tmp_path / "models.json").symlink_to(secret)
    return tmp_path


def test_read_refuses_symlink(workdir: Path) -> None:
    """读取软链必须被拒绝：否则 root 读取的文件内容会被写进用户可读的文件。"""
    got = safe_files.read_managed_json(workdir / "models.json", label="测试配置")
    assert got == {}
    assert (workdir / "target.json").read_text(encoding="utf-8") == "ORIGINAL-SECRET"


def test_write_refuses_symlink_and_leaves_target_intact(workdir: Path) -> None:
    """写入软链必须被跳过：否则 root 会覆盖软链指向的任意文件。"""
    safe_files.write_managed_json_atomic(
        workdir / "models.json", {"providers": {"x": {}}}, uid=os.geteuid(),
        gid=os.getegid(), label="测试配置",
    )
    assert (workdir / "target.json").read_text(encoding="utf-8") == "ORIGINAL-SECRET"
    # 软链本身不被替换或删除（不擅自修改用户的文件）
    assert (workdir / "models.json").is_symlink()


def test_normal_file_written_with_0600(workdir: Path) -> None:
    """正常路径仍可写入，且权限收紧到 0600。"""
    safe_files.write_managed_json_atomic(
        workdir / "models.json.new", {"ok": True}, uid=os.geteuid(),
        gid=os.getegid(), label="测试配置",
    )
    target = workdir / "models.json.new"
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert target.stat().st_mode & 0o777 == 0o600


def test_write_replaces_existing_regular_file(workdir: Path) -> None:
    """已存在的正常文件可被原子替换（这是配置更新的常规路径）。"""
    path = workdir / "settings.json"
    path.write_text('{"theme":"light"}', encoding="utf-8")
    safe_files.write_managed_json_atomic(
        path, {"theme": "dark"}, uid=os.geteuid(), gid=os.getegid(), label="测试配置"
    )
    assert json.loads(path.read_text(encoding="utf-8")) == {"theme": "dark"}
    # 不留临时文件
    assert not [p for p in workdir.iterdir() if p.name.endswith(".tmp")]


def test_text_helpers_refuse_symlink(workdir: Path) -> None:
    """DSH 覆盖文件走同一套保护。"""
    path = workdir / "overlay.yml"
    path.symlink_to(workdir / "target.json")
    assert safe_files.read_text_secure(path, label="测试") is None
    assert safe_files.write_text_secure(
        path, "data: 1", uid=os.geteuid(), gid=os.getegid(), label="测试"
    ) is False
    assert (workdir / "target.json").read_text(encoding="utf-8") == "ORIGINAL-SECRET"


def test_text_helper_creates_file_with_0600(workdir: Path) -> None:
    path = workdir / "agent-bridge-model.patch.yml"
    assert safe_files.write_text_secure(
        path, "llm-pi-ai: {}\n", uid=os.geteuid(), gid=os.getegid(), label="测试"
    ) is True
    assert path.read_text(encoding="utf-8") == "llm-pi-ai: {}\n"
    assert path.stat().st_mode & 0o777 == 0o600


def test_unlink_removes_symlink_not_target(workdir: Path) -> None:
    """删除托管文件时只删链接本身，不触碰其目标。"""
    link = workdir / "models.json"
    safe_files.unlink_if_exists(link, label="测试")
    assert not link.exists()
    assert (workdir / "target.json").read_text(encoding="utf-8") == "ORIGINAL-SECRET"


def test_corrupt_json_is_treated_as_empty(workdir: Path) -> None:
    """损坏内容按空配置处理，不抛出中断启动。"""
    path = workdir / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    assert safe_files.read_managed_json(path, label="测试") == {}


def test_non_dict_json_is_treated_as_empty(workdir: Path) -> None:
    path = workdir / "list.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert safe_files.read_managed_json(path, label="测试") == {}


def test_missing_file_returns_empty(workdir: Path) -> None:
    assert safe_files.read_managed_json(workdir / "absent.json", label="测试") == {}