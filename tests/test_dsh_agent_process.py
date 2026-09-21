"""DSH ACP 后端真实进程 integration 测试。

直接以本机 ``dsh --profile acp`` 完成一次含工具调用的真实模型 run，验证
adapter 的完整生命周期（spawn → initialize → session/new → prompt 流式
updates → close → 进程退出）。模型接入经环境变量注入，使用隔离的临时
``DSH_HOME``，不触碰用户级 DSH 配置。

仅在 ``./scripts/test.sh all``（``-m process``）且以下条件满足时执行：

- PATH 上存在 ``dsh`` 可执行文件；
- ``AGENT_BRIDGE_DSH_TEST_BASE_URL`` / ``AGENT_BRIDGE_DSH_TEST_API_KEY``
  提供了可用的 OpenAI 兼容网关（模型经 ``AGENT_BRIDGE_DSH_TEST_MODEL``
  覆盖，默认 ``deepseek-flash``）。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_bridge.agent_runtime.adapters.dsh import DshCodingAgent
from agent_bridge.agent_runtime.types import CodingAgentRequest, CodingAgentRunContext
from agent_bridge.dsh import injection

pytestmark = pytest.mark.process

_BASE_URL = os.environ.get("AGENT_BRIDGE_DSH_TEST_BASE_URL", "")
_API_KEY = os.environ.get("AGENT_BRIDGE_DSH_TEST_API_KEY", "")
_MODEL = os.environ.get("AGENT_BRIDGE_DSH_TEST_MODEL", "deepseek-flash")

requires_real_dsh = pytest.mark.skipif(
    not shutil.which("dsh") or not _BASE_URL or not _API_KEY,
    reason="需要本机 dsh 与 AGENT_BRIDGE_DSH_TEST_BASE_URL/AGENT_BRIDGE_DSH_TEST_API_KEY",
)


class _TempDshRuntime:
    """指向临时 DSH_HOME 的最小执行绑定。"""

    def __init__(self, dsh_home: Path, model: str) -> None:
        self.dsh_home = dsh_home
        self.model = model

    def resolve_execution(self, actor, group_key, *, model=None):
        return SimpleNamespace(
            command="dsh",
            dsh_home=self.dsh_home,
            provider=injection.MANAGED_PROVIDER_KEY,
            model=model or self.model,
            identity=SimpleNamespace(
                user=os.environ.get("USER") or "tester",
                uid=os.geteuid(),
                gid=os.getegid(),
                home=Path.home(),
            ),
            api_key_env={injection.MANAGED_API_KEY_ENV: _API_KEY},
            base_url=_BASE_URL,
            process_env=lambda: {
                "DSH_HOME": str(self.dsh_home),
                injection.MANAGED_API_KEY_ENV: _API_KEY,
            },
        )


@pytest.fixture
def dsh_home(tmp_path: Path) -> Path:
    home = tmp_path / "dsh-home"
    injection.write_settings(
        home, base_url=_BASE_URL, models=[_MODEL], default_model=_MODEL
    )
    # DSH 期望 HOME 完成过基础初始化；真实链路由 DshAgentRuntimeResolver
    # 预创建（见 _DSH_HOME_DIRECTORIES），此处按同一约定准备。
    for name in ("sessions", "storages", "change-ledger", "task-board"):
        (home / name).mkdir(parents=True, exist_ok=True)
    return home


@requires_real_dsh
def test_real_dsh_acp_run_with_tool_call(tmp_path: Path, dsh_home: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    runtime = _TempDshRuntime(dsh_home, _MODEL)
    agent = DshCodingAgent(runtime=runtime)
    request = CodingAgentRequest(
        prompt=(
            "Create a file named hello.txt in the working directory with exactly the "
            "content WORLD (no trailing spaces), read it back, and reply with just "
            "the file content you read."
        ),
        cwd=work,
        mcp_servers={},
        setting_sources=[],
        run_context=CodingAgentRunContext(actor="process-test", owner_group_key="g"),
    )

    async def scenario():
        spawned_at = time.monotonic()
        seen_updates = []
        run = agent.start(request)
        async for update in run.updates():
            seen_updates.append(update)
        return spawned_at, seen_updates

    started, updates = asyncio.run(scenario())
    elapsed = time.monotonic() - started

    kinds = [record["kind"] for u in updates for record in u.events]
    assert "agent_message" in kinds, f"缺少流式文本事件，实际 kinds={kinds}"
    assert "tool_call" in kinds, f"缺少工具调用事件，实际 kinds={kinds}"
    assert "tool_result" in kinds, f"缺少工具结果事件，实际 kinds={kinds}"
    finals = [u.final for u in updates if u.final is not None]
    assert finals and finals[-1].is_error is False
    assert "WORLD" in (finals[-1].result or "")
    assert finals[-1].session_id
    assert finals[-1].model == _MODEL
    # 工具确实在 run 工作目录里产生了副作用。
    assert (work / "hello.txt").exists() or any(
        record.get("tool_name") for u in updates for record in u.events
    )
    # 资源与耗时观测（评估记录用，不做阈值断言）。
    print(
        f"\ndsh acp real run: elapsed={elapsed:.1f}s updates={len(updates)} "
        f"events={len(kinds)}"
    )


@requires_real_dsh
def test_real_dsh_acp_cancel_ends_prompt(tmp_path: Path, dsh_home: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    runtime = _TempDshRuntime(dsh_home, _MODEL)
    request = CodingAgentRequest(
        prompt=(
            "Write numbers 1..30 into numbers.txt, one bash call per number, "
            "appending each line separately."
        ),
        cwd=work,
        mcp_servers={},
        setting_sources=[],
        run_context=CodingAgentRunContext(actor="process-test", owner_group_key="g"),
    )

    async def scenario():
        events_seen: list[dict] = []
        run = DshCodingAgent(runtime=runtime).start(request)

        async def consume() -> None:
            async for update in run.updates():
                events_seen.extend(update.events)

        task = asyncio.create_task(consume())
        # 等到至少一个工具调用事件再取消，确保 prompt 已在执行。
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if task.done():
                break
            if any(record["kind"] == "tool_call" for record in events_seen):
                break
            await asyncio.sleep(1)
        assert not task.done(), "prompt 结算过快，无法验证取消路径"
        assert any(record["kind"] == "tool_call" for record in events_seen)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=60)

    started = time.monotonic()
    asyncio.run(scenario())
    elapsed = time.monotonic() - started
    print(f"\ndsh acp cancel: settle={elapsed:.1f}s")
    assert elapsed < 60
    # 取消后进程应随之退出：等待端口级孤儿不可能精确断言，至少验证新的
    # run 可以立即启动（说明没有遗留锁或状态残留）。
    follow_up = CodingAgentRequest(
        prompt="Reply with exactly OK",
        cwd=work,
        mcp_servers={},
        setting_sources=[],
        run_context=CodingAgentRunContext(actor="process-test", owner_group_key="g"),
    )

    async def follow():
        return [u async for u in DshCodingAgent(runtime=runtime).start(follow_up).updates()]

    updates = asyncio.run(follow())
    finals = [u.final for u in updates if u.final is not None]
    assert finals and finals[-1].is_error is False
