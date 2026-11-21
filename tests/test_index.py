# SPDX-License-Identifier: Apache-2.0

"""The index chooses which records a listing reads; the records still give the answer."""

import contextlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ocura_oss import index, model, run
from ocura_oss.store import STATE_DIR_NAME, Store

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ledger-0.8.0" / "state"


class IndexTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "project"
        shutil.copytree(FIXTURE, self.root / STATE_DIR_NAME)
        smallest = mock.patch.object(index, "SMALLEST", 1)
        smallest.start()
        self.addCleanup(smallest.stop)
        self.store = Store(self.root)
        self.file = self.store.state_dir / index.FILE_NAME
        run([sys.executable, "-c", "raise SystemExit(3)"], root=self.root)
        self.points = sorted(self.store.branch_points_dir.glob("*.json"))

    def listings(self, **options):
        """Ask everything the index answers, the way `runs` and `show` ask it."""
        store = Store(self.root, **options)
        first = store.list_runs(limit=2)
        child = store.list_runs(limit=None).runs[-2].run
        return [
            first.to_dict(),
            store.list_runs(limit=2, after=first.next_after).to_dict(),
            store.list_runs(outcome=model.Outcome.FAILED).to_dict(),
            store.list_runs(pathway_id=child.pathway_id, limit=None).to_dict(),
            store.inspect_run(child.id).to_dict(),
        ]

    def scanned(self):
        with mock.patch.dict(os.environ, {"OCURA_OSS_INDEX": "off"}):
            return self.listings()

    def sql(self, statement):
        with contextlib.closing(sqlite3.connect(self.file)) as connection, connection:
            return connection.execute(statement).fetchall()

    def test_listings_match_a_scan_and_follow_new_and_changed_records(self):
        expected = self.scanned()
        with mock.patch.object(index, "SMALLEST", len(self.points) + 1):
            self.assertEqual(self.listings(), expected)
        self.assertFalse(self.file.exists())  # Neither a scan nor a small ledger makes one.
        self.assertEqual(self.listings(), expected)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM points"), [(len(self.points),)])
        self.sql("CREATE TABLE kept (x)")

        run([sys.executable, "-c", "print('new')"], root=self.root)
        # A record that needs a later version has no place in the order and is counted.
        envelope = json.loads(self.points[0].read_text("utf-8"))
        checksum = model.checksum_for(5, "branch_point", envelope["payload"])
        later = {**envelope, "schema_version": 5, "checksum": checksum}
        self.points[0].write_text(json.dumps(later), "utf-8")
        # A row is read again when its file's size or modification time has changed.
        self.sql(f"UPDATE points SET outcome = 'failed' WHERE name = '{self.points[1].stem}'")
        os.utime(self.points[1], ns=(1, 1))
        expected = self.scanned()
        self.assertEqual(expected[0]["newer_runs"], 1)
        self.assertEqual(self.listings(), expected)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM kept"), [(0,)])  # The same index.

    def test_a_row_that_disagrees_with_its_record_drops_the_index(self):
        expected = self.listings()
        self.sql("CREATE TABLE kept (x)")
        self.sql("UPDATE points SET outcome = 'failed'")
        self.assertEqual(self.listings(), expected)
        failed = [(len(expected[2]["runs"]),)]
        self.assertEqual(self.sql("SELECT COUNT(*) FROM points WHERE outcome = 'failed'"), failed)
        self.assertEqual(self.sql("SELECT name FROM sqlite_master WHERE name = 'kept'"), [])

    def test_an_index_that_cannot_be_used_means_a_scan(self):
        expected = self.scanned()
        self.assertEqual(self.listings(archive=True), expected)
        self.assertFalse(self.file.exists())  # Archive mode writes nothing.
        with mock.patch.dict(sys.modules, {"sqlite3": None}):
            self.assertEqual(self.listings(), expected)
        self.file.write_bytes(b"not a database " * 100)
        self.assertEqual(self.listings(), expected)
        rows = [(len(self.points),)]
        self.assertEqual(self.sql("SELECT COUNT(*) FROM points"), rows)  # Built again.

        busy = contextlib.closing(sqlite3.connect(self.file))
        with busy as other, mock.patch.object(index, "_BUSY_SECONDS", 0.01):
            other.execute("BEGIN IMMEDIATE")
            self.assertEqual(self.listings(), expected)
        self.sql("DELETE FROM points")
        self.sql(f"PRAGMA user_version = {index.LAYOUT + 1}")
        self.assertEqual(self.listings(), expected)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM points"), [(0,)])  # A later version's.


if __name__ == "__main__":
    unittest.main()
