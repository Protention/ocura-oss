# SPDX-License-Identifier: Apache-2.0

"""Failures must not imply that reissuing an already launched command is safe."""

import errno
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ocura_oss import Store, StoreError, initialize, recover, run
from ocura_oss.errors import recovery_action


class ErrorProvenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        initialize(self.root)
        self.store = Store(self.root)

    def test_invalid_input_and_journal_failure_are_known_not_started(self):
        with self.assertRaises(StoreError) as invalid:
            run([], root=self.root)
        self.assertEqual(
            (invalid.exception.code, invalid.exception.execution), ("invalid_input", "not_started")
        )
        with (
            patch.object(Store, "_write_record", side_effect=PermissionError("denied")),
            patch("ocura_oss.runner._execute") as execute,
        ):
            with self.assertRaises(StoreError) as denied:
                run([sys.executable, "-c", "print('never launched')"], root=self.root)
            execute.assert_not_called()
        self.assertEqual(
            (denied.exception.code, denied.exception.execution),
            ("permission_denied", "not_started"),
        )

    def test_failure_after_launch_retains_ids_and_recovery_does_not_resubmit(self):
        command = [
            sys.executable,
            "-c",
            "from pathlib import Path; p=Path('executions'); "
            "p.write_text(p.read_text()+'x' if p.exists() else 'x'); print('evidence')",
        ]
        with (
            patch.object(Store, "_save_chokepoint", side_effect=PermissionError("denied")),
            self.assertRaises(StoreError) as failure,
        ):
            run(command, root=self.root)
        error = failure.exception
        self.assertEqual((error.code, error.execution), ("permission_denied", "unknown"))
        self.assertEqual(set(error.ids), {"run_id", "branch_point_id", "pathway_id"})
        self.assertEqual(recovery_action(error.code, error.execution), "inspect_attempts_and_runs")
        self.assertEqual((self.root / "executions").read_text(), "x")
        closed = recover(self.root)
        self.assertEqual(closed[0].run_id, error.ids["run_id"])
        self.assertEqual(closed[0].branch_point_id, error.ids["branch_point_id"])
        self.assertEqual((self.root / "executions").read_text(), "x")
        self.assertTrue(self.store.verify_state().ok)

    def test_failure_after_terminal_publication_reports_recorded(self):
        with (
            patch.object(Store, "_remove_attempt", side_effect=PermissionError("denied")),
            self.assertRaises(StoreError) as failure,
        ):
            run([sys.executable, "-c", "print('completed')"], root=self.root)
        error = failure.exception
        self.assertEqual(error.execution, "recorded")
        self.assertEqual(recovery_action(error.code, error.execution), "inspect_recorded_run")
        self.assertEqual(self.store.load_run(error.ids["run_id"]).id, error.ids["run_id"])
        self.assertEqual(
            self.store.load_branch_point(error.ids["branch_point_id"]).run_id, error.ids["run_id"]
        )
        recover(self.root)
        self.assertTrue(self.store.verify_state().ok)

    def test_structural_failures_and_read_permissions_have_distinct_codes(self):
        with (
            patch.object(Path, "read_bytes", side_effect=PermissionError("denied")),
            self.assertRaises(StoreError) as denied,
        ):
            self.store.load_ledger()
        self.assertEqual(denied.exception.code, "permission_denied")
        den_path = self.root / ".ocura-oss" / "den.json"
        envelope = json.loads(den_path.read_text())
        envelope["payload"]["name"] = "changed without checksum"
        den_path.write_text(json.dumps(envelope))
        with self.assertRaises(StoreError) as damaged:
            self.store.load_ledger()
        self.assertEqual(damaged.exception.code, "integrity_error")

    def test_bad_log_range_is_input_error_and_changed_content_is_integrity_error(self):
        recorded = run([sys.executable, "-c", "print('evidence')"], root=self.root)
        with (
            self.assertRaises(StoreError) as invalid,
            self.store.open_verified_log(run_id=recorded.record.id, offset=-1),
        ):
            self.fail("invalid range must not release content")
        self.assertEqual(invalid.exception.code, "invalid_input")
        path = self.store.resolve_log_path(recorded.record, "stdout")
        path.write_bytes(b"changed")
        with self.assertRaises(StoreError) as damaged:
            self.store.read_verified_log(run_id=recorded.record.id)
        self.assertEqual(damaged.exception.code, "integrity_error")
        self.assertEqual(damaged.exception.ids, {"run_id": recorded.record.id})

    def test_log_io_errors_are_not_reported_as_corruption(self):
        recorded = run([sys.executable, "-c", "print('x' * 200)"], root=self.root)
        path = self.store.resolve_log_path(recorded.record, "stdout")
        original_open = Path.open
        for fault, expected in (
            (PermissionError(errno.EACCES, "denied"), "permission_denied"),
            (OSError(errno.EIO, "failed"), "io_error"),
        ):
            with self.subTest(code=expected):

                def denied(candidate, *args, fault=fault, **kwargs):
                    if candidate == path:
                        raise fault
                    return original_open(candidate, *args, **kwargs)

                with (
                    patch.object(Path, "open", denied),
                    self.assertRaises(StoreError) as failure,
                ):
                    self.store.read_verified_log(run_id=recorded.record.id)
                self.assertEqual(failure.exception.code, expected)
        self.store.compress_logs(run_id=recorded.record.id, min_bytes=0)
        with (
            patch("ocura_oss.logstorage.gzip.open", side_effect=PermissionError("denied")),
            self.assertRaises(StoreError) as failure,
        ):
            self.store.read_verified_log(run_id=recorded.record.id)
        self.assertEqual(failure.exception.code, "permission_denied")
        compressed = self.store.resolve_log_path(recorded.record, "stdout")
        self.assertEqual(compressed.suffix, ".gz")
        compressed.write_bytes(b"invalid gzip")
        with self.assertRaises(StoreError) as damaged:
            self.store.read_verified_log(run_id=recorded.record.id)
        self.assertEqual(damaged.exception.code, "integrity_error")
