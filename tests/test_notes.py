# SPDX-License-Identifier: Apache-2.0

"""A run's note and tags can be edited; its record and the manifest stay as they were."""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from ocura_oss import cli, model
from ocura_oss.store import STATE_DIR_NAME, Store, StoreError

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ledger-0.8.0" / "state"


class NoteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "project"
        shutil.copytree(FIXTURE, self.root / STATE_DIR_NAME)
        self.store = Store(self.root)
        self.runs = [item.run.id for item in self.store.list_runs(limit=None).runs]
        self.path = self.store.notes_dir / f"{self.runs[0]}.json"

    def command(self, *arguments):
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            status = cli.main([*arguments, "--root", str(self.root), "--json"])
        self.assertEqual(status, 0, printed.getvalue())
        return json.loads(printed.getvalue())

    def stored(self):
        return json.loads(self.path.read_text("utf-8"))

    def test_a_note_and_tags_are_edited_beside_an_unchanged_run(self):
        first, second, third = self.runs[:3]
        manifest = self.store.verify_state().manifest
        record = (self.store.runs_dir / f"{first}.json").read_bytes()
        self.command("note", first[:10], "lr too high")
        self.command("tag", first, "diverged", "baseline")
        self.command("tag", second, "baseline")
        note = self.command("tag", first, "--remove", "baseline")["note"]
        self.assertEqual((note["text"], note["tags"]), ("lr too high", ["diverged"]))
        edited = self.command("note", first, "lr 3e-4 was too high")["note"]
        self.assertEqual(edited["tags"], ["diverged"])
        self.assertEqual(self.command("note", first)["note"], edited)
        self.assertEqual(self.command("show", first)["note"], edited)
        self.assertIsNone(self.command("show", third)["note"])
        for indexed in ("on", "off"):  # Through the index, then by a scan.
            with mock.patch.dict(os.environ, {"OCURA_OSS_INDEX": indexed}):
                listed = self.command("runs", "--tag", "baseline", "--limit", "1")
            found = [(item["id"], item["tags"]) for item in listed["runs"]]
            self.assertEqual((found, listed["next_after"]), ([(second, ["baseline"])], None))

        self.assertEqual((self.store.runs_dir / f"{first}.json").read_bytes(), record)
        self.assertEqual(self.store.verify_state(against=manifest).manifest, manifest)
        self.command("note", first, "--clear")
        self.assertIsNone(self.command("tag", first, "--remove", "diverged")["note"])
        self.assertFalse(self.path.exists())  # A note with nothing in it is not kept.

    def test_notes_follow_the_ledger_schema_and_are_verified(self):
        first = self.runs[0]
        self.store.annotate(first, text="kept", add=["a"])
        self.assertEqual(self.stored()["schema_version"], 3)  # What 0.8 wrote this ledger in.
        self.store.upgrade_records()
        self.assertEqual(self.stored()["schema_version"], model.SCHEMA_VERSION)
        self.assertEqual(self.store.load_note(first).text, "kept")

        envelope = self.stored()
        checksum = model.checksum_for(5, "note", envelope["payload"])
        self.path.write_text(json.dumps({**envelope, "schema_version": 5, "checksum": checksum}))
        report = self.store.verify_state()
        self.assertEqual((report.ok, report.newer), (True, ((f"notes/{first}.json", "schema 5"),)))
        self.assertIsNone(self.store.inspect_run(first).note)

        changed = {**envelope, "payload": {**envelope["payload"], "text": "changed by hand"}}
        self.path.write_text(json.dumps(changed))
        stray = model.make_id("run")
        payload = {**envelope["payload"], "run_id": stray}
        self.store._write_record(self.path.with_name(f"{stray}.json"), "note", payload)
        problems = [name for name, _problem in self.store.verify_state().problems]
        self.assertEqual(problems, [f"notes/{first}.json", f"notes/{stray}.json"])

    def test_a_change_is_refused_whole_or_applied_whole(self):
        first = self.runs[0]
        refused = (
            (self.store, first, {"add": ["ok", "two words"]}),
            (self.store, first, {"text": 3}),
            (self.store, model.make_id("run"), {"text": "no such run"}),
            (Store(self.root, archive=True), first, {"text": "read-only"}),
        )
        for store, run_id, change in refused:
            with self.subTest(change=change), self.assertRaises(StoreError):
                store.annotate(run_id, **change)
        self.assertFalse(self.store.notes_dir.exists())
        tags = [f"t{number}" for number in range(8)]
        with ThreadPoolExecutor(len(tags)) as pool:
            list(pool.map(lambda tag: Store(self.root).annotate(first, add=[tag]), tags))
        self.assertEqual(list(self.store.load_note(first).tags), tags)


if __name__ == "__main__":
    unittest.main()
