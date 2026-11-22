# SPDX-License-Identifier: Apache-2.0

"""A disposable SQLite index of branch point records.

The JSON records are the ledger. The index only says where each branch point falls in
finish order, so a listing opens the records it returns instead of every record. Each
row is keyed to its record file's size and modification time, and a file that is new
or changed is read again. When the index cannot be used, callers scan the records.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

FILE_NAME = "index.sqlite"
LAYOUT = 1
_BUSY_SECONDS = 2.0
_TABLE = """CREATE TABLE points (
    name TEXT PRIMARY KEY, size INTEGER, mtime INTEGER,
    created_at TEXT, pathway_id TEXT, outcome TEXT, run_id TEXT
) WITHOUT ROWID"""

# created_at, pathway_id, outcome, run_id.
Facts = tuple[str, str, str, str]


class Unavailable(Exception):
    """The index cannot answer now; scan the records instead."""


class Index:
    """The index of one branch point directory.

    *read* returns a record file's facts, or None for a record this version cannot
    read; such a record is tried again on every use.
    """

    def __init__(self, path: Path, directory: Path, read: Callable[[Path], Facts | None]) -> None:
        self.path = path
        self._directory = directory
        self._read = read

    def page(
        self,
        *,
        before: str,
        after: tuple[str, str] | None,
        pathway_id: str | None,
        outcome: str | None,
        limit: int | None,
        run_ids: frozenset[str] | None = None,
    ) -> tuple[list[str], int]:
        """Return branch point IDs in finish order, and how many records have no place in it.

        *run_ids* keeps the branch points of those runs.
        """
        where, values = ["created_at < ?"], [before]
        for column, value in (("pathway_id", pathway_id), ("outcome", outcome)):
            if value is not None:
                where.append(f"{column} = ?")
                values.append(value)
        if after is not None:
            where.append("(created_at > ? OR (created_at = ? AND name > ?))")
            values += [after[0], after[0], after[1]]
        chosen, order = " AND ".join(where), "ORDER BY created_at, name LIMIT ?"
        query = f"SELECT name, run_id FROM points WHERE {chosen} {order}"
        with self._synced() as connection:
            # A list of runs is matched here, so SQL cannot cut the page short.
            bound = -1 if limit is None or run_ids is not None else limit
            found = connection.execute(query, [*values, bound])
            names = [name for name, run in found if run_ids is None or run in run_ids][:limit]
            (unplaced,) = connection.execute(
                "SELECT COUNT(*) FROM points WHERE created_at IS NULL"
            ).fetchone()
        return names, unplaced

    def of_run(self, run_id: str) -> str | None:
        """Return the ID of the branch point that ends *run_id*, if it has one."""
        with self._synced() as connection:
            row = connection.execute(
                "SELECT name FROM points WHERE run_id = ?", [run_id]
            ).fetchone()
        return None if row is None else row[0]

    def discard(self) -> None:
        """Remove the index file; the next use builds a new one."""
        with contextlib.suppress(OSError):
            self.path.unlink()

    @contextlib.contextmanager
    def _synced(self) -> Iterator[sqlite3.Connection]:
        """Open the index with a row for every record file as it is now."""
        try:
            with os.scandir(self._directory) as entries:
                listed = {
                    entry.name[:-5]: (entry.stat().st_size, entry.stat().st_mtime_ns)
                    for entry in entries
                    if entry.name.endswith(".json")
                }
        except OSError as exc:
            raise Unavailable from exc
        if not listed:
            raise Unavailable  # Nothing to order yet, so no file either.
        try:
            import sqlite3
        except ImportError as exc:  # Some Python builds leave it out.
            raise Unavailable from exc
        try:
            connection = sqlite3.connect(self.path, timeout=_BUSY_SECONDS, isolation_level=None)
            with contextlib.closing(connection):
                connection.execute("BEGIN IMMEDIATE")
                self._bring_up_to_date(connection, listed)
                connection.execute("COMMIT")
                yield connection
        except sqlite3.OperationalError as exc:  # Busy, read-only, or it cannot be opened.
            raise Unavailable from exc
        except sqlite3.DatabaseError as exc:  # Not a database, or a damaged one.
            self.discard()
            raise Unavailable from exc

    def _bring_up_to_date(
        self, connection: sqlite3.Connection, listed: dict[str, tuple[int, int]]
    ) -> None:
        (layout,) = connection.execute("PRAGMA user_version").fetchone()
        if layout > LAYOUT:
            raise Unavailable  # A later version's index: left for that version.
        if layout < LAYOUT:
            connection.execute("DROP TABLE IF EXISTS points")
            connection.execute(_TABLE)
            connection.execute("CREATE INDEX finish ON points (created_at, name)")
            connection.execute(f"PRAGMA user_version = {LAYOUT}")
        known = {
            name: (size, mtime) if placed else None
            for name, size, mtime, placed in connection.execute(
                "SELECT name, size, mtime, created_at IS NOT NULL FROM points"
            )
        }
        gone = [(name,) for name in known.keys() - listed.keys()]
        rows = [
            (name, *status, *(self._read(self._directory / f"{name}.json") or (None,) * 4))
            for name, status in listed.items()
            if known.get(name) != status
        ]
        connection.executemany("DELETE FROM points WHERE name = ?", gone)
        connection.executemany("INSERT OR REPLACE INTO points VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
