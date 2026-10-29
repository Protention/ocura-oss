# SPDX-License-Identifier: Apache-2.0

"""Direct process launch, binary log capture, timing, and terminal evidence."""

from __future__ import annotations

import contextlib
import datetime
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import BinaryIO

from ocura_oss import logstorage, model, supervise
from ocura_oss.context import capture as capture_context
from ocura_oss.errors import ExecutionState, classify_os_error, with_execution
from ocura_oss.store import (
    STATE_DIR_NAME,
    Store,
    StoreError,
    _validate_compression_min_bytes,
    size_and_sha256,
)

_PUMP_CHUNK = 65536
_STOP_TIMEOUT = 5.0

_LAUNCH_CATEGORIES: tuple[tuple[type[BaseException], str], ...] = (
    (FileNotFoundError, "executable_not_found"),
    (PermissionError, "permission_denied"),
    (NotADirectoryError, "not_a_directory"),
    (ValueError, "invalid_argument"),
)


@dataclass(frozen=True)
class RunExecution:
    """Atom and terminal chokepoint produced by one command attempt."""

    atom: model.Atom
    chokepoint: model.Chokepoint
    storage_warnings: tuple[str, ...] = ()

    @property
    def record(self) -> model.RunRecord:
        return self.atom

    @property
    def branch_point(self) -> model.BranchPoint:
        return self.chokepoint


def categorize_launch_error(exc: BaseException) -> str:
    for exception_type, category in _LAUNCH_CATEGORIES:
        if isinstance(exc, exception_type):
            return category
    return "os_error"


