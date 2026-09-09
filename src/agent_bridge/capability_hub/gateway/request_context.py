"""请求级工作流 capability token 的跨模块透传。

gateway 在校验出工作流运行 capability 后把 token 写入此处的请求级
ContextVar；builtin provider（``run_script``）在派生脚本子进程前读取并
透传，使脚本内回调能力执行端点时仍能绑定运行时归属组作用域。独立成
模块是为了让 ``sources/builtin`` 复用而不反向 import ``gateway.metamcp``
形成循环依赖。
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_request_capability_token: ContextVar[str | None] = ContextVar(
    "_request_capability_token", default=None
)


def set_request_capability_token(token: str | None) -> Token:
    """记录当前请求校验通过的工作流运行 capability token。"""
    return _request_capability_token.set(token or None)


def reset_request_capability_token(token: Token) -> None:
    _request_capability_token.reset(token)


def current_capability_token() -> str | None:
    """当前请求的工作流运行 capability token；未携带或未校验时为 None。"""
    return _request_capability_token.get()
