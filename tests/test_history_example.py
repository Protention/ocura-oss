# SPDX-License-Identifier: Apache-2.0

import base64
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from ocura_oss import Attempt, OutputCapture, Store, StoreError, branch, initialize, model, run

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "history" / "inspect_history.py"


@unittest.skipUnless(EXAMPLE.is_file(), "repository-only history example")
class HistoryExampleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.initial = initialize(self.root)
        self.store = Store(self.root)
        spec = importlib.util.spec_from_file_location("history_example", EXAMPLE)
        self.example = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.example)

    def record(self, *, pathway=None, code=0):
        return run(
            [sys.executable, "-c", f"import sys; print('evidence' * 80); sys.exit({code})"],
            root=self.root,
            pathway_id=pathway,
            compress=False,
        )

    def test_fresh_caller_retains_retries_empty_variations_and_exact_references(self):
        baseline = self.record()
        first = branch(baseline.branch_point.id, root=self.root, reason="first variation")
        failed = self.record(pathway=first.id, code=7)
        retry = self.record(pathway=first.id)
        second = branch(baseline.branch_point.id, root=self.root, reason="second variation")
        other = self.record(pathway=second.id)
        empty = branch(baseline.branch_point.id, root=self.root, reason="planned variation")
        self.store.compress_logs(min_bytes=1)
        files_before = {
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        completed = subprocess.run(
            [sys.executable, str(EXAMPLE), "--root", str(self.root), "--archive"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        expected = {item.record.id for item in (baseline, failed, retry, other)}
        self.assertEqual({item["run_id"] for item in result["runs"]}, expected)
        pathways = {item["pathway_id"]: item for item in result["pathways"]}
        self.assertEqual(pathways[empty.id]["run_ids"], [])
        self.assertEqual(pathways[first.id]["run_ids"], [failed.record.id, retry.record.id])
        self.assertEqual(pathways[second.id]["reason"], "second variation")
        self.assertEqual(pathways[first.id]["source_branch_point_id"], baseline.branch_point.id)
        self.assertEqual(result["discovery"]["completeness"], "stable_archive")
        self.assertFalse(result["verification"]["whole_ledger_checked"])
        self.assertEqual(result["verification"]["streams_checked"], 8)
        failed_report = next(item for item in result["runs"] if item["run_id"] == failed.record.id)
        self.assertEqual(failed_report["outcome"], "failed")
        excerpt = failed_report["evidence"][0]
        self.assertEqual(excerpt["reference"]["sha256"], failed.record.stdout_sha256)
        self.assertEqual(excerpt["reference"]["length"], 256)
        self.assertEqual(base64.b64decode(excerpt["content_base64"]), (b"evidence" * 80)[:256])
        self.assertTrue(excerpt["truncated"])
        files_after = {
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(files_before, files_after)

    def test_unfinished_work_stays_visible_without_recovery_or_execution(self):
        completed = self.record()
        attempt = Attempt(
            id="atom-" + uuid.uuid4().hex,
            pathway_id=self.initial.pathway.id,
            chokepoint_id="chokepoint-" + uuid.uuid4().hex,
            started_at=model.format_timestamp(model.utc_now()),
            declared_parameters={},
            command=("synthetic-unfinished-test",),
            stdout_log=None,
            stderr_log=None,
            output_capture=OutputCapture.NONE,
        )
        # Only fixture setup uses a private writer; the report uses public readers.
        with self.store._recording(attempt):
            result = self.example.report(self.root)
            self.assertEqual(result["unfinished_attempts"][0]["run_id"], attempt.id)
            self.assertEqual(result["unfinished_attempts"][0]["state"], "running")
            self.assertEqual(result["discovery"]["completeness"], "observed_only")
            self.assertEqual(result["discovery"]["unfinished_at_run_scan"], 1)
            self.assertEqual([item["run_id"] for item in result["runs"]], [completed.record.id])
            self.assertEqual(len(self.store.list_attempts()), 1)
            self.assertIn("never reruns", result["guidance"])
            with self.assertRaises(StoreError):
                self.example.report(self.root, archive=True)

    def test_missing_stream_is_visible_without_hiding_other_evidence(self):
        recorded = self.record().record
        self.store.resolve_log_path(recorded, "stdout").unlink()
        result = self.example.report(self.root)
        self.assertEqual(result["verification"]["stream_errors"], 1)
        self.assertEqual(result["verification"]["streams_checked"], 1)
        self.assertEqual(result["runs"][0]["evidence"][0]["status"], "unavailable")
        self.assertNotIn("content_base64", result["runs"][0]["evidence"][0])


if __name__ == "__main__":
    unittest.main()
