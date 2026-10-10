"""工作台后端的会话 Cookie 命名。

放在 ``agent_workspace`` 而非代理层，避免 adapter（需要按目标端口过滤 Cookie）
反向导入 ``api.workspace_proxy`` 形成循环依赖。
"""

from __future__ import annotations

import base64
import hashlib


def dsh_session_cookie_name(authority: str) -> str:
    """DSH 浏览器会话 Cookie 名：``dsh-auth-`` + authority 的 SHA-256（base64url）。

    DSH 把会话 Cookie 绑定到请求 authority（Host:port），并用进程内 signing
    secret 签名——因此它跨进程重启后必然失效，只能用于定位自己换取的 Cookie，
    不能当作"已完成鉴权"的依据。
    """
    digest = hashlib.sha256(authority.encode("utf-8")).digest()
    return "dsh-auth-" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
