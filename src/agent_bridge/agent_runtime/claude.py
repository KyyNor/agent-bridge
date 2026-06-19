from __future__ import annotations

import json
from pathlib import Path


def claude_settings_env(settings_path: Path | None = None) -> dict[str, str]:
    path = settings_path or (Path.home() / ".claude" / "settings.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    env = raw.get("env")
    if not isinstance(env, dict):
        return {}
    return {str(key): str(value) for key, value in env.items() if value is not None}
