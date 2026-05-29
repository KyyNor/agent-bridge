from __future__ import annotations

from pathlib import Path

import pytest

from wiki_manager.config import WikiManagerPaths


@pytest.fixture
def wm_paths(tmp_path: Path) -> WikiManagerPaths:
    return WikiManagerPaths.from_root(tmp_path / "wiki-manager")
