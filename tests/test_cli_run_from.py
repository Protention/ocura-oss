# SPDX-License-Identifier: Apache-2.0

import contextlib
import io
import itertools
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ocura_oss import Store, StoreError, cli, initialize, model, run


class RunFromCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "project with spaces"
        self.initial = initialize(self.root)
        self.baseline = run([sys.executable, "-c", "print('baseline')"], root=self.root)
        self.store = Store(self.root)

    def invoke(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(["run", "--root", str(self.root), *arguments])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_installed_entrypoint_resolves_source_prefix_and_keeps_command_tokens(self):
        command = [
            sys.executable,
            "-c",
            "import json,sys; print(json.dumps(sys.argv[1:]))",
            "--from",
            "child option",
            "--reason",
            "--json-errors",
            "{mode}",
        ]
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ocura_oss",
                "run",
                "--root",
                str(self.root),
                "--from",
                self.baseline.branch_point.id[:24],
                "--reason",
                "explicit source variation",
                "--param",
                "mode=normalize",
                "--substitute",
                "--json-errors",
                "--",
                *command,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        ordinary_code, ordinary, _ = self.invoke("--json", "--", sys.executable, "-c", "pass")
        self.assertEqual(ordinary_code, 0)
        self.assertEqual(set(data), set(json.loads(ordinary)))
        record = self.store.load_run(data["atom_id"])
        child = self.store.load_pathway(data["pathway_id"])
        self.assertEqual(child.source_branch_point_id, self.baseline.branch_point.id)
        self.assertEqual(child.parameters, {"mode": "normalize"})
        self.assertEqual(record.declared_parameters, child.parameters)
        self.assertEqual(
            json.loads(self.store.read_verified_log(record.id)),
            ["--from", "child option", "--reason", "--json-errors", "normalize"],
        )
        self.assertEqual(len(self.store.list_pathways()), 2)
        self.assertTrue(self.store.verify_state().ok)

    def test_invalid_selection_does_not_create_pathway_or_launch(self):
        # Two points make the valid common prefix deliberately ambiguous.
        numbers = itertools.count(1)
        with patch.object(
            model, "make_id", side_effect=lambda kind: f"{kind}-abcd{next(numbers):028x}"
        ):
            for _ in range(2):
                run([sys.executable, "-c", "pass"], root=self.root)
        source = self.baseline.branch_point.id
        cases = (
            (["--from", source], [sys.executable, "-c", "pass"], "invalid_input"),
            (["--from", source, "--reason", "  "], ["never"], "invalid_input"),
            (["--reason", "missing source"], ["never"], "invalid_input"),
            (
                ["--from", source, "--reason", "x", "--pathway", self.initial.pathway.id],
                ["never"],
                "invalid_input",
            ),
            (["--from", "chokepoint-abcd", "--reason", "x"], ["never"], "ambiguous_id"),
            (["--from", source, "--reason", "x"], [], "invalid_input"),
            (["--from", source, "--reason", "x", "--param", "bad"], ["never"], "invalid_input"),
        )
        before = self.store.list_pathways(), self.store.list_runs(limit=None).runs
        for options, command, expected in cases:
            with self.subTest(options=options), patch("ocura_oss.runner._execute") as execute:
                code, text, _ = self.invoke(*options, "--json-errors", "--", *command)
                self.assertEqual(code, 2)
                error = json.loads(text)["error"]
                self.assertEqual(error["code"], expected)
                self.assertEqual(error["execution"], "not_started")
                execute.assert_not_called()
                self.assertEqual(
                    (self.store.list_pathways(), self.store.list_runs(limit=None).runs), before
                )

    def test_late_validation_returns_child_for_deliberate_continuation(self):
        code, text, _ = self.invoke(
            "--from",
            self.baseline.branch_point.id,
            "--reason",
            "x",
            "--substitute",
            "--json-errors",
            "--",
            sys.executable,
            "-c",
            "print('{missing}')",
        )
        self.assertEqual(code, 2)
        error = json.loads(text)["error"]
        self.assertEqual(error["execution"], "not_started")
        self.assertEqual(error["ids"]["source_branch_point_id"], self.baseline.branch_point.id)
        child_id = error["ids"]["pathway_id"]
        self.assertEqual(len(self.store.list_runs(limit=None).runs), 1)
        code, text, _ = self.invoke(
            "--pathway", child_id, "--json", "--", sys.executable, "-c", "pass"
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)["pathway_id"], child_id)
        self.assertEqual(len(self.store.list_pathways()), 2)

    def test_recorded_failures_and_interruptions_keep_existing_exit_contract(self):
        cases = (
            ([sys.executable, "-c", "import sys; sys.exit(7)"], 1, "failed", False),
            (["ocura-run-from-no-such-executable"], 3, "launch_failed", False),
            ([sys.executable, "-c", "pass"], 1, "interrupted", True),
        )
        for command, expected_code, outcome, interrupted in cases:
            context = (
                patch("ocura_oss.runner._execute", return_value=(None, "interrupted", True))
                if interrupted
                else contextlib.nullcontext()
            )
            with self.subTest(outcome=outcome), context:
                code, text, _ = self.invoke(
                    "--from",
                    self.baseline.branch_point.id,
                    "--reason",
                    outcome,
                    "--json-errors",
                    "--",
                    *command,
                )
            self.assertEqual(code, expected_code)
            result = json.loads(text)
            self.assertEqual(result["outcome"], outcome)
            self.assertNotIn("error", result)
            self.assertEqual(self.store.load_run(result["atom_id"]).outcome.value, outcome)
        self.assertEqual(len(self.store.list_runs(limit=None).runs), 4)
        self.assertTrue(self.store.verify_state().ok)

    def test_post_launch_errors_preserve_execution_and_ids(self):
        source = self.baseline.branch_point.id
        for state, recovery in (
            ("unknown", "inspect_attempts_and_runs"),
            ("recorded", "inspect_recorded_run"),
        ):
            failure = StoreError(
                "injected storage failure",
                code="io_error",
                execution=state,
                ids={"run_id": "known-run", "branch_point_id": "terminal-point"},
            )
            with (
                self.subTest(state=state),
                patch("ocura_oss.runner.run_command", side_effect=failure) as execute,
            ):
                code, text, _ = self.invoke(
                    "--from",
                    source,
                    "--reason",
                    "x",
                    "--json-errors",
                    "--",
                    sys.executable,
                    "-c",
                    "pass",
                )
            execute.assert_called_once()
            self.assertEqual(code, 2)
            error = json.loads(text)["error"]
            self.assertEqual(error["execution"], state)
            self.assertEqual(error["recovery"], recovery)
            self.assertEqual(error["ids"]["run_id"], "known-run")
            self.assertEqual(error["ids"]["branch_point_id"], "terminal-point")
            self.assertEqual(error["ids"]["source_branch_point_id"], source)
            self.store.load_pathway(error["ids"]["pathway_id"])

    def test_combined_cli_appends_to_frozen_ledger_without_rewriting_old_evidence(self):
        fixture = Path(__file__).parent / "fixtures" / "ledger-0.5.0" / "state"
        self.root = self.root.parent / "old ledger"
        shutil.copytree(fixture, self.root / ".ocura-oss")
        old_store = Store(self.root)
        manifest = old_store.verify_state().manifest
        original = {
            path.relative_to(fixture): path.read_bytes()
            for path in fixture.rglob("*")
            if path.is_file()
        }
        code, text, _ = self.invoke(
            "--from",
            "chokepoint-1592911372f143208bfd75af9a5c83a5",
            "--reason",
            "continue old ledger",
            "--param",
            "batch=2",
            "--json",
            "--",
            sys.executable,
            "-c",
            "print('new')",
        )
        self.assertEqual(code, 0, text)
        result = json.loads(text)
        self.assertEqual(
            old_store.load_atom(result["atom_id"]), old_store.load_run(result["atom_id"])
        )
        self.assertTrue(old_store.verify_state(against=manifest).ok)
        for relative, contents in original.items():
            self.assertEqual((old_store.state_dir / relative).read_bytes(), contents, str(relative))


if __name__ == "__main__":
    unittest.main()
