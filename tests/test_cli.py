# SPDX-License-Identifier: Apache-2.0

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from ocura_oss import __version__, cli


@contextlib.contextmanager
def captured_output():
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        yield stdout, stderr


class CliTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.parent = Path(self._temporary.name)
        self.root = self.parent / "proj"
        with captured_output() as (_stdout, _stderr):
            code = cli.main(["init", "--root", str(self.root)])
        self.assertEqual(code, 0)

    def _run(self, *arguments):
        with captured_output() as (stdout, stderr):
            code = cli.main(list(arguments))
        return code, stdout.getvalue(), stderr.getvalue()

    def _run_in_root(self, *arguments):
        with contextlib.chdir(str(self.root)):
            return self._run(*arguments)

    def test_version_flag_prints_package_version(self):
        with self.assertRaises(SystemExit) as raised, captured_output() as (stdout, _stderr):
            cli.main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(stdout.getvalue().strip(), f"ocura-oss {__version__}")

    def test_init_prints_ids_and_json_mode_parses(self):
        other = self.parent / "other"
        code, text, _ = self._run("init", "--root", str(other))
        self.assertEqual(code, 0)
        self.assertIn("project ledger:", text)
        self.assertIn("default pathway:", text)

    def test_init_refuses_existing_state(self):
        code, _text, stderr = self._run("init", "--root", str(self.root))
        self.assertEqual(code, 2)
        self.assertIn("error:", stderr)

    def test_run_exit_codes_for_pass_fail_and_launch_failure(self):
        passed, text, _ = self._run(
            "run",
            "--root",
            str(self.root),
            "--",
            sys.executable,
            "-c",
            "print('fine')",
        )
        self.assertEqual(passed, 0)
        self.assertIn("outcome: passed", text)

        failed, text, _ = self._run(
            "run",
            "--root",
            str(self.root),
            "--",
            sys.executable,
            "-c",
            "import sys; sys.exit(4)",
        )
        self.assertEqual(failed, 1)
        self.assertIn("outcome: failed", text)
        self.assertIn("return code: 4", text)

        launch, text, _ = self._run(
            "run",
            "--root",
            str(self.root),
            "--",
            "no-such-executable-ocura-oss",
        )
        self.assertEqual(launch, 3)
        self.assertIn("outcome: launch_failed", text)
        self.assertIn("launch error: executable_not_found", text)

    def test_run_rejects_missing_command_malformed_and_duplicate_params(self):
        code, _text, _err = self._run("run", "--root", str(self.root), "--")
        self.assertEqual(code, 2)

        code, _text, _err = self._run("run", "--root", str(self.root))
        self.assertEqual(code, 2)

        code, _text, _err = self._run(
            "run",
            "--root",
            str(self.root),
            "--param",
            "novalue",
            "--",
            sys.executable,
            "-c",
            "pass",
        )
        self.assertEqual(code, 2)

        code, _text, _err = self._run(
            "run",
            "--root",
            str(self.root),
            "--param",
            "a=1",
            "--param",
            "a=2",
            "--",
            sys.executable,
            "-c",
            "pass",
        )
        self.assertEqual(code, 2)

    def test_run_rejects_unknown_pathway_before_launch(self):
        code, _text, stderr = self._run(
            "run",
            "--root",
            str(self.root),
            "--pathway",
            "pathway-" + "0" * 32,
            "--",
            sys.executable,
            "-c",
            "print('never')",
        )
        self.assertEqual(code, 2)
        self.assertIn("error:", stderr)

    def test_summaries_omit_command_environment_and_log_content(self):
        argument_marker = "ARGUMENT-SUMMARY-SENTINEL"
        environment_marker = "ENVIRONMENT-SUMMARY-SENTINEL"
        log_marker = "LOG-CONTENT-SUMMARY-SENTINEL"
        environment_name = "OCURA_OSS_SUMMARY_ENV_SENTINEL"
        log_environment_name = "OCURA_OSS_SUMMARY_LOG_SENTINEL"
        environment_digest = hashlib.sha256(environment_marker.encode()).hexdigest()
        script = (
            "import hashlib, os; "
            f"assert hashlib.sha256(os.environ[{environment_name!r}].encode()).hexdigest() "
            f"== {environment_digest!r}; "
            f"print(os.environ[{log_environment_name!r}])"
        )
        previous_environment = {
            environment_name: os.environ.get(environment_name),
            log_environment_name: os.environ.get(log_environment_name),
        }
        os.environ[environment_name] = environment_marker
        os.environ[log_environment_name] = log_marker
        try:
            code, run_summary, _ = self._run(
                "run",
                "--root",
                str(self.root),
                "--quiet",
                "--",
                sys.executable,
                "-c",
                script,
                argument_marker,
            )
        finally:
            for name, previous in previous_environment.items():
                if previous is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = previous

        self.assertEqual(code, 0)
        run_id = next(
            line.removeprefix("run:").strip()
            for line in run_summary.splitlines()
            if line.startswith("run:")
        )
        branch_point_id = next(
            line.removeprefix("branch point:").strip()
            for line in run_summary.splitlines()
            if line.startswith("branch point:")
        )
        run_path = self.root / ".ocura-oss" / "runs" / f"{run_id}.json"
        run_text = run_path.read_text("utf-8")
        run_payload = json.loads(run_text)["payload"]
        self.assertIn(argument_marker, run_payload["command"])
        self.assertNotIn(environment_marker, run_text)
        self.assertNotIn(log_marker, run_text)
        stdout_log = (self.root / run_payload["stdout_log"]).read_text("utf-8")
        self.assertIn(log_marker, stdout_log)
        self.assertNotIn(environment_marker, stdout_log)

        summary_outputs = [run_summary]
        summary_commands = (
            ("pathways", "--root", str(self.root)),
            ("pathways", "--root", str(self.root), "--json"),
            ("branch-points", "--root", str(self.root)),
            ("branch-points", "--root", str(self.root), "--json"),
            (
                "branch",
                "--root",
                str(self.root),
                "--from",
                branch_point_id,
                "--reason",
                "summary boundary",
            ),
            (
                "branch",
                "--root",
                str(self.root),
                "--from",
                branch_point_id,
                "--reason",
                "JSON summary boundary",
                "--json",
            ),
            ("compare", "--root", str(self.root), "--from", branch_point_id),
            ("compare", "--root", str(self.root), "--from", branch_point_id, "--json"),
            ("verify", "--root", str(self.root)),
            ("verify", "--root", str(self.root), "--json"),
        )
        for command in summary_commands:
            summary_code, summary, _ = self._run(*command)
            self.assertEqual(summary_code, 0, command)
            summary_outputs.append(summary)

        for summary in summary_outputs:
            self.assertNotIn(argument_marker, summary)
            self.assertNotIn(environment_marker, summary)
            self.assertNotIn(log_marker, summary)

        combined_records = "\n".join(
            path.read_text("utf-8") for path in sorted((self.root / ".ocura-oss").rglob("*.json"))
        )
        self.assertIn(argument_marker, combined_records)
        self.assertNotIn(environment_marker, combined_records)
        self.assertNotIn(log_marker, combined_records)

    def test_summaries_omit_command_field(self):
        code, _, _ = self._run(
            "run",
            "--root",
            str(self.root),
            "--quiet",
            "--",
            sys.executable,
            "-c",
            "print('summary')",
        )
        self.assertEqual(code, 0)

        code, listing, _ = self._run("branch-points", "--root", str(self.root))
        self.assertEqual(code, 0)

        code, listing, _ = self._run("branch-points", "--root", str(self.root), "--json")
        self.assertEqual(code, 0)
        self.assertNotIn("command", listing)

        code, pathways_json, _ = self._run("pathways", "--root", str(self.root), "--json")
        self.assertEqual(code, 0)
        self.assertNotIn('"command"', pathways_json)

    def test_pathways_listing_reports_lineage_and_evidence(self):
        code, text, _ = self._run("pathways", "--root", str(self.root))
        self.assertEqual(code, 0)
        self.assertIn("terminal evidence: no", text)

        code, text, _ = self._run("pathways", "--root", str(self.root), "--json")
        self.assertEqual(code, 0)
        payload = json.loads(text)
        self.assertEqual(len(payload["pathways"]), 1)

    def test_branch_compare_flow_via_cli(self):
        code, run_text, _ = self._run(
            "run", "--root", str(self.root), "--", sys.executable, "-c", "print('base')"
        )
        self.assertEqual(code, 0)
        branch_point_id = None
        for line in run_text.splitlines():
            if line.startswith("branch point:"):
                branch_point_id = line.split(":", 1)[1].strip()
        self.assertTrue(branch_point_id)

        code, branch_text, _ = self._run_in_root(
            "branch",
            "--from",
            branch_point_id,
            "--reason",
            "try batch two",
            "--param",
            "batch=2",
        )
        self.assertEqual(code, 0)
        child_id = None
        for line in branch_text.splitlines():
            if line.startswith("pathway:"):
                child_id = line.split(":", 1)[1].strip()
        self.assertTrue(child_id)

        code, compare_text, _ = self._run(
            "compare", "--root", str(self.root), "--from", branch_point_id
        )
        self.assertEqual(code, 0)
        self.assertIn("comparison: partial", compare_text)

        code, automatic_text, _ = self._run("compare", "--root", str(self.root))
        self.assertEqual(code, 0)
        self.assertIn("comparison: partial", automatic_text)

        code, _text, _err = self._run(
            "run",
            "--root",
            str(self.root),
            "--pathway",
            child_id,
            "--",
            sys.executable,
            "-c",
            "print('child')",
        )
        self.assertEqual(code, 0)

        code, compare_text, _ = self._run(
            "compare", "--root", str(self.root), "--from", branch_point_id
        )
        self.assertEqual(code, 0)
        self.assertIn("comparison: ready", compare_text)
        self.assertIn("added batch=2", compare_text)
        self.assertIn("run parameters:", compare_text)

        code, compare_json, _ = self._run(
            "compare", "--root", str(self.root), "--from", branch_point_id, "--json"
        )
        self.assertEqual(code, 0)
        payload = json.loads(compare_json)
        self.assertEqual(payload["state"], "ready")
        self.assertIn("run_parameters", compare_json)
        self.assertNotIn("command", compare_json)

        code, automatic_ready, _ = self._run("compare", "--root", str(self.root))
        self.assertEqual(code, 0)
        self.assertIn("comparison: ready", automatic_ready)
        self.assertIn(f"source branch point: {branch_point_id}", automatic_ready)

    def test_branch_requires_reason_and_valid_source(self):
        code, _text, _err = self._run(
            "branch", "--from", "branch-point-" + "0" * 32, "--reason", "x"
        )
        self.assertEqual(code, 2)

        code, run_text, _ = self._run(
            "run", "--root", str(self.root), "--", sys.executable, "-c", "print('base')"
        )
        branch_point_id = [
            line.split(":", 1)[1].strip()
            for line in run_text.splitlines()
            if line.startswith("branch point:")
        ][0]

        with self.assertRaises(SystemExit) as ctx, contextlib.chdir(str(self.root)):
            cli.main(["branch", "--from", branch_point_id])
        self.assertEqual(ctx.exception.code, 2)

        code, _text, _err = self._run_in_root(
            "branch", "--from", branch_point_id, "--reason", "   "
        )
        self.assertEqual(code, 2)

    def test_demo_completes_retains_state_and_refuses_reuse(self):
        destination = self.parent / "demo-out"
        code, text, _ = self._run("demo", "--root", str(destination))
        self.assertEqual(code, 0)
        self.assertIn("demo completed", text)
        self.assertIn("comparison: ready", text)
        self.assertIn("verify:", text)
        self.assertTrue((destination / ".ocura-oss" / "ledger.json").is_file())
        self.assertTrue((destination / ".ocura-oss" / "logs").is_dir())

        before = (destination / ".ocura-oss" / "ledger.json").read_bytes()
        code, _text, stderr = self._run("demo", "--root", str(destination))
        self.assertEqual(code, 2)
        self.assertIn("already exists", stderr)
        after = (destination / ".ocura-oss" / "ledger.json").read_bytes()
        self.assertEqual(before, after)

    def test_demo_json_mode(self):
        destination = self.parent / "demo-json"
        code, text, _ = self._run("demo", "--root", str(destination), "--json")
        self.assertEqual(code, 0)
        payload = json.loads(text)
        self.assertNotIn("completed", payload)
        self.assertEqual(payload["comparison"]["state"], "ready")
        steps = [item["step"] for item in payload["steps"]]
        self.assertEqual(
            steps,
            [
                "initialize",
                "baseline-run",
                "branch-point",
                "branch",
                "rerun",
                "verify",
                "compare",
            ],
        )

    def test_demo_requires_passing_runs(self):
        from unittest import mock

        from ocura_oss import model
        from ocura_oss.runner import RunExecution

        digest = hashlib.sha256(b"").hexdigest()
        run_id = "run-" + "c" * 32
        failing = RunExecution(
            record=model.RunRecord(
                id=run_id,
                pathway_id="pathway-" + "0" * 32,
                started_at="2026-01-01T00:00:00.000000+00:00",
                finished_at="2026-01-01T00:00:01.000000+00:00",
                duration_seconds=1.0,
                outcome=model.Outcome.FAILED,
                return_code=1,
                launch_error_category=None,
                declared_parameters={},
                command=("x",),
                stdout_log=".ocura-oss/logs/x.stdout.log",
                stderr_log=".ocura-oss/logs/x.stderr.log",
                stdout_bytes=0,
                stderr_bytes=0,
                stdout_sha256=digest,
                stderr_sha256=digest,
            ),
            branch_point=model.BranchPoint(
                id="branch-point-" + "0" * 32,
                pathway_id="pathway-" + "0" * 32,
                run_id=run_id,
                created_at="2026-01-01T00:00:01.000000+00:00",
                kind="terminal",
                outcome=model.Outcome.FAILED,
                branchable=True,
            ),
        )
        destination = self.parent / "demo-failing"
        with mock.patch("ocura_oss.demo.runner.run_command", return_value=failing):
            code, _text, stderr = self._run("demo", "--root", str(destination))
        self.assertEqual(code, 2)
        self.assertIn("baseline-run", stderr)
        self.assertIn("failed", stderr)
        self.assertTrue(destination.is_dir())

    def test_commands_require_existing_state(self):
        missing = self.parent / "missing"
        code, _text, stderr = self._run("branch-points", "--root", str(missing))
        self.assertEqual(code, 2)
        self.assertIn("error:", stderr)

    def test_unknown_subcommand_exits_two(self):
        with self.assertRaises(SystemExit) as ctx:
            self._run("frobnicate")
        self.assertEqual(ctx.exception.code, 2)

    def test_tokens_before_separator_are_rejected(self):
        code, _text, stderr = self._run(
            "run",
            "--root",
            str(self.root),
            "stray",
            "--",
            sys.executable,
            "-c",
            "print('no')",
        )
        self.assertEqual(code, 2)
        self.assertIn("before --", stderr)

    def test_root_paths_with_spaces_work(self):
        spaced = self.parent / "some folder" / "proj"
        code, text, _ = self._run("init", "--root", str(spaced))
        self.assertEqual(code, 0)
        self.assertIn("project ledger:", text)

        code, text, _ = self._run(
            "run",
            "--root",
            str(spaced),
            "--",
            sys.executable,
            "-c",
            "print('spaced')",
        )
        self.assertEqual(code, 0)
        self.assertIn("outcome: passed", text)

    def test_relative_root_resolves_against_working_directory(self):
        with contextlib.chdir(str(self.parent)):
            code, _text, _err = self._run("init", "--root", "relproj")
        self.assertEqual(code, 0)
        self.assertTrue((self.parent / "relproj" / ".ocura-oss" / "ledger.json").is_file())

    def test_invalid_parameter_keys_are_rejected(self):
        for token in ("1bad=2", "bad key=2", "=value"):
            code, _text, _err = self._run(
                "run",
                "--root",
                str(self.root),
                "--param",
                token,
                "--",
                sys.executable,
                "-c",
                "print('never')",
            )
            self.assertEqual(code, 2, token)
        self.assertEqual(self.store_run_count(), 0)

    def store_run_count(self):
        records = self.root / ".ocura-oss" / "runs"
        return len(list(records.glob("*.json")))

    def test_parameter_value_with_equals_round_trips(self):
        code, _text, _err = self._run(
            "run",
            "--root",
            str(self.root),
            "--param",
            "eq=a=b",
            "--",
            sys.executable,
            "-c",
            "print('eq')",
        )
        self.assertEqual(code, 0)
        records = sorted((self.root / ".ocura-oss" / "runs").glob("*.json"))
        payload = json.loads(records[-1].read_text("utf-8"))["payload"]
        self.assertEqual(payload["declared_parameters"], {"eq": "a=b"})

    def test_json_listings_are_deterministic(self):
        self._run("run", "--root", str(self.root), "--", sys.executable, "-c", "print('one')")
        first = self._run("branch-points", "--root", str(self.root), "--json")
        second = self._run("branch-points", "--root", str(self.root), "--json")
        self.assertEqual(first[0], 0)
        self.assertEqual(first[1], second[1])
        pathways_first = self._run("pathways", "--root", str(self.root), "--json")
        pathways_second = self._run("pathways", "--root", str(self.root), "--json")
        self.assertEqual(pathways_first[1], pathways_second[1])

    def test_demo_failure_is_reported_and_directory_retained(self):
        from unittest import mock

        destination = self.parent / "demo-fail"
        with mock.patch(
            "ocura_oss.demo.runner.run_command",
            side_effect=RuntimeError("boom"),
        ):
            code, _text, stderr = self._run("demo", "--root", str(destination))
        self.assertEqual(code, 2)
        self.assertIn("baseline-run", stderr)
        self.assertIn("boom", stderr)
        self.assertTrue(destination.is_dir())
        self.assertTrue((destination / ".ocura-oss" / "ledger.json").is_file())

        with mock.patch(
            "ocura_oss.demo.runner.run_command",
            side_effect=RuntimeError("boom"),
        ):
            code, _text, stderr = self._run("demo", "--root", str(destination))
        self.assertEqual(code, 2)
        self.assertIn("already exists", stderr)

    def test_branch_json_output_is_pure_json(self):
        code, run_text, _ = self._run(
            "run", "--root", str(self.root), "--", sys.executable, "-c", "print('base')"
        )
        self.assertEqual(code, 0)
        branch_point_id = [
            line.split(":", 1)[1].strip()
            for line in run_text.splitlines()
            if line.startswith("branch point:")
        ][0]
        code, branch_json, _ = self._run_in_root(
            "branch",
            "--from",
            branch_point_id,
            "--reason",
            "json purity",
            "--param",
            "batch=2",
            "--json",
        )
        self.assertEqual(code, 0)
        payload = json.loads(branch_json)
        self.assertEqual(payload["reason"], "json purity")
        self.assertEqual(payload["parameters"], {"batch": "2"})

    def test_verify_reports_ok_for_intact_state(self):
        self._run("run", "--root", str(self.root), "--", sys.executable, "-c", "print('v')")
        code, text, _ = self._run("verify", "--root", str(self.root))
        self.assertEqual(code, 0)
        self.assertIn("integrity: ok", text)
        self.assertIn("1 runs", text)
        self.assertIn("2 logs", text)

        code, verify_json, _ = self._run("verify", "--root", str(self.root), "--json")
        self.assertEqual(code, 0)
        payload = json.loads(verify_json)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["problems"], [])
        self.assertEqual(payload["counts"]["logs_checked"], 2)

    def test_verify_fails_closed_on_missing_log(self):
        code, run_text, _ = self._run(
            "run", "--root", str(self.root), "--", sys.executable, "-c", "print('v')"
        )
        self.assertEqual(code, 0)
        run_id = [
            line.split(":", 1)[1].strip()
            for line in run_text.splitlines()
            if line.startswith("run:")
        ][0]
        (self.root / ".ocura-oss" / "logs" / f"{run_id}.stdout.log").unlink()

        code, text, _ = self._run("verify", "--root", str(self.root))
        self.assertEqual(code, 2)
        self.assertIn("integrity: failed", text)
        self.assertIn(f"{run_id}.json", text)

        code, verify_json, _ = self._run("verify", "--root", str(self.root), "--json")
        self.assertEqual(code, 2)
        payload = json.loads(verify_json)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(len(payload["problems"]), 1)

    def test_verify_requires_existing_state(self):
        missing = self.parent / "no-state"
        code, _text, stderr = self._run("verify", "--root", str(missing))
        self.assertEqual(code, 2)
        self.assertIn("error:", stderr)

    def test_run_without_separator_gets_guidance(self):
        code, _text, stderr = self._run(
            "run",
            "--root",
            str(self.root),
            sys.executable,
            "-c",
            "print('never runs')",
        )
        self.assertEqual(code, 2)
        self.assertIn("--", stderr)
        self.assertIn("ocura-oss run -- python script.py", stderr)

    def test_branch_accepts_explicit_root(self):
        code, run_text, _ = self._run(
            "run", "--root", str(self.root), "--", sys.executable, "-c", "print('base')"
        )
        self.assertEqual(code, 0)
        branch_point_id = [
            line.split(":", 1)[1].strip()
            for line in run_text.splitlines()
            if line.startswith("branch point:")
        ][0]

        elsewhere = self.parent / "elsewhere"
        elsewhere.mkdir()
        with contextlib.chdir(str(elsewhere)):
            code, branch_text, _err = self._run(
                "branch",
                "--from",
                branch_point_id,
                "--reason",
                "explicit root",
                "--root",
                str(self.root),
            )
        self.assertEqual(code, 0)
        child_id = [
            line.split(":", 1)[1].strip()
            for line in branch_text.splitlines()
            if line.startswith("pathway:")
        ][0]
        pathways_dir = self.root / ".ocura-oss" / "pathways"
        self.assertIn(f"{child_id}.json", {p.name for p in pathways_dir.iterdir()})
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_verify_json_names_run_with_broken_reference(self):
        from ocura_oss import model
        from ocura_oss.store import Store

        root = self.parent / "broken-run"
        store = Store(root)
        store.initialize_state(name="broken ref")
        empty_digest = hashlib.sha256(b"").hexdigest()
        run_id = model.make_id("run")
        for suffix in ("stdout", "stderr"):
            (store.logs_dir / f"{run_id}.{suffix}.log").write_bytes(b"")
        store._save_run(
            model.RunRecord(
                id=run_id,
                pathway_id="pathway-" + "f" * 32,
                started_at="2026-01-01T00:00:00.000000+00:00",
                finished_at="2026-01-01T00:00:01.000000+00:00",
                duration_seconds=1.0,
                outcome=model.Outcome.PASSED,
                return_code=0,
                launch_error_category=None,
                declared_parameters={},
                command=("x",),
                stdout_log=f".ocura-oss/logs/{run_id}.stdout.log",
                stderr_log=f".ocura-oss/logs/{run_id}.stderr.log",
                stdout_bytes=0,
                stderr_bytes=0,
                stdout_sha256=empty_digest,
                stderr_sha256=empty_digest,
            )
        )
        code, verify_json, _ = self._run("verify", "--root", str(root), "--json")
        self.assertEqual(code, 2)
        payload = json.loads(verify_json)
        self.assertEqual(payload["status"], "failed")
        named = [item for item in payload["problems"] if item["record"] == f"{run_id}.json"]
        self.assertTrue(named)
        self.assertIn("pathway", named[0]["problem"])

    def test_deep_lineage_recursion_maps_to_clean_error(self):
        from unittest import mock

        with mock.patch("ocura_oss.branching.compare", side_effect=RecursionError):
            code, _text, stderr = self._run("compare", "--root", str(self.root))
        self.assertEqual(code, 2)
        self.assertIn("too deeply nested", stderr)


if __name__ == "__main__":
    unittest.main()
