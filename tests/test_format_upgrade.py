# SPDX-License-Identifier: Apache-2.0

"""A ledger written by 0.8 stays usable as it is and is converted only on request."""

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ocura_oss
from ocura_oss import cli, locking, model
from ocura_oss.store import STATE_DIR_NAME, UPGRADE_ENV, NewerRecord, Store, StoreError

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ledger-0.8.0" / "state"
BASELINE = "run-17a15137e9a14cfdbbe777be50d7446d"


def write_record(path, schema_version, kind, payload):
    checksum = model.checksum_for(schema_version, kind, payload)
    envelope = {
        "schema_version": schema_version,
        "kind": kind,
        "payload": payload,
        "checksum": checksum,
    }
    path.write_text(json.dumps(envelope), "utf-8")


class OlderLedgerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "project"
        shutil.copytree(FIXTURE, self.root / STATE_DIR_NAME)
        self.store = Store(self.root)
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop(UPGRADE_ENV, None)

    def files(self, under=""):
        state = self.store.state_dir
        return {
            path.relative_to(state).as_posix(): path.read_bytes()
            for path in (state / under).rglob("*")
            if path.is_file()
        }

    def formats(self):
        return {
            json.loads(text)["schema_version"]
            for name, text in self.files().items()
            if name.endswith(".json") and name != "settings.json"
        }

    def loaded(self):
        store = self.store
        return (
            store.load_ledger(),
            store.list_pathways(),
            store.list_run_records(),
            store.list_branch_points(),
        )

    def invoke(self, command, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main([command, "--root", str(self.root), *arguments])
        self.assertEqual(code, 0, stderr.getvalue())
        return stdout.getvalue(), stderr.getvalue()

    def test_recording_keeps_the_older_ledgers_schema(self):
        before = self.files()
        retained = ocura_oss.verify(self.root).manifest
        done = ocura_oss.run([sys.executable, "-c", "print(1)"], root=self.root)
        after = self.files()
        self.assertEqual({name: after[name] for name in before}, before)
        stored = json.loads(after[f"runs/{done.record.id}.json"])
        self.assertEqual(self.formats(), {3})
        self.assertIn("stdout_log", stored["payload"])
        self.assertNotIn("streams", stored["payload"])
        self.assertTrue(ocura_oss.verify(self.root, against=retained).ok)

    def test_overwrite_converts_every_record_and_keeps_what_it_says(self):
        logs, loaded = self.files("logs"), self.loaded()
        self.assertIsNone(self.store.upgrade_if_enabled())
        self.store.set_setting("upgrade", "overwrite")
        self.assertEqual(self.store.upgrade_if_enabled(), (13, 0))
        self.assertEqual(self.formats(), {4})
        self.assertEqual(self.loaded(), loaded)
        self.assertEqual(self.files("logs"), logs)
        self.assertTrue(ocura_oss.verify(self.root).ok)
        self.assertIsNone(self.store.upgrade_if_enabled())

    def test_project_setting_wins_over_the_environment(self):
        self.assertEqual(self.store.setting("upgrade"), "off")
        os.environ[UPGRADE_ENV] = "overwrite"
        self.assertEqual(self.store.setting("upgrade"), "overwrite")
        self.store.settings_path.write_text('{"retention": "30d"}', "utf-8")
        self.store.set_setting("upgrade", "off")
        self.assertEqual(self.store.setting("upgrade"), "off")
        # A setting only a later version knows is kept.
        stored = json.loads(self.store.settings_path.read_text("utf-8"))
        self.assertEqual(stored, {"retention": "30d", "upgrade": "off"})
        with self.assertRaises(StoreError):
            self.store.set_setting("upgrade", "sometimes")

    def test_stopped_upgrade_is_finished_by_the_next_one(self):
        real, written = self.store._write_record, []

        def stop(path, kind, payload, **options):
            if len(written) == 3:
                raise KeyboardInterrupt
            written.append(real(path, kind, payload, **options))

        with (
            mock.patch.object(self.store, "_write_record", stop),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.store.upgrade_records()
        self.assertEqual(self.store.older_format(), 3)
        self.assertTrue(ocura_oss.verify(self.root).ok)
        self.assertEqual(self.store.upgrade_records(), (10, 0))
        self.assertEqual(self.formats(), {4})

    def test_upgrade_waits_for_an_earlier_version_still_recording(self):
        attempt = self.store._attempt_path(model.make_id("run"))
        attempt.parent.mkdir()
        with locking.hold(attempt.with_suffix(".lock")):
            write_record(attempt, 3, "attempt", {})
            self.assertEqual(self.store.upgrade_records(), (12, 1))
            self.assertEqual(self.store.older_format(), 3)
            # A recorder of this version writes the current format, so it holds nothing back.
            write_record(attempt, 4, "attempt", {})
            self.assertEqual(self.store.upgrade_records(), (1, 0))
        self.assertIsNone(self.store.older_format())

    def test_upgrade_converts_what_was_recorded_while_it_ran(self):
        path = self.store.runs_dir / f"{BASELINE}.json"
        late = path.read_bytes()  # Written back in schema 3 just before the ledger record.
        with mock.patch.object(self.store, "_older_recorders", lambda: path.write_bytes(late) * 0):
            self.assertEqual(self.store.upgrade_records(), (14, 0))
        self.assertEqual(self.formats(), {4})

    def test_other_records_needing_a_newer_version_are_not_damage(self):
        default = self.store.pathways_dir / f"{self.store.load_ledger().default_pathway_id}.json"
        point = min(self.store.branch_points_dir.glob("*.json"))
        for path, kind in ((default, "pathway"), (point, "branch_point")):
            write_record(path, 5, kind, json.loads(path.read_text("utf-8"))["payload"])
        attempt = self.store._attempt_path(model.make_id("run"))
        attempt.parent.mkdir()
        write_record(attempt, 5, "attempt", {})
        report = ocura_oss.verify(self.root)
        self.assertTrue(report.ok, report.problems)
        self.assertIn((f"attempts/{attempt.name}", "schema 5"), report.newer)
        page = self.store.list_runs()
        self.assertEqual((len(page.runs), page.newer_runs), (0, 5))

    def test_record_needing_a_newer_version_is_named_and_left_alone(self):
        path = self.store.runs_dir / f"{BASELINE}.json"
        payload = json.loads(path.read_text("utf-8"))["payload"]
        cases = (
            (4, {**payload, "requires": ["terminal-capture"]}, "terminal-capture"),
            (5, payload, "schema 5"),
            (4, {**payload, "requires": [{"name": "x"}]}, "a later form of `requires`"),
        )
        for schema_version, working, needs in cases:
            with self.subTest(needs=needs):
                write_record(path, schema_version, "run", model.stored_payload("run", working))
                with self.assertRaises(NewerRecord) as raised:
                    self.store.load_run(BASELINE)
                self.assertEqual(
                    (raised.exception.code, raised.exception.needs), ("newer_record", (needs,))
                )
                report = ocura_oss.verify(self.root)
                self.assertTrue(report.ok, report.problems)
                self.assertEqual(report.newer, ((f"{BASELINE}.json", needs),))
                page = self.store.list_runs()
                # Runs branched from it are still read: only the run itself is left out.
                self.assertEqual((len(page.runs), page.newer_runs), (4, 1))
                self.assertNotIn(BASELINE, [item.run.id for item in page.runs])
        path.write_text(path.read_text("utf-8").replace('"passed"', '"failed"'), "utf-8")
        self.assertFalse(ocura_oss.verify(self.root).ok)
        # A later schema may change the envelope itself.
        later = {"schema_version": 5, "kind": "run", "payload": payload, "signatures": []}
        path.write_text(json.dumps(later), "utf-8")
        report = ocura_oss.verify(self.root)
        self.assertEqual((report.ok, report.newer), (True, ((f"{BASELINE}.json", "schema 5"),)))

    def test_a_stream_a_later_version_adds_is_verified_like_stdout(self):
        path = self.store.runs_dir / f"{BASELINE}.json"
        stored = model.stored_payload("run", json.loads(path.read_text("utf-8"))["payload"])
        log = Path(stored["streams"]["stdout"]["log"]).with_suffix(".terminal")
        (self.root / log).write_bytes(b"later\n")
        digest = hashlib.sha256(b"later\n").hexdigest()
        stored["streams"]["terminal"] = {"log": log.as_posix(), "bytes": 6, "sha256": digest}
        write_record(path, 4, "run", stored)
        checked = ocura_oss.verify(self.root)
        self.assertTrue(checked.ok, checked.problems)
        (self.root / log).write_bytes(b"edited")
        self.assertFalse(ocura_oss.verify(self.root).ok)

    def test_cli_says_how_to_opt_in_and_upgrades_on_the_next_run(self):
        _, notes = self.invoke("verify")
        self.assertIn("`ocura-oss settings upgrade=overwrite`", notes)
        self.assertIn(f"{UPGRADE_ENV}=overwrite", notes)
        listing, notes = self.invoke("runs", "--json")
        self.assertEqual(notes, "")
        self.assertNotIn("notices", json.loads(listing))
        self.invoke("settings", "upgrade=off")  # Keeping schema 3 on purpose stops the note.
        self.assertEqual(self.invoke("verify")[1], "")
        self.invoke("settings", "upgrade=overwrite")
        result, notes = self.invoke("run", "--quiet", "--json", "--", sys.executable, "-c", "pass")
        (notice,) = json.loads(result)["notices"]
        self.assertIn("upgraded ledger from schema 3 to 4 (13 records)", notice)
        self.assertIn(notice, notes)
        self.assertEqual(self.formats(), {4})


if __name__ == "__main__":
    unittest.main()
