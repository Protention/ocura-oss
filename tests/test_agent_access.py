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
        child = branch(baseline.branch_point.id, root=self.root, reason="try a variation")
        failed = self.record(pathway=child.id, code=7)
        retry = self.record(pathway=child.id)
        points = [baseline.branch_point, failed.branch_point, retry.branch_point]
        for point in points:
            self.store._save_branch_point(replace(point, created_at=points[0].created_at))
        first = self.store.list_runs(limit=2)
        second = self.store.list_runs(limit=2, after=first.next_after)
        listed = [item.branch_point_id for page in (first, second) for item in page.runs]
        self.assertEqual(listed, sorted(point.id for point in points))
        self.assertIsNone(second.next_after)
        self.assertEqual(self.store.list_runs(limit=None).runs, first.runs + second.runs)
        selected = self.store.list_runs(pathway_id=child.id, outcome=Outcome.FAILED)
        self.assertEqual(
            selected.to_dict()["runs"],
            [
                {
                    **model.run_summary_from_run(failed.record).to_dict(),
                    "branch_point_id": points[1].id,
                }
            ],
        )
        self.assertEqual(
            selected.to_dict()["verification"], {"scope": "records", "logs_checked": 0}
        )

    def test_a_page_counts_runs_that_can_finish_behind_its_cursor(self):
        early, later = self.record(), self.record()
        # `early` finished first, but publishes its branch point while a page scans.
        point = self.store.branch_points_dir / f"{early.branch_point.id}.json"
        attempt = self.store.attempts_dir / f"{early.record.id}.json"
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
        self.assertEqual([item.run.id for item in page.runs], [later.record.id])
        self.assertEqual(page.unfinished_attempts, 1)
        behind = self.store.list_runs(after=later.branch_point.id)
        self.assertEqual((behind.runs, behind.unfinished_attempts), ((), 0))

    def test_a_run_tied_with_the_page_clock_is_not_skipped(self):
        points = [self.record().branch_point, self.record().branch_point]
        stamp = points[1].created_at
        for point in points:
            self.store._save_branch_point(replace(point, created_at=stamp))
        # The smaller ID publishes after the first page's scan, at that page's clock reading.
        late = self.store.branch_points_dir / f"{min(point.id for point in points)}.json"
        record = late.read_bytes()
        late.unlink()
        with patch("ocura_oss.model.utc_now", return_value=model.parse_timestamp(stamp, "time")):
            first = self.store.list_runs(limit=1)
        late.write_bytes(record)
        # Resume after the last branch point shown, as a caller polling for new runs would.
        rest = self.store.list_runs(after=first.runs[-1].branch_point_id if first.runs else None)
        self.assertEqual((first.unfinished_attempts, rest.unfinished_attempts), (0, 0))
        self.assertEqual(len(first.runs + rest.runs), 2)

    def test_discovery_and_inspection_do_not_read_log_contents(self):
        baseline = self.record()
        child = branch(baseline.branch_point.id, root=self.root, reason="try a variation")
        record = self.record(pathway=child.id).record
        self.store.resolve_log_path(record, "stdout").unlink()
        with patch.object(
            self.store, "verify_run_evidence", side_effect=AssertionError("read a log")
        ):
            self.assertEqual(len(self.store.list_runs().runs), 2)
            inspected = self.store.inspect_run(record.id)
            source = self.store.inspect_run(baseline.record.id).to_dict()
        self.assertEqual(source["branch_point"]["id"], baseline.branch_point.id)
        self.assertEqual([item["id"] for item in source["children"]], [child.id])
        payload = inspected.to_dict()
        self.assertEqual(payload["children"], [])
        self.assertEqual(payload["pathway"]["reason"], "try a variation")
        self.assertEqual(payload["pathway"]["source_branch_point_id"], baseline.branch_point.id)
        self.assertEqual(payload["run"]["stdout_sha256"], record.stdout_sha256)
        self.assertNotIn("command", payload["run"])
        self.assertEqual(
            inspected.to_dict(include_command=True)["run"]["command"], list(record.command)
        )
        self.assertEqual(payload["verification"]["logs_checked"], 0)

    def test_inspection_checks_lineage(self):
        record = self.record().record
        (self.store.pathways_dir / f"{record.pathway_id}.json").unlink()
        with self.assertRaisesRegex(StoreError, "missing pathway record"):
            self.store.inspect_run(record.id)

    def test_discovery_rejects_invalid_limits_and_missing_cursor(self):
        for limit in (0, -1, 1001, True, 1.5):
            with self.subTest(limit=limit), self.assertRaisesRegex(StoreError, "page limit"):
                self.store.list_runs(limit=limit)
        with self.assertRaisesRegex(StoreError, "missing branch point record"):
            self.store.list_runs(after="branch-point-" + "0" * 32)

    def test_discovery_does_not_hide_malformed_records_behind_a_filter(self):
        point = self.record().branch_point
        (self.store.branch_points_dir / f"{point.id}.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(StoreError, "malformed branch point record"):
            self.store.list_runs(outcome=Outcome.FAILED)

    def test_cli_discovery_and_explicit_command_access(self):
        first = self.record()
        second = self.record(code=9)
        page = self.invoke("runs", "--json", "--limit", "1")
        self.assertEqual(page.returncode, 0, page.stderr)
        payload = json.loads(page.stdout)
        self.assertEqual(payload["runs"][0]["id"], first.record.id)
        self.assertEqual(payload["next_after"], first.branch_point.id)
        following = self.invoke("runs", "--json", "--after", payload["next_after"])
        self.assertEqual(json.loads(following.stdout)["runs"][0]["id"], second.record.id)
        everything = json.loads(self.invoke("runs", "--json", "--all").stdout)
        self.assertEqual(
            [item["id"] for item in everything["runs"]], [first.record.id, second.record.id]
        )
        shown = self.invoke("show", second.record.id[4:12], "--json")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertNotIn("command", json.loads(shown.stdout)["run"])
        explicit = self.invoke("show", second.record.id, "--json", "--command")
        self.assertEqual(json.loads(explicit.stdout)["run"]["command"], list(second.record.command))

    def test_cli_text_views_are_metadata_and_binary_output_stays_explicit(self):
        record = self.record().record
        for command in (("runs",), ("show", record.id)):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(cli.main([*command, "--root", str(self.root)]), 0)
            self.assertIn("logs not checked", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
