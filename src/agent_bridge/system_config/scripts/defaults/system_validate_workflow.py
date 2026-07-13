from __future__ import annotations

from agent_bridge_runtime import execute


def main(envelope):
    response = execute(
        "built-in",
        "validate_workflow",
        {"workflow": envelope["script_params"]["workflow"]},
    )
    if not isinstance(response, dict) or not isinstance(response.get("result"), dict):
        raise RuntimeError("validate_workflow returned an invalid response")
    return response["result"]
