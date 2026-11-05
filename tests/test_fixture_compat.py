# SPDX-License-Identifier: Apache-2.0

"""A ledger in the 0.8.0 layout must stay readable for as long as schema 3 is kept.

The fixtures under ``tests/fixtures/`` are frozen. Do not regenerate or edit
them: a change that makes these tests fail is a format break and needs a new
schema version.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

import ocura_oss
from ocura_oss import model
from ocura_oss.store import STATE_DIR_NAME, Store

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIXTURES / "ledger-0.8.0" / "state"

LEDGER = "ledger-13d7ee10f1134fdfacbc6baa7eb34084"
DEFAULT_PATHWAY = "pathway-c6132f322f6d463ab8b7876c695eb1e9"
CHILD_PATHWAY = "pathway-1ac386d75d104e4abb977fd43ea0bb55"
BASELINE = ("run-17a15137e9a14cfdbbe777be50d7446d", "branch-point-1592911372f143208bfd75af9a5c83a5")
FAILED = ("run-be8ce17fdceb49debd9cf29e56923107", "branch-point-2e49e6425f0949a9bd67e02fe5d3974d")
UNLAUNCHED = (
    "run-50f6086ef6d64817a3360313043adfb8",
    "branch-point-da5671fe22bc467ca6e09578ed99ac2a",
)
ABANDONED = (
    "run-bd901829c6b44e91a8def06ffb2e9c64",
    "branch-point-79b6741543fe4a8cbd424ffcd52eec5a",
)
UNCAPTURED = (
    "run-869bc8340bf043569f61d779a0beccfa",
    "branch-point-eb99f2380d7040ea802c62a0b67d16a7",
)


class FrozenLedgerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "project"
        shutil.copytree(FIXTURE, self.root / STATE_DIR_NAME)
        self.store = Store(self.root)

    def test_complete_state_verifies(self):
        report = ocura_oss.verify(self.root)
        self.assertEqual(report.problems, ())
        self.assertEqual(
            (report.pathways, report.run_records, report.branch_points, report.logs_checked),
            (2, 5, 5, 8),
        )
        self.assertEqual((report.running_attempts, report.abandoned_attempts), ((), ()))
        self.assertEqual(len(report.manifest), 13)
        self.assertEqual(
            report.digest, "c188a4bc7d2720a363d8074948c4746404518ff3f7c97fe7163d5df753b8e8c3"
        )
        self.assertEqual(self.store.list_attempts(), [])
        self.assertEqual(ocura_oss.recover(self.root), ())

    def test_ledger_and_lineage_are_read(self):
        ledger = self.store.load_ledger()
        self.assertEqual(
            (ledger.id, ledger.name, ledger.default_pathway_id),
            (LEDGER, "fixture 0.5.0", DEFAULT_PATHWAY),
        )
        child = self.store.load_pathway(CHILD_PATHWAY)
        self.assertEqual(child.parent_pathway_id, DEFAULT_PATHWAY)
        self.assertEqual(child.source_branch_point_id, BASELINE[1])
        self.assertEqual(child.reason, "try batch 4")
        self.assertEqual(child.parameters, {"batch": "4"})
        self.assertEqual(
            [item.id for item in self.store.list_pathways()], [DEFAULT_PATHWAY, CHILD_PATHWAY]
        )

    def test_every_outcome_is_read_with_its_branch_point(self):
        expected = {
            BASELINE: (model.Outcome.PASSED, 0, None),
            FAILED: (model.Outcome.FAILED, 3, None),
            UNLAUNCHED: (model.Outcome.LAUNCH_FAILED, None, "executable_not_found"),
            ABANDONED: (model.Outcome.ABANDONED, None, None),
            UNCAPTURED: (model.Outcome.PASSED, 0, None),
        }
        for (run_id, branch_point_id), (outcome, return_code, category) in expected.items():
            with self.subTest(record=run_id):
                record = self.store.load_run(run_id)
                self.assertIs(record.outcome, outcome)
                self.assertEqual(record.return_code, return_code)
                self.assertEqual(record.launch_error_category, category)
                branch_point = self.store.load_branch_point(branch_point_id)
                self.assertEqual(branch_point.run_id, run_id)
                self.assertIs(branch_point.outcome, outcome)
                self.assertTrue(branch_point.branchable)
        self.assertEqual(len(self.store.list_run_records()), len(expected))

    def test_captured_output_is_read_verified(self):
        self.assertEqual(self.store.read_verified_log(BASELINE[0]), b"loss=0.50\n")
        self.assertEqual(
            self.store.read_verified_log(BASELINE[0], stream="stderr"), b"baseline warning\n"
        )
        self.assertEqual(self.store.read_verified_log(FAILED[0], stream="stderr"), b"boom\n")
        baseline = self.store.load_run(BASELINE[0])
        self.assertIs(baseline.output_capture, model.OutputCapture.FULL)
        self.assertEqual(baseline.declared_parameters, {"batch": "1"})
        self.assertEqual(baseline.masked_arguments, ())
        self.assertEqual(baseline.stdout_bytes, 10)

    def test_abandoned_run_keeps_what_was_known(self):
        record = self.store.load_run(ABANDONED[0])
        self.assertEqual(record.pathway_id, CHILD_PATHWAY)
        self.assertEqual(record.command, ("python", "train.py", "--batch", "4"))
        self.assertIsNone(record.finished_at)
        self.assertIsNone(record.duration_seconds)
        self.assertEqual(self.store.read_verified_log(record.id), b"partial\n")
        self.assertEqual(self.store.read_verified_log(record.id, stream="stderr"), b"")

    def test_uncaptured_masked_run_is_read(self):
        record = self.store.load_run(UNCAPTURED[0])
        self.assertIs(record.output_capture, model.OutputCapture.NONE)
        self.assertEqual(record.command, ("python", "-c", "pass", "--token", model.MASKED_ARGUMENT))
        self.assertEqual(record.masked_arguments, (4,))
        self.assertIsNone(record.stdout_log)
        self.assertIsNone(record.stderr_sha256)
        self.assertIsNotNone(record.duration_seconds)

    def test_comparison_uses_the_newest_child_run(self):
        for source in (None, BASELINE[1]):
            with self.subTest(source=source):
                comparison = ocura_oss.compare(source, root=self.root)
                self.assertIs(comparison.state, model.ComparisonState.READY)
                self.assertEqual(comparison.source_branch_point_id, BASELINE[1])
                (child,) = comparison.children
                self.assertEqual(child.pathway_id, CHILD_PATHWAY)
                self.assertEqual(child.child_run.id, UNCAPTURED[0])
                self.assertEqual(
                    child.parameters.to_dict(),
                    {"inherited": {}, "added": {"batch": "4"}, "changed": {}},
                )
                self.assertEqual(
                    child.run_parameters.to_dict()["changed"],
                    {"batch": {"child": "4", "source": "1"}},
                )

    def test_the_ledger_accepts_new_work(self):
        execution = ocura_oss.run(["ocura-fixture-no-such-executable"], root=self.root)
        self.assertIs(execution.record.outcome, model.Outcome.LAUNCH_FAILED)
        child = ocura_oss.branch(ABANDONED[1], root=self.root, reason="retry the abandoned run")
        self.assertEqual(child.parent_pathway_id, CHILD_PATHWAY)
        report = ocura_oss.verify(self.root)
        self.assertTrue(report.ok, report.problems)
        self.assertEqual((report.pathways, report.run_records), (3, 6))


class SchemaTwoLedgerTests(unittest.TestCase):
    def test_a_ledger_from_before_0_8_is_refused_untouched(self):
        old = FIXTURES / "ledger-0.5.0" / "state"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(old, root / STATE_DIR_NAME)
            store = Store(root)
            for call in (
                lambda: ocura_oss.verify(root),
                lambda: ocura_oss.run(["x"], root=root),
                store.list_pathways,
                store.list_run_records,
                store.list_branch_points,
            ):
                with self.subTest(call=call), self.assertRaises(ocura_oss.StoreError) as caught:
                    call()
                self.assertEqual(caught.exception.code, "unsupported_schema")
                self.assertIn('Keep using "ocura-oss<0.8"', str(caught.exception))
            self.assertEqual(_contents(root / STATE_DIR_NAME), _contents(old))


def _contents(directory):
    return {
        path.relative_to(directory): path.read_bytes() if path.is_file() else None
        for path in directory.rglob("*")
    }


if __name__ == "__main__":
    unittest.main()
