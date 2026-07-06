"""Convert legacy Microsoft Office files (.doc/.ppt) to OOXML via LibreOffice.

``markitdown`` only understands the modern OOXML formats (.docx/.pptx); the
legacy binary formats (.doc/.ppt) need to be pre-converted. This module wraps
the ``soffice`` (LibreOffice) headless converter and raises a friendly,
user-actionable error when LibreOffice is not installed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# Legacy binary Office suffix -> the OOXML suffix markitdown can consume.
SOFFICE_TARGETS = {".doc": ".docx", ".ppt": ".pptx"}

# Default per-invocation timeout. soffice cold start can be slow (~5-15s on
# first launch), so we keep this generous.
DEFAULT_TIMEOUT = 180

# Standard LibreOffice install location on macOS (used as a fallback when the
# binary is not on PATH).
_MACOS_SOFFICE_CANDIDATES = (
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
)


class OfficeConversionError(RuntimeError):
    """Raised when LibreOffice is unavailable or conversion fails.

    The message is intended to be shown directly to end users and always
    includes install guidance when the cause is a missing ``soffice`` binary.
    """


def find_soffice() -> str | None:
    """Locate the LibreOffice ``soffice`` executable.

    Resolution order: ``soffice`` on PATH → ``libreoffice`` on PATH → the
    standard macOS ``/Applications/LibreOffice.app`` path. Returns ``None``
    when nothing usable is found.
    """
    for name in ("soffice", "libreoffice"):
        path = shutil.which(name)
        if path:
            return path
    if sys.platform == "darwin":
        for candidate in _MACOS_SOFFICE_CANDIDATES:
            if Path(candidate).exists():
                return candidate
    return None


def _install_hint() -> str:
    """Return a platform-appropriate install hint for the error message."""
    if sys.platform == "darwin":
        return (
            "Install LibreOffice, e.g. `brew install --cask libreoffice`, "
            "or download it from https://www.libreoffice.org/download/."
        )
    if shutil.which("apt-get"):
        return "Install LibreOffice, e.g. `sudo apt-get install -y libreoffice`."
    if shutil.which("dnf") or shutil.which("yum"):
        return "Install LibreOffice, e.g. `sudo dnf install -y libreoffice`."
    return "Install LibreOffice from https://www.libreoffice.org/download/."


def convert_via_soffice(
    src: Path,
    target_suffix: str,
    outdir: Path,
    *,
    timeout: int = DEFAULT_TIMEOUT,
) -> Path:
    """Convert ``src`` to ``target_suffix`` (e.g. ``.docx``) via LibreOffice.

    The output lands at ``outdir/<src-stem><target_suffix>``. Raises
    :class:`OfficeConversionError` with a friendly message when LibreOffice is
    missing, times out, exits non-zero, or fails to produce the expected file.
    """
    binary = find_soffice()
    if binary is None:
        raise OfficeConversionError(
            f"Cannot convert legacy Office file {src.name!r}: LibreOffice "
            f"(soffice) is not installed. {_install_hint()}"
        )

    outdir.mkdir(parents=True, exist_ok=True)
    # Use a dedicated user-profile dir per call. soffice refuses to run two
    # headless instances against the same profile concurrently, which would
    # otherwise cause intermittent failures under batch ingestion.
    profile = outdir / f".lo-profile-{src.stem}"
    profile.mkdir(parents=True, exist_ok=True)

    cmd = [
        binary,
        "--headless",
        "--norestore",
        "-env:UserInstallation=file://" + str(profile.resolve()),
        "--convert-to",
        target_suffix.lstrip("."),
        "--outdir",
        str(outdir),
        str(src),
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OfficeConversionError(
            f"LibreOffice conversion of {src.name!r} timed out after "
            f"{timeout}s. The file may be too large or LibreOffice may be "
            f"stuck; retry, or convert it to {target_suffix} manually."
        ) from exc

    expected = outdir / (src.stem + target_suffix)
    if proc.returncode != 0 or not expected.exists():
        stderr_tail = (proc.stderr or "").strip().splitlines()[-3:]
        detail = "; ".join(stderr_tail) if stderr_tail else proc.stdout.strip()
        raise OfficeConversionError(
            f"LibreOffice failed to convert {src.name!r} to {target_suffix} "
            f"(exit {proc.returncode}). {detail}"
        )

    # Clean up the scratch profile to avoid accumulating per-file dirs.
    shutil.rmtree(profile, ignore_errors=True)
    return expected
