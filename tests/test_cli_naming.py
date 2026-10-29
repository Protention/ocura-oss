# SPDX-License-Identifier: Apache-2.0

"""Familiar CLI vocabulary retains stored IDs and legacy command results."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from ocura_oss import Store, cli, initialize, run


class CliNamingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        initialize(self.root)
        self.baseline = run([sys.executable, "-c", "pass"], root=self.root)

    def invoke(self, command, *arguments):
        code, stdout, stderr = self.invoke_result(command, *arguments)
        self.assertEqual((code, stderr), (0, ""))
        return stdout

    def invoke_result(self, command, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main([command, "--root", str(self.root), *arguments])
        return code, stdout.getvalue(), stderr.getvalue()

    def record_compressible_output(self):
        return run(
            [sys.executable, "-c", "print('saved evidence' * 1024)"],
            root=self.root,
            compress=False,
        ).atom

    def state_bytes(self):
        state = self.root / ".ocura-oss"
        return {
            path.relative_to(state): path.read_bytes()
            for path in state.rglob("*")
            if path.is_file()
        }

    def test_canonical_and_legacy_listing_have_identical_json(self):
        canonical = json.loads(self.invoke("branch-points", "--json"))
        legacy = json.loads(self.invoke("chokepoints", "--json"))
        self.assertEqual(canonical, legacy)
        self.assertEqual(set(canonical), {"chokepoints"})
        self.assertEqual(canonical["chokepoints"][0]["id"], self.baseline.chokepoint.id)
        self.assertEqual(canonical["chokepoints"][0]["atom_id"], self.baseline.atom.id)

    def test_canonical_text_uses_run_and_legacy_listing_keeps_atom(self):
        self.assertIn(f"run={self.baseline.atom.id}", self.invoke("branch-points"))
        self.assertIn(f"atom={self.baseline.atom.id}", self.invoke("chokepoints"))

    def test_familiar_text_keeps_original_identifiers(self):
        text = self.invoke("show", self.baseline.atom.id)
        self.assertIn(f"run: {self.baseline.atom.id}\n", text)
        self.assertIn(f"branch point: {self.baseline.chokepoint.id}\n", text)
        branch = json.loads(
            self.invoke(
                "branch",
                "--from",
                self.baseline.chokepoint.id,
                "--reason",
                "new variation",
                "--json",
            )
        )
        self.assertEqual(branch["source_chokepoint_id"], self.baseline.chokepoint.id)
        self.assertFalse(branch["has_terminal_evidence"])

    def test_help_explains_legacy_ids_without_promising_checkpoint_restore(self):
        parser = cli._build_parser()
        self.assertIn("branch-points", parser.format_help())
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as stopped:
            cli.main(["branch-points", "--help"])
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn("chokepoint-... IDs remain valid", stdout.getvalue())
        self.assertIn("does not restore files, processes, or model state", stdout.getvalue())

    def test_run_and_atom_storage_selectors_have_identical_results(self):
        atom = self.record_compressible_output()
        state = Store(self.root)
        original = state.read_verified_log(atom.id)
        previews = [
            json.loads(
                self.invoke(
                    "compress", selector, atom.id, "--min-bytes", "1", "--dry-run", "--json"
                )
            )
            for selector in ("--run", "--atom")
        ]
        self.assertEqual(previews[0], previews[1])
        first_compression = json.loads(
            self.invoke("compress", "--run", atom.id, "--min-bytes", "1", "--json")
        )
        restore_previews = [
            json.loads(self.invoke("decompress", selector, atom.id, "--dry-run", "--json"))
            for selector in ("--run", "--atom")
        ]
        self.assertEqual(restore_previews[0], restore_previews[1])
        first_restore = json.loads(self.invoke("decompress", "--atom", atom.id, "--json"))
        second_compression = json.loads(
            self.invoke("compress", "--atom", atom.id, "--min-bytes", "1", "--json")
        )
        second_restore = json.loads(self.invoke("decompress", "--run", atom.id, "--json"))
        self.assertEqual(first_compression, second_compression)
        self.assertEqual(first_restore, second_restore)
        self.assertEqual(state.read_verified_log(atom.id), original)
        self.assertTrue(state.verify_state().ok)

    def test_equivalent_run_and_atom_prefixes_can_be_combined(self):
        atom = self.record_compressible_output()
        for command, extra in (("compress", ["--min-bytes", "1"]), ("decompress", [])):
            with self.subTest(command=command):
                combined = json.loads(
                    self.invoke(
                        command, "--run", atom.id[5:17], "--atom", atom.id, *extra, "--json"
                    )
                )
                self.assertEqual({item["atom_id"] for item in combined["changes"]}, {atom.id})
        self.assertTrue(Store(self.root).verify_state().ok)

    def test_conflicting_storage_selectors_leave_all_evidence_unchanged(self):
        atom = self.record_compressible_output()
        for command in ("compress", "decompress"):
            with self.subTest(command=command):
                if command == "decompress":
                    self.invoke("compress", "--run", atom.id, "--min-bytes", "1", "--json")
                before = self.state_bytes()
                code, stdout, stderr = self.invoke_result(
                    command, "--run", atom.id, "--atom", self.baseline.atom.id, "--json-errors"
                )
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(stdout)["error"]["code"], "invalid_input")
                self.assertIn("--run and --atom must identify the same run", stderr)
                self.assertEqual(self.state_bytes(), before)

    def test_help_prefers_ledger_and_run_terms(self):
        for command, expected in (
            ("init", "name recorded in the project ledger"),
            ("logs", "run ID (atom-...)"),
            ("compress", "--run ID"),
            ("decompress", "--run ID"),
        ):
            with self.subTest(command=command):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as stopped:
                    cli.main([command, "--help"])
                self.assertEqual(stopped.exception.code, 0)
                self.assertIn(expected, stdout.getvalue())

    def test_demo_text_uses_branch_point_but_json_preserves_step_name(self):
        for suffix, flags in (("text", []), ("json", ["--json"])):
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = cli.main(["demo", "--root", str(self.root / suffix), *flags])
            self.assertEqual((code, stderr.getvalue()), (0, ""))
            if flags:
                self.assertIn(
                    "chokepoint", [step["step"] for step in json.loads(stdout.getvalue())["steps"]]
                )
            else:
                self.assertIn("  branch-point:", stdout.getvalue())
                self.assertNotIn("  chokepoint:", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
