# SPDX-License-Identifier: Apache-2.0

"""Stable diagnostic details shared by storage, execution, and CLI callers."""

from __future__ import annotations

import errno
from collections.abc import Mapping
from typing import Literal

from ocura_oss.model import ModelError, ValidationError

ExecutionState = Literal["not_started", "recorded", "unknown", "not_applicable"]


class StoreError(Exception):
    """An operation failed, with optional machine-readable recovery context.

    ``execution`` describes the requested command, not whether ledger files were
    changed. An unknown result must be inspected before the command is retried.
    """

    def __init__(
        self,
        *args: object,
        code: str = "invalid_state",
        execution: ExecutionState = "not_applicable",
        ids: Mapping[str, str] | None = None,
        recovery: str | None = None,
    ) -> None:
        super().__init__(*args)
        self.code = code
        self.execution = execution
        self.ids = dict(ids or {})
        self.recovery = recovery


class _InvalidInput(StoreError, ValidationError):
    """Invalid input that callers can still catch as ``ValidationError``."""


class _StorageFailure(StoreError, OSError):
    """An operating-system failure that callers can still catch as ``OSError``."""


def with_execution(
    error: StoreError | ModelError | OSError,
    execution: ExecutionState,
    ids: Mapping[str, str],
) -> StoreError:
    """Add execution context; other errors are wrapped but keep their base type."""
    if isinstance(error, StoreError):
        wrapped = error
    elif isinstance(error, OSError):
        wrapped = _StorageFailure(*error.args, code=classify_os_error(error))
        if error.filename is not None:
            wrapped.filename = error.filename
    else:
        wrapped = _InvalidInput(*error.args, code="invalid_input")
    if wrapped is not error:
        wrapped.__cause__ = error
    wrapped.execution = execution
    wrapped.ids = {**ids, **wrapped.ids}
    return wrapped


def classify_os_error(error: OSError) -> str:
    """Classify an operating-system failure without inspecting its message."""
    if isinstance(error, PermissionError) or error.errno in (errno.EACCES, errno.EPERM):
        return "permission_denied"
    return "io_error"


def recovery_action(code: str, execution: ExecutionState) -> str:
    """Suggest inspection first whenever the command may already have run."""
    if execution == "unknown":
        return "inspect_attempts_and_runs"
    if execution == "recorded":
        return "inspect_recorded_run"
    return {
        "invalid_input": "correct_input",
        "state_not_found": "initialize_state",
        "record_not_found": "select_record",
        "ambiguous_id": "select_record",
        "unsupported_schema": "use_supported_reader",
        "integrity_error": "verify_state",
        "permission_denied": "check_permissions",
        "io_error": "check_storage",
        "invalid_state": "verify_state",
    }.get(code, "inspect_state")
