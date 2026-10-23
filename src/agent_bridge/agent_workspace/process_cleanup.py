"""公共进程回收：后代树、环境标记孤儿与 SIGTERM→SIGKILL 升级。"""
from __future__ import annotations

import logging
import os
import signal
import time
from typing import Any

from agent_bridge.agent_workspace import process_tree

logger = logging.getLogger(__name__)


class RuntimeProcessController:
    """Web runtime 与 PTY bridge 共用的完整进程集合回收。"""

    def __init__(self, *, mark_env: str, display_name: str):
        self.process_mark_env = mark_env
        self.process_display_name = display_name
        self._processes: dict[int, Any] = {}
        self._process_tree = process_tree

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _terminate_pid(
        self, pid: int, *, grace_seconds: float, mark: str | None = None
    ) -> bool:
        """终止一个 runtime：主进程、全部后代与带标记的孤儿一起回收。"""
        targets = self._collect_runtime_pids(pid, mark)
        if not targets:
            return False
        for target in sorted(targets):
            self._signal_process(target, signal.SIGTERM)
        self._wait_targets_exit(targets, grace_seconds=grace_seconds)
        alive = self._alive_targets(targets)
        if alive:
            alive |= self._collect_runtime_pids(pid, mark)
            logger.warning(
                "%s Runtime 优雅退出超时，升级为 SIGKILL pid=%s 存活=%d pids=%s",
                self.process_display_name,
                pid,
                len(alive),
                sorted(alive),
            )
            for target in sorted(alive):
                self._signal_process(target, signal.SIGKILL)
            self._wait_targets_exit(alive, grace_seconds=grace_seconds)
            remaining = self._alive_targets(alive)
            if remaining:
                logger.warning(
                    "%s Runtime SIGKILL 后仍有存活进程 pid=%s pids=%s（请人工排查）",
                    self.process_display_name,
                    pid,
                    sorted(remaining),
                )
        self._processes.pop(pid, None)
        return True

    def _collect_runtime_pids(self, pid: int, mark: str | None) -> set[int]:
        """收集一个 runtime 的全部存活进程：主进程后代树 + 环境标记命中的孤儿。"""
        targets: set[int] = set()
        if pid > 0 and self._pid_alive(pid):
            tree = self._process_tree.descendants(pid)
            targets |= tree
            if not tree:
                targets.add(pid)
        if mark:
            # 必须传入该后端的标记环境变量名：各 Agent 的标记值格式相同
            # （scope:runtime_key），用错变量名会误杀另一种 Agent 的进程，
            # 且回收不到本 Agent 自己的 detached 孤儿。
            targets |= self._process_tree.marked_pids(
                mark, mark_env=self.process_mark_env
            )
        targets = self._process_tree.discard_own_process(targets)
        return {target for target in targets if self._pid_alive(target)}

    def _alive_targets(self, pids: set[int]) -> set[int]:
        """仍在运行的目标；僵尸视为已退出，避免宽限期被僵尸进程拖满。"""
        return {
            pid
            for pid in pids
            if self._pid_alive(pid) and not self._process_tree.process_is_zombie(pid)
        }

    def _wait_targets_exit(self, pids: set[int], *, grace_seconds: float) -> None:
        """等待目标集合退出：本进程启动的句柄先 wait() 收尸，其余轮询存活。"""
        deadline = time.monotonic() + max(0.0, grace_seconds)
        for pid in sorted(pids):
            process = self._processes.get(pid)
            if process is None:
                continue
            try:
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except Exception:
                pass
        while time.monotonic() < deadline:
            if not self._alive_targets(pids):
                return
            time.sleep(0.1)

    @staticmethod
    def _signal_process(pid: int, sig: int) -> None:
        try:
            process_group_id = os.getpgid(pid)
        except OSError:
            process_group_id = pid
        try:
            if process_group_id == os.getpgrp():
                # 子进程尚未完成 setsid 时不能向 bridge/平台自己的进程组发信号。
                os.kill(pid, sig)
            else:
                os.killpg(process_group_id, sig)
        except ProcessLookupError:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        except OSError:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass

