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

from ocura_oss import Outcome, Store, StoreError, branch, cli, initialize, model, run


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

    def test_discovery_retains_failed_retries_and_pages_equal_finish_times(self):
        baseline = self.record()
        child = branch(baseline.chokepoint.id, root=self.root, reason="try a variation")
        failed = self.record(pathway=child.id, code=7)
        retry = self.record(pathway=child.id)
        points = [baseline.chokepoint, failed.chokepoint, retry.chokepoint]
        for point in points:
            self.store._save_chokepoint(replace(point, created_at=points[0].created_at))
        first = self.store.list_runs(limit=2)
        second = self.store.list_runs(limit=2, after=first.next_after)
        listed = [item.chokepoint_id for page in (first, second) for item in page.runs]
        self.assertEqual(listed, sorted(point.id for point in points))
        self.assertIsNone(second.next_after)
        self.assertEqual(self.store.list_runs(limit=None).runs, first.runs + second.runs)
        selected = self.store.list_runs(pathway_id=child.id, outcome=Outcome.FAILED)
        self.assertEqual(
            selected.to_dict()["runs"],
            [{**model.run_summary_from_atom(failed.atom).to_dict(), "chokepoint_id": points[1].id}],
        )
        self.assertEqual(
            selected.to_dict()["verification"], {"scope": "records", "logs_checked": 0}
        )

    def test_a_page_counts_runs_that_can_finish_behind_its_cursor(self):
        early, later = self.record(), self.record()
        # `early` finished first, but publishes its chokepoint while a page scans.
        point = self.store.chokepoints_dir / f"{early.chokepoint.id}.json"
        attempt = self.store.attempts_dir / f"{early.atom.id}.json"
        record = point.read_bytes()
        point.unlink()
        attempt.write_text("{}", encoding="utf-8")
        scan = self.store._iter_typed

        def publish_after_scan(*arguments):
            scanned = list(scan(*arguments))
            point.write_bytes(record)
            attempt.unlink()
            return iter(scanned)

        with patch.object(self.store, "_iter_typed", side_effect=publish_after_scan):
            page = self.store.list_runs()
        self.assertEqual([item.run.id for item in page.runs], [later.atom.id])
        self.assertEqual(page.unfinished_attempts, 1)
        behind = self.store.list_runs(after=later.chokepoint.id)
        self.assertEqual((behind.runs, behind.unfinished_attempts), ((), 0))

    def test_a_run_tied_with_the_page_clock_is_not_skipped(self):
        points = [self.record().chokepoint, self.record().chokepoint]
        stamp = points[1].created_at
        for point in points:
            self.store._save_chokepoint(replace(point, created_at=stamp))
        # The smaller ID publishes after the first page's scan, at that page's clock reading.
        late = self.store.chokepoints_dir / f"{min(point.id for point in points)}.json"
        record = late.read_bytes()
        late.unlink()
        with patch("ocura_oss.model.utc_now", return_value=model.parse_timestamp(stamp, "time")):
            first = self.store.list_runs(limit=1)
        late.write_bytes(record)
        # Resume after the last chokepoint shown, as a caller polling for new runs would.
        rest = self.store.list_runs(after=first.runs[-1].chokepoint_id if first.runs else None)
        self.assertEqual((first.unfinished_attempts, rest.unfinished_attempts), (0, 0))
        self.assertEqual(len(first.runs + rest.runs), 2)

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
            source = self.store.inspect_run(baseline.atom.id).to_dict()
        self.assertEqual(source["chokepoint"]["id"], baseline.chokepoint.id)
        self.assertEqual([item["id"] for item in source["children"]], [child.id])
        payload = inspected.to_dict()
        self.assertEqual(payload["children"], [])
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
        with self.assertRaisesRegex(StoreError, "missing chokepoint record"):
            self.store.list_runs(after="chokepoint-" + "0" * 32)

    def test_discovery_does_not_hide_malformed_records_behind_a_filter(self):
        point = self.record().chokepoint
        (self.store.chokepoints_dir / f"{point.id}.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(StoreError, "malformed chokepoint record"):
            self.store.list_runs(outcome=Outcome.FAILED)

    def test_cli_discovery_and_explicit_command_access(self):
        first = self.record()
        second = self.record(code=9)
        page = self.invoke("runs", "--json", "--limit", "1")
        self.assertEqual(page.returncode, 0, page.stderr)
        payload = json.loads(page.stdout)
        self.assertEqual(payload["runs"][0]["id"], first.atom.id)
        self.assertEqual(payload["next_after"], first.chokepoint.id)
        following = self.invoke("runs", "--json", "--after", payload["next_after"])
        self.assertEqual(json.loads(following.stdout)["runs"][0]["id"], second.atom.id)
        everything = json.loads(self.invoke("runs", "--json", "--all").stdout)
        self.assertEqual(
            [item["id"] for item in everything["runs"]], [first.atom.id, second.atom.id]
        )
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
