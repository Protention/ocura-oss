# SPDX-License-Identifier: Apache-2.0

"""Exercise conversion of an actual 0.4 ledger and rejection without changing inputs."""

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parents[1]
SCRIPT = TOOLS / "migrate_0_4.py"
FIXTURE = TOOLS / "fixtures" / "ledger-0.4.0" / "state"
spec = importlib.util.spec_from_file_location("migrate_0_4", SCRIPT)
migration = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = migration
spec.loader.exec_module(migration)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ocura-migration-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "original"
        self.source.mkdir()
        self.state = self.source / migration.STATE
        shutil.copytree(FIXTURE, self.state)
        self.destination = self.root / "converted"
        self.before = migration.inventory(self.state, 1)

    def record(self, directory):
        return sorted((self.state / directory).glob("*.json"))[0]

    def change(self, path, update):
        value = migration.read_json(path)
        update(value)
        value["checksum"] = migration.checksum(value)
        path.write_text(json.dumps(value), encoding="utf-8")

    def reject(self, message):
        before = migration.inventory(self.state, 1)
        with self.assertRaisesRegex(migration.MigrationError, message):
            migration.migrate(self.source, self.destination)
        self.assertEqual(migration.inventory(self.state, 1), before)
        self.assertFalse(self.destination.exists())

    def test_fixture_is_complete_and_contains_all_04_outcomes(self):
        source = migration.read_ledger(self.source, 1)
        self.assertEqual(
            migration.manifest_digest(source.files),
            "8b41c4c3435266fba7b428dcccb2d32c1dee33ab12157027530cbca0dec88b39",
        )
        self.assertEqual(source.counts, {"pathways": 2, "atoms": 5, "chokepoints": 5, "logs": 10})
        self.assertEqual(
            {
                record["payload"]["outcome"]
                for record in source.records.values()
                if record["kind"] == "atom"
            },
            migration.OUTCOMES,
        )

    def test_dry_run_writes_nothing(self):
        result = migration.migrate(self.source, self.destination, dry_run=True)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(migration.inventory(self.state, 1), self.before)
        self.assertFalse(self.destination.exists())

    def test_empty_complete_ledger_can_be_migrated(self):
        for directory in ("atoms", "chokepoints", "logs"):
            for path in (self.state / directory).iterdir():
                path.unlink()
        for path in (self.state / "pathways").iterdir():
            if migration.read_json(path)["payload"]["parent_pathway_id"] is not None:
                path.unlink()
        result = migration.migrate(self.source, self.destination)
        self.assertEqual(result["counts"], {"pathways": 1, "atoms": 0, "chokepoints": 0, "logs": 0})

    def test_copy_preserves_historical_payloads_logs_and_original(self):
        original = migration.read_ledger(self.source, 1)
        result = migration.migrate(self.source, self.destination)
        copied = migration.read_ledger(self.destination, 2)
        self.assertEqual(result["status"], "migrated")
        self.assertEqual(migration.inventory(self.state, 1), self.before)
        self.assertEqual(original.counts, copied.counts)
        for name, envelope in original.records.items():
            old_payload = envelope["payload"]
            new_payload = copied.records[name]["payload"]
            self.assertEqual({key: new_payload[key] for key in old_payload}, old_payload)
            if envelope["kind"] == "atom":
                self.assertEqual(new_payload["output_capture"], "full")
                self.assertEqual(new_payload["masked_arguments"], [])
                self.assertNotIn("context", new_payload)
        for path in (self.state / "logs").iterdir():
            self.assertEqual(
                path.read_bytes(),
                (self.destination / migration.STATE / "logs" / path.name).read_bytes(),
            )
        self.assertEqual(list((self.destination / migration.STATE / "attempts").iterdir()), [])

    def test_receipt_connects_original_bytes_and_converted_checksums(self):
        result = migration.migrate(self.source, self.destination)
        receipt = migration.read_json(Path(result["receipt"]))
        self.assertEqual(receipt["tool_sha256"], migration.file_digest(SCRIPT)["sha256"])
        self.assertEqual(receipt["source_manifest"], migration.manifest(self.before))
        output = migration.read_ledger(self.destination, 2)
        self.assertEqual(receipt["destination_manifest"], migration.manifest(output.files))
        self.assertEqual(receipt["destination_digest"], migration.manifest_digest(output.files))
        for entry in receipt["record_checksums"]:
            original = migration.read_json(self.state / entry["path"])
            converted = migration.read_json(self.destination / migration.STATE / entry["path"])
            self.assertEqual(entry["source"], original["checksum"]["value"])
            self.assertEqual(entry["destination"], converted["checksum"]["value"])
            self.assertNotEqual(entry["source"], entry["destination"])

    def test_existing_destination_is_not_touched(self):
        self.destination.mkdir()
        sentinel = self.destination / "keep.txt"
        sentinel.write_bytes(b"existing work")
        with self.assertRaisesRegex(migration.MigrationError, "already exists"):
            migration.migrate(self.source, self.destination)
        self.assertEqual(sentinel.read_bytes(), b"existing work")

    def test_destination_created_during_validation_is_not_overwritten(self):
        original_read = migration.read_ledger
        sentinel = self.destination / "keep.txt"

        def read_then_reserve(root, schema):
            result = original_read(root, schema)
            self.destination.mkdir()
            sentinel.write_bytes(b"other writer")
            return result

        with (
            mock.patch.object(migration, "read_ledger", side_effect=read_then_reserve),
            self.assertRaises(FileExistsError),
        ):
            migration.migrate(self.source, self.destination)
        self.assertEqual(sentinel.read_bytes(), b"other writer")

    def test_overlapping_project_directories_are_rejected(self):
        with self.assertRaisesRegex(migration.MigrationError, "overlap"):
            migration.migrate(self.source, self.source / "nested-output")
        self.assertFalse((self.source / "nested-output").exists())

    def test_missing_destination_parent_is_not_created(self):
        missing = self.root / "missing-parent"
        with self.assertRaises(OSError):
            migration.migrate(self.source, missing / "output")
        self.assertFalse(missing.exists())

    def test_checksum_mismatch_is_not_laundered(self):
        path = self.record("atoms")
        value = migration.read_json(path)
        value["payload"]["declared_parameters"]["batch"] = "999"
        path.write_text(json.dumps(value), encoding="utf-8")
        self.reject("checksum mismatch")

    def test_corrupt_log_is_rejected(self):
        path = next((self.state / "logs").glob("*.stdout.log"))
        path.write_bytes(path.read_bytes() + b"altered")
        self.reject("log size or checksum mismatch")

    def test_missing_log_is_rejected(self):
        next((self.state / "logs").glob("*.stdout.log")).unlink()
        self.reject("log size or checksum mismatch")

    def test_unreferenced_log_is_rejected(self):
        (self.state / "logs" / "unfinished.stdout.log").write_bytes(b"unfinished")
        self.reject("unreferenced logs")

    def test_missing_chokepoint_is_rejected(self):
        self.record("chokepoints").unlink()
        self.reject("exactly one chokepoint")

    def test_missing_parent_is_rejected_without_traceback(self):
        child = next(
            path
            for path in (self.state / "pathways").glob("*.json")
            if migration.read_json(path)["payload"]["parent_pathway_id"] is not None
        )
        self.change(
            child, lambda item: item["payload"].update(parent_pathway_id="pathway-" + "f" * 32)
        )
        self.reject("lineage reference is missing")

    def test_cycle_is_rejected(self):
        den = migration.read_json(self.state / "den.json")["payload"]
        default = self.state / "pathways" / f"{den['default_pathway_id']}.json"
        source = migration.read_json(self.record("chokepoints"))["payload"]["id"]
        self.change(
            default,
            lambda item: item["payload"].update(
                parent_pathway_id=den["default_pathway_id"], source_chokepoint_id=source
            ),
        )
        self.reject("cycle")

    def test_false_outcome_is_rejected_even_with_a_valid_checksum(self):
        point = self.record("chokepoints")
        self.change(point, lambda item: item["payload"].update(outcome="failed"))
        self.reject("disagrees")

    def test_path_escape_is_rejected_even_with_a_valid_checksum(self):
        atom = self.record("atoms")
        self.change(
            atom,
            lambda item: item["payload"].update(stdout_log=".ocura-oss/logs/../../outside.txt"),
        )
        self.reject("directly inside")

    def test_symlinked_log_is_rejected(self):
        log = next((self.state / "logs").glob("*.stdout.log"))
        outside = self.root / "outside.txt"
        outside.write_bytes(log.read_bytes())
        log.unlink()
        try:
            log.symlink_to(outside)
        except OSError:
            self.skipTest("This host cannot create symlinks")
        with self.assertRaisesRegex(migration.MigrationError, "links and junctions"):
            migration.migrate(self.source, self.destination)
        self.assertEqual(outside.read_bytes(), (FIXTURE / "logs" / log.name).read_bytes())
        self.assertFalse(self.destination.exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows junction check")
    def test_windows_log_directory_junction_is_rejected(self):
        logs = self.state / "logs"
        retained = self.root / "retained-logs"
        self.assertTrue(logs.resolve().is_relative_to(self.root.resolve()))
        self.assertTrue(retained.resolve().is_relative_to(self.root.resolve()))
        logs.rename(retained)
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(logs), str(retained)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        with self.assertRaisesRegex(migration.MigrationError, "links and junctions"):
            migration.migrate(self.source, self.destination)
        for name, digest in self.before.items():
            if name.startswith("logs/"):
                self.assertEqual(migration.file_digest(retained / Path(name).name), digest)
        self.assertFalse(self.destination.exists())

    def test_unknown_state_directory_is_not_discarded(self):
        (self.state / "private-notes").mkdir()
        with self.assertRaisesRegex(migration.MigrationError, "unsupported file or directory"):
            migration.migrate(self.source, self.destination)
        self.assertTrue((self.state / "private-notes").exists())
        self.assertFalse(self.destination.exists())

    def test_schema_two_input_is_not_reconverted(self):
        self.change(self.state / "den.json", lambda item: item.update(schema_version=2))
        self.reject("expected schema 1")

    def test_boolean_schema_is_rejected(self):
        self.change(self.state / "den.json", lambda item: item.update(schema_version=True))
        self.reject("expected schema 1")

    def test_duplicate_json_keys_are_rejected(self):
        path = self.state / "den.json"
        body = path.read_text(encoding="utf-8")
        path.write_text(
            body.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'),
            encoding="utf-8",
        )
        self.reject("duplicate JSON key")

    def test_invalid_parameter_type_is_rejected(self):
        self.change(
            self.record("atoms"),
            lambda item: item["payload"]["declared_parameters"].update(batch=4),
        )
        self.reject("nonempty string")

    def test_partial_destination_is_removed_after_write_failure(self):
        original_copy = migration.copy_ledger

        def interrupted_copy(ledger, destination):
            original_copy(ledger, destination)
            raise OSError("simulated disk failure")

        with (
            mock.patch.object(migration, "copy_ledger", side_effect=interrupted_copy),
            self.assertRaisesRegex(OSError, "simulated disk failure"),
        ):
            migration.migrate(self.source, self.destination)
        self.assertFalse(self.destination.exists())
        self.assertEqual(migration.inventory(self.state, 1), self.before)

    def test_partial_destination_is_removed_after_interrupt(self):
        with (
            mock.patch.object(migration, "copy_ledger", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            migration.migrate(self.source, self.destination)
        self.assertFalse(self.destination.exists())
        self.assertEqual(migration.inventory(self.state, 1), self.before)

    def test_replaced_destination_is_retained_after_failure(self):
        original_copy = migration.copy_ledger
        displaced = self.root / "displaced-output"
        sentinel = self.destination / "keep.txt"

        def replace_after_copy(ledger, destination):
            original_copy(ledger, destination)
            self.assertTrue(destination.resolve().is_relative_to(self.root.resolve()))
            self.assertTrue(displaced.resolve().is_relative_to(self.root.resolve()))
            destination.rename(displaced)
            destination.mkdir()
            sentinel.write_bytes(b"replacement directory")
            raise OSError("simulated replacement")

        with (
            mock.patch.object(migration, "copy_ledger", side_effect=replace_after_copy),
            self.assertRaisesRegex(migration.MigrationError, "incomplete destination retained"),
        ):
            migration.migrate(self.source, self.destination)
        self.assertEqual(sentinel.read_bytes(), b"replacement directory")
        self.assertTrue(displaced.exists())
        self.assertEqual(migration.inventory(self.state, 1), self.before)

    def test_unexpected_file_in_output_is_retained_after_failure(self):
        original_copy = migration.copy_ledger
        sentinel = self.destination / "keep.txt"

        def add_file_after_copy(ledger, destination):
            original_copy(ledger, destination)
            sentinel.write_bytes(b"other work")
            raise OSError("simulated concurrent addition")

        with (
            mock.patch.object(migration, "copy_ledger", side_effect=add_file_after_copy),
            self.assertRaisesRegex(migration.MigrationError, "incomplete destination retained"),
        ):
            migration.migrate(self.source, self.destination)
        self.assertEqual(sentinel.read_bytes(), b"other work")
        self.assertEqual(migration.inventory(self.state, 1), self.before)

    def test_mutating_source_is_rejected_and_failed_copy_removed(self):
        original_copy = migration.copy_ledger
        log = next((self.state / "logs").glob("*.stdout.log"))

        def copy_then_mutate(ledger, destination):
            original_copy(ledger, destination)
            log.write_bytes(log.read_bytes() + b"concurrent writer")

        with (
            mock.patch.object(migration, "copy_ledger", side_effect=copy_then_mutate),
            self.assertRaisesRegex(migration.MigrationError, "source changed"),
        ):
            migration.migrate(self.source, self.destination)
        self.assertFalse(self.destination.exists())
        self.assertTrue(log.read_bytes().endswith(b"concurrent writer"))

    def test_parsed_record_bytes_must_match_the_verified_inventory(self):
        atom = self.record("atoms")
        alternate = migration.read_json(atom)
        alternate["payload"]["declared_parameters"]["batch"] = "999"
        alternate["checksum"] = migration.checksum(alternate)
        raw_alternate = json.dumps(alternate).encode("utf-8")
        original_read_bytes = Path.read_bytes

        def unstable_read(path):
            return raw_alternate if path == atom else original_read_bytes(path)

        with (
            mock.patch.object(Path, "read_bytes", unstable_read),
            self.assertRaisesRegex(migration.MigrationError, "record changed"),
        ):
            migration.migrate(self.source, self.destination)
        self.assertEqual(migration.inventory(self.state, 1), self.before)
        self.assertFalse(self.destination.exists())

    def test_standalone_cli_needs_no_installed_packages(self):
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                str(SCRIPT),
                "--source",
                str(self.source),
                "--destination",
                str(self.destination),
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "migrated")

    def test_cli_dry_run_and_failure_exit_codes(self):
        command = [
            sys.executable,
            "-I",
            "-S",
            str(SCRIPT),
            "--source",
            str(self.source),
            "--destination",
            str(self.destination),
            "--json",
            "--dry-run",
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "ready")
        self.assertFalse(self.destination.exists())
        self.record("chokepoints").unlink()
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_current_package_reads_compares_and_extends_converted_state(self):
        migration.migrate(self.source, self.destination)
        code = """import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
import ocura_oss
root = pathlib.Path(sys.argv[2])
report = ocura_oss.verify(root)
assert report.ok, report.problems
comparison = ocura_oss.compare(root=root)
assert comparison.children
execution = ocura_oss.run([sys.executable, '-c', "print('new work')"], root=root)
assert execution.atom.outcome.value == 'passed'
assert ocura_oss.verify(root).ok
print(json.dumps({'version': ocura_oss.__version__, 'verified': True}))
"""
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", code, str(REPO / "src"), str(self.destination)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["verified"])


if __name__ == "__main__":
    unittest.main()
