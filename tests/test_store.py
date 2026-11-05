# SPDX-License-Identifier: Apache-2.0

import contextlib
import copy
import datetime
import hashlib
import json
import random
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from ocura_oss import model
from ocura_oss.store import STATE_DIR_NAME, Store, StoreError


@contextlib.contextmanager
def temp_root():
    with tempfile.TemporaryDirectory() as temporary:
        yield Path(temporary)


def new_store(root: Path) -> Store:
    store = Store(root)
    store.initialize_state(name="test ledger")
    return store


class InitializationTests(unittest.TestCase):
    def test_init_creates_layout_and_valid_default_records(self):
        with temp_root() as root:
            store = new_store(root)
            self.assertTrue((store.state_dir / "ledger.json").is_file())
            for sub in ("pathways", "runs", "branch-points", "attempts", "logs"):
                self.assertTrue((store.state_dir / sub).is_dir())

            ledger = store.load_ledger()
            self.assertEqual(ledger.name, "test ledger")
            self.assertTrue(model.is_valid_id(ledger.id, "ledger"))
            parsed = model.parse_timestamp(ledger.created_at, "created_at")
            self.assertIsNotNone(parsed.utcoffset())

            pathways = store.list_pathways()
            self.assertEqual(len(pathways), 1)
            pathway = pathways[0]
            self.assertEqual(pathway.id, ledger.default_pathway_id)
            self.assertIsNone(pathway.parent_pathway_id)
            self.assertIsNone(pathway.source_branch_point_id)
            self.assertEqual(pathway.parameters, {})
            self.assertTrue(model.is_valid_id(pathway.id, "pathway"))

            envelope = json.loads((store.state_dir / "ledger.json").read_text("utf-8"))
            self.assertEqual(envelope["schema_version"], 3)
            self.assertEqual(envelope["kind"], "ledger")
            expected = model.checksum_for(3, "ledger", envelope["payload"])["value"]
            self.assertEqual(envelope["checksum"]["algorithm"], "sha256")
            self.assertEqual(envelope["checksum"]["value"], expected)

    def test_init_refuses_existing_state(self):
        with temp_root() as root:
            store = new_store(root)
            before = (store.state_dir / "ledger.json").read_bytes()
            with self.assertRaises(StoreError):
                store.initialize_state(name="again")
            self.assertEqual((store.state_dir / "ledger.json").read_bytes(), before)

    def test_init_refuses_existing_empty_state_dir(self):
        with temp_root() as root:
            (root / STATE_DIR_NAME).mkdir()
            with self.assertRaises(StoreError):
                Store(root).initialize_state(name="again")

    def test_init_rejects_blank_name(self):
        with temp_root() as root:
            store = Store(root)
            for name in ("", "   "):
                with self.assertRaises(StoreError):
                    store.initialize_state(name=name)
            self.assertFalse(store.exists())

    def test_missing_state_is_rejected(self):
        with temp_root() as root:
            store = Store(root)
            with self.assertRaises(StoreError):
                store.require()


