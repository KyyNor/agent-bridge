"""Tests for the LibreOffice (soffice) legacy-Office pre-converter."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from agent_bridge.knowledge_management.docs_knowledge.backends._office_convert import (
    OfficeConversionError,
    SOFFICE_TARGETS,
    convert_via_soffice,
    find_soffice,
)


# ---------------------------------------------------------------------------
# find_soffice
# ---------------------------------------------------------------------------


def test_soffice_targets_covers_legacy_office() -> None:
    """The legacy -> OOXML mapping must include the formats we advertise."""
    assert SOFFICE_TARGETS == {".doc": ".docx", ".ppt": ".pptx"}


def test_find_soffice_returns_path_when_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "agent_bridge.knowledge_management.docs_knowledge.backends._office_convert.shutil.which",
        lambda name: "/usr/bin/soffice" if name == "soffice" else None,
    )
    # Even on macOS, PATH should win over the /Applications fallback.
    monkeypatch.setattr(sys, "platform", "darwin")
    assert find_soffice() == "/usr/bin/soffice"


def test_find_soffice_falls_back_to_libreoffice_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When `soffice` is missing, `libreoffice` is the next candidate."""
    monkeypatch.setattr(
        "agent_bridge.knowledge_management.docs_knowledge.backends._office_convert.shutil.which",
        lambda name: "/usr/bin/libreoffice" if name == "libreoffice" else None,
    )
    assert find_soffice() == "/usr/bin/libreoffice"


def test_find_soffice_falls_back_to_macos_app_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """On macOS with nothing on PATH, the /Applications install is used."""
    monkeypatch.setattr(
        "agent_bridge.knowledge_management.docs_knowledge.backends._office_convert.shutil.which",
        lambda name: None,
    )
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_app = tmp_path / "LibreOffice.app"
    fake_app.mkdir(parents=True)
    macos_path = "agent_bridge.knowledge_management.docs_knowledge.backends._office_convert._MACOS_SOFFICE_CANDIDATES"
    monkeypatch.setattr(macos_path, [str(fake_app / "soffice")])
    (fake_app / "soffice").write_text("#!/bin/sh\n")
    assert find_soffice() == str(fake_app / "soffice")


def test_find_soffice_returns_none_when_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_bridge.knowledge_management.docs_knowledge.backends._office_convert.shutil.which",
        lambda name: None,
    )
    monkeypatch.setattr(sys, "platform", "linux")
    assert find_soffice() is None


# ---------------------------------------------------------------------------
# convert_via_soffice — failure paths
# ---------------------------------------------------------------------------


def test_convert_raises_friendly_error_when_soffice_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "agent_bridge.knowledge_management.docs_knowledge.backends._office_convert.find_soffice",
        lambda: None,
    )
    src = tmp_path / "a.doc"
    src.write_bytes(b"\xd0\xcf\x11\xe0")

    with pytest.raises(OfficeConversionError) as exc_info:
        convert_via_soffice(src, ".docx", tmp_path / "out")

    msg = str(exc_info.value)
    assert "LibreOffice" in msg
    assert "not installed" in msg
    # Must include actionable install guidance.
    assert "install" in msg.lower()


def test_convert_raises_on_nonzero_exit_and_missing_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "agent_bridge.knowledge_management.docs_knowledge.backends._office_convert.find_soffice",
        lambda: "/usr/bin/soffice",
    )
    monkeypatch.setattr(
        "agent_bridge.knowledge_management.docs_knowledge.backends._office_convert.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(
            args=k.get("args", a), returncode=1, stdout="", stderr="boom: corrupted"
        ),
    )
    src = tmp_path / "bad.doc"
    src.write_bytes(b"\xd0\xcf\x11\xe0")

    with pytest.raises(OfficeConversionError, match="failed to convert") as exc_info:
        convert_via_soffice(src, ".docx", tmp_path / "out")

    assert "corrupted" in str(exc_info.value)


def test_convert_raises_on_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "agent_bridge.knowledge_management.docs_knowledge.backends._office_convert.find_soffice",
        lambda: "/usr/bin/soffice",
    )

    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd=k.get("args", a), timeout=1)

    monkeypatch.setattr(
        "agent_bridge.knowledge_management.docs_knowledge.backends._office_convert.subprocess.run",
        _boom,
    )
    src = tmp_path / "big.doc"
    src.write_bytes(b"\xd0\xcf\x11\xe0")

    with pytest.raises(OfficeConversionError, match="timed out"):
        convert_via_soffice(src, ".docx", tmp_path / "out", timeout=1)


# ---------------------------------------------------------------------------
# convert_via_soffice — success path (mocked subprocess, real file I/O)
# ---------------------------------------------------------------------------


def test_convert_writes_output_and_returns_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A successful conversion must return the produced OOXML file path."""
    monkeypatch.setattr(
        "agent_bridge.knowledge_management.docs_knowledge.backends._office_convert.find_soffice",
        lambda: "/usr/bin/soffice",
    )
    outdir = tmp_path / "out"

    def _fake_run(cmd, **kwargs):
        # soffice writes <outdir>/<src-stem><target_suffix>
        # Emulate by locating the input in the argv and creating the output.
        src_arg = cmd[-1]
        target = cmd[cmd.index("--convert-to") + 1]
        outdir.mkdir(parents=True, exist_ok=True)
        out_file = outdir / (Path(src_arg).stem + "." + target)
        out_file.write_bytes(b"FAKE-OOXML")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(
        "agent_bridge.knowledge_management.docs_knowledge.backends._office_convert.subprocess.run",
        _fake_run,
    )

    src = tmp_path / "report.doc"
    src.write_bytes(b"\xd0\xcf\x11\xe0")

    result = convert_via_soffice(src, ".docx", outdir)

    assert result == outdir / "report.docx"
    assert result.exists()
    assert result.read_bytes() == b"FAKE-OOXML"
