# SPDX-License-Identifier: Apache-2.0

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ocura_oss import Outcome, Store, StoreError, branch, cli, initialize, run


class AgentAccessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        initialize(self.root)
        self.store = Store(self.root)

    def record(self, data=b"output\x00\xff\r\n", *, pathway=None, code=0):
        return run(
            [
                sys.executable,
                "-c",
                f"import sys; sys.stdout.buffer.write({data!r}); sys.exit({code})",
            ],
            root=self.root,
            pathway_id=pathway,
        )

    def invoke(self, *arguments):
        return subprocess.run(
            [sys.executable, "-m", "ocura_oss", *arguments, "--root", str(self.root)],
            capture_output=True,
            timeout=30,
            check=False,
        )

    def test_discovery_retains_failed_retries_and_pages_equal_start_times(self):
        baseline = self.record()
        child = branch(baseline.chokepoint.id, root=self.root, reason="try a variation")
        failed = self.record(pathway=child.id, code=7)
        retry = self.record(pathway=child.id)
        atoms = [baseline.atom, failed.atom, retry.atom]
        for atom in atoms:
            self.store._save_atom(replace(atom, started_at=baseline.atom.started_at))
        first = self.store.list_runs(limit=2)
        second = self.store.list_runs(limit=2, after=first.next_after)
        ids = [item.id for page in (first, second) for item in page.runs]
        self.assertEqual(ids, sorted(atom.id for atom in atoms))
        self.assertIsNone(second.next_after)
        selected = self.store.list_runs(pathway_id=child.id, outcome=Outcome.FAILED)
        self.assertEqual([item.id for item in selected.runs], [failed.atom.id])
        self.assertEqual(
            selected.to_dict()["verification"], {"scope": "records", "logs_checked": 0}
        )

    def test_discovery_and_inspection_do_not_read_log_contents(self):
        baseline = self.record()
        child = branch(baseline.chokepoint.id, root=self.root, reason="try a variation")
        atom = self.record(pathway=child.id).atom
        self.store.resolve_log_path(atom, "stdout").unlink()
        with patch.object(
            self.store, "verify_atom_evidence", side_effect=AssertionError("read a log")
        ):
            self.assertEqual(len(self.store.list_runs().runs), 2)
            inspected = self.store.inspect_run(atom.id)
        payload = inspected.to_dict()
        self.assertEqual(payload["pathway"]["reason"], "try a variation")
        self.assertEqual(payload["pathway"]["source_chokepoint_id"], baseline.chokepoint.id)
        self.assertEqual(payload["atom"]["stdout_sha256"], atom.stdout_sha256)
        self.assertNotIn("command", payload["atom"])
        self.assertEqual(
            inspected.to_dict(include_command=True)["atom"]["command"], list(atom.command)
        )
        self.assertEqual(payload["verification"]["logs_checked"], 0)

    def test_inspection_checks_lineage(self):
        atom = self.record().atom
        (self.store.pathways_dir / f"{atom.pathway_id}.json").unlink()
        with self.assertRaisesRegex(StoreError, "missing pathway record"):
            self.store.inspect_run(atom.id)

    def test_discovery_rejects_invalid_limits_and_missing_cursor(self):
        for limit in (0, -1, 1001, True, 1.5):
            with self.subTest(limit=limit), self.assertRaisesRegex(StoreError, "page limit"):
                self.store.list_runs(limit=limit)
        with self.assertRaisesRegex(StoreError, "missing atom record"):
            self.store.list_runs(after="atom-" + "0" * 32)

    def test_discovery_does_not_hide_malformed_records_behind_a_filter(self):
        atom = self.record().atom
        (self.store.atoms_dir / f"{atom.id}.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(StoreError, "malformed atom record"):
            self.store.list_runs(outcome=Outcome.FAILED)

    def test_cli_discovery_and_explicit_command_access(self):
        first = self.record()
        second = self.record(code=9)
        page = self.invoke("runs", "--json", "--limit", "1")
        self.assertEqual(page.returncode, 0, page.stderr)
        payload = json.loads(page.stdout)
        self.assertEqual(payload["runs"][0]["id"], first.atom.id)
        following = self.invoke("runs", "--json", "--after", payload["next_after"])
        self.assertEqual(json.loads(following.stdout)["runs"][0]["id"], second.atom.id)
        shown = self.invoke("show", second.atom.id[5:13], "--json")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertNotIn("command", json.loads(shown.stdout)["atom"])
        explicit = self.invoke("show", second.atom.id, "--json", "--command")
        self.assertEqual(json.loads(explicit.stdout)["atom"]["command"], list(second.atom.command))

    def test_cli_text_views_are_metadata_and_binary_output_stays_explicit(self):
        atom = self.record().atom
        for command in (("runs",), ("show", atom.id)):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(cli.main([*command, "--root", str(self.root)]), 0)
            self.assertIn("logs not checked", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
