# SPDX-License-Identifier: Apache-2.0

"""Core-only example checks plus explicitly selected real backend integration tests."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ocura_oss import Store, branch, initialize, run

EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "autoregressive"


def load_example(name):
    spec = importlib.util.spec_from_file_location(f"example_{name}", EXAMPLE_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AutoregressiveContractTests(unittest.TestCase):
    def test_next_character_inputs_never_include_target_or_future(self):
        model = load_example("train")
        x, y = model.examples(["red\n"])
        changed, _ = model.examples(["rex\n"])
        self.assertEqual(x[:3], changed[:3])
        self.assertNotEqual(x[3], changed[3])
        self.assertEqual(y, [model.VOCAB.index(char) for char in "red\n"])
        self.assertEqual(x[0], model.encode_context(""))
        self.assertEqual(x[1], model.encode_context("r"))
        self.assertFalse(set(model.TRAIN_LINES) & set(model.VALIDATION_LINES))

    def test_generation_uses_previously_generated_characters(self):
        model = load_example("train")
        contexts = []
        tokens = iter("fox\n")

        def predict(context):
            contexts.append(context)
            token = next(tokens)
            return [float(char == token) for char in model.VOCAB]

        self.assertEqual(model.generate(predict), "red fox\n")
        self.assertEqual(contexts[0], model.encode_context("red "))
        self.assertEqual(contexts[1], model.encode_context("red f"))
        self.assertEqual(contexts[-1], model.encode_context("red fox"))

    def test_invalid_training_configuration_rejected_before_backend_import(self):
        model = load_example("train")
        for steps, rate in ((0, 0.5), (-1, 0.5), (1, float("nan")), (1, -0.5)):
            with self.subTest(steps=steps, rate=rate), self.assertRaises(ValueError):
                model.train("pytorch", steps, rate, 7)

    def test_combined_driver_retains_two_runs_and_matching_settings(self):
        driver = load_example("experiment")
        # Exercise the real recorder/CLI using a cheap stand-in workload; the
        # opt-in integration tests below separately validate actual training.
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            (work / "train.py").write_text(
                "import argparse, json\n"
                "p = argparse.ArgumentParser()\n"
                "p.add_argument('--backend')\n"
                "p.add_argument('--steps', type=int)\n"
                "p.add_argument('--learning-rate', type=float)\n"
                "p.add_argument('--seed', type=int)\n"
                "a = vars(p.parse_args())\n"
                "print(json.dumps(dict(a, executor='local', "
                "example='autoregressive-character-v1', validation_loss=1/a['steps'])))\n",
                encoding="utf-8",
            )
            args = driver.argparse.Namespace(
                root=work / "experiment",
                backend="pytorch",
                executor="local",
                baseline_steps=2,
                variant_steps=4,
                learning_rate=0.5,
                seed=7,
            )
            with (
                mock.patch.object(driver, "__file__", str(work / "experiment.py")),
                mock.patch.object(driver.importlib.util, "find_spec", return_value=object()),
            ):
                result = driver.run_experiment(args)
            self.assertEqual(result["verification"], {"ok": True, "atoms": 2, "logs": 4})
            self.assertEqual(result["validation_loss_delta"], -0.25)
            child = result["comparison"]["children"][0]
            self.assertEqual(child["reason"], "test 4 training steps against 2")
            self.assertEqual(child["source_chokepoint_id"], result["source_chokepoint_id"])
            store = Store(args.root)
            self.assertEqual(len(store.list_pathways()), 2)
            for role in ("baseline", "variant"):
                record = store.load_run(result[role]["atom_id"])
                self.assertEqual(record.declared_parameters["steps"], str(result[role]["steps"]))
                self.assertIn("--steps", record.command)

    def test_failed_workload_retains_run_identity_and_verified_stderr(self):
        driver = load_example("experiment")
        with tempfile.TemporaryDirectory() as temporary:
            root = initialize(Path(temporary) / "project").root
            with self.assertRaises(RuntimeError) as raised:
                driver.cli(
                    "run",
                    "--",
                    sys.executable,
                    "-c",
                    "import sys; print('training diagnostic', file=sys.stderr); sys.exit(7)",
                    root=root,
                )
            record = Store(root).list_run_records()[0]
            message = str(raised.exception)
            for detail in (
                record.id,
                record.stdout_log,
                record.stderr_log,
                '"outcome": "failed"',
                '"return_code": 7',
                "verified stderr tail:",
                "training diagnostic",
            ):
                self.assertIn(detail, message)
            self.assertEqual(len(Store(root).list_run_records()), 1)

    def test_cli_preflight_failure_retains_structured_execution_state(self):
        driver = load_example("experiment")
        with tempfile.TemporaryDirectory() as temporary:
            root = initialize(Path(temporary) / "project").root
            with self.assertRaises(RuntimeError) as raised:
                driver.cli("run", "--", root=root)
            message = str(raised.exception)
            self.assertIn('"code": "invalid_input"', message)
            self.assertIn('"execution": "not_started"', message)
            self.assertEqual(Store(root).list_run_records(), [])

    def test_explicit_report_reads_legacy_pair_after_newer_work_without_changes(self):
        driver = load_example("experiment")
        with tempfile.TemporaryDirectory() as temporary:
            root = initialize(Path(temporary) / "project").root

            def recorded_pair(steps):
                parameters = {
                    "backend": "pytorch",
                    "executor": "local",
                    "steps": str(steps),
                    "learning_rate": "0.5",
                    "seed": "7",
                }
                metrics = {
                    **parameters,
                    "example": "autoregressive-character-v1",
                    "validation_loss": 0.5,
                }
                command = [sys.executable, "-c", f"print({json.dumps(metrics)!r})"]
                baseline = run(command, root=root, parameters=parameters)
                # Earlier callers use these names and create the child separately.
                child = branch(baseline.chokepoint.id, root=root, reason="legacy two-step")
                run(command, root=root, pathway_id=child.id, parameters=parameters)
                return baseline

            earlier = recorded_pair(2)
            later = recorded_pair(4)
            before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            selected = driver.report(root, source_branch_point_id=earlier.chokepoint.id)
            automatic = driver.report(root)
            self.assertEqual(selected["source_chokepoint_id"], earlier.chokepoint.id)
            self.assertEqual(selected["baseline"]["atom_id"], earlier.atom.id)
            self.assertEqual(automatic["source_chokepoint_id"], later.chokepoint.id)
            self.assertEqual(selected["verification"], {"ok": True, "atoms": 4, "logs": 8})
            self.assertEqual(before, {p: p.read_bytes() for p in root.rglob("*") if p.is_file()})


class AutoregressiveIntegrationTests(unittest.TestCase):
    def exercise(self, executor):
        with tempfile.TemporaryDirectory() as temporary:
            reports = {}
            for backend in ("pytorch", "jax"):
                root = Path(temporary) / backend
                command = [sys.executable, str(EXAMPLE_DIR / "experiment.py")]
                completed = subprocess.run(
                    [
                        *command,
                        "run",
                        "--root",
                        str(root),
                        "--backend",
                        backend,
                        "--executor",
                        executor,
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=False,
                    timeout=240,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                result = json.loads(completed.stdout)
                reports[backend] = result
                self.assertEqual(result["verification"], {"ok": True, "atoms": 2, "logs": 4})
                for key, steps in (("baseline", 40), ("variant", 120)):
                    metrics = result[key]
                    self.assertEqual(metrics["backend"], backend)
                    self.assertEqual(metrics["executor"], executor)
                    self.assertEqual(metrics["steps"], steps)
                    self.assertLess(metrics["train_loss"], metrics["initial_train_loss"])
                    self.assertTrue(metrics["generated_text"].startswith("red "))
                self.assertLess(result["validation_loss_delta"], 0)
                paths = list((root / ".ocura-oss").rglob("*"))
                before = {path: path.read_bytes() for path in paths if path.is_file()}
                restored = subprocess.run(
                    [*command, "report", "--root", str(root)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=False,
                    timeout=30,
                )
                self.assertEqual(restored.returncode, 0, restored.stderr)
                self.assertEqual(json.loads(restored.stdout), result)
                self.assertEqual(before, {path: path.read_bytes() for path in before})
                rejected = subprocess.run(
                    [*command, "run", "--root", str(root), "--backend", backend],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                self.assertEqual(rejected.returncode, 2)
                self.assertEqual(before, {path: path.read_bytes() for path in before})
                log = root / result["variant"]["stdout_log"]
                log.write_bytes(log.read_bytes() + b"changed")
                rejected = subprocess.run(
                    [*command, "report", "--root", str(root)],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                self.assertEqual(rejected.returncode, 2)
                self.assertEqual(rejected.stdout, "")
                self.assertIn("verification failed", rejected.stderr)
            # Shared data, initialization, and updates should agree within float32 tolerance.
            self.assertAlmostEqual(
                reports["pytorch"]["variant"]["validation_loss"],
                reports["jax"]["variant"]["validation_loss"],
                places=4,
            )

    @unittest.skipUnless(
        os.environ.get("OCURA_OSS_EXAMPLE_TESTS") in ("local", "all"),
        "set OCURA_OSS_EXAMPLE_TESTS=local or all; needs PyTorch and JAX",
    )
    def test_local_backends(self):
        self.exercise("local")

    @unittest.skipUnless(
        os.environ.get("OCURA_OSS_EXAMPLE_TESTS") in ("ray", "all"),
        "set OCURA_OSS_EXAMPLE_TESTS=ray or all; needs Ray, PyTorch, and JAX",
    )
    def test_ray_backends(self):
        self.exercise("ray")