def run_command(
    store: Store,
    *,
    pathway_id: str,
    argv: Sequence[str],
    declared_parameters: Mapping[str, str],
    mirror: bool = False,
    capture: bool = True,
    compress: bool = True,
    compression_min_bytes: int = logstorage.DEFAULT_MIN_BYTES,
    masked_arguments: Iterable[int] = (),
    substitute: bool = False,
    context: bool = False,
    context_files: Iterable[os.PathLike[str] | str] = (),
    create_pathway: Callable[[], model.Pathway] | None = None,
    now: Callable[[], datetime.datetime] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> RunExecution:
    """Run one command directly and persist its terminal evidence.

    The command runs with shell=False, cwd set to the project root, and the
    caller's inherited environment. No environment keys or values are ever
    serialized into records.

    When ``mirror`` is true the command's stdout/stderr are echoed to the
    terminal. When ``capture`` is false no output is retained and the atom
    records that it has no logs. ``masked_arguments`` names command token
    positions whose values are replaced by a placeholder in every record; the
    command itself still receives them.

    Completed logs at least ``compression_min_bytes`` large are compressed
    once when this run finishes, if gzip saves at least ten percent. Pass
    ``compress=False`` to keep raw files. Compression failures are returned in
    ``storage_warnings`` without changing the recorded command's outcome.

    The run records its pathway's effective parameters as its own labels;
    ``declared_parameters`` adds to them and wins for a key both name. When
    ``substitute`` is true, ``{KEY}`` in a command token is replaced by that
    label's value.
    ``context`` records the platform and git state at launch, and
    ``context_files`` records the size and digest of each named file.
    ``create_pathway`` is called once these inputs are prepared, and the run is
    recorded on the pathway it returns; ``pathway_id`` then supplies the labels.

    The attempt is journaled before the command launches. A Ctrl+C
    interruption stops the command and is recorded as an ``interrupted``
    atom and chokepoint. If this process stops before it can finalize, the
    journal entry remains and :meth:`Store.recover` closes the attempt as
    ``abandoned``; on Windows and Linux the command is then ended by the
    operating system, as :mod:`ocura_oss.supervise` describes.
    """
    execution: ExecutionState = "not_started"
    ids: dict[str, str] = {}
    try:
        clock = now or model.utc_now
        counter = monotonic or time.monotonic
        _validate_compression_min_bytes(compression_min_bytes)
        if not isinstance(compress, bool):
            raise StoreError("compress must be a boolean", code="invalid_input")
        if isinstance(argv, (str, bytes)):
            raise StoreError(
                "command must be a sequence of argument tokens, not a string", code="invalid_input"
            )
        tokens = tuple(argv) if isinstance(argv, Iterable) else ()
        if not tokens or not all(isinstance(token, str) and token for token in tokens):
            raise StoreError(
                "command must be a nonempty sequence of nonempty strings", code="invalid_input"
            )
        declared = model.validate_parameters(declared_parameters)
        pathway = store.load_pathway(pathway_id)
        parameters = {**pathway.parameters, **declared}
        try:
            if substitute:
                tokens, _used = model.substitute_parameters(tokens, parameters)
            recorded_command, masked = model.mask_command(tokens, masked_arguments)
        except model.ValidationError as exc:
            raise StoreError(str(exc), code="invalid_input") from exc
        run_context = capture_context(store.root, environment=context, files=context_files)
        if create_pathway is not None:
            pathway = create_pathway()
            ids["pathway_id"] = pathway.id

        atom_id = model.make_id("atom")
        chokepoint_id = model.make_id("chokepoint")
        stdout_path = store.logs_dir / f"{atom_id}.stdout.log"
        stderr_path = store.logs_dir / f"{atom_id}.stderr.log"
        stdout_log = f"{STATE_DIR_NAME}/logs/{stdout_path.name}" if capture else None
        stderr_log = f"{STATE_DIR_NAME}/logs/{stderr_path.name}" if capture else None
        output_capture = model.OutputCapture.FULL if capture else model.OutputCapture.NONE

        started_at = clock()
        attempt = model.Attempt(
            id=atom_id,
            pathway_id=pathway.id,
            chokepoint_id=chokepoint_id,
            started_at=model.format_timestamp(started_at),
            declared_parameters=parameters,
            command=recorded_command,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
            output_capture=output_capture,
            masked_arguments=masked,
            context=run_context,
        )
        ids = {"run_id": atom_id, "branch_point_id": chokepoint_id, "pathway_id": pathway.id}
        storage_warnings: tuple[str, ...] = ()
        with store._recording(attempt), contextlib.ExitStack() as logs:
            stdout_handle: BinaryIO | None = None
            stderr_handle: BinaryIO | None = None
            if capture:
                try:
                    stdout_handle = logs.enter_context(open(stdout_path, "wb"))
                    stderr_handle = logs.enter_context(open(stderr_path, "wb"))
                except OSError as exc:
                    # Nothing was launched, so there is no attempt to leave behind.
                    logs.close()
                    for path in (stdout_path, stderr_path):
                        with contextlib.suppress(OSError):
                            path.unlink(missing_ok=True)
                    store._remove_attempt(attempt.id)
                    detail = exc.strerror or exc.__class__.__name__
                    raise StoreError(
                        f"cannot create log files under {STATE_DIR_NAME}/logs/: {detail}",
                        code=classify_os_error(exc),
                    ) from exc
            started_counter = counter()
            execution = "unknown"
            return_code, launch_category, interrupted = _execute(
                tokens, store, stdout_handle, stderr_handle, mirror
            )
            logs.close()
            duration = max(counter() - started_counter, 0.0)
            finished_at = clock()

            if interrupted:
                outcome = model.Outcome.INTERRUPTED
                launch_category = "interrupted"
                return_code = None
            elif launch_category is not None:
                outcome = model.Outcome.LAUNCH_FAILED
            elif return_code == 0:
                outcome = model.Outcome.PASSED
            else:
                outcome = model.Outcome.FAILED

            stdout_size, stdout_digest = size_and_sha256(stdout_path) if capture else (None, None)
            stderr_size, stderr_digest = size_and_sha256(stderr_path) if capture else (None, None)
            atom = model.Atom(
                id=atom_id,
                pathway_id=pathway.id,
                started_at=attempt.started_at,
                finished_at=model.format_timestamp(finished_at),
                duration_seconds=round(duration, 6),
                outcome=outcome,
                return_code=return_code,
                launch_error_category=launch_category,
                declared_parameters=parameters,
                command=recorded_command,
                stdout_log=stdout_log,
                stderr_log=stderr_log,
                stdout_bytes=stdout_size,
                stderr_bytes=stderr_size,
                stdout_sha256=stdout_digest,
                stderr_sha256=stderr_digest,
                output_capture=output_capture,
                masked_arguments=masked,
                context=run_context,
            )
            store._save_atom(atom)
            chokepoint = model.Chokepoint(
                id=chokepoint_id,
                pathway_id=pathway.id,
                atom_id=atom.id,
                created_at=model.format_timestamp(finished_at),
                kind=model.TERMINAL_KIND,
                outcome=outcome,
                branchable=True,
            )
            store._save_chokepoint(chokepoint)
            execution = "recorded"
            # Only our own finalized streams are considered. The atom is already
            # durable, so recovery never treats compressed output as an absent
            # unfinished log. The attempt lock still excludes manual maintenance.
            if capture and compress:
                try:
                    store._change_atom_storage(
                        atom, min_bytes=compression_min_bytes, eligible_only=True
                    )
                except StoreError as exc:
                    storage_warnings = (f"automatic log compression failed: {exc}",)
            # Last, and while still holding the attempt's lock: a reader then sees
            # either a live attempt or a complete atom and chokepoint.
            store._remove_attempt(attempt.id)
        return RunExecution(atom=atom, chokepoint=chokepoint, storage_warnings=storage_warnings)
    except (StoreError, model.ValidationError, OSError) as exc:
        raise with_execution(exc, execution, ids)  # noqa: B904 - it sets the cause


def _execute(
    tokens: tuple[str, ...],
    store: Store,
    stdout_handle: BinaryIO | None,
    stderr_handle: BinaryIO | None,
    mirror: bool,
) -> tuple[int | None, str | None, bool]:
    """Launch the process, drain its output, and normalize how it ended.

    A missing log handle means that stream is not retained. Returns
    ``(return_code, launch_category, interrupted)``.
    """
    echo_out = getattr(sys.stdout, "buffer", None) if mirror else None
    echo_err = getattr(sys.stderr, "buffer", None) if mirror else None
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "flush"):
            stream.flush()

    tether = supervise.Tether()
    try:
        process = tether.launch(
            list(tokens),
            cwd=str(store.root),
            stdout=subprocess.PIPE if mirror else (stdout_handle or subprocess.DEVNULL),
            stderr=subprocess.PIPE if mirror else (stderr_handle or subprocess.DEVNULL),
            shell=False,
        )
    except (OSError, ValueError) as exc:
        return None, categorize_launch_error(exc), False

    interrupted = False
    finished = False
    return_code: int | None = None
    pumps: list[threading.Thread] = []
    if mirror:
        assert process.stdout is not None and process.stderr is not None
        pumps.append(
            threading.Thread(
                target=_pump, args=(process.stdout, stdout_handle, echo_out), daemon=True
            )
        )
        pumps.append(
            threading.Thread(
                target=_pump, args=(process.stderr, stderr_handle, echo_err), daemon=True
            )
        )
        for thread in pumps:
            thread.start()

    try:
        while not finished:
            try:
                return_code = process.wait(timeout=0.05)
                finished = True
            except subprocess.TimeoutExpired:
                continue
    except KeyboardInterrupt:
        # Stop requests can arrive between wait calls or inside the timeout
        # handler, not only while wait() itself is running.
        interrupted = True
        finished = False
        _stop_process(process)
        return_code = None
    finally:
        # First, so that nothing the command started still holds the output pipes.
        tether.release(command_finished=finished)
        for thread in pumps:
            thread.join(timeout=_STOP_TIMEOUT)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                with contextlib.suppress(OSError, ValueError):
                    stream.close()

    return return_code, None, interrupted


def _stop_process(process: subprocess.Popen) -> None:
    with contextlib.suppress(OSError):
        process.terminate()
    try:
        process.wait(timeout=_STOP_TIMEOUT)
    except Exception:  # noqa: BLE001 - a stuck child must never block evidence
        with contextlib.suppress(OSError):
            process.kill()
        with contextlib.suppress(Exception):
            process.wait(timeout=_STOP_TIMEOUT)


def _pump(source: BinaryIO, sink: BinaryIO | None, echo: BinaryIO | None) -> None:
    """Copy one output stream to its log file and, optionally, the console."""
    try:
        while chunk := source.read(_PUMP_CHUNK):
            if sink is not None:
                try:
                    sink.write(chunk)
                except (OSError, ValueError):
                    break
            if echo is not None:
                with contextlib.suppress(OSError, ValueError):
                    echo.write(chunk)
                    echo.flush()
    finally:
        with contextlib.suppress(OSError, ValueError):
            source.close()
