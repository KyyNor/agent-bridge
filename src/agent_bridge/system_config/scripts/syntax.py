"""Python syntax checking for managed scripts.

Uses :func:`compile` to parse the source without executing it. No third-party
dependencies. Pure functions — safe to call from anywhere.
"""

from __future__ import annotations

from typing import Any


def check_python_syntax(code: str) -> dict[str, Any]:
    """Parse ``code`` with :func:`compile` and report any syntax errors.

    Never raises. Returns a dict::

        {"ok": bool, "errors": [{"line", "col", "msg", "text"}]}

    The result mirrors the "warn but allow save" policy: callers decide whether
    to reject; this function only reports.
    """
    if not isinstance(code, str):
        return {"ok": False, "errors": [{"line": None, "col": None, "msg": "code must be a string", "text": None}]}
    source = code
    # compile() needs a trailing newline for clean parsing of the last line.
    if source and not source.endswith("\n"):
        source = source + "\n"
    try:
        compile(source, "<script>", "exec")
    except SyntaxError as exc:
        error = {
            "line": exc.lineno,
            "col": exc.offset,
            "msg": exc.msg,
            "text": (exc.text.rstrip("\n") if exc.text else None),
        }
        return {"ok": False, "errors": [error]}
    except ValueError as exc:
        # compile() raises ValueError for NUL bytes or other source-level issues.
        return {"ok": False, "errors": [{"line": None, "col": None, "msg": str(exc), "text": None}]}
    return {"ok": True, "errors": []}
