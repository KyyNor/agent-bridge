from __future__ import annotations

import pytest

from wiki_manager.server_process import server_status


def test_server_status_reports_invalid_pid_file(wm_paths) -> None:
    wm_paths.run_dir.mkdir(parents=True)
    wm_paths.server_pid_path.write_text("not-a-pid", encoding="utf-8")

    with pytest.raises(RuntimeError, match="invalid pid"):
        server_status(wm_paths)
