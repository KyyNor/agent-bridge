"""Tests for the service-level upload extension gate.

These assert the exact set of extensions the knowledge-base ingestion
accepts, so accidental drift (e.g. dropping legacy Office formats or
forgetting a newly added type) is caught here rather than at runtime.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_bridge.app.service import ALLOWED_EXTENSIONS, AgentBridgeService
from agent_bridge.core.domain import ValidationError


def _service() -> AgentBridgeService:
    """Build a bare instance for exercising _validate_source.

    _validate_source touches neither ``__init__`` nor instance state, so we
    bypass ``__init__`` (which expects a full collaborator graph) via
    ``__new__``. This keeps the test focused on the gate logic.
    """
    return AgentBridgeService.__new__(AgentBridgeService)


@pytest.mark.parametrize(
    "suffix",
    [
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".txt", ".md", ".markdown", ".csv", ".json",
    ],
)
def test_allowed_extensions_accepts_supported_suffix(tmp_path: Path, suffix: str) -> None:
    f = tmp_path / f"doc{suffix}"
    f.write_bytes(b"x")
    _service()._validate_source(f)  # must not raise


@pytest.mark.parametrize("suffix", [".rtf", ".odt", ".png", ".mp3", ".html", ".xml", ".zip", ""])
def test_allowed_extensions_rejects_unsupported_suffix(tmp_path: Path, suffix: str) -> None:
    name = "doc" + (suffix or "")
    f = tmp_path / name
    f.write_bytes(b"x")
    with pytest.raises(ValidationError, match="unsupported file type"):
        _service()._validate_source(f)


def test_allowed_extensions_set_is_exactly_the_documented_set() -> None:
    """Lock the public contract: add/remove here must be intentional."""
    assert ALLOWED_EXTENSIONS == {
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".txt", ".md", ".markdown", ".csv", ".json",
    }


def test_validate_source_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="does not exist"):
        _service()._validate_source(tmp_path / "nope.docx")
