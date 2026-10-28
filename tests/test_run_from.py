# SPDX-License-Identifier: Apache-2.0

"""Creating a variation and running it preserves the existing workflow contracts."""

import errno
import sys
import tempfile
import unittest
from collections import UserDict
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch, sentinel

from ocura_oss import Outcome, RunExecution, Store, StoreError, api


class RunFromTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        api.initialize(self.root)
        self.store = Store(self.root)
        baseline = api.run([sys.executable, "-c", "print('baseline')"], root=self.root)
        self.parent = api.branch(
            baseline.branch_point.id,
            root=self.root,
            reason="source pathway",
            parameters={"inherited": "pathway", "changed": "pathway value"},
        )
        self.source = api.run(
            [sys.executable, "-c", "print('source')"],
            root=self.root,
            pathway_id=self.parent.id,
            parameters={"run_only": "source only", "changed": "source run value"},
        )
        self.source_id = self.source.branch_point.id

    def inventory(self):
        return {
            path.relative_to(self.store.state_dir): path.read_bytes()
            for path in self.store.state_dir.rglob("*")
            if path.is_file()
        }

    def run_from(self, command, **options):
        return api.run(
            command,
            root=self.root,
            source_branch_point_id=self.source_id,
            reason="try a variation",
            **options,
        )

    def test_matches_explicit_branch_then_run_without_inheriting_source_run_labels(self):
        command = [
            sys.executable,
            "-c",
            "import sys; print('|'.join(sys.argv[1:]))",
            "{inherited}",
            "{changed}",
        ]
        overrides = {"changed": "variation", "added": "yes"}
        manual = api.branch(
            self.source_id, root=self.root, reason="try a variation", parameters=overrides
        )
        explicit = api.run(command, root=self.root, pathway_id=manual.id, substitute=True)
        combined = self.run_from(command, parameters=overrides, substitute=True)
        child = self.store.load_pathway(combined.record.pathway_id)
        self.assertIs(type(combined), RunExecution)
        self.assertEqual(
            [field.name for field in fields(combined)], ["atom", "chokepoint", "storage_warnings"]
        )
        self.assertEqual(child.parent_pathway_id, self.parent.id)
        self.assertEqual(child.source_branch_point_id, self.source_id)
        self.assertEqual(child.reason, manual.reason)
        self.assertEqual(child.parameters, manual.parameters)
        self.assertEqual(
            child.parameters, {"inherited": "pathway", "changed": "variation", "added": "yes"}
        )
        self.assertEqual(combined.record.declared_parameters, explicit.record.declared_parameters)
        self.assertEqual(combined.record.command, explicit.record.command)
        self.assertEqual(combined.record.outcome, explicit.record.outcome)
        self.assertEqual(
            self.store.read_verified_log(combined.record.id),
            self.store.read_verified_log(explicit.record.id),
        )
        self.assertNotIn("run_only", combined.record.declared_parameters)
        comparison = api.compare(self.source_id, root=self.root)
        self.assertEqual(comparison.state.value, "ready")
        self.assertEqual(len(comparison.children), 2)
        self.assertTrue(self.store.verify_state().ok)

    def test_invalid_combinations_and_command_shapes_do_not_create_a_child(self):
        valid = [sys.executable, "-c", "pass"]
        base = {"source_branch_point_id": self.source_id, "reason": "variation"}
        cases = (
            (valid, {**base, "pathway_id": self.parent.id}),
            (valid, {**base, "pathway_id": ""}),
            (valid, {"reason": "reason alone"}),
            (valid, {"source_branch_point_id": self.source_id}),
            (valid, {**base, "reason": " \t"}),
            (valid, {**base, "reason": 7}),
            (valid, {**base, "source_branch_point_id": ""}),
            (valid, {**base, "source_branch_point_id": self.source.record.id}),
            (valid, {**base, "source_branch_point_id": self.source_id[:15]}),
            (valid, {**base, "source_branch_point_id": 7}),
            ([], base),
            ("python -V", base),
            (b"python -V", base),
            (None, base),
            (7, base),
            ([sys.executable, ""], base),
            ([sys.executable, 7], base),
            (valid, {**base, "parameters": 7}),
            (valid, {**base, "parameters": "bad"}),
            (valid, {**base, "parameters": b"bad"}),
            (valid, {**base, "parameters": []}),
            (valid, {**base, "parameters": [("key", "value")]}),
            (valid, {**base, "parameters": (("key", "value"),)}),
            (valid, {**base, "parameters": iter([("key", "value")])}),
            (valid, {**base, "parameters": {("key", "value")}}),
            (valid, {**base, "parameters": {"invalid key": "value"}}),
            (valid, {**base, "parameters": {"key": 7}}),
        )
        before = self.inventory()
        for command, arguments in cases:
            with (
                self.subTest(command=command, arguments=arguments),
                patch("ocura_oss.api.runner.run_command") as execute,
            ):
                with self.assertRaises(StoreError) as failed:
                    api.run(command, root=self.root, **arguments)
                self.assertEqual(
                    (failed.exception.code, failed.exception.execution),
                    ("invalid_input", "not_started"),
                )
                execute.assert_not_called()
                self.assertEqual(self.inventory(), before)

    def test_custom_parameter_mapping_is_used_by_child_and_run(self):
        parameters = UserDict({"changed": "custom mapping", "added": "yes"})
        result = self.run_from(
            [sys.executable, "-c", "import sys; print(sys.argv[1])", "{changed}"],
            parameters=parameters,
            substitute=True,
        )
        child = self.store.load_pathway(result.record.pathway_id)
        expected = {"inherited": "pathway", "changed": "custom mapping", "added": "yes"}
        self.assertEqual(child.parameters, expected)
        self.assertEqual(result.record.declared_parameters, expected)
        self.assertEqual(self.store.read_verified_log(result.record.id).strip(), b"custom mapping")
        self.assertEqual(parameters, {"changed": "custom mapping", "added": "yes"})
        self.assertTrue(self.store.verify_state().ok)

    def test_missing_or_corrupted_source_is_rejected_without_child_creation(self):
        before = self.inventory()
        with self.assertRaises(StoreError) as missing:
            api.run(
                [sys.executable, "-c", "pass"],
                root=self.root,
                source_branch_point_id="chokepoint-" + "0" * 32,
                reason="missing source",
            )
        self.assertEqual(missing.exception.code, "record_not_found")
        self.assertEqual(missing.exception.execution, "not_started")
        self.assertEqual(self.inventory(), before)

        self.store.resolve_log_path(self.source.record, "stdout").write_bytes(b"changed evidence")
        damaged = self.inventory()
        with patch("ocura_oss.api.runner.run_command") as execute:
            with self.assertRaises(StoreError) as failure:
                self.run_from([sys.executable, "-c", "pass"])
            execute.assert_not_called()
        self.assertEqual(
            (failure.exception.code, failure.exception.execution),
            ("integrity_error", "not_started"),
        )
        self.assertEqual(failure.exception.ids["source_branch_point_id"], self.source_id)
        self.assertEqual(failure.exception.ids["run_id"], self.source.record.id)
        self.assertEqual(self.inventory(), damaged)

    def test_source_diagnostics_remain_authoritative(self):
        cause = PermissionError("source denied")
        diagnostic = StoreError(
            "cannot inspect source",
            code="permission_denied",
            recovery="check_permissions",
            ids={"source_branch_point_id": "source diagnostic", "run_id": self.source.record.id},
        )
        diagnostic.__cause__ = cause
        before = self.inventory()
        with (
            patch("ocura_oss.api.branching.load_verified_source", side_effect=diagnostic),
            self.assertRaises(StoreError) as failed,
        ):
            self.run_from([sys.executable, "-c", "pass"])
        self.assertIs(failed.exception, diagnostic)
        self.assertIs(failed.exception.__cause__, cause)
        self.assertEqual(failed.exception.code, "permission_denied")
        self.assertEqual(failed.exception.recovery, "check_permissions")
        self.assertEqual(failed.exception.ids["source_branch_point_id"], "source diagnostic")
        self.assertEqual(failed.exception.execution, "not_started")
        self.assertEqual(self.inventory(), before)

    def test_later_preparation_failure_retains_child_for_explicit_continuation(self):
        before = len(self.store.list_pathways())
        records_before = len(self.store.list_atoms())
        command = [sys.executable, "-c", "import sys; print(sys.argv[1])", "{missing}"]
        with self.assertRaises(StoreError) as failure:
            self.run_from(command, parameters={"candidate": "yes"}, substitute=True)
        error = failure.exception
        self.assertEqual((error.code, error.execution), ("invalid_input", "not_started"))
        self.assertEqual(set(error.ids), {"source_branch_point_id", "pathway_id"})
        child = self.store.load_pathway(error.ids["pathway_id"])
        self.assertEqual(child.source_branch_point_id, self.source_id)
        self.assertEqual(len(self.store.list_pathways()), before + 1)
        self.assertEqual(len(self.store.list_atoms()), records_before)
        self.assertEqual(api.compare(self.source_id, root=self.root).state.value, "partial")
        continued = api.run(
            command,
            root=self.root,
            pathway_id=child.id,
            parameters={"missing": "supplied"},
            substitute=True,
        )
        self.assertEqual(continued.record.pathway_id, child.id)
        self.assertEqual(self.store.read_verified_log(continued.record.id).strip(), b"supplied")
        self.assertEqual(len(self.store.list_pathways()), before + 1)
        self.assertEqual(len(self.store.list_atoms()), records_before + 1)
        self.assertNotIn("missing", self.store.load_pathway(child.id).parameters)

    def test_failed_and_unlaunched_commands_return_normal_results(self):
        for command, outcome in (
            ([sys.executable, "-c", "raise SystemExit(7)"], Outcome.FAILED),
            (["ocura-run-from-no-such-command"], Outcome.LAUNCH_FAILED),
        ):
            with self.subTest(outcome=outcome):
                execution = self.run_from(command)
                self.assertIs(type(execution), RunExecution)
                self.assertIs(execution.record.outcome, outcome)
                self.assertEqual(
                    self.store.load_pathway(execution.record.pathway_id).source_branch_point_id,
                    self.source_id,
                )
                self.assertEqual(execution.branch_point.run_id, execution.record.id)
        self.assertTrue(self.store.verify_state().ok)

    def test_interrupted_command_returns_normal_result(self):
        with patch(
            "ocura_oss.runner._execute", return_value=(None, "interrupted", True)
        ) as execute:
            execution = self.run_from(["simulated-command"])
        execute.assert_called_once()
        self.assertIs(execution.record.outcome, Outcome.INTERRUPTED)
        self.assertIsNone(execution.record.return_code)
        self.assertEqual(self.store.list_attempts(), [])
        self.assertTrue(self.store.verify_state().ok)

    def test_post_launch_failure_preserves_unknown_or_recorded_and_does_not_retry(self):
        for method, execution in (("_save_chokepoint", "unknown"), ("_remove_attempt", "recorded")):
            with self.subTest(method=method):
                marker = self.root / method
                command = [
                    sys.executable,
                    "-c",
                    f"from pathlib import Path; Path({str(marker)!r}).open('a').write('once\\n')",
                ]
                with (
                    patch.object(
                        Store, method, side_effect=OSError(errno.ENOSPC, "injected disk full")
                    ),
                    self.assertRaises(StoreError) as failure,
                ):
                    self.run_from(command)
                error = failure.exception
                self.assertEqual((error.code, error.execution), ("io_error", execution))
                self.assertIsInstance(error.__cause__, OSError)
                self.assertEqual(
                    set(error.ids),
                    {"source_branch_point_id", "pathway_id", "run_id", "branch_point_id"},
                )
                self.assertEqual(error.ids["source_branch_point_id"], self.source_id)
                child = self.store.load_pathway(error.ids["pathway_id"])
                self.assertEqual(child.source_branch_point_id, self.source_id)
                self.assertEqual(self.store.load_run(error.ids["run_id"]).pathway_id, child.id)
                self.assertEqual(marker.read_text(), "once\n")
                self.assertEqual(len(api.recover(self.root)), 1)
                self.assertEqual(marker.read_text(), "once\n")
                self.assertTrue(self.store.verify_state().ok)

    def test_all_execution_options_reach_the_single_existing_runner(self):
        command = [sys.executable, "-c", "print('forwarded')", "secret"]
        options = {
            "parameters": {"candidate": "yes"},
            "mirror": True,
            "capture": False,
            "compress": False,
            "compression_min_bytes": 4096,
            "masked_arguments": [3],
            "substitute": True,
            "context": True,
            "context_files": [Path("settings.json")],
        }
        with patch("ocura_oss.api.runner.run_command", return_value=sentinel.execution) as execute:
            result = self.run_from(command, **options)
        self.assertIs(result, sentinel.execution)
        execute.assert_called_once()
        arguments = execute.call_args.kwargs
        self.assertEqual(arguments["argv"], tuple(command))
        self.assertEqual(arguments["declared_parameters"], options["parameters"])
        for key, value in options.items():
            if key != "parameters":
                self.assertEqual(arguments[key], value)
        child = self.store.load_pathway(arguments["pathway_id"])
        self.assertEqual(child.parameters["candidate"], "yes")
        self.assertEqual(child.source_branch_point_id, self.source_id)


if __name__ == "__main__":
    unittest.main()
