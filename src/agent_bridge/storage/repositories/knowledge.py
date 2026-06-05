from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterator

import sqlite3


class KnowledgeRepository:
    def __init__(self, db_path: Path, connect: Callable[[], Iterator[sqlite3.Connection]]) -> None:
        self._db_path = db_path
        self._connect = connect
