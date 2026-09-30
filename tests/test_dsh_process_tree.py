"""DSH Runtime 进程树枚举与环境标记扫描的单元测试。

用伪造的 ``/proc`` 目录（``<pid>/stat``、``<pid>/environ``）验证 Linux
路径的解析；父进程树、标记匹配、僵尸判定与精确条目匹配都在这里闭环。
"""

from __future__ import annotations

import os
from pathlib import Path

from agent_bridge.dsh import process_tree


def _write_proc_stat(proc: Path, pid: int, ppid: int, *, comm: str = "node", state: str = "S") -> None:
    entry = proc / str(pid)
    entry.mkdir(parents=True, exist_ok=True)
    # comm 可含空格与括号：解析必须取最后一个 ')' 之后的部分
    (entry / "stat").write_text(f"{pid} ({comm}) {state} {ppid} 0 0 0", encoding="ascii")


def _write_proc_environ(proc: Path, pid: int, entries: dict[str, str]) -> None:
    entry = proc / str(pid)
    entry.mkdir(parents=True, exist_ok=True)
    payload = "".join(f"{key}={value}\x00" for key, value in entries.items())
    (entry / "environ").write_bytes(payload.encode("utf-8"))


def test_runtime_mark_value_scopes_runtime_identity() -> None:
    assert process_tree.runtime_mark_value("personal", "user1") == "personal:user1"
    assert process_tree.runtime_mark_value("shared", "groupa") == "shared:groupa"


def test_runtime_mark_env_name_survives_dsh_env_scrub() -> None:
    # DSH 的 scrubbedParentEnv 剥掉 DSH_ 前缀与凭据形态（KEY/PASSWORD/
    # SECRET/TOKEN）变量：标记名必须同时避开两类规则，否则收不到子进程。
    name = process_tree.RUNTIME_MARK_ENV
    assert not name.upper().startswith("DSH_")
    for word in ("KEY", "PASSWORD", "SECRET", "TOKEN"):
        assert word not in name.upper()


def test_descendants_covers_detached_children_and_nested_tree() -> None:
    # detached 子进程换的是进程组/会话，父进程链仍在树上：100 → 101 → 102
    parent_map = {100: 1, 101: 100, 102: 101, 103: 100, 999: 1}
    found = process_tree.descendants(100, parent_map=parent_map)
    assert found == {100, 101, 102, 103}
    # 与主进程无关的进程不进集合
    assert 999 not in found


def test_descendants_of_missing_root_is_empty() -> None:
    assert process_tree.descendants(12345, parent_map={1: 0}) == set()
    assert process_tree.descendants(0, parent_map={1: 0}) == set()


def test_parent_map_from_proc_parses_stat_with_spaces_in_comm(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    _write_proc_stat(proc, 100, 1, comm="dsh web (web)")
    _write_proc_stat(proc, 101, 100, comm="bash")
    # 损坏的 stat 条目跳过、不影响其余解析
    (proc / "102").mkdir()
    (proc / "102" / "stat").write_text("garbage", encoding="ascii")
    parents = process_tree.process_parent_map(proc)
    assert parents == {100: 1, 101: 100}


def test_marked_pids_matches_full_env_entries_only(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    _write_proc_environ(proc, 100, {"AGENT_BRIDGE_DSH_RUNTIME": "personal:user1"})
    # 前缀相近的 runtime（user1 vs user12）不得误中
    _write_proc_environ(proc, 200, {"AGENT_BRIDGE_DSH_RUNTIME": "personal:user12"})
    # 同名变量但值不同的其它 runtime 不中
    _write_proc_environ(proc, 300, {"AGENT_BRIDGE_DSH_RUNTIME": "shared:groupa"})
    # 无标记的无关进程不中
    _write_proc_environ(proc, 400, {"HOME": "/tmp"})
    matched = process_tree.marked_pids("personal:user1", proc_root=proc)
    assert matched == {100}


def test_marked_pids_skips_unreadable_entries(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    _write_proc_environ(proc, 100, {"AGENT_BRIDGE_DSH_RUNTIME": "personal:user1"})
    entry = proc / "101"  # 目录存在但 environ 缺失（权限/竞态）→ 跳过
    entry.mkdir()
    matched = process_tree.marked_pids("personal:user1", proc_root=proc)
    assert matched == {100}


def test_marked_pids_without_proc_root_returns_empty(tmp_path: Path) -> None:
    # 非 Linux（无 /proc）：标记扫描退化为空集，由父进程树兜底
    assert process_tree.marked_pids("personal:user1", proc_root=tmp_path / "missing") == set()


def test_process_is_zombie_detected_from_stat(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    _write_proc_stat(proc, 100, 1, state="Z")
    _write_proc_stat(proc, 101, 1, state="S")
    assert process_tree.process_is_zombie(100, proc_root=proc) is True
    assert process_tree.process_is_zombie(101, proc_root=proc) is False
    assert process_tree.process_is_zombie(999, proc_root=proc) is False


def test_discard_own_process() -> None:
    assert process_tree.discard_own_process({1, os.getpid(), 2}) == {1, 2}
