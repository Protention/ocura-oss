# SPDX-License-Identifier: Apache-2.0

"""Opt-in error JSON preserves existing result and execution contracts."""

import contextlib
import errno
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ocura_oss import Store, api, cli, initialize
from ocura_oss.errors import StoreError, classify_os_error
from ocura_oss.model import ValidationError


class StructuredErrorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "project"
        initialize(self.root)

    def invoke(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = cli.main(list(arguments))
            except SystemExit as exc:
                code = exc.code
        return code, stdout.getvalue(), stderr.getvalue()

    def test_legacy_exception_arguments_remain_supported_with_diagnostics(self):
        for arguments in ((), ("message",), ("first", "second"), (None,)):
            with self.subTest(arguments=arguments):
                expected = Exception(*arguments)
                error = StoreError(
                    *arguments,
                    code="io_error",
                    execution="unknown",
                    ids={"run_id": "retained-id"},
                    recovery="inspect_attempts_and_runs",
                )
                self.assertEqual(error.args, expected.args)
                self.assertEqual(str(error), str(expected))
                self.assertEqual(error.code, "io_error")
                self.assertEqual(error.execution, "unknown")
                self.assertEqual(error.ids, {"run_id": "retained-id"})
                self.assertEqual(error.recovery, "inspect_attempts_and_runs")

    def test_legacy_json_errors_keep_empty_stdout_and_human_stderr(self):
        for arguments in (
            ["runs", "--json", "--limit", "0", "--root", str(self.root)],
            ["runs", "--json", "--limit", "not-an-integer"],
            ["run", "--json", "--root", str(self.root)],
            ["run", "--json", "--root", str(self.root), "--"],
        ):
            with self.subTest(arguments=arguments):
                code, stdout, stderr = self.invoke(*arguments)
                self.assertEqual(code, 2)
                self.assertEqual(stdout, "")
                self.assertIn("error:", stderr)

    def test_parser_errors_are_structured_without_launching(self):
        for arguments, execution in (
            (["runs", "--limit", "not-an-integer", "--json-errors"], "not_applicable"),
            (["branch", "--json-errors"], "not_applicable"),
            (["--json-errors", "unknown-command"], "not_applicable"),
            (["run", "--json-errors", "--bogus", "--", sys.executable], "not_started"),
        ):
            with self.subTest(arguments=arguments), patch("ocura_oss.runner.run_command") as run:
                code, stdout, stderr = self.invoke(*arguments)
                error = json.loads(stdout)["error"]
                self.assertEqual(code, 2)
                self.assertEqual(error["code"], "invalid_input")
                self.assertEqual(error["execution"], execution)
                self.assertEqual(error["recovery"], "correct_input")
                self.assertEqual(error["ids"], {})
                self.assertIn("error:", stderr)
                run.assert_not_called()

    def test_missing_separator_and_preflight_errors_are_not_started(self):
        for arguments in (
            [],
            ["--"],
            ["--param", "invalid", "--", sys.executable, "-c", "pass"],
            ["--param", "x=1", "--param", "x=2", "--", sys.executable, "-c", "pass"],
        ):
            with self.subTest(arguments=arguments), patch("ocura_oss.runner.run_command") as run:
                code, stdout, _ = self.invoke(
                    "run", "--root", str(self.root), "--json-errors", *arguments
                )
                error = json.loads(stdout)["error"]
                self.assertEqual((code, error["code"]), (2, "invalid_input"))
                self.assertEqual(error["execution"], "not_started")
                run.assert_not_called()

    def test_missing_state_is_classified_at_the_source(self):
        missing = self.root / "missing"
        for arguments, execution in (
            (["runs", "--root", str(missing), "--json-errors"], "not_applicable"),
            (
                ["run", "--root", str(missing), "--json-errors", "--", sys.executable],
                "not_started",
            ),
        ):
            with self.subTest(arguments=arguments):
                code, stdout, _ = self.invoke(*arguments)
                error = json.loads(stdout)["error"]
                self.assertEqual((code, error["code"]), (2, "state_not_found"))
                self.assertEqual(error["execution"], execution)
                self.assertEqual(error["recovery"], "initialize_state")

    def test_success_and_workload_failures_remain_normal_results(self):
        expected_fields = {
            "atom_id",
            "chokepoint_id",
            "pathway_id",
            "outcome",
            "duration_seconds",
            "return_code",
            "launch_error_category",
            "output_capture",
            "stdout_log",
            "stderr_log",
            "log_files",
        }
        for command, status, outcome in (
            ([sys.executable, "-c", "print('ordinary output')"], 0, "passed"),
            ([sys.executable, "-c", "raise SystemExit(7)"], 1, "failed"),
            (["ocura-no-such-executable-structured-error-test"], 3, "launch_failed"),
        ):
            with self.subTest(outcome=outcome):
                code, stdout, _ = self.invoke(
                    "run", "--root", str(self.root), "--json-errors", "--", *command
                )
                result = json.loads(stdout)
                self.assertEqual(code, status)
                self.assertEqual(result["outcome"], outcome)
                self.assertEqual(set(result), expected_fields)
                self.assertTrue(Store(self.root).verify_state().ok)

    def test_recorded_interruption_is_a_normal_result(self):
        with patch("ocura_oss.runner._execute", return_value=(None, "interrupted", True)):
            code, stdout, _ = self.invoke(
                "run", "--root", str(self.root), "--json-errors", "--", "unused"
            )
        result = json.loads(stdout)
        self.assertEqual(code, 1)
        self.assertEqual(result["outcome"], "interrupted")
        self.assertNotIn("error", result)

    def test_global_opt_in_implies_json(self):
        code, stdout, stderr = self.invoke("--json-errors", "runs", "--root", str(self.root))
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["runs"], [])

    def test_logs_still_print_bytes_with_the_opt_in(self):
        run = api.run([sys.executable, "-c", "print('evidence')"], root=self.root)
        stdout = io.TextIOWrapper(io.BytesIO())
        arguments = ["--json-errors", "logs", run.record.id, "--offset", "1", "--length", "3"]
        with contextlib.redirect_stdout(stdout):
            code = cli.main([*arguments, "--root", str(self.root)])
        stdout.flush()
        self.assertEqual((code, stdout.buffer.getvalue()), (0, b"vid"))

    def test_wrapped_run_errors_keep_their_earlier_type(self):
        with self.assertRaises(ValidationError) as failure:
            api.run([sys.executable, "-c", "pass"], root=self.root, parameters={"bad key": "x"})
        self.assertIsInstance(failure.exception, StoreError)
        self.assertEqual(failure.exception.execution, "not_started")

    def test_child_option_does_not_enable_recorder_json(self):
        code, stdout, stderr = self.invoke(
            "run",
            "--root",
            str(self.root),
            "--quiet",
            "--",
            sys.executable,
            "-c",
            "import sys; print(sys.argv[1])",
            "--json-errors",
        )
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("run: atom-", stdout)
        atom = Store(self.root).list_atoms()[0]
        self.assertEqual(Store(self.root).read_verified_log(atom.id).strip(), b"--json-errors")

    def test_source_diagnostics_preserve_ids_and_execution_uncertainty(self):
        for execution, expected, recovery in (
            ("not_applicable", "unknown", "inspect_attempts_and_runs"),
            ("unknown", "unknown", "inspect_attempts_and_runs"),
            ("recorded", "recorded", "inspect_recorded_run"),
            ("not_started", "not_started", "check_storage"),
        ):
            error = StoreError(
                "sentinel source failure",
                code="io_error",
                execution=execution,
                ids={"atom_id": "atom-" + "a" * 32},
            )
            with (
                self.subTest(execution=execution),
                patch("ocura_oss.cli._dispatch", side_effect=error),
            ):
                code, stdout, stderr = self.invoke("run", "--json-errors", "--", "unused")
                result = json.loads(stdout)["error"]
                self.assertEqual(code, 2)
                self.assertEqual(result["execution"], expected)
                self.assertEqual(result["recovery"], recovery)
                self.assertEqual(result["ids"], error.ids)
                self.assertEqual(stderr, "error: sentinel source failure\n")

    def test_os_failure_classification_uses_type_or_errno_not_message(self):
        for error, expected in (
            (PermissionError("arbitrary wording"), "permission_denied"),
            (OSError(errno.EPERM, "arbitrary wording"), "permission_denied"),
            (OSError(errno.ENOSPC, "permission denied is just text"), "io_error"),
        ):
            with self.subTest(error=error), patch("ocura_oss.cli._dispatch", side_effect=error):
                self.assertEqual(classify_os_error(error), expected)
                code, stdout, _ = self.invoke("run", "--json-errors", "--", "unused")
                result = json.loads(stdout)["error"]
                self.assertEqual((code, result["code"]), (2, expected))
                self.assertEqual(result["execution"], "unknown")

    def test_wrapped_os_failure_preserves_message_and_os_details(self):
        originals = [
            OSError(errno.EIO, "cannot write"),
            OSError(errno.EIO, "cannot read", "only.log"),
            OSError(
                errno.EXDEV,
                "cannot replace",
                "source.tmp",
                17 if sys.platform == "win32" else None,
                "record.json",
            ),
        ]
        if sys.platform == "win32":
            originals.append(
                OSError(errno.EACCES, "file is in use", "source.tmp", 32, "record.json")
            )
        for original in originals:
            with self.subTest(original=original):
                with (
                    patch.object(Store, "_save_atom", side_effect=original),
                    self.assertRaises(StoreError) as failure,
                ):
                    api.run([sys.executable, "-c", "pass"], root=self.root)
                error = failure.exception
                self.assertIsInstance(error, StoreError)
                self.assertIsInstance(error, OSError)
                self.assertIs(error.__cause__, original)
                self.assertEqual(error.args, original.args)
                self.assertEqual(str(error), str(original))
                self.assertEqual(error.errno, original.errno)
                self.assertEqual(error.strerror, original.strerror)
                self.assertEqual(error.filename, original.filename)
                self.assertEqual(error.filename2, original.filename2)
                if sys.platform == "win32" and hasattr(original, "winerror"):
                    self.assertEqual(error.winerror, original.winerror)
                self.assertEqual(error.code, classify_os_error(original))
                self.assertEqual(error.execution, "unknown")
                self.assertEqual(set(error.ids), {"run_id", "branch_point_id", "pathway_id"})

    def test_run_error_json_keeps_the_original_two_path_os_message(self):
        original = OSError(
            errno.EXDEV,
            "cannot replace",
            "source.tmp",
            17 if sys.platform == "win32" else None,
            "record.json",
        )
        with patch.object(Store, "_save_atom", side_effect=original):
            code, stdout, stderr = self.invoke(
                "run",
                "--root",
                str(self.root),
                "--json-errors",
                "--",
                sys.executable,
                "-c",
                "pass",
            )
        error = json.loads(stdout)["error"]
        self.assertEqual((code, error["code"]), (2, "io_error"))
        self.assertEqual(error["message"], str(original))
        self.assertEqual(stderr, f"error: {original}\n")
        self.assertEqual(error["execution"], "unknown")
        self.assertEqual(error["recovery"], "inspect_attempts_and_runs")
        self.assertEqual(set(error["ids"]), {"run_id", "branch_point_id", "pathway_id"})

    def test_finalization_failure_reports_known_ids_without_inviting_rerun(self):
        for method, execution, recovery in (
            ("_save_chokepoint", "unknown", "inspect_attempts_and_runs"),
            ("_remove_attempt", "recorded", "inspect_recorded_run"),
        ):
            script = f"from pathlib import Path; Path('{method}').open('a').write('started\\n')"
            with (
                self.subTest(method=method),
                patch.object(
                    Store,
                    method,
                    side_effect=OSError(errno.ENOSPC, "simulated persistence failure"),
                ),
            ):
                code, stdout, _ = self.invoke(
                    "run",
                    "--root",
                    str(self.root),
                    "--json-errors",
                    "--",
                    sys.executable,
                    "-c",
                    script,
                )
            error = json.loads(stdout)["error"]
            self.assertEqual((code, error["code"]), (2, "io_error"))
            self.assertEqual(error["execution"], execution)
            self.assertEqual(error["recovery"], recovery)
            self.assertEqual(set(error["ids"]), {"run_id", "branch_point_id", "pathway_id"})
            state = Store(self.root)
            self.assertEqual(state.load_atom(error["ids"]["run_id"]).outcome.value, "passed")
            state.recover()
            self.assertTrue(state.verify_state().ok)
            self.assertEqual((self.root / method).read_text(), "started\n")


if __name__ == "__main__":
    unittest.main()
