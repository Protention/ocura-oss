# SPDX-License-Identifier: Apache-2.0

import contextlib
import gzip
import hashlib
import io
import json
import random
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from ocura_oss import api, cli, locking, model, runner
from ocura_oss.store import Store, StoreError

_MIB = 1024 * 1024


class CompressionTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = (Path(self._temporary.name) / "project").resolve()
        self.store = Store(self.root)
        _, self.pathway = self.store.initialize_state(name="compression tests")

    def _run_bytes(self, data, *, stderr=b"", compress=False, **options):
        (self.root / "stdout.bin").write_bytes(data)
        (self.root / "stderr.bin").write_bytes(stderr)
        script = (
            "from pathlib import Path; import sys; "
            "sys.stdout.buffer.write(Path('stdout.bin').read_bytes()); "
            "sys.stderr.buffer.write(Path('stderr.bin').read_bytes())"
        )
        return runner.run_command(
            self.store,
            pathway_id=self.pathway.id,
            argv=[sys.executable, "-c", script],
            declared_parameters={},
            compress=compress,
            **options,
        )

    def _paths(self, atom, stream="stdout"):
        raw = self.root / getattr(atom, f"{stream}_log")
        return raw, raw.with_suffix(raw.suffix + ".gz")

    def _snapshot(self):
        return {
            path.relative_to(self.store.state_dir).as_posix(): path.read_bytes()
            for path in self.store.state_dir.rglob("*")
            if path.is_file()
        }

    def _stdout_change(self, changes, atom):
        return next(
            change for change in changes if change.atom_id == atom.id and change.stream == "stdout"
        )

    def _cli(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main([*arguments, "--root", str(self.root)])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_binary_roundtrip_preserves_records_manifest_and_content_hash(self):
        data = bytes(range(256)) * 4096 + b"\x00\xff\r\n\x80last"
        atom = self._run_bytes(data).atom
        before = self.store.verify_state()
        self.assertTrue(before.ok, before.problems)
        records = {
            path: content for path, content in self._snapshot().items() if path.endswith(".json")
        }
        raw, packed = self._paths(atom)
        info_before = self.store.log_info(atom.id)

        change = self._stdout_change(self.store.compress_logs(atom_id=atom.id), atom)
        self.assertEqual(change.action, "compressed")
        self.assertEqual(change.original_bytes, len(data))
        self.assertEqual(change.stored_bytes, packed.stat().st_size)
        self.assertFalse(raw.exists())
        self.assertEqual(gzip.decompress(packed.read_bytes()), data)
        self.assertEqual(self.store.resolve_log_path(atom, "stdout"), packed)
        self.assertEqual(self.store.read_verified_log(atom.id), data)
        info = self.store.log_info(atom.id)
        self.assertEqual(info.encoding, "gzip")
        self.assertEqual(info.original_bytes, len(data))
        self.assertEqual(info.stored_bytes, packed.stat().st_size)
        self.assertEqual(info.sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(info.fingerprint, info_before.fingerprint)
        self.assertTrue(info.fingerprint)
        self.assertEqual(info.started_at, atom.started_at)
        self.assertEqual(info.atom_id, atom.id)
        self.assertEqual(info.stream, "stdout")
        after = self.store.verify_state(against=before.manifest)
        self.assertTrue(after.ok, after.problems)
        self.assertEqual(after.manifest, before.manifest)
        self.assertEqual(after.digest, before.digest)
        self.assertEqual(
            {path: content for path, content in self._snapshot().items() if path.endswith(".json")},
            records,
        )

        restored = self._stdout_change(self.store.decompress_logs(atom_id=atom.id), atom)
        self.assertEqual(restored.action, "restored")
        self.assertEqual(raw.read_bytes(), data)
        self.assertFalse(packed.exists())
        self.assertEqual(self.store.resolve_log_path(atom, "stdout"), raw)
        self.assertEqual(self.store.read_verified_log(atom.id), data)
        self.assertEqual(self.store.verify_state().digest, before.digest)

    def test_logs_stay_raw_by_default_and_compress_at_threshold_on_request(self):
        old = api.run(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'old'*1048576)"],
            root=self.root,
        ).atom
        old_raw, old_packed = self._paths(old)
        execution = api.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write(b'a'*1048576); "
                "sys.stderr.buffer.write(b'b'*1048575)",
            ],
            root=self.root,
            compress=True,
        )
        stdout, stdout_packed = self._paths(execution.atom)
        stderr, stderr_packed = self._paths(execution.atom, "stderr")
        self.assertFalse(stdout.exists())
        self.assertTrue(stdout_packed.is_file())
        self.assertEqual(stderr.stat().st_size, _MIB - 1)
        self.assertFalse(stderr_packed.exists())
        self.assertTrue(old_raw.is_file())
        self.assertFalse(old_packed.exists())
        self.assertTrue(self.store.verify_state().ok)
        self.assertEqual(self.store.list_attempts(), [])

    def test_compression_threshold_can_be_changed(self):
        small = self._run_bytes(b"x" * 1024, compress=True, compression_min_bytes=1024).atom
        raw, packed = self._paths(small)
        self.assertFalse(raw.exists())
        self.assertTrue(packed.is_file())

    def test_automatic_small_logs_do_not_attempt_a_compression_pass(self):
        with mock.patch("ocura_oss.logstorage.compress") as compress:
            execution = self._run_bytes(b"small result", compress=True)
        compress.assert_not_called()
        self.assertEqual(execution.storage_warnings, ())
        self.assertEqual(self.store.read_verified_log(execution.atom.id), b"small result")

    def test_incompressible_log_remains_raw(self):
        data = random.Random(1729).randbytes(128 * 1024)
        atom = self._run_bytes(data).atom
        before = self._snapshot()
        change = self._stdout_change(self.store.compress_logs(atom_id=atom.id, min_bytes=1), atom)
        self.assertEqual(change.action, "no-savings")
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(self.store.read_verified_log(atom.id), data)

    def test_small_and_empty_logs_are_not_compressed(self):
        atom = self._run_bytes(b"a" * 1024).atom
        before = self._snapshot()
        changes = self.store.compress_logs(atom_id=atom.id)
        self.assertEqual({change.action for change in changes}, {"small"})
        self.assertEqual(self._snapshot(), before)

    def test_compression_requires_at_least_ten_percent_savings(self):
        noise = random.Random(831).randbytes(9000)
        for repeated, expected_action in ((1000, "no-savings"), (1500, "compressed")):
            with self.subTest(expected_action=expected_action):
                data = noise + b"a" * repeated
                encoded_size = len(gzip.compress(data, compresslevel=6, mtime=0))
                self.assertLess(encoded_size, len(data), "both fixtures have some savings")
                self.assertEqual(encoded_size <= len(data) * 0.9, expected_action == "compressed")
                atom = self._run_bytes(data).atom
                change = self._stdout_change(
                    self.store.compress_logs(atom_id=atom.id, min_bytes=1), atom
                )
                self.assertEqual(change.action, expected_action)
                self.assertEqual(self.store.read_verified_log(atom.id), data)

    def test_dry_runs_leave_every_state_file_byte_identical(self):
        atom = self._run_bytes(b"abc" * _MIB).atom
        before = self._snapshot()
        change = self._stdout_change(self.store.compress_logs(atom_id=atom.id, dry_run=True), atom)
        self.assertEqual(change.action, "would-compress")
        self.assertEqual(self._snapshot(), before)
        self.store.compress_logs(atom_id=atom.id)
        before = self._snapshot()
        change = self._stdout_change(
            self.store.decompress_logs(atom_id=atom.id, dry_run=True), atom
        )
        self.assertEqual(change.action, "would-restore")
        self.assertEqual(self._snapshot(), before)

    def test_gzip_representation_is_deterministic_and_has_no_filename_or_timestamp(self):
        atom = self._run_bytes(b"repeatable" * 10000).atom
        self.store.compress_logs(atom_id=atom.id, min_bytes=1)
        _, packed = self._paths(atom)
        first = packed.read_bytes()
        self.assertEqual(first[:3], b"\x1f\x8b\x08")
        self.assertEqual(first[3] & 8, 0, "gzip must not embed a local filename")
        self.assertEqual(first[4:8], b"\x00" * 4)
        self.store.decompress_logs(atom_id=atom.id)
        self.store.compress_logs(atom_id=atom.id, min_bytes=1)
        self.assertEqual(packed.read_bytes(), first)

    def test_raw_log_with_gzip_magic_is_returned_without_decoding(self):
        data = gzip.compress(b"contents of an independently captured gzip file", mtime=0)
        atom = self._run_bytes(data).atom
        self.assertEqual(self.store.read_verified_log(atom.id), data)
        self.assertEqual(self.store.log_info(atom.id).original_bytes, len(data))
        self.assertTrue(self.store.verify_state().ok)

    def test_corrupt_truncated_trailing_and_oversized_gzip_fail_closed(self):
        data = b"recorded bytes" * 2000
        atom = self._run_bytes(data).atom
        self.store.compress_logs(atom_id=atom.id, min_bytes=1)
        _, packed = self._paths(atom)
        valid = packed.read_bytes()
        corrupted = bytearray(valid)
        corrupted[-5] ^= 1
        variants = {
            "checksum": bytes(corrupted),
            "truncated": valid[:-8],
            "trailing": valid + b"unrecorded trailing data",
            "oversized": gzip.compress(data + b"x" * (4 * _MIB), mtime=0),
            "different-content": gzip.compress(b"z" * len(data), mtime=0),
        }
        for name, content in variants.items():
            with self.subTest(name=name):
                packed.write_bytes(content)
                with self.assertRaises(StoreError):
                    self.store.read_verified_log(atom.id)
                with self.assertRaises(StoreError):
                    self.store.log_info(atom.id)
                with self.assertRaises(StoreError):
                    self.store.decompress_logs(atom_id=atom.id)
                self.assertFalse(self.store.verify_state().ok)
                self.assertEqual(packed.read_bytes(), content)
        packed.write_bytes(valid)
        self.assertEqual(self.store.read_verified_log(atom.id), data)

    def test_both_valid_copies_are_readable_but_corrupt_raw_cannot_hide_behind_gzip(self):
        data = b"verified dual copies" * 1000
        atom = self._run_bytes(data).atom
        raw, packed = self._paths(atom)
        self.store.compress_logs(atom_id=atom.id, min_bytes=1)
        raw.write_bytes(data)
        self.assertEqual(self.store.read_verified_log(atom.id), data)
        self.assertTrue(self.store.verify_state().ok)
        raw.write_bytes(b"bad" + data[3:])
        with self.assertRaises(StoreError):
            self.store.read_verified_log(atom.id)
        with self.assertRaises(StoreError):
            self.store.log_info(atom.id)
        with self.assertRaises(StoreError):
            self.store.compress_logs(atom_id=atom.id, min_bytes=1)
        self.assertFalse(self.store.verify_state().ok)
        self.assertTrue(packed.is_file())

    def test_corrupt_gzip_cannot_hide_behind_valid_raw_copy(self):
        data = b"raw copy remains intact" * 1000
        atom = self._run_bytes(data).atom
        raw, packed = self._paths(atom)
        packed.write_bytes(b"invalid gzip")
        with self.assertRaises(StoreError):
            self.store.read_verified_log(atom.id)
        with self.assertRaises(StoreError):
            self.store.decompress_logs(atom_id=atom.id)
        self.assertEqual(raw.read_bytes(), data)
        self.assertFalse(self.store.verify_state().ok)

    def test_compressed_symlink_escape_is_rejected(self):
        data = b"symlink protected" * 1000
        atom = self._run_bytes(data).atom
        raw, packed = self._paths(atom)
        outside = self.root.parent / "outside.log.gz"
        outside.write_bytes(gzip.compress(data, mtime=0))
        try:
            packed.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        raw.unlink()
        with self.assertRaises(StoreError):
            self.store.read_verified_log(atom.id)
        with self.assertRaises(StoreError):
            self.store.log_info(atom.id)
        with self.assertRaises(StoreError):
            self.store.decompress_logs(atom_id=atom.id)
        self.assertFalse(self.store.verify_state().ok)
        self.assertEqual(gzip.decompress(outside.read_bytes()), data)

    def test_unchanged_operations_are_idempotent(self):
        data = b"idempotent" * 1000
        atom = self._run_bytes(data).atom
        first = self._stdout_change(self.store.decompress_logs(atom_id=atom.id), atom)
        self.assertEqual(first.action, "unchanged")
        self.store.compress_logs(atom_id=atom.id, min_bytes=1)
        before = self._snapshot()
        second = self._stdout_change(self.store.compress_logs(atom_id=atom.id, min_bytes=1), atom)
        self.assertEqual(second.action, "unchanged")
        self.assertEqual(self._snapshot(), before)

    def test_manual_operations_leave_unfinished_attempt_logs_untouched(self):
        with (
            mock.patch.object(
                self.store, "_remove_attempt", side_effect=RuntimeError("stopped before cleanup")
            ),
            self.assertRaisesRegex(RuntimeError, "stopped before cleanup"),
        ):
            self._run_bytes(b"unfinished" * _MIB)
        atom = self.store.list_atoms()[0]
        before = self._snapshot()
        self.assertEqual(self.store.compress_logs(atom_id=atom.id, min_bytes=1), ())
        self.assertEqual(self.store.decompress_logs(atom_id=atom.id), ())
        self.assertEqual(self._snapshot(), before)

    def test_no_capture_and_launch_failures_remain_valid(self):
        no_capture = self._run_bytes(b"secret" * _MIB, capture=False, compress=True).atom
        self.assertIs(no_capture.output_capture, model.OutputCapture.NONE)
        self.assertEqual(self.store.compress_logs(atom_id=no_capture.id), ())
        self.assertEqual(self.store.decompress_logs(atom_id=no_capture.id), ())
        self.assertEqual(list(self.store.logs_dir.iterdir()), [])
        with self.assertRaises(StoreError):
            self.store.log_info(no_capture.id)
        execution = runner.run_command(
            self.store,
            pathway_id=self.pathway.id,
            argv=["not-a-real-executable-ocura-oss-compression-test"],
            declared_parameters={},
        )
        self.assertIs(execution.atom.outcome, model.Outcome.LAUNCH_FAILED)
        self.assertTrue(self.store.verify_state().ok)

    def test_invalid_threshold_is_rejected_before_running_a_command(self):
        marker = self.root / "should-not-exist"
        for minimum in (-1, True, 1.5):
            with self.subTest(minimum=minimum), self.assertRaises((StoreError, ValueError)):
                api.run(
                    [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; Path('should-not-exist').touch()",
                    ],
                    root=self.root,
                    compression_min_bytes=minimum,
                )
        self.assertFalse(marker.exists())
        self.assertEqual(self.store.list_atoms(), [])
        self.assertEqual(self.store.list_attempts(), [])

    def test_gzip_read_stops_after_recorded_size_plus_one_detection_byte(self):
        data = b"r" * 512
        atom = self._run_bytes(data).atom
        self.store.compress_logs(atom_id=atom.id, min_bytes=1)
        _, packed = self._paths(atom)
        packed.write_bytes(gzip.compress(data + b"excess" * _MIB, mtime=0))
        original_read = gzip.GzipFile.read
        returned_sizes = []

        def measured_read(source, size=-1):
            self.assertGreater(size, 0, "decompression must always request a bounded read")
            self.assertLessEqual(size, len(data) + 1)
            result = original_read(source, size)
            returned_sizes.append(len(result))
            return result

        with (
            mock.patch.object(gzip.GzipFile, "read", new=measured_read),
            self.assertRaises(StoreError),
        ):
            self.store.read_verified_log(atom.id)
        self.assertEqual(sum(returned_sizes), len(data) + 1)

    def test_failed_encoding_or_publication_preserves_original_and_cleans_temporary_files(self):
        data = b"preserved original" * 1000
        atom = self._run_bytes(data).atom
        raw, packed = self._paths(atom)
        before = self._snapshot()
        for target in ("_encode", "os.replace"):
            with self.subTest(target=target):
                with (
                    mock.patch(
                        f"ocura_oss.logstorage.{target}",
                        side_effect=OSError("injected write failure"),
                    ),
                    self.assertRaises(StoreError),
                ):
                    self.store.compress_logs(atom_id=atom.id, min_bytes=1)
                self.assertEqual(raw.read_bytes(), data)
                self.assertFalse(packed.exists())
                self.assertEqual(self._snapshot(), before)
                self.assertTrue(self.store.verify_state().ok)

    def test_interruption_after_publication_leaves_verified_copies_and_can_resume(self):
        data = b"publish before removal" * 1000
        atom = self._run_bytes(data).atom
        raw, packed = self._paths(atom)
        original_unlink = Path.unlink

        def fail_raw_removal(path, *args, **kwargs):
            if path == raw:
                raise PermissionError("injected interruption before raw removal")
            return original_unlink(path, *args, **kwargs)

        with (
            mock.patch.object(Path, "unlink", new=fail_raw_removal),
            self.assertRaises(StoreError),
        ):
            self.store.compress_logs(atom_id=atom.id, min_bytes=1)
        self.assertEqual(raw.read_bytes(), data)
        self.assertEqual(gzip.decompress(packed.read_bytes()), data)
        self.assertTrue(self.store.verify_state().ok)
        self.assertEqual(self.store.read_verified_log(atom.id), data)
        self.store.compress_logs(atom_id=atom.id, min_bytes=1)
        self.assertFalse(raw.exists())

        def fail_packed_removal(path, *args, **kwargs):
            if path == packed:
                raise PermissionError("injected interruption before gzip removal")
            return original_unlink(path, *args, **kwargs)

        with (
            mock.patch.object(Path, "unlink", new=fail_packed_removal),
            self.assertRaises(StoreError),
        ):
            self.store.decompress_logs(atom_id=atom.id)
        self.assertEqual(raw.read_bytes(), data)
        self.assertEqual(gzip.decompress(packed.read_bytes()), data)
        self.assertTrue(self.store.verify_state().ok)
        self.store.decompress_logs(atom_id=atom.id)
        self.assertFalse(packed.exists())
        self.assertEqual(raw.read_bytes(), data)

    def test_failed_restore_publication_preserves_compressed_original(self):
        data = b"restore transaction" * 1000
        atom = self._run_bytes(data).atom
        self.store.compress_logs(atom_id=atom.id, min_bytes=1)
        raw, packed = self._paths(atom)
        before = self._snapshot()
        with (
            mock.patch(
                "ocura_oss.logstorage.os.replace",
                side_effect=OSError("injected publication failure"),
            ),
            self.assertRaises(StoreError),
        ):
            self.store.decompress_logs(atom_id=atom.id)
        self.assertFalse(raw.exists())
        self.assertEqual(gzip.decompress(packed.read_bytes()), data)
        self.assertEqual(self._snapshot(), before)
        self.assertTrue(self.store.verify_state().ok)

    def test_automatic_compression_failure_preserves_command_result_and_reports_warning(self):
        with mock.patch.object(
            self.store, "_change_atom_storage", side_effect=StoreError("injected storage failure")
        ):
            execution = self._run_bytes(b"recorded output", compress=True)
        self.assertIs(execution.atom.outcome, model.Outcome.PASSED)
        self.assertEqual(execution.atom.return_code, 0)
        self.assertTrue(execution.storage_warnings)
        self.assertIn("injected storage failure", execution.storage_warnings[0])
        self.assertEqual(self.store.read_verified_log(execution.atom.id), b"recorded output")
        self.assertEqual(self.store.list_attempts(), [])
        self.assertTrue(self.store.verify_state().ok)

    @unittest.skipUnless(sys.platform == "win32", "Windows lock-file handoff")
    def test_reader_retries_transient_windows_lock_open_denial(self):
        atom = self._run_bytes(b"retained output").atom
        lock_path = self.store.state_dir / "log-locks" / f"{atom.id}.stdout.log.lock"
        original_open = locking.os.open
        denied = False

        def deny_once(path, *args, **kwargs):
            nonlocal denied
            if Path(path) == lock_path and not denied:
                denied = True
                raise PermissionError("lock name is being removed")
            return original_open(path, *args, **kwargs)

        with mock.patch.object(locking.os, "open", side_effect=deny_once):
            self.assertEqual(self.store.read_verified_log(atom.id), b"retained output")
        self.assertTrue(denied)

    def test_readers_and_opposing_storage_operations_coordinate(self):
        data = bytes(range(256)) * 1000
        atom = self._run_bytes(data).atom
        ready = threading.Barrier(4)

        def compress_repeatedly():
            ready.wait(timeout=5)
            for _ in range(12):
                self.store.compress_logs(atom_id=atom.id, min_bytes=1)

        def restore_repeatedly():
            ready.wait(timeout=5)
            for _ in range(12):
                self.store.decompress_logs(atom_id=atom.id)

        def read_repeatedly():
            ready.wait(timeout=5)
            for _ in range(24):
                self.assertEqual(self.store.read_verified_log(atom.id), data)

        def verify_repeatedly():
            ready.wait(timeout=5)
            for _ in range(12):
                report = self.store.verify_state()
                self.assertTrue(report.ok, report.problems)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(operation)
                for operation in (
                    compress_repeatedly,
                    restore_repeatedly,
                    read_repeatedly,
                    verify_repeatedly,
                )
            ]
            for future in futures:
                future.result(timeout=20)
        self.assertTrue(self.store.verify_state().ok)
        self.assertEqual(self.store.read_verified_log(atom.id), data)

    def test_cli_compress_decompress_and_info_json(self):
        data = b"CLI readable evidence" * 10000
        atom = self._run_bytes(data).atom
        code, text, error = self._cli(
            "compress", "--atom", atom.id, "--min-bytes", "1", "--dry-run", "--json"
        )
        self.assertEqual(code, 0, error)
        self.assertIn("would-compress", json.dumps(json.loads(text)))
        raw, packed = self._paths(atom)
        self.assertTrue(raw.is_file())
        code, text, error = self._cli("compress", "--atom", atom.id, "--min-bytes", "1", "--json")
        self.assertEqual(code, 0, error)
        self.assertIn("compressed", json.dumps(json.loads(text)))
        self.assertTrue(packed.is_file())
        code, text, error = self._cli("logs", atom.id, "--info", "--json")
        self.assertEqual(code, 0, error)
        info = json.loads(text)
        self.assertEqual(info["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(info["original_bytes"], len(data))
        self.assertEqual(info["encoding"], "gzip")
        self.assertTrue(info["fingerprint"])
        code, text, error = self._cli("decompress", "--atom", atom.id, "--json")
        self.assertEqual(code, 0, error)
        self.assertIn("restored", json.dumps(json.loads(text)))
        self.assertEqual(raw.read_bytes(), data)

    def test_cli_logs_emit_original_binary_and_stderr_bytes(self):
        data = bytes(range(256)) * 1000 + b"\r\n\x00last"
        error = b"error\x00\xff\r\n" * 1000
        atom = self._run_bytes(data, stderr=error).atom
        self.store.compress_logs(atom_id=atom.id, min_bytes=1)
        for stream, expected in (("stdout", data), ("stderr", error)):
            with self.subTest(stream=stream):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "ocura_oss",
                        "logs",
                        atom.id,
                        "--stream",
                        stream,
                        "--root",
                        str(self.root),
                    ],
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)
                self.assertEqual(result.stderr, b"")

    def test_cli_rejects_late_gzip_corruption_without_releasing_log_bytes(self):
        data = b"original verified output\n" * 150000
        atom = self._run_bytes(data).atom
        self.store.compress_logs(atom_id=atom.id, min_bytes=1)
        _raw, packed = self._paths(atom)
        original = packed.read_bytes()
        for changed, error in (
            (gzip.compress(data[:-1] + b"!", mtime=0), b"checksum mismatch"),
            (original[:-8], b"unreadable"),
        ):
            with self.subTest(error=error):
                packed.write_bytes(changed)
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "ocura_oss",
                        "logs",
                        atom.id,
                        "--root",
                        str(self.root),
                    ],
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertIn(error, result.stderr)

    def test_log_lookup_failure_raises_but_show_keeps_the_record(self):
        atom = self._run_bytes(b"retained evidence").atom
        raw, packed = self._paths(atom)
        packed.mkdir()
        with self.assertRaises(StoreError) as failure:
            self.store.log_files(atom)
        self.assertEqual(failure.exception.code, "integrity_error")
        self.assertEqual(failure.exception.ids, {"run_id": atom.id})
        for flags in ((), ("--archive",)):
            with self.subTest(flags=flags):
                code, stdout, stderr = self._cli("show", atom.id, "--json-errors", *flags)
                self.assertEqual(code, 0)
                shown = json.loads(stdout)
                self.assertEqual(shown["atom"]["id"], atom.id)
                self.assertEqual(shown["log_files"], {})
                self.assertIn("not a regular file", shown["storage_warnings"][0])
                self.assertIn("log file lookup failed", stderr)
        self.assertIn("stdout: unavailable", self._cli("show", atom.id)[1])
        self.assertEqual(raw.read_bytes(), b"retained evidence")

    def test_cli_log_lookup_failure_preserves_completed_run_results(self):
        script = (
            "from pathlib import Path; import sys; "
            "attempt = next(Path('.ocura-oss/attempts').glob('*.json')); "
            "Path('.ocura-oss/logs', attempt.stem + '.stdout.log.gz').mkdir(); "
            "sys.stdout.write('x' * 2048); sys.exit(int(sys.argv[1]))"
        )
        for flags, return_code in ((("--json-errors",), 7), ((), 0)):
            with self.subTest(flags=flags):
                previous = {atom.id for atom in self.store.list_atoms()}
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = cli.main(
                        [
                            "run",
                            "--root",
                            str(self.root),
                            "--quiet",
                            *flags,
                            "--compress-min-bytes",
                            "1024",
                            "--",
                            sys.executable,
                            "-c",
                            script,
                            str(return_code),
                        ]
                    )
                self.assertEqual(code, 0 if return_code == 0 else 1)
                (atom,) = [item for item in self.store.list_atoms() if item.id not in previous]
                self.assertEqual(atom.return_code, return_code)
                if flags:
                    payload = json.loads(stdout.getvalue())
                    self.assertEqual(payload["atom_id"], atom.id)
                    self.assertEqual(payload["log_files"], {})
                    self.assertEqual(len(payload["storage_warnings"]), 2)
                else:
                    self.assertIn("stdout log: unavailable", stdout.getvalue())
                self.assertIn("log file lookup failed", stderr.getvalue())
                self.assertEqual((self.root / atom.stdout_log).read_bytes(), b"x" * 2048)
                self.assertEqual(self.store.list_attempts(), [])

    def test_cli_run_compression_flags(self):
        for flags, compressed in (
            ([], False),
            (["--compress-min-bytes", "1024"], True),
        ):
            with self.subTest(flags=flags):
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = cli.main(
                        [
                            "run",
                            "--root",
                            str(self.root),
                            "--json",
                            *flags,
                            "--",
                            sys.executable,
                            "-c",
                            "import sys; sys.stdout.buffer.write(b'x'*2048)",
                        ]
                    )
                self.assertEqual(code, 0, stderr.getvalue())
                result = json.loads(stdout.getvalue())
                atom = self.store.load_atom(result["atom_id"])
                raw, packed = self._paths(atom)
                self.assertEqual(packed.is_file(), compressed)
                self.assertEqual(raw.is_file(), not compressed)
                # Reported files exist, so standard tools can open them.
                stored = packed if compressed else raw
                self.assertEqual(self.root / result["log_files"]["stdout"], stored)
                shown = io.StringIO()
                with contextlib.redirect_stdout(shown):
                    cli.main(["show", atom.id, "--root", str(self.root), "--json"])
                self.assertEqual(json.loads(shown.getvalue())["log_files"], result["log_files"])


if __name__ == "__main__":
    unittest.main()
