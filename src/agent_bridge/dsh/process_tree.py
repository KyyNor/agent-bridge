"""DSH Runtime 进程树枚举与存活判定。

DSH 的 ``dsh-subprocess-local`` 以 ``detached: true``（POSIX 上等价于
setsid）启动它的全部子进程——bash 工具、终端、agent 会话各自落在独立
进程组/会话里，对 ``dsh web`` 主进程组发的信号覆盖不到它们，主进程退出
后它们也不会随之结束。回收一个 runtime 需要两路枚举互补：

1. **父进程树**：从主进程 pid 出发按 PPID 枚举全部后代，覆盖 detached
   但父链仍在的子进程（收集必须在发信号之前完成，否则中间父进程先退
   出、后代被 reparent 到 init 后就无法再从树上找到）；
2. **环境标记**：启动 ``dsh web`` 时注入
   ``AGENT_BRIDGE_DSH_RUNTIME=<scope>:<runtime_key>``，DSH 的
   ``scrubbedParentEnv`` 只剥 ``DSH_`` 前缀与凭据形态（KEY/PASSWORD/
   SECRET/TOKEN）的变量，本标记会原样继承给全部子进程；扫描
   ``/proc/<pid>/environ`` 即可定位已脱离父链的孤儿。

``/proc`` 只在 Linux 存在：生产部署为 Linux，走 /proc 精确枚举；macOS
等开发环境回退到 ``ps`` 父进程表，标记扫描不可用（返回空集），由父进程
树兜底。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# runtime 进程标记的环境变量名。命名约束：不得以 ``DSH_`` 开头、不得包含
# KEY/PASSWORD/SECRET/TOKEN（大小写不敏感），否则会被 DSH 的
# scrubbedParentEnv 从子进程环境中剥掉。
RUNTIME_MARK_ENV = "AGENT_BRIDGE_DSH_RUNTIME"

_PS_TIMEOUT_SECONDS = 10.0


def runtime_mark_value(scope: str, runtime_key: str) -> str:
    """构造 runtime 进程标记值；scope + runtime key 唯一确定一个 runtime。"""
    return f"{scope}:{runtime_key}"


def process_parent_map(proc_root: Path | None = None) -> dict[int, int]:
    """返回当前系统全部进程的 ``{pid: ppid}``（尽力而为，失败返回空表）。"""
    if proc_root is not None:
        return _parent_map_from_proc(proc_root)
    default_root = Path("/proc")
    if default_root.is_dir():
        return _parent_map_from_proc(default_root)
    return _parent_map_from_ps()


def _parent_map_from_proc(proc_root: Path) -> dict[int, int]:
    parents: dict[int, int] = {}
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return parents
    for entry in entries:
        if not entry.name.isdigit():
            continue
        ppid = _ppid_from_stat(entry / "stat")
        if ppid is not None:
            parents[int(entry.name)] = ppid
    return parents


def _ppid_from_stat(stat_path: Path) -> int | None:
    """解析 ``/proc/<pid>/stat`` 的 PPID；comm 字段可含空格与括号。"""
    try:
        text = stat_path.read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    tail = text.rpartition(")")[2].split()
    if len(tail) < 2:
        return None
    try:
        return int(tail[1])
    except ValueError:
        return None


def _parent_map_from_ps() -> dict[int, int]:
    """无 /proc 的平台（macOS 等）用 ``ps`` 构建父进程表。"""
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,ppid="],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    parents: dict[int, int] = {}
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            parents[int(parts[0])] = int(parts[1])
        except ValueError:
            continue
    return parents


def descendants(root_pid: int, *, parent_map: dict[int, int] | None = None) -> set[int]:
    """枚举 ``root_pid`` 及其全部后代 pid（含 root 自身，不含脱离父链的孤儿）。

    root 不在进程表（已退出或快照竞态）时返回空集。
    """
    parents = process_parent_map() if parent_map is None else parent_map
    if root_pid not in parents:
        return set()
    children: dict[int, list[int]] = {}
    for pid, ppid in parents.items():
        children.setdefault(ppid, []).append(pid)
    found = {root_pid}
    queue = [root_pid]
    while queue:
        for child in children.get(queue.pop(), ()):
            if child not in found:
                found.add(child)
                queue.append(child)
    return found


def marked_pids(mark: str, *, proc_root: Path | None = None) -> set[int]:
    """扫描 ``/proc/<pid>/environ`` 匹配 runtime 标记的进程集合。

    按完整 ``NAME=value\\0`` 条目匹配，避免 ``user1`` 误中 ``user12``；
    非 Linux（无 /proc）返回空集。读取失败（权限、进程退出竞态）的条目
    静默跳过——父进程树枚举仍在，缺一两个条目只降低覆盖率、不误杀。
    """
    root = proc_root if proc_root is not None else _default_proc_root()
    if root is None:
        return set()
    needle = f"{RUNTIME_MARK_ENV}={mark}".encode("utf-8") + b"\x00"
    matched: set[int] = set()
    try:
        entries = list(root.iterdir())
    except OSError:
        return matched
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            environ = (entry / "environ").read_bytes()
        except OSError:
            continue
        if needle in environ:
            matched.add(int(entry.name))
    return matched


def _default_proc_root() -> Path | None:
    root = Path("/proc")
    return root if root.is_dir() else None


def process_is_zombie(pid: int, *, proc_root: Path | None = None) -> bool:
    """判断 pid 是否为僵尸（已终止、等待父进程收尸）。

    僵尸进程对 ``kill(pid, 0)`` 仍然可见，必须排除出“存活”判定，否则
    优雅退出等待会被永不消失的僵尸拖满整个宽限期。非 Linux 平台无法
    低成本判定，返回 False（本进程亲生子进程由句柄 wait() 收尸）。
    """
    root = proc_root if proc_root is not None else _default_proc_root()
    if root is None:
        return False
    try:
        text = (root / str(pid) / "stat").read_text(encoding="ascii", errors="replace")
    except OSError:
        return False
    tail = text.rpartition(")")[2].split()
    return bool(tail) and tail[0] == "Z"


def discard_own_process(pids: set[int]) -> set[int]:
    """剔除 Agent Bridge 自身进程，防止极端场景下误杀自己。"""
    return {pid for pid in pids if pid != os.getpid()}