class ReadValidationTests(unittest.TestCase):
    @staticmethod
    def _rewrite(path: Path, schema_version, kind, payload):
        checksum = model.checksum_for(schema_version, kind, payload)
        path.write_text(
            json.dumps(
                {
                    "schema_version": schema_version,
                    "kind": kind,
                    "payload": payload,
                    "checksum": checksum,
                }
            ),
            "utf-8",
        )

    def test_tampered_payload_fails_checksum(self):
        with temp_root() as root:
            store = new_store(root)
            path = store.state_dir / "ledger.json"
            envelope = json.loads(path.read_text("utf-8"))
            envelope["payload"]["name"] = "rewritten"
            path.write_text(json.dumps(envelope), "utf-8")
            with self.assertRaises(StoreError):
                store.load_ledger()

    def test_wrong_kind_is_rejected(self):
        with temp_root() as root:
            store = new_store(root)
            ledger_path = store.state_dir / "ledger.json"
            envelope = json.loads(ledger_path.read_text("utf-8"))
            self._rewrite(ledger_path, model.SCHEMA_VERSION, "run", envelope["payload"])
            with self.assertRaises(StoreError):
                store.load_ledger()

    def test_unsupported_schema_version_is_rejected(self):
        with temp_root() as root:
            store = new_store(root)
            ledger_path = store.state_dir / "ledger.json"
            envelope = json.loads(ledger_path.read_text("utf-8"))
            for version in (2, 4):
                with self.subTest(version=version):
                    self._rewrite(ledger_path, version, "ledger", envelope["payload"])
                    with self.assertRaises(StoreError) as ctx:
                        store.load_ledger()
                    self.assertIn(f"unsupported schema version {version}", str(ctx.exception))

    def test_schema_one_rejection_names_the_writing_versions(self):
        with temp_root() as root:
            store = new_store(root)
            ledger_path = store.state_dir / "ledger.json"
            envelope = json.loads(ledger_path.read_text("utf-8"))
            self._rewrite(ledger_path, 1, "ledger", envelope["payload"])
            with self.assertRaises(StoreError) as ctx:
                store.load_ledger()
            message = str(ctx.exception)
            self.assertIn("Ocura OSS 0.4 and earlier wrote this state", message)
            self.assertIn('Keep using "ocura-oss<0.5" for it', message)
            self.assertIn("move .ocura-oss aside and run `ocura-oss init`", message)

    def test_every_entry_point_rejects_state_from_an_earlier_schema(self):
        with temp_root() as root:
            store = new_store(root)
            ledger_path = store.state_dir / "ledger.json"
            envelope = json.loads(ledger_path.read_text("utf-8"))
            self._rewrite(ledger_path, 1, "ledger", envelope["payload"])
            for operation in (
                store.load_ledger,
                store.verify_state,
                store.list_attempts,
                store.recover,
                lambda: store.load_pathway(envelope["payload"]["default_pathway_id"]),
            ):
                with self.subTest(operation=operation), self.assertRaises(StoreError) as ctx:
                    operation()
                self.assertIn("unsupported schema version 1", str(ctx.exception))

    def test_missing_required_field_is_rejected(self):
        with temp_root() as root:
            store = new_store(root)
            ledger_path = store.state_dir / "ledger.json"
            envelope = json.loads(ledger_path.read_text("utf-8"))
            del envelope["payload"]["default_pathway_id"]
            self._rewrite(ledger_path, model.SCHEMA_VERSION, "ledger", envelope["payload"])
            with self.assertRaises(StoreError):
                store.load_ledger()

    def test_malformed_identifier_is_rejected(self):
        with temp_root() as root:
            store = new_store(root)
            with self.assertRaises(StoreError):
                store.load_pathway("../escape")
            with self.assertRaises(StoreError):
                store.load_branch_point("branch-point-not-hex")


class ListingTests(unittest.TestCase):
    def test_listings_are_deterministic(self):
        fixed = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        counter = {"value": 0}

        def clock():
            counter["value"] += 1
            return fixed + datetime.timedelta(seconds=counter["value"])

        with temp_root() as root:
            store = new_store(root)
            ledger = store.load_ledger()
            ids = [ledger.default_pathway_id]
            for index in range(3):
                identifier = model.make_id("pathway")
                ids.append(identifier)
                store._save_pathway(
                    model.Pathway(
                        id=identifier,
                        ledger_id=ledger.id,
                        created_at=model.format_timestamp(clock()),
                        parent_pathway_id=None,
                        source_branch_point_id=None,
                        reason=f"extra {index}",
                        parameters={},
                    )
                )
            listed = [item.id for item in store.list_pathways()]
            self.assertEqual(listed, ids[1:] + [ids[0]])
            again = [item.id for item in store.list_pathways()]
            self.assertEqual(listed, again)

    def test_invalid_records_fail_listings_closed(self):
        with temp_root() as root:
            store = new_store(root)
            good = model.make_id("branch_point")
            store._save_branch_point(
                model.BranchPoint(
                    id=good,
                    pathway_id=store.load_ledger().default_pathway_id,
                    run_id=model.make_id("run"),
                    created_at=model.format_timestamp(model.utc_now()),
                    kind="terminal",
                    outcome=model.Outcome.PASSED,
                    branchable=True,
                )
            )
            bad = store.branch_points_dir / "branch-point-ffffffffffffffffffffffffffffffff.json"
            bad.write_text("{not json", "utf-8")
            with self.assertRaises(StoreError) as ctx:
                store.list_branch_points()
            self.assertIn(bad.name, str(ctx.exception))


class PayloadValidationTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.store = new_store(Path(self._temporary.name) / "proj")
        self.pathway_id = self.store.load_ledger().default_pathway_id

    @staticmethod
    def valid_run_payload(pathway_id):
        empty = hashlib.sha256(b"").hexdigest()
        return {
            "id": model.make_id("run"),
            "pathway_id": pathway_id,
            "started_at": "2026-01-01T00:00:00.000000+00:00",
            "finished_at": "2026-01-01T00:00:01.000000+00:00",
            "duration_seconds": 1.0,
            "outcome": "passed",
            "return_code": 0,
            "launch_error_category": None,
            "declared_parameters": {},
            "command": ["x"],
            "masked_arguments": [],
            "output_capture": "full",
            "stdout_log": ".ocura-oss/logs/x.stdout.log",
            "stderr_log": ".ocura-oss/logs/x.stderr.log",
            "stdout_bytes": 0,
            "stderr_bytes": 0,
            "stdout_sha256": empty,
            "stderr_sha256": empty,
        }

    def _write_run_payload(self, payload):
        path = self.store.runs_dir / f"{payload['id']}.json"
        checksum = model.checksum_for(model.SCHEMA_VERSION, "run", payload)
        path.write_text(
            json.dumps(
                {
                    "schema_version": model.SCHEMA_VERSION,
                    "kind": "run",
                    "payload": payload,
                    "checksum": checksum,
                }
            ),
            "utf-8",
        )
        return payload["id"]

    def test_malformed_run_payloads_are_rejected(self):
        base = self.valid_run_payload(self.pathway_id)

        missing = copy.deepcopy(base)
        del missing["command"]

        wrong_prefix = copy.deepcopy(base)
        wrong_prefix["id"] = model.make_id("ledger")

        bad_outcome = copy.deepcopy(base)
        bad_outcome["outcome"] = "exploded"

        incoherent_launch = copy.deepcopy(base)
        incoherent_launch["outcome"] = "launch_failed"

        incoherent_passed = copy.deepcopy(base)
        incoherent_passed["return_code"] = None

        negative_duration = copy.deepcopy(base)
        negative_duration["duration_seconds"] = -0.5

        bad_digest = copy.deepcopy(base)
        bad_digest["stdout_sha256"] = "zz-not-hex"

        empty_command = copy.deepcopy(base)
        empty_command["command"] = []

        nonstring_token = copy.deepcopy(base)
        nonstring_token["command"] = ["ok", 7]

        naive_timestamp = copy.deepcopy(base)
        naive_timestamp["started_at"] = "2026-01-01T00:00:00"

        nonstring_param = copy.deepcopy(base)
        nonstring_param["declared_parameters"] = {"a": 2}

        boolean_code = copy.deepcopy(base)
        boolean_code["return_code"] = True

        passed_nonzero = copy.deepcopy(base)
        passed_nonzero["return_code"] = 3

        failed_zero = copy.deepcopy(base)
        failed_zero["outcome"] = "failed"
        failed_zero["return_code"] = 0

        nan_duration = copy.deepcopy(base)
        nan_duration["duration_seconds"] = float("nan")

        cases = [
            ("missing field", missing),
            ("wrong id prefix", wrong_prefix),
            ("invalid outcome", bad_outcome),
            ("launch_failed with code", incoherent_launch),
            ("passed without code", incoherent_passed),
            ("negative duration", negative_duration),
            ("non-finite duration", nan_duration),
            ("bad digest", bad_digest),
            ("empty command", empty_command),
            ("non-string token", nonstring_token),
            ("naive timestamp", naive_timestamp),
            ("non-string parameter", nonstring_param),
            ("boolean return code", boolean_code),
            ("passed with nonzero code", passed_nonzero),
            ("failed with zero code", failed_zero),
        ]
        for label, payload in cases:
            with self.subTest(case=label):
                run_id = self._write_run_payload(payload)
                with self.assertRaises(StoreError):
                    self.store.load_run(run_id)

    def test_valid_run_payload_round_trips(self):
        payload = self.valid_run_payload(self.pathway_id)
        run_id = self._write_run_payload(payload)
        record = self.store.load_run(run_id)
        self.assertEqual(record.command, ("x",))
        self.assertEqual(record.outcome, model.Outcome.PASSED)
        self.assertIs(record.output_capture, model.OutputCapture.FULL)
        self.assertEqual(record.masked_arguments, ())

    def test_fields_added_by_a_newer_writer_are_ignored(self):
        payload = self.valid_run_payload(self.pathway_id)
        payload["added_later"] = {"any": ["shape"]}
        record = self.store.load_run(self._write_run_payload(payload))
        self.assertEqual(record.command, ("x",))
        self.assertFalse(hasattr(record, "added_later"))

    def test_uncaptured_and_masked_run_payloads_round_trip(self):
        payload = self.valid_run_payload(self.pathway_id)
        payload.update(
            command=["tool", "<masked>", "--flag", "<masked>"],
            masked_arguments=[1, 3],
            output_capture="none",
            stdout_log=None,
            stderr_log=None,
            stdout_bytes=None,
            stderr_bytes=None,
            stdout_sha256=None,
            stderr_sha256=None,
        )
        record = self.store.load_run(self._write_run_payload(payload))
        self.assertIs(record.output_capture, model.OutputCapture.NONE)
        self.assertEqual(record.masked_arguments, (1, 3))
        self.assertIsNone(record.stdout_log)
        self.assertIsNone(record.stderr_sha256)
        self.store.verify_run_evidence(record)

    def test_abandoned_run_payload_round_trips(self):
        payload = self.valid_run_payload(self.pathway_id)
        payload.update(
            outcome="abandoned", return_code=None, finished_at=None, duration_seconds=None
        )
        record = self.store.load_run(self._write_run_payload(payload))
        self.assertIs(record.outcome, model.Outcome.ABANDONED)
        self.assertIsNone(record.finished_at)
        self.assertIsNone(record.duration_seconds)

    def test_incoherent_capture_masking_and_abandonment_are_rejected(self):
        base = self.valid_run_payload(self.pathway_id)

        def variant(**changes):
            payload = copy.deepcopy(base)
            payload["id"] = model.make_id("run")
            payload.update(changes)
            return payload

        cases = [
            ("unknown capture mode", variant(output_capture="partial")),
            ("uncaptured with a log path", variant(output_capture="none")),
            ("captured without a log path", variant(stdout_log=None)),
            ("captured without a digest", variant(stderr_sha256=None)),
            ("mask outside the command", variant(masked_arguments=[1])),
            ("mask on an unmasked token", variant(masked_arguments=[0])),
            ("masks out of order", variant(command=["<masked>"] * 2, masked_arguments=[1, 0])),
            ("duplicate masks", variant(command=["<masked>"], masked_arguments=[0, 0])),
            ("boolean mask", variant(command=["<masked>"], masked_arguments=[False])),
            ("masks not a list", variant(masked_arguments=None)),
            ("finished without a finish time", variant(finished_at=None)),
            ("finished without a duration", variant(duration_seconds=None)),
            ("abandoned with a finish time", variant(outcome="abandoned", return_code=None)),
            (
                "abandoned with a return code",
                variant(outcome="abandoned", finished_at=None, duration_seconds=None),
            ),
        ]
        for label, payload in cases:
            with self.subTest(case=label):
                run_id = self._write_run_payload(payload)
                with self.assertRaises(StoreError):
                    self.store.load_run(run_id)


