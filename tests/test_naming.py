# SPDX-License-Identifier: Apache-2.0

"""Public vocabulary changes must not rewrite schema-2 evidence or break callers."""

import json
import shutil
import sys
import tempfile
import unittest
import warnings
from dataclasses import asdict, fields
from pathlib import Path
from unittest.mock import patch

import ocura_oss
from ocura_oss import model
from ocura_oss.store import STATE_DIR_NAME

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ledger-0.5.0" / "state"
BASELINE = "atom-17a15137e9a14cfdbbe777be50d7446d"
SOURCE = "chokepoint-1592911372f143208bfd75af9a5c83a5"
CHILD = "pathway-1ac386d75d104e4abb977fd43ea0bb55"


class NamingCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "project"
        shutil.copytree(FIXTURE, self.root / STATE_DIR_NAME)
        self.store = ocura_oss.Store(self.root)

    def test_canonical_names_are_classes_and_legacy_names_are_exact_aliases(self):
        for canonical, legacy in (
            ("Ledger", "Den"),
            ("RunRecord", "Atom"),
            ("BranchPoint", "Chokepoint"),
        ):
            with self.subTest(canonical=canonical):
                cls = getattr(ocura_oss, canonical)
                self.assertEqual(cls.__name__, canonical)
                self.assertIs(cls, getattr(ocura_oss, legacy))
                self.assertIs(cls, getattr(model, canonical))
                self.assertIs(cls, getattr(model, legacy))
                self.assertIn(canonical, ocura_oss.__all__)

    def test_canonical_properties_leave_legacy_fields_and_json_projections_intact(self):
        ledger = self.store.load_den()
        record = self.store.load_atom(BASELINE)
        point = self.store.load_chokepoint(SOURCE)
        pathway = self.store.load_pathway(CHILD)
        self.assertIs(type(ledger), ocura_oss.Ledger)
        self.assertIs(type(record), ocura_oss.RunRecord)
        self.assertIs(type(point), ocura_oss.BranchPoint)
        self.assertEqual(pathway.ledger_id, ledger.id)
        self.assertEqual(pathway.source_branch_point_id, point.id)
        self.assertEqual(point.run_id, record.id)
        self.assertIn("den_id", asdict(pathway))
        self.assertIn("source_chokepoint_id", asdict(pathway))
        self.assertNotIn("ledger_id", asdict(pathway))
        self.assertNotIn("source_branch_point_id", asdict(pathway))
        self.assertIn("atom_id", asdict(point))
        self.assertNotIn("run_id", asdict(point))
        self.assertEqual(
            {field.name for field in fields(point)},
            {"id", "pathway_id", "atom_id", "created_at", "kind", "outcome", "branchable"},
        )
        # Constructors still accept exactly the dataclass fields used by existing callers.
        self.assertEqual(ocura_oss.Ledger(**asdict(ledger)), ledger)
        self.assertEqual(ocura_oss.RunRecord(**asdict(record)), record)
        self.assertEqual(ocura_oss.BranchPoint(**asdict(point)), point)

        attempt = model.Attempt(
            id=record.id,
            pathway_id=record.pathway_id,
            chokepoint_id=point.id,
            started_at=record.started_at,
            declared_parameters=record.declared_parameters,
            command=record.command,
            stdout_log=record.stdout_log,
            stderr_log=record.stderr_log,
        )
        self.assertEqual((attempt.run_id, attempt.branch_point_id), (record.id, point.id))
        self.assertNotIn("branch_point_id", asdict(attempt))
        comparison = ocura_oss.compare(source_branch_point_id=point.id, root=self.root)
        self.assertEqual(comparison.source_branch_point_id, point.id)
        self.assertEqual(comparison.source_run.run_id, record.id)
        self.assertEqual(comparison.children[0].source_branch_point_id, point.id)
        legacy_json = comparison.to_dict()
        self.assertEqual(legacy_json["source_chokepoint_id"], point.id)
        self.assertNotIn("source_branch_point_id", legacy_json)
        self.assertNotIn("source_branch_point_id", legacy_json["children"][0])
        self.assertNotIn("run_id", legacy_json["source_run"])

    def test_existing_payloads_bytes_and_manifest_survive_canonical_access(self):
        state = self.root / STATE_DIR_NAME
        before = {
            path.relative_to(state): path.read_bytes()
            for path in state.rglob("*")
            if path.is_file()
        }
        original = self.store.verify_state()
        self.assertTrue(original.ok, original.problems)
        self.assertEqual(
            original.digest, "db93479659913104ed914d9e290cb0625808d1a1b7d9b221fb6a946c961ca570"
        )
        codecs = {
            "den": (model.den_from_payload, model.den_to_payload, ocura_oss.Ledger),
            "atom": (model.atom_from_payload, model.atom_to_payload, ocura_oss.RunRecord),
            "chokepoint": (
                model.chokepoint_from_payload,
                model.chokepoint_to_payload,
                ocura_oss.BranchPoint,
            ),
        }
        for relative, content in before.items():
            if relative.suffix != ".json":
                continue
            envelope = json.loads(content)
            if envelope["kind"] not in codecs:
                continue
            decode, encode, cls = codecs[envelope["kind"]]
            with self.subTest(record=relative):
                record = decode(envelope["payload"])
                self.assertIs(type(record), cls)
                self.assertEqual(encode(record), envelope["payload"])
                self.assertEqual(
                    model.checksum_for(
                        envelope["schema_version"], envelope["kind"], encode(record)
                    ),
                    envelope["checksum"],
                )
        self.assertEqual(self.store.read_verified_log(BASELINE), b"loss=0.50\n")
        self.assertEqual(self.store.load_branch_point(SOURCE).run_id, BASELINE)
        self.assertIs(type(self.store.load_ledger()), ocura_oss.Ledger)
        self.assertIs(type(self.store.load_run(BASELINE)), ocura_oss.RunRecord)
        self.assertTrue(self.store.verify_state(against=original.manifest).ok)
        after = {
            path.relative_to(state): path.read_bytes()
            for path in state.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)

    def test_source_keywords_support_existing_and_canonical_workflows_without_warnings(self):
        retained = self.store.verify_state().manifest
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            legacy = ocura_oss.branch(SOURCE, root=self.root, reason="legacy positional")
            legacy_keyword = ocura_oss.branch(
                source_chokepoint_id=SOURCE, root=self.root, reason="legacy keyword"
            )
            canonical = ocura_oss.branch(
                source_branch_point_id=SOURCE, root=self.root, reason="canonical keyword"
            )
            same_source = ocura_oss.branch(
                SOURCE,
                source_branch_point_id=SOURCE,
                root=self.root,
                reason="same source through both names",
            )
            for pathway in (legacy, legacy_keyword, canonical, same_source):
                self.assertEqual(pathway.source_branch_point_id, SOURCE)
            execution = ocura_oss.run(
                [sys.executable, "-c", "import sys; print('failed trial'); sys.exit(7)"],
                root=self.root,
                pathway_id=canonical.id,
            )
            self.assertIs(type(execution.atom), ocura_oss.RunRecord)
            self.assertIs(type(execution.chokepoint), ocura_oss.BranchPoint)
            self.assertIs(execution.record, execution.atom)
            self.assertIs(execution.branch_point, execution.chokepoint)
            self.assertIs(execution.atom.outcome, ocura_oss.Outcome.FAILED)
            expected = ocura_oss.compare(SOURCE, root=self.root)
            for arguments in (
                {"source_chokepoint_id": SOURCE},
                {"source_branch_point_id": SOURCE},
                {"source_chokepoint_id": SOURCE, "source_branch_point_id": SOURCE},
                {},
            ):
                self.assertEqual(ocura_oss.compare(root=self.root, **arguments), expected)
        self.assertEqual(caught, [])
        self.assertTrue(ocura_oss.verify(self.root, against=retained).ok)

    def test_run_readers_accept_legacy_and_canonical_ids_and_reject_conflicts(self):
        def read_window(**arguments):
            with self.store.open_verified_log(offset=5, length=4, **arguments) as source:
                return source.read()

        operations = (
            ("read", self.store.read_verified_log),
            ("open", read_window),
            ("info", self.store.log_info),
            ("inspect", self.store.inspect_run),
        )
        for name, operation in operations:
            expected = operation(atom_id=BASELINE)
            for arguments in (
                {"run_id": BASELINE},
                {"atom_id": BASELINE, "run_id": BASELINE},
            ):
                with self.subTest(operation=name, arguments=arguments):
                    self.assertEqual(operation(**arguments), expected)
            with self.subTest(operation=name), self.assertRaises(ocura_oss.StoreError) as caught:
                operation(atom_id=BASELINE, run_id="different")
            self.assertEqual(caught.exception.code, "invalid_input")

        self.assertEqual(self.store.log_info(run_id=BASELINE).run_id, BASELINE)
        inspected = self.store.inspect_run(run_id=BASELINE)
        self.assertIs(inspected.record, inspected.atom)
        self.assertIs(inspected.branch_point, inspected.chokepoint)
        self.assertEqual(
            set(inspected.to_dict()), {"atom", "pathway", "chokepoint", "children", "verification"}
        )
        listed = next(item for item in self.store.list_runs().runs if item.run.run_id == BASELINE)
        self.assertEqual(listed.branch_point_id, SOURCE)
        self.assertIn("chokepoint_id", listed.to_dict())
        self.assertNotIn("branch_point_id", listed.to_dict())
        report = self.store.verify_state()
        self.assertEqual((report.run_records, report.branch_points), (5, 5))
        self.assertNotIn("run_records", asdict(report))
        self.assertNotIn("branch_points", asdict(report))

    def test_storage_aliases_preserve_manifest_and_original_log_bytes(self):
        execution = ocura_oss.run(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 4096)"],
            root=self.root,
            compress=False,
        )
        run_id = execution.record.id
        retained = self.store.verify_state().manifest
        for arguments in (
            {"run_id": run_id},
            {"atom_id": run_id},
            {"atom_id": run_id, "run_id": run_id},
        ):
            with self.subTest(arguments=arguments):
                changes = self.store.compress_logs(min_bytes=0, **arguments)
                self.assertTrue(all(change.run_id == run_id for change in changes))
                self.assertEqual(self.store.log_info(run_id=run_id).encoding, "gzip")
                self.assertEqual(self.store.read_verified_log(run_id=run_id), b"x" * 4096)
                changes = self.store.decompress_logs(**arguments)
                self.assertTrue(all(change.run_id == run_id for change in changes))
                self.assertEqual(self.store.log_info(run_id=run_id).encoding, "raw")
                self.assertTrue(self.store.verify_state(against=retained).ok)
        for operation in (self.store.compress_logs, self.store.decompress_logs):
            with self.assertRaises(ocura_oss.StoreError) as caught:
                operation(atom_id=run_id, run_id="different")
            self.assertEqual(caught.exception.code, "invalid_input")

    def test_run_marks_only_pre_runner_store_errors_as_not_started(self):
        with self.assertRaises(ocura_oss.StoreError) as missing:
            ocura_oss.run([sys.executable, "-c", "pass"], root=self.root / "absent")
        self.assertEqual(missing.exception.execution, "not_started")
        denied = PermissionError("preflight access denied")
        with (
            patch("ocura_oss.api._required_store", side_effect=denied),
            self.assertRaises(ocura_oss.StoreError) as inaccessible,
        ):
            ocura_oss.run([sys.executable, "-c", "pass"], root=self.root)
        self.assertEqual(
            (inaccessible.exception.code, inaccessible.exception.execution),
            ("permission_denied", "not_started"),
        )
        self.assertEqual(str(inaccessible.exception), str(denied))
        self.assertIs(inaccessible.exception.__cause__, denied)
        with (
            patch("ocura_oss.api.Store.load_den", side_effect=ocura_oss.StoreError("bad ledger")),
            self.assertRaises(ocura_oss.StoreError) as unreadable,
        ):
            ocura_oss.run([sys.executable, "-c", "pass"], root=self.root)
        self.assertEqual(unreadable.exception.execution, "not_started")
        uncertain = ocura_oss.StoreError("recorder failed", execution="unknown")
        with (
            patch("ocura_oss.api.runner.run_command", side_effect=uncertain),
            self.assertRaises(ocura_oss.StoreError) as launched,
        ):
            ocura_oss.run([sys.executable, "-c", "pass"], root=self.root)
        self.assertIs(launched.exception, uncertain)
        self.assertEqual(launched.exception.execution, "unknown")

    def test_conflicts_and_missing_required_source_fail_before_state_access(self):
        absent = self.root / "does-not-exist"
        conflicting = {"source_chokepoint_id": SOURCE, "source_branch_point_id": "different"}
        for operation in (
            lambda: ocura_oss.branch(root=absent, reason="conflict", **conflicting),
            lambda: ocura_oss.compare(root=absent, **conflicting),
        ):
            with (
                self.subTest(operation=operation),
                self.assertRaisesRegex(
                    ocura_oss.StoreError,
                    "source_branch_point_id conflicts with source_chokepoint_id",
                ) as caught,
            ):
                operation()
            self.assertEqual(
                (caught.exception.code, caught.exception.execution),
                ("invalid_input", "not_started"),
            )
        with self.assertRaisesRegex(
            ocura_oss.StoreError, "source branch point id is required"
        ) as caught:
            ocura_oss.branch(root=absent, reason="missing")
        self.assertEqual(
            (caught.exception.code, caught.exception.execution), ("invalid_input", "not_started")
        )
        self.assertFalse(absent.exists())

    def test_initialization_returns_canonical_ledger_with_legacy_field(self):
        initialized = ocura_oss.initialize(self.root / "new", name="canonical ledger")
        self.assertIs(type(initialized.ledger), ocura_oss.Ledger)
        self.assertIs(initialized.ledger, initialized.den)
        self.assertEqual(initialized.pathway.ledger_id, initialized.ledger.id)
        self.assertEqual(set(asdict(initialized)), {"root", "den", "pathway"})


if __name__ == "__main__":
    unittest.main()
