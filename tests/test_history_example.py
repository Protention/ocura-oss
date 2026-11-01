# SPDX-License-Identifier: Apache-2.0

import base64
import contextlib
import errno
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from ocura_oss import Attempt, OutputCapture, Store, StoreError, branch, cli, initialize, model, run

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
        )

    def invoke(self, *arguments, root=None):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = self.example.main(["--root", str(root or self.root), *arguments])
        return code, json.loads(stdout.getvalue())

    def ids_for(self, execution):
        return {
            "ledger_id": self.initial.ledger.id,
            "run_id": execution.record.id,
            "branch_point_id": execution.branch_point.id,
            "pathway_id": execution.record.pathway_id,
        }

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
        self.assertNotIn("error", result)
        self.assertNotIn("error_details", result)
        failed_report = next(item for item in result["runs"] if item["run_id"] == failed.record.id)
        self.assertEqual(failed_report["outcome"], "failed")
        excerpt = failed_report["evidence"][0]
        self.assertEqual(excerpt["reference"]["sha256"], failed.record.stdout_sha256)
        self.assertEqual(excerpt["reference"]["length"], 256)
        self.assertEqual(base64.b64decode(excerpt["content_base64"]), (b"evidence" * 80)[:256])
        self.assertTrue(excerpt["truncated"])
        self.assertNotIn("error_details", excerpt)
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
        execution = self.record()
        recorded = execution.record
        self.store.resolve_log_path(recorded, "stdout").unlink()
        code, result = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(result["verification"]["stream_errors"], 1)
        self.assertEqual(result["verification"]["streams_checked"], 1)
        unavailable, readable = result["runs"][0]["evidence"]
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertNotIn("content_base64", unavailable)
        self.assertEqual(unavailable["error_details"]["message"], unavailable["error"])
        self.assertEqual(unavailable["error_details"]["code"], "integrity_error")
        self.assertEqual(unavailable["error_details"]["ids"], self.ids_for(execution))
        self.assertEqual(readable["status"], "verified")
        self.assertNotIn("error_details", readable)

    def test_missing_state_keeps_source_message_and_category(self):
        missing = self.root / "missing"
        with self.assertRaises(StoreError) as caught:
            Store(missing).load_ledger()
        code, result = self.invoke(root=missing)
        details = result["error_details"]
        self.assertEqual(code, 2)
        self.assertEqual(result["error"], str(caught.exception))
        self.assertEqual(details["message"], result["error"])
        self.assertEqual(details["code"], caught.exception.code)
        self.assertEqual(details["execution"], "not_applicable")
        self.assertEqual(details["ids"], {})
        self.assertFalse(missing.exists())

    def test_invalid_excerpt_bounds_are_classified_before_reading_state(self):
        for length in (-1, 65_537):
            with self.subTest(length=length), patch.object(Store, "load_ledger") as reader:
                code, result = self.invoke("--snippet-bytes", str(length))
                self.assertEqual(code, 2)
                self.assertEqual(result["error"], "snippet_bytes must be between 0 and 65536")
                self.assertEqual(result["error_details"]["code"], "invalid_input")
                self.assertEqual(result["error_details"]["execution"], "not_applicable")
                self.assertEqual(result["error_details"]["recovery"], "correct_input")
                self.assertEqual(result["error_details"]["ids"], {})
                reader.assert_not_called()

    def test_corrupt_raw_and_compressed_streams_retain_original_references(self):
        for compressed in (False, True):
            with self.subTest(compressed=compressed):
                execution = self.record()
                if compressed:
                    self.store.compress_logs(run_id=execution.record.id, min_bytes=1)
                path = self.store.resolve_log_path(execution.record, "stdout")
                path.write_bytes(b"changed content")
                code, result = self.invoke()
                self.assertEqual(code, 2)
                selected = next(
                    item for item in result["runs"] if item["run_id"] == execution.record.id
                )
                evidence = selected["evidence"][0]
                self.assertEqual(evidence["status"], "unavailable")
                self.assertEqual(evidence["error_details"]["code"], "integrity_error")
                self.assertEqual(evidence["error_details"]["recovery"], "verify_state")
                self.assertEqual(evidence["error_details"]["ids"], self.ids_for(execution))
                self.assertEqual(evidence["reference"]["sha256"], execution.record.stdout_sha256)
                self.assertNotIn("content_base64", evidence)

    def test_stream_error_preserves_source_diagnostics_and_other_stream(self):
        execution = self.record()
        error = StoreError(
            "source detail",
            code="custom_source_code",
            execution="unknown",
            ids={"run_id": "ancestor-run", "source_id": "related-record"},
            recovery="source_recovery_hint",
        )
        original_reader = Store.open_verified_log

        def read_stream(state, *arguments, **options):
            if options["stream"] == "stdout":
                raise error
            return original_reader(state, *arguments, **options)

        with patch.object(Store, "open_verified_log", read_stream):
            code, result = self.invoke()
        self.assertEqual(code, 2)
        unavailable, readable = result["runs"][0]["evidence"]
        details = unavailable["error_details"]
        self.assertEqual(unavailable["error"], "source detail")
        self.assertEqual(details["code"], error.code)
        self.assertEqual(details["execution"], error.execution)
        self.assertEqual(details["recovery"], error.recovery)
        self.assertEqual(details["ids"], {**self.ids_for(execution), **error.ids})
        self.assertEqual(unavailable["reference"]["run_id"], execution.record.id)
        self.assertEqual(readable["status"], "verified")

    def test_fatal_metadata_errors_retain_known_ids_and_source_details(self):
        execution = self.record()
        for method, known in (
            ("list_attempts", {"ledger_id": self.initial.ledger.id}),
            ("load_run", self.ids_for(execution)),
        ):
            error = StoreError(
                "metadata unavailable",
                code="permission_denied",
                execution="recorded",
                ids={"source_id": "related-record"},
                recovery="source_hint",
            )
            source_ids = dict(error.ids)
            with self.subTest(method=method), patch.object(Store, method, side_effect=error):
                code, result = self.invoke()
                self.assertEqual(code, 2)
                self.assertEqual(result["error"], str(error))
                self.assertEqual(result["error_details"]["code"], error.code)
                self.assertEqual(result["error_details"]["execution"], error.execution)
                self.assertEqual(result["error_details"]["recovery"], error.recovery)
                self.assertEqual(result["error_details"]["ids"], {**known, **source_ids})

    def test_raw_os_errors_use_type_or_errno_and_retain_available_ids(self):
        execution = self.record()
        for method, known in (
            ("load_ledger", {}),
            ("load_run", self.ids_for(execution)),
            ("open_verified_log", self.ids_for(execution)),
        ):
            for error, expected in (
                (PermissionError("arbitrary wording"), "permission_denied"),
                (OSError(errno.EPERM, "arbitrary wording"), "permission_denied"),
                (OSError(errno.ENOSPC, "permission denied is only text"), "io_error"),
            ):
                with (
                    self.subTest(method=method, error=error),
                    patch.object(Store, method, side_effect=error),
                ):
                    code, result = self.invoke()
                self.assertEqual(code, 2)
                if method == "open_verified_log":
                    result = result["runs"][0]["evidence"][0]
                self.assertEqual(result["error"], str(error))
                self.assertEqual(result["error_details"]["code"], expected)
                self.assertEqual(result["error_details"]["execution"], "not_applicable")
                self.assertEqual(result["error_details"]["ids"], known)

    def test_example_recovery_hints_match_cli_contract(self):
        codes = (
            "invalid_input",
            "state_not_found",
            "record_not_found",
            "ambiguous_id",
            "unsupported_schema",
            "integrity_error",
            "permission_denied",
            "io_error",
            "invalid_state",
            "future_code",
        )
        for code in codes:
            for execution in ("not_applicable", "unknown", "recorded"):
                error = StoreError("diagnostic", code=code, execution=execution)
                stdout, stderr = io.StringIO(), io.StringIO()
                with (
                    self.subTest(code=code, execution=execution),
                    patch("ocura_oss.cli._dispatch", side_effect=error),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    status = cli.main(["runs", "--json-errors"])
                self.assertEqual(status, 2)
                self.assertEqual(
                    self.example._error_details(error), json.loads(stdout.getvalue())["error"]
                )


if __name__ == "__main__":
    unittest.main()
