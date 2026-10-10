"""Pi 工作台依赖的版本清单与默认运行配置。

pi 官方没有 Web 服务模式，工作台界面由社区实现 ``@agegr/pi-web`` 提供。
Agent Bridge 只依赖其公开启动契约（CLI 参数 + 环境变量），不 fork、不打
补丁；两个包**成对锁定精确版本**，随 Agent Bridge 版本发布，升级 = 在本文件
改版本号。

版本钉死的原因：pi-web 直接依赖官方 ``@earendil-works/*``，其 API/页面随
minor 版本变化；锁版本保证各部署装出一致的可用组合，也便于整体替换实现。

部署依赖：Node ≥ 22.19（pi-web 的 ``engines`` 声明）；内置终端依赖
``node-pty``，0.11.0 自带 linux-x64/arm64 预编译二进制，无需本地工具链。
"""

from __future__ import annotations

import re
from pathlib import Path

# 包内清单文件：每行一个 ``<包名>@<精确版本>``，``#`` 注释。
VERSION_MANIFEST_FILENAME = "pi-versions.txt"
_PACKAGE_LINE = re.compile(r"^(?P<name>@[A-Za-z0-9._-]+/[A-Za-z0-9._-]+|[A-Za-z0-9._-]+)@(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?)$")


def manifest_path() -> Path:
    return Path(__file__).resolve().parent / VERSION_MANIFEST_FILENAME


def read_version_manifest() -> list[str]:
    """读取钉死版本的包清单；行必须是精确版本，不允许范围或 tag。"""
    path = manifest_path()
    specs: list[str] = []
    if not path.exists():
        return specs
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not _PACKAGE_LINE.fullmatch(line):
            raise ValueError(
                f"Pi 版本清单必须使用精确版本（name@x.y.z）：{line!r}（{path}）"
            )
        specs.append(line)
    return specs


# pi-web 的 agent 会话空闲回收（毫秒）。注意：这是 **pi 会话**级回收，与我们
# 的进程级空闲回收（按 runtime last_access_at）并存，二者独立生效。
SESSION_IDLE_TIMEOUT_MS = 30 * 60 * 1000

# 默认启动命令模板：``{port}`` 由 runtime 服务替换为动态分配的 127.0.0.1 端口。
# ``--no-open`` 关闭浏览器自启（工作台经站内反向代理访问）。
DEFAULT_PI_WEB_COMMAND = "pi-web --no-open --port {port} --hostname 127.0.0.1"
# 命令模板支持的全部占位符。
COMMAND_PLACEHOLDERS = ("port",)


def normalize_pi_web_command(value: str) -> str:
    """校验并归一化 pi-web 启动命令模板；只支持 ``{port}`` 占位符。

    模板会被 ``shlex`` 拆分后直接执行，因此拒绝换行与未知占位符（未知占位符
    在渲染时会抛 KeyError，此处提前给出可读报错）。
    """
    command = str(value or "").strip() or DEFAULT_PI_WEB_COMMAND
    if not command or "\n" in command or "\r" in command:
        from agent_bridge.core.domain import ValidationError

        raise ValidationError("Pi 启动命令不能为空，且不能包含换行")
    if "{" in command:
        unknown = [
            name
            for name in re.findall(r"\{([^{}]*)\}", command)
            if name not in COMMAND_PLACEHOLDERS
        ]
        if unknown:
            from agent_bridge.core.domain import ValidationError

            raise ValidationError(
                f"Pi 启动命令模板只支持 {{port}} 占位符，"
                f"检测到：{'、'.join(sorted(set(unknown)))}"
            )
    return command