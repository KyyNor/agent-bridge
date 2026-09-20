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

import yaml

logger = logging.getLogger(__name__)

# 插件安装目标 profile：与 ``dsh web`` 前端一致。
PLUGIN_PROFILE = "web"
# 名单文件（包内、随版本发布）。
PLUGIN_LIST_FILENAME = "dsh-plugins.txt"
# 已安装 marker 文件（相对 DSH_HOME）。
PLUGIN_MARKER_FILENAME = "agent-bridge-plugins.txt"
# 显式声明「不构建」的原生依赖（随版本维护）：pnpm 10 默认拦截依赖的
# install/postinstall 脚本并给出 ``Ignored build scripts`` 待定告警，构建
# 与否悬而未决。node-pty 只随包附带 win32/darwin 预编译、Linux 必须走
# node-gyp 编译（需要工具链与 Node headers）；为了不让单个原生依赖的构建
# 阻塞整份插件名单，这里默认声明不构建（``allowBuilds`` map，pnpm 10.33
# 的正式键，等价于 ``ignoredBuiltDependencies``）：安装静默跳过构建、退出 0，
# 依赖 node-pty 的能力（如 dsh-better-sidebar 的终端）在运行期自行降级。
# 内网如需终端能力：移出本名单并预置 python3/make/g++ 与 Node headers。
BLOCKED_BUILDS = ("node-pty",)


def plugin_list_path() -> Path:
    return Path(__file__).parent / PLUGIN_LIST_FILENAME


def profile_dir_for(dsh_home: Path, profile: str = PLUGIN_PROFILE) -> Path:
    return Path(dsh_home) / "profiles" / profile


def plugin_marker_path(dsh_home: Path) -> Path:
    return Path(dsh_home) / PLUGIN_MARKER_FILENAME


def build_profile_install_command(dsh_binary: str) -> list[str]:
    """构造 profile 初始化命令：``<dsh> plugin --profile web install``。"""
    return [dsh_binary, "plugin", "--profile", PLUGIN_PROFILE, "install"]


def ensure_build_blocks(profile_dir: Path) -> bool:
    """在 profile 显式声明不构建 BLOCKED_BUILDS；需要变更时返回 True。

    pnpm 10 对依赖的 install/postinstall 脚本默认只警告不执行（``Ignored
    build scripts`` 待定），安装仍退出 0 但原生模块没有编译产物；显式声明
    ``allowBuilds.<pkg>: false`` 后跳过是确定行为、不再产生待定告警，插件
    安装也不会因单个原生依赖的构建失败而整体失败。合并语义：保留
    ``pnpm-workspace.yaml`` 其余键与用户已有条目，并从历史放行名单
    （``onlyBuiltDependencies``）里移除这些包，避免放行/拒绝并存；profile
    未初始化或内容未变化时返回 False 且不触盘。
    """
    path = profile_dir / "pnpm-workspace.yaml"
    if not path.exists():
        return False
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("DSH profile pnpm-workspace.yaml 读取失败 path=%s 原因=%s", path, exc)
        return False
    payload = dict(loaded) if isinstance(loaded, dict) else {}
    allow_builds = payload.get("allowBuilds")
    allow_builds = dict(allow_builds) if isinstance(allow_builds, dict) else {}
    allowed = payload.get("onlyBuiltDependencies")
    allowed_list = [str(item) for item in allowed] if isinstance(allowed, list) else []
    kept_allowed = [item for item in allowed_list if item not in BLOCKED_BUILDS]
    needs_block = [dep for dep in BLOCKED_BUILDS if allow_builds.get(dep) is not False]
    list_changed = kept_allowed != allowed_list
    if not needs_block and not list_changed:
        return False
    for dep in needs_block:
        allow_builds[dep] = False
    payload["allowBuilds"] = allow_builds
    if isinstance(allowed, list):
        if kept_allowed:
            payload["onlyBuiltDependencies"] = kept_allowed
        else:
            payload.pop("onlyBuiltDependencies", None)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    logger.info(
        "DSH profile 已声明不构建原生依赖 path=%s dependencies=%s", path, ",".join(BLOCKED_BUILDS)
    )
    return True


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
