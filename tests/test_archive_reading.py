# SPDX-License-Identifier: Apache-2.0

import errno
import hashlib
import subprocess
import sys
import tempfile
import tracemalloc
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ocura_oss import Store, StoreError, initialize, logstorage, run


class ArchiveReadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        initialize(self.root)
        self.store = Store(self.root)

    def record(self, data=b"output\x00\xff\r\n", *, pathway=None, code=0):
        return run(
            [
                sys.executable,
                "-c",
                f"import sys; sys.stdout.buffer.write({data!r}); sys.exit({code})",
            ],
            root=self.root,
            pathway_id=pathway,
        )

    def invoke(self, *arguments):
        return subprocess.run(
            [sys.executable, "-m", "ocura_oss", *arguments, "--root", str(self.root)],
            capture_output=True,
            timeout=30,
            check=False,
        )

    def inventory(self):
        return {
            path.relative_to(self.store.state_dir): (path.stat().st_mtime_ns, path.read_bytes())
            for path in self.store.state_dir.rglob("*")
            if path.is_file()
        }

    def test_raw_and_gzip_snapshots_are_seekable_and_closed(self):
        data = b"binary\x00\xff\r\n" * 10000
        atom = self.record().atom
        raw = self.store.resolve_log_path(atom, "stdout")
        raw.write_bytes(data)
        atom = replace(atom, stdout_bytes=len(data), stdout_sha256=hashlib.sha256(data).hexdigest())
        self.store._save_atom(atom)
        for compressed in (False, True):
            with self.subTest(compressed=compressed):
                if compressed:
                    self.store.compress_logs(atom_id=atom.id, min_bytes=1)
                with self.store.open_verified_log(atom.id) as source:
                    source.seek(17)
                    self.assertEqual(source.read(23), data[17:40])
                    source.seek(-9, 2)
                    self.assertEqual(source.read(), data[-9:])
                self.assertTrue(source.closed)

    def test_snapshot_remains_exact_after_original_changes(self):
        data = b"original evidence"
        atom = self.record(data).atom
        raw = self.store.resolve_log_path(atom, "stdout")
        with self.store.open_verified_log(atom.id) as source:
            raw.write_bytes(b"different evidence")
            self.assertEqual(source.read(), data)
        with self.assertRaises(StoreError), self.store.open_verified_log(atom.id):
            self.fail("corrupt content was exposed")

    def test_snapshot_checks_other_present_representation(self):
        atom = self.record(b"repetitive evidence" * 100).atom
        raw = self.store.resolve_log_path(atom, "stdout")
        self.store.compress_logs(atom_id=atom.id, min_bytes=1)
        raw.write_bytes(b"repetitive evidence" * 100)
        packed = raw.with_suffix(".log.gz")
        packed.write_bytes(packed.read_bytes()[:-5])
        with self.assertRaises(StoreError), self.store.open_verified_log(atom.id):
            self.fail("corrupt duplicate storage was exposed")

    def test_large_snapshot_bounds_memory_and_closes_on_caller_error(self):
        atom = self.record().atom
        raw = self.store.resolve_log_path(atom, "stdout")
        chunk = b"large evidence\n" * 65536
        digest = hashlib.sha256()
        size = len(chunk) * 32
        with raw.open("wb") as destination:
            for _ in range(32):
                destination.write(chunk)
                digest.update(chunk)
        self.store._save_atom(replace(atom, stdout_bytes=size, stdout_sha256=digest.hexdigest()))
        for compressed in (False, True):
            with self.subTest(compressed=compressed):
                if compressed:
                    self.store.compress_logs(atom_id=atom.id, min_bytes=1)
                tracemalloc.start()
                try:
                    with (
                        self.assertRaisesRegex(RuntimeError, "caller stopped"),
                        self.store.open_verified_log(atom.id) as source,
                    ):
                        self.assertEqual(source.seek(0, 2), size)
                        source.seek(0)
                        self.assertEqual(source.read(20), chunk[:20])
                        raise RuntimeError("caller stopped")
                    peak = tracemalloc.get_traced_memory()[1]
                finally:
                    tracemalloc.stop()
                self.assertTrue(source.closed)
                self.assertLess(peak, 6 * 1024 * 1024)

    def test_failed_large_snapshot_closes_its_temporary_file(self):
        atom = self.record().atom
        self.store._save_atom(replace(atom, stdout_bytes=2 * 1024 * 1024))
        with tempfile.TemporaryFile() as scratch:
            with (
                patch.object(logstorage.tempfile, "TemporaryFile", return_value=scratch),
                self.assertRaises(StoreError),
                self.store.open_verified_log(atom.id),
            ):
                self.fail("size mismatch was exposed")
            self.assertTrue(scratch.closed)

    def test_cli_ranges_are_original_bytes_with_explicit_bounds(self):
        data = bytes(range(256)) * 10
        atom = self.record(data).atom
        self.store.compress_logs(atom_id=atom.id, min_bytes=1)
        for arguments, expected in (
            (("--offset", "255", "--length", "6"), data[255:261]),
            (("--offset", str(len(data))), b""),
            (("--length", "0"), b""),
            (("--offset", "2550", "--length", "100"), data[2550:]),
        ):
            with self.subTest(arguments=arguments):
                result = self.invoke("logs", atom.id, *arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)
        for arguments in (
            ("--offset", "-1"),
            ("--length", "-1"),
            ("--offset", str(len(data) + 1)),
            ("--json", "--length", "1"),
        ):
            with self.subTest(arguments=arguments):
                result = self.invoke("logs", atom.id, *arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")

    def test_small_window_of_large_log_does_not_allocate_temporary_storage(self):
        atom = self.record().atom
        raw = self.store.resolve_log_path(atom, "stdout")
        data = b"large evidence\n" * 100000
        raw.write_bytes(data)
        self.store._save_atom(
            replace(atom, stdout_bytes=len(data), stdout_sha256=hashlib.sha256(data).hexdigest())
        )
        self.store.compress_logs(atom_id=atom.id, min_bytes=1)
        with (
            patch.object(
                logstorage.tempfile, "TemporaryFile", side_effect=AssertionError("disk copy")
            ),
            self.store.open_verified_log(atom.id, offset=1024, length=128) as source,
        ):
            self.assertEqual(source.read(), data[1024:1152])

    def test_small_range_still_rejects_corruption_at_end_of_log(self):
        atom = self.record(b"prefix followed by corrupt evidence").atom
        raw = self.store.resolve_log_path(atom, "stdout")
        raw.write_bytes(raw.read_bytes()[:-1] + b"!")
        result = self.invoke("logs", atom.id, "--length", "6")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")

    def test_archive_reads_do_not_write_or_take_ledger_locks(self):
        data = b"archived binary evidence\x00\xff" * 100
        atom = self.record(data).atom
        self.store.compress_logs(atom_id=atom.id, min_bytes=1)
        before = self.inventory()
        archive = Store(self.root, archive=True)
        with (
            patch("ocura_oss.locking.hold", side_effect=AssertionError("tried a writer lock")),
            patch.object(Path, "mkdir", side_effect=AssertionError("tried to create a directory")),
        ):
            archive.require()
            self.assertEqual(archive.read_verified_log(atom.id), data)
            with archive.open_verified_log(atom.id) as source:
                self.assertEqual(source.read(), data)
            self.assertEqual(archive.log_info(atom.id).encoding, "gzip")
            self.assertTrue(archive.verify_state().ok)
        self.assertEqual(self.inventory(), before)
        for arguments in (("verify", "--json"), ("logs", atom.id), ("runs",), ("show", atom.id)):
            result = self.invoke(*arguments, "--archive")
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.inventory(), before)

    def test_archive_rejects_mutations_and_unfinished_attempts(self):
        atom = self.record().atom
        archive = Store(self.root, archive=True)
        before = self.inventory()
        for operation in (
            archive.recover,
            archive.compress_logs,
            archive.decompress_logs,
            lambda: archive.initialize_state(name="invalid"),
            lambda: archive._save_atom(atom),
        ):
            with (
                self.subTest(operation=operation),
                self.assertRaisesRegex(StoreError, "archive mode"),
            ):
                operation()
        self.assertEqual(self.inventory(), before)
        (archive.attempts_dir / f"{atom.id}.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(StoreError, "1 unfinished attempt.*recover"):
            archive.verify_state()
        result = self.invoke("logs", atom.id, "--archive")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")

    def test_normal_reads_skip_locks_only_on_write_protected_storage(self):
        data = b"evidence " * 100
        atom = self.record(data).atom
        with patch("ocura_oss.locking.hold", side_effect=PermissionError("lock denied")) as hold:
            self.assertEqual(self.store.read_verified_log(atom.id), data)
            self.assertTrue(self.store.verify_state().ok)
            self.assertEqual(hold.call_count, 1)
            with self.assertRaisesRegex(StoreError, "unreadable"):
                self.store.compress_logs(atom_id=atom.id, min_bytes=1)
        self.assertEqual(self.store.log_info(atom.id).encoding, "raw")
        with (
            patch("ocura_oss.locking.hold", side_effect=OSError(errno.EIO, "lock failed")),
            self.assertRaisesRegex(StoreError, "log is unreadable"),
            Store(self.root).open_verified_log(atom.id),
        ):
            self.fail("normal read skipped writer coordination")


if __name__ == "__main__":
    unittest.main()