class FuzzReaderTests(unittest.TestCase):
    def test_seeded_mutations_always_raise_store_error(self):
        with temp_root() as root:
            store = new_store(root)
            ledger_path = store.state_dir / "ledger.json"
            pristine = json.loads(ledger_path.read_text("utf-8"))

            def mutate(envelope, rng):
                mutated = copy.deepcopy(envelope)
                choice = rng.randrange(9)
                if choice == 0:
                    key = rng.choice(sorted(mutated))
                    del mutated[key]
                elif choice == 1:
                    mutated["extra"] = True
                elif choice == 2:
                    mutated["schema_version"] = rng.choice([2, 4, True, "3", 3.0, None])
                elif choice == 3:
                    mutated["kind"] = rng.choice(["run", "", None, "LEDGER"])
                elif choice == 4:
                    value = mutated["checksum"]["value"]
                    digit = "0" if value[0] != "0" else "1"
                    mutated["checksum"]["value"] = digit + value[1:]
                elif choice == 5:
                    mutated["checksum"]["algorithm"] = rng.choice(["md5", "", None])
                elif choice == 6:
                    keys = sorted(mutated["payload"])
                    key = rng.choice(keys)
                    mutated["payload"][key] = rng.choice([None, 123, {}, ["x"]])
                elif choice == 7:
                    keys = sorted(mutated["payload"])
                    del mutated["payload"][rng.choice(keys)]
                else:
                    mutated["checksum"] = rng.choice([None, "sha256", {}, {"value": "x"}])
                return mutated

            for seed in range(200):
                rng = random.Random(seed)
                corrupted = mutate(pristine, rng)
                if rng.randrange(2) == 0:
                    text = json.dumps(corrupted)
                else:
                    text = json.dumps(corrupted)[: max(1, len(json.dumps(corrupted)) // 2)]
                ledger_path.write_text(text, "utf-8")
                try:
                    store.load_ledger()
                except StoreError:
                    pass
                except Exception as exc:
                    self.fail(f"seed {seed} leaked {type(exc).__name__}: {exc}")
                else:
                    self.fail(f"seed {seed} accepted a corrupted record")

            ledger_path.write_text(json.dumps(pristine), "utf-8")
            self.assertEqual(store.load_ledger().name, "test ledger")


class LineageIntegrityTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.store = new_store(Path(self._temporary.name) / "proj")
        self.pathway_id = self.store.load_ledger().default_pathway_id
        self.empty_digest = hashlib.sha256(b"").hexdigest()

    def _save_run(self, run_id, pathway_id):
        self.store._save_run(
            model.RunRecord(
                id=run_id,
                pathway_id=pathway_id,
                started_at="2026-01-01T00:00:00.000000+00:00",
                finished_at="2026-01-01T00:00:01.000000+00:00",
                duration_seconds=1.0,
                outcome=model.Outcome.PASSED,
                return_code=0,
                launch_error_category=None,
                declared_parameters={},
                command=("x",),
                stdout_log=f"{STATE_DIR_NAME}/logs/{run_id}.stdout.log",
                stderr_log=f"{STATE_DIR_NAME}/logs/{run_id}.stderr.log",
                stdout_bytes=0,
                stderr_bytes=0,
                stdout_sha256=self.empty_digest,
                stderr_sha256=self.empty_digest,
            )
        )

    def test_cyclic_lineage_is_rejected(self):
        run_id = model.make_id("run")
        branch_point_id = model.make_id("branch_point")
        self._save_run(run_id, self.pathway_id)
        self.store._save_branch_point(
            model.BranchPoint(
                id=branch_point_id,
                pathway_id=self.pathway_id,
                run_id=run_id,
                created_at="2026-01-01T00:00:02.000000+00:00",
                kind="terminal",
                outcome=model.Outcome.PASSED,
                branchable=True,
            )
        )
        pathway = self.store.load_pathway(self.pathway_id)
        cyclic = replace(pathway, source_branch_point_id=branch_point_id)
        self.store._write_record(
            self.store.pathways_dir / f"{self.pathway_id}.json",
            "pathway",
            model.pathway_to_payload(cyclic),
        )
        with self.assertRaises(StoreError):
            self.store.load_pathway(self.pathway_id)

    def test_iterated_branch_chain_validates_each_record_once(self):
        # Each generation branches from a run on the previous one, so a pathway
        # reaches its parent both directly and through its source branch point.
        ledger_id = self.store.load_ledger().id
        pathway_id = self.pathway_id
        depth = 12
        for generation in range(depth):
            run_id = model.make_id("run")
            branch_point_id = model.make_id("branch_point")
            self._save_run(run_id, pathway_id)
            self.store._save_branch_point(
                model.BranchPoint(
                    id=branch_point_id,
                    pathway_id=pathway_id,
                    run_id=run_id,
                    created_at="2026-01-01T00:00:02.000000+00:00",
                    kind="terminal",
                    outcome=model.Outcome.PASSED,
                    branchable=True,
                )
            )
            child = model.make_id("pathway")
            self.store._save_pathway(
                model.Pathway(
                    id=child,
                    ledger_id=ledger_id,
                    created_at="2026-01-01T00:00:03.000000+00:00",
                    parent_pathway_id=pathway_id,
                    source_branch_point_id=branch_point_id,
                    reason=f"generation {generation}",
                    parameters={},
                )
            )
            pathway_id = child
        with mock.patch.object(
            self.store, "_read_envelope", wraps=self.store._read_envelope
        ) as reads:
            self.store.load_pathway(pathway_id)
            loaded = reads.call_count
            self.store.verify_state()
        # Doubling per generation would need thousands of reads at this depth.
        self.assertLess(loaded, 5 * depth)
        self.assertLess(reads.call_count, 30 * depth)

    def test_branch_point_run_pathway_disagreement_is_rejected(self):
        other = model.make_id("pathway")
        self.store._save_pathway(
            model.Pathway(
                id=other,
                ledger_id=self.store.load_ledger().id,
                created_at="2026-01-01T00:00:00.000000+00:00",
                parent_pathway_id=None,
                source_branch_point_id=None,
                reason="other",
                parameters={},
            )
        )
        run_id = model.make_id("run")
        self._save_run(run_id, other)
        branch_point_id = model.make_id("branch_point")
        self.store._save_branch_point(
            model.BranchPoint(
                id=branch_point_id,
                pathway_id=self.pathway_id,
                run_id=run_id,
                created_at="2026-01-01T00:00:02.000000+00:00",
                kind="terminal",
                outcome=model.Outcome.PASSED,
                branchable=True,
            )
        )
        with self.assertRaises(StoreError):
            self.store.load_branch_point(branch_point_id)


class TieBreakTests(unittest.TestCase):
    def test_branch_points_with_equal_timestamps_sort_by_id_descending(self):
        with temp_root() as root:
            store = new_store(root)
            pathway_id = store.load_ledger().default_pathway_id
            first = "branch-point-" + "0" * 31 + "a"
            second = "branch-point-" + "0" * 31 + "b"
            created = "2026-01-01T00:00:00.000000+00:00"
            for identifier in (first, second):
                store._save_branch_point(
                    model.BranchPoint(
                        id=identifier,
                        pathway_id=pathway_id,
                        run_id=model.make_id("run"),
                        created_at=created,
                        kind="terminal",
                        outcome=model.Outcome.PASSED,
                        branchable=True,
                    )
                )
            listed = [item.id for item in store.list_branch_points()]
            self.assertEqual(listed, [second, first])


class EvidenceVerificationTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.store = new_store(Path(self._temporary.name) / "proj")
        self.pathway_id = self.store.load_ledger().default_pathway_id
        self.empty_digest = hashlib.sha256(b"").hexdigest()

    def _run_once(self):
        from ocura_oss import runner

        return runner.run_command(
            self.store,
            pathway_id=self.pathway_id,
            argv=[sys.executable, "-c", "print('evidence')"],
            declared_parameters={},
        )

    def test_intact_evidence_passes(self):
        execution = self._run_once()
        self.store.verify_run_evidence(execution.record)
        report = self.store.verify_state()
        self.assertTrue(report.ok)
        self.assertEqual(report.pathways, 1)
        self.assertEqual(report.run_records, 1)
        self.assertEqual(report.branch_points, 1)
        self.assertEqual(report.logs_checked, 2)

    def test_missing_log_is_rejected(self):
        execution = self._run_once()
        (self.store.logs_dir / f"{execution.record.id}.stdout.log").unlink()
        with self.assertRaises(StoreError):
            self.store.verify_run_evidence(execution.record)

    def test_size_mismatch_is_rejected(self):
        execution = self._run_once()
        log = self.store.logs_dir / f"{execution.record.id}.stderr.log"
        log.write_bytes(b"x")
        with self.assertRaises(StoreError):
            self.store.verify_run_evidence(execution.record)

    def test_content_tamper_is_rejected(self):
        execution = self._run_once()
        log = self.store.logs_dir / f"{execution.record.id}.stdout.log"
        data = bytearray(log.read_bytes())
        data[0] = data[0] ^ 0x20
        log.write_bytes(bytes(data))
        with self.assertRaises(StoreError):
            self.store.verify_run_evidence(execution.record)

    def test_escaping_log_paths_are_rejected(self):
        cases = ["../escape.log", ".ocura-oss/logs/../../escape.log", "C:\\evil\\x.log"]
        for index, stdout_log in enumerate(cases):
            with self.subTest(path=stdout_log):
                record = model.RunRecord(
                    id=model.make_id("run"),
                    pathway_id=self.pathway_id,
                    started_at="2026-01-01T00:00:00.000000+00:00",
                    finished_at="2026-01-01T00:00:01.000000+00:00",
                    duration_seconds=1.0,
                    outcome=model.Outcome.PASSED,
                    return_code=0,
                    launch_error_category=None,
                    declared_parameters={},
                    command=("x",),
                    stdout_log=stdout_log,
                    stderr_log=f".ocura-oss/logs/{index}.stderr.log",
                    stdout_bytes=0,
                    stderr_bytes=0,
                    stdout_sha256=self.empty_digest,
                    stderr_sha256=self.empty_digest,
                )
                self.store._save_run(record)
                with self.assertRaises(StoreError):
                    self.store.verify_run_evidence(record)

    def test_verify_state_counts_only_logs_it_checked(self):
        execution = self._run_once()
        (self.store.logs_dir / f"{execution.record.id}.stdout.log").unlink()
        report = self.store.verify_state()
        self.assertFalse(report.ok)
        self.assertEqual(report.run_records, 1)
        self.assertEqual(report.logs_checked, 1)

    def test_verify_state_keeps_checksum_mismatch_out_of_orphan_report(self):
        execution = self._run_once()
        log = self.store.logs_dir / f"{execution.record.id}.stdout.log"
        contents = bytearray(log.read_bytes())
        contents[0] ^= 0x20
        log.write_bytes(contents)

        report = self.store.verify_state()

        self.assertFalse(report.ok)
        self.assertEqual(report.logs_checked, 1)
        self.assertTrue(
            any("log checksum mismatch" in problem for _record, problem in report.problems)
        )
        self.assertFalse(
            any(
                record == f"logs/{log.name}" and "orphaned" in problem
                for record, problem in report.problems
            )
        )

    def test_verify_state_detects_orphaned_log_files(self):
        self._run_once()
        orphan = self.store.logs_dir / "orphaned.stdout.log"
        orphan.write_bytes(b"unreferenced")
        report = self.store.verify_state()
        self.assertFalse(report.ok)
        self.assertTrue(
            any(record == "logs/orphaned.stdout.log" for record, _problem in report.problems)
        )

    def test_verify_state_reports_problems_with_record_names(self):
        self._run_once()
        target = sorted(self.store.branch_points_dir.glob("*.json"))[0]
        target.write_text("{broken", "utf-8")
        report = self.store.verify_state()
        self.assertFalse(report.ok)
        self.assertTrue(any(record == target.name for record, _problem in report.problems))


class DuplicateRecordTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.store = new_store(Path(self._temporary.name) / "proj")

    def _run_once(self):
        from ocura_oss import runner

        return runner.run_command(
            self.store,
            pathway_id=self.store.load_ledger().default_pathway_id,
            argv=[sys.executable, "-c", "print('duplicate test')"],
            declared_parameters={},
        )

    def _copy_run_under_new_name(self):
        execution = self._run_once()
        original = self.store.runs_dir / f"{execution.record.id}.json"
        other_id = model.make_id("run")
        copy_path = self.store.runs_dir / f"{other_id}.json"
        copy_path.write_bytes(original.read_bytes())
        return other_id

    def test_listing_fails_closed_on_duplicated_record(self):
        other_id = self._copy_run_under_new_name()
        with self.assertRaises(StoreError) as ctx:
            self.store.list_run_records()
        self.assertIn(f"{other_id}.json", str(ctx.exception))

    def test_verify_state_reports_copied_filename_while_original_passes(self):
        other_id = self._copy_run_under_new_name()
        report = self.store.verify_state()
        self.assertFalse(report.ok)
        self.assertTrue(any(record == f"{other_id}.json" for record, _problem in report.problems))
        mismatch_records = [
            record
            for record, problem in report.problems
            if "does not match its filename" in problem
        ]
        self.assertEqual(mismatch_records, [f"{other_id}.json"])

    def test_branch_point_copy_fails_branch_point_listing(self):
        self._run_once()
        original = sorted(self.store.branch_points_dir.glob("*.json"))[0]
        other_id = model.make_id("branch_point")
        (self.store.branch_points_dir / f"{other_id}.json").write_bytes(original.read_bytes())
        with self.assertRaises(StoreError):
            self.store.list_branch_points()


class SemanticInvariantTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.store = new_store(Path(self._temporary.name) / "proj")
        self.pathway_id = self.store.load_ledger().default_pathway_id
        self.ledger_id = self.store.load_ledger().id
        self.empty_digest = hashlib.sha256(b"").hexdigest()

    def _craft_run(self, pathway_id, *, outcome=model.Outcome.PASSED, return_code=0):
        run_id = model.make_id("run")
        for suffix in ("stdout", "stderr"):
            (self.store.logs_dir / f"{run_id}.{suffix}.log").write_bytes(b"")
        record = model.RunRecord(
            id=run_id,
            pathway_id=pathway_id,
            started_at="2026-01-01T00:00:00.000000+00:00",
            finished_at="2026-01-01T00:00:01.000000+00:00",
            duration_seconds=1.0,
            outcome=outcome,
            return_code=return_code,
            launch_error_category=None,
            declared_parameters={},
            command=("x",),
            stdout_log=f"{STATE_DIR_NAME}/logs/{run_id}.stdout.log",
            stderr_log=f"{STATE_DIR_NAME}/logs/{run_id}.stderr.log",
            stdout_bytes=0,
            stderr_bytes=0,
            stdout_sha256=self.empty_digest,
            stderr_sha256=self.empty_digest,
        )
        self.store._save_run(record)
        return record

    def _craft_branch_point(self, run_id, outcome=model.Outcome.PASSED):
        branch_point = model.BranchPoint(
            id=model.make_id("branch_point"),
            pathway_id=self.pathway_id,
            run_id=run_id,
            created_at="2026-01-01T00:00:02.000000+00:00",
            kind="terminal",
            outcome=outcome,
            branchable=True,
        )
        self.store._save_branch_point(branch_point)
        return branch_point

    def test_run_with_nonexistent_pathway_fails_verification(self):
        ghost = "pathway-" + "f" * 32
        record = self._craft_run(ghost)
        report = self.store.verify_state()
        self.assertFalse(report.ok)
        self.assertTrue(
            any(
                name == f"{record.id}.json" and "pathway" in problem
                for name, problem in report.problems
            )
        )

    def test_filename_id_mismatch_is_rejected(self):
        record = self._craft_run(self.pathway_id)
        original = self.store.runs_dir / f"{record.id}.json"
        other_id = model.make_id("run")
        copied = self.store.runs_dir / f"{other_id}.json"
        copied.write_bytes(original.read_bytes())
        with self.assertRaises(StoreError):
            self.store.load_run(other_id)

    def test_branch_point_outcome_must_match_run_outcome(self):
        record = self._craft_run(self.pathway_id, outcome=model.Outcome.PASSED)
        branch_point = self._craft_branch_point(record.id, outcome=model.Outcome.FAILED)
        with self.assertRaises(StoreError):
            self.store.load_branch_point(branch_point.id)

    def test_source_branch_point_must_belong_to_parent_pathway(self):
        from ocura_oss import runner

        execution = runner.run_command(
            self.store,
            pathway_id=self.pathway_id,
            argv=[sys.executable, "-c", "print('base')"],
            declared_parameters={},
        )
        legitimate = model.Pathway(
            id=model.make_id("pathway"),
            ledger_id=self.ledger_id,
            created_at=execution.branch_point.created_at,
            parent_pathway_id=self.pathway_id,
            source_branch_point_id=execution.branch_point.id,
            reason="legitimate branch",
            parameters={},
        )
        self.store._save_pathway(legitimate)
        swapped = model.Pathway(
            id=model.make_id("pathway"),
            ledger_id=self.ledger_id,
            created_at=legitimate.created_at,
            parent_pathway_id=legitimate.id,
            source_branch_point_id=execution.branch_point.id,
            reason="source belongs to grandparent, not parent",
            parameters={},
        )
        self.store._save_pathway(swapped)
        with self.assertRaises(StoreError):
            self.store.load_pathway(swapped.id)


if __name__ == "__main__":
    unittest.main()
