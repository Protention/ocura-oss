# SPDX-License-Identifier: Apache-2.0

"""End-to-end checks for the repository-only CI report example."""

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ocura_oss
from ocura_oss import Outcome, Store, StoreError, initialize

REPOSITORY = Path(__file__).resolve().parents[1]
EXAMPLE = REPOSITORY / "examples" / "ci_report"


def load_workflow():
    spec = importlib.util.spec_from_file_location("ci_report_workflow", EXAMPLE / "workflow.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot(root):
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


@unittest.skipUnless(EXAMPLE.is_dir(), "repository-only CI report example")
class CIReportExampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.workspace = Path(cls.temporary.name).resolve()
        cls.root = cls.workspace / "original"
        cls.workflow = load_workflow()
        cls.result = cls.workflow.run_example(cls.root)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()

    def copy_root(self):
        return Path(shutil.copytree(self.root, self.workspace / "copied"))

    def test_fresh_run_preserves_real_failure_then_pass_and_source_versions(self):
        self.assertEqual(self.result["status"], "complete")
        first, second = self.result["runs"]
        self.assertEqual(first["outcome"], "failed")
        self.assertEqual(first["return_code"], 1)
        self.assertGreater(first["checks"]["failed"], 0)
        self.assertIn("FAIL out-of-order attempts", first["stderr_excerpt"]["text"])
        self.assertIn('"jobs": 8', first["stderr_excerpt"]["text"])
        self.assertEqual(second["outcome"], "passed")
        self.assertEqual(second["return_code"], 0)
        self.assertEqual(second["checks"], {"checks": 31, "passed": 31, "failed": 0})
        self.assertEqual(second["source_branch_point_id"], first["branch_point_id"])
        self.assertEqual(second["reason"], self.workflow.REASON)
        self.assertEqual(second["declared_parameters"]["intent"], self.workflow.INTENT)
        self.assertEqual(self.result["verification"]["logs_checked"], 4)
        for name in self.workflow.SOURCE_FILES:
            self.assertEqual(
                (self.root / "sources" / name).read_bytes(), (EXAMPLE / name).read_bytes()
            )

    def test_completed_reporter_invocation_and_fixture(self):
        completed = subprocess.run(
            [sys.executable, str(EXAMPLE / "report.py"), str(EXAMPLE / "events.jsonl")],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout),
            {
                "jobs": 5,
                "passed": 3,
                "failed": 2,
                "retried_jobs": 3,
                "recovered_jobs": 2,
                "total_duration_ms": 1885,
            },
        )

    def test_report_from_only_a_frozen_copy_is_read_only(self):
        root = self.copy_root()
        for path in root.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IREAD)
        try:
            before = snapshot(root)
            with (
                patch.object(self.workflow, "run", side_effect=AssertionError("must not run")),
                patch.object(Store, "recover", side_effect=AssertionError("must not recover")),
            ):
                result = self.workflow.report(root)
            self.assertEqual(result, self.result)
            # A fresh process needs only the copied driver, installed/source package and root.
            imported_package = Path(ocura_oss.__file__).resolve()
            env = {**os.environ, "PYTHONPATH": str(imported_package.parent.parent)}
            provenance = subprocess.run(
                [sys.executable, "-B", "-c", "import ocura_oss; print(ocura_oss.__file__)"],
                cwd=self.workspace,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(provenance.returncode, 0, provenance.stderr)
            self.assertEqual(Path(provenance.stdout.strip()).resolve(), imported_package)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(root / "sources" / "workflow.py"),
                    "report",
                    "--root",
                    str(root),
                    "--json",
                ],
                cwd=self.workspace,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout), self.result)
            self.assertEqual(before, snapshot(root))
            self.assertNotIn(str(self.root), completed.stdout)
            self.assertNotIn(str(root), completed.stdout)
        finally:
            for path in root.rglob("*"):
                if path.is_file():
                    path.chmod(stat.S_IREAD | stat.S_IWRITE)

    def test_verified_excerpt_reference_identifies_exact_retained_bytes(self):
        store = Store(self.root, archive=True)
        for item in self.result["runs"]:
            evidence = item["stderr_excerpt"]
            reference = evidence["reference"]
            record = store.load_run(item["run_id"])
            raw = store.read_verified_log(run_id=item["run_id"], stream="stderr")
            self.assertEqual(reference["run_id"], item["run_id"])
            self.assertEqual(reference["ledger_id"], self.result["ledger_id"])
            self.assertEqual(reference["sha256"], record.stderr_sha256)
            self.assertEqual(reference["length"], min(2048, len(raw)))
            self.assertEqual(evidence["text"], raw[:2048].decode("utf-8"))
            self.assertEqual(evidence["truncated"], len(raw) > 2048)

    def test_changed_log_is_rejected_without_workload_execution(self):
        root = self.copy_root()
        store = Store(root, archive=True)
        record = store.load_run(self.result["runs"][0]["run_id"])
        path = store.resolve_log_path(record, "stderr")
        with path.open("ab") as stream:
            stream.write(b"changed\n")
        before = snapshot(root)
        with (
            patch.object(self.workflow, "run", side_effect=AssertionError("must not run")),
            self.assertRaises(StoreError),
        ):
            self.workflow.report(root)
        self.assertEqual(before, snapshot(root))

    def test_changed_preserved_source_is_rejected(self):
        root = self.copy_root()
        with (root / "sources" / "report.py").open("a", encoding="utf-8") as stream:
            stream.write("\n# changed after recording\n")
        with self.assertRaisesRegex(ValueError, "source hash mismatch"):
            self.workflow.report(root)

    def test_run_never_overwrites_any_existing_destination(self):
        empty = self.workspace / "empty"
        empty.mkdir()
        occupied = self.workspace / "occupied"
        occupied.mkdir()
        (occupied / "keep.txt").write_text("keep", encoding="utf-8")
        file = self.workspace / "file"
        file.write_text("keep", encoding="utf-8")
        for target in (empty, occupied, file, self.root):
            with self.subTest(target=target), patch.object(self.workflow, "initialize") as init:
                before = snapshot(target) if target.is_dir() else target.read_bytes()
                with self.assertRaises(FileExistsError):
                    self.workflow.run_example(target)
                after = snapshot(target) if target.is_dir() else target.read_bytes()
                self.assertEqual(before, after)
                init.assert_not_called()

    def test_incomplete_root_stays_incomplete_without_workload_execution(self):
        root = self.workspace / "incomplete"
        initialize(root, name="ci-report-example")
        before = snapshot(root)
        with patch.object(self.workflow, "run", side_effect=AssertionError("must not run")):
            result = self.workflow.report(root)
        self.assertEqual(result["status"], "unexpected_or_incomplete_results")
        self.assertEqual(result["runs"], [])
        self.assertEqual(before, snapshot(root))

    def test_unexpected_starter_result_stops_before_corrected_run(self):
        for outcome, code in (
            (Outcome.PASSED, 0),
            (Outcome.INTERRUPTED, 130),
            (Outcome.LAUNCH_FAILED, None),
            (Outcome.ABANDONED, None),
            (Outcome.FAILED, 2),
        ):
            root = self.workspace / outcome.value
            result = SimpleNamespace(record=SimpleNamespace(outcome=outcome, return_code=code))
            with (
                self.subTest(outcome=outcome),
                patch.object(self.workflow, "run", return_value=result) as recorder,
            ):
                with self.assertRaisesRegex(ValueError, "stopped before the corrected run"):
                    self.workflow.run_example(root)
                self.assertEqual(recorder.call_count, 1)
                self.assertTrue((root / "sources" / "starter.py").is_file())
                self.assertTrue((root / ".ocura-oss").is_dir())


if __name__ == "__main__":
    unittest.main()
