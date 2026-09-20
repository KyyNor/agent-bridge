"""DSH 首次初始化后的插件安装。

名单内置于包内（``agent_bridge/dsh/dsh-plugins.txt``，随版本发布）：
每行一个插件 spec（如 ``@scope/name@latest`` 或裸包名），空行与 ``#``
注释忽略；调整名单即修改该文件并随版本部署。

安装进度记录在 ``<DSH_HOME>/agent-bridge-plugins.txt``（已成功安装的
spec 列表，0600、归属目标 Linux 用户）：web 进程启动前补装名单中尚未
记录的条目，全部就位后不再触盘——插件安装实际只发生在首次初始化，以及
版本名单新增条目时的一次补装；名单删除条目不会卸载已装插件。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# 插件安装目标 profile：与 ``dsh web`` 前端一致。
PLUGIN_PROFILE = "web"
# 名单文件（包内、随版本发布）。
PLUGIN_LIST_FILENAME = "dsh-plugins.txt"
# 已安装 marker 文件（相对 DSH_HOME）。
PLUGIN_MARKER_FILENAME = "agent-bridge-plugins.txt"


def plugin_list_path() -> Path:
    return Path(__file__).parent / PLUGIN_LIST_FILENAME


def plugin_marker_path(dsh_home: Path) -> Path:
    return Path(dsh_home) / PLUGIN_MARKER_FILENAME


def parse_plugin_list(text: str) -> list[str]:
    """解析名单文本：去空白、忽略 ``#`` 注释与空行，保序去重。"""
    specs: list[str] = []
    for raw_line in text.splitlines():
        spec = raw_line.split("#", 1)[0].strip()
        if spec and spec not in specs:
            specs.append(spec)
    return specs


def read_plugin_list() -> list[str]:
    """读取包内插件名单；文件缺失、为空或不可读时返回空列表（不安装）。"""
    path = plugin_list_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warning("DSH 插件名单读取失败 path=%s 原因=%s", path, exc)
        return []
    return parse_plugin_list(text)


def read_installed_specs(dsh_home: Path) -> list[str]:
    """读取已成功安装的 spec 列表；marker 缺失视为空。"""
    path = plugin_marker_path(dsh_home)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return parse_plugin_list(text)


def write_installed_specs(dsh_home: Path, specs: list[str]) -> Path:
    """覆写已安装 marker（调用方负责归属与权限）。"""
    path = plugin_marker_path(dsh_home)
    path.write_text("\n".join(specs) + ("\n" if specs else ""), encoding="utf-8")
    return path


def build_install_command(dsh_binary: str, spec: str) -> list[str]:
    """构造单个插件的安装命令：``<dsh> plugin --profile web add <spec>``。"""
    return [dsh_binary, "plugin", "--profile", PLUGIN_PROFILE, "add", spec]


def ensure_marker_owner(path: Path, *, uid: int, gid: int) -> None:
    """root 下把 marker 归属目标 Linux 用户（非 root 或同用户时跳过）。"""
    if os.geteuid() != 0 or uid == os.geteuid():
        return
    try:
        os.chown(path, uid, gid)
    except OSError as exc:
        logger.warning("DSH 插件 marker chown 失败 path=%s uid=%s 原因=%s", path, uid, exc)
