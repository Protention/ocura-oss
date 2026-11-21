# SPDX-License-Identifier: Apache-2.0

"""A Model Context Protocol server over stdio: tools that read one ledger and annotate its runs.

Each tool runs the CLI command of the same name and returns what that command
prints with ``--json``, so the two cannot drift apart. The server speaks the
2026-07-28 revision, where every request carries its protocol version, and the
``initialize`` handshake of the revisions before it.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import BinaryIO

from ocura_oss import __version__, model

MODERN = "2026-07-28"
LEGACY = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
_META = "io.modelcontextprotocol/"
_INFO = {"name": "ocura-oss", "version": __version__}
_CAPABILITIES: dict = {"tools": {}}
_INSTRUCTIONS = (
    "Access to this project's Ocura OSS ledger of recorded command runs."
    " `runs` lists them, failed and interrupted ones included; `show` describes one;"
    " `logs` reads the end of its output or searches it; `compare` sets a run beside"
    " the runs branched from it. `note` and `tag` keep editable commentary on a run"
    " without changing its record. An ID may be shortened to its first four or more"
    " hexadecimal characters. To record a run, use `ocura-oss run` in a shell."
)
_LOG_LIMIT = 64 * 1024
_TYPES = {"string": str, "integer": int, "boolean": bool, "array": list}
_POSITIONAL = ("run", "text", "tags")  # Arguments the commands take as positionals.


def _tool(
    description: str, properties: dict, *, required: Sequence[str] = (), writes: bool = False
) -> dict:
    return {
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        },
        # A writing tool keeps the default destructiveHint: a note keeps no earlier text.
        "annotations": {"readOnlyHint": not writes, "openWorldHint": False},
    }


def _text(description: str, **more: object) -> dict:
    return {"type": "string", "description": description, **more}


_RUN = _text("Run ID (run-...) or a unique prefix of it.")
_TOOLS = {
    "runs": _tool(
        "List finished runs, oldest first, with outcome, timing, and the branch point ID"
        " of each. Failed and interrupted runs are included. No commands or log contents.",
        {
            "pathway": _text("Only runs on this pathway (pathway-... or a unique prefix)."),
            "outcome": _text("Only this outcome.", enum=[item.value for item in model.Outcome]),
            "tag": _text("Only runs carrying this tag."),
            "after": _text("Continue after this branch point: the last page's next_after."),
            "limit": {"type": "integer", "description": "Page size, 1 to 1000. Default 100."},
        },
    ),
    "show": _tool(
        "Describe one run: its pathway and the reason it was made, parameters, outcome,"
        " timing, log sizes and digests, and the pathways branched from it.",
        {
            "run": _RUN,
            "command": {"type": "boolean", "description": "Also return the arguments."},
        },
        required=["run"],
    ),
    "logs": _tool(
        "Read the end of one run's captured output after verifying it against the"
        " recorded checksum. With search, only lines containing the text are returned,"
        " each after its line number. At most 64 KiB is returned.",
        {
            "run": _RUN,
            "stream": _text("Which output. Default stdout.", enum=["stdout", "stderr"]),
            "tail": {"type": "integer", "description": "Last N lines. Default 100."},
            "search": _text("Only lines containing this exact text."),
        },
        required=["run"],
    ),
    "compare": _tool(
        "Set a source run beside the run on each pathway branched from it: the reason"
        " for each branch, parameter changes, outcomes, and timing.",
        {
            "from": _text(
                "Source branch point (branch-point-... or a unique prefix)."
                " Default: the newest one that has branches."
            )
        },
    ),
    "verify": _tool(
        "Recheck every record and every log a run refers to, and list the problems"
        " found. Reads every log, so it takes time on a large ledger.",
        {},
    ),
    "attempts": _tool("List runs that have started and are not finished.", {}),
    "note": _tool(
        "Replace the text of a finished run's note: commentary kept beside the run, never"
        " part of its record. Empty text removes it; earlier text is not kept.",
        {"run": _RUN, "text": _text("The note's new text.")},
        required=["run", "text"],
        writes=True,
    ),
    "tag": _tool(
        "Add tags to a finished run's note, or remove them. `runs` with tag lists the runs"
        " carrying one.",
        {
            "run": _RUN,
            "tags": {"type": "array", "items": _text("Letters, digits, _ . -"), "minItems": 1},
            "remove": {"type": "boolean", "description": "Remove these tags instead."},
        },
        required=["run", "tags"],
        writes=True,
    ),
}


class _Rejected(Exception):
    """A request the protocol answers with a JSON-RPC error."""

    def __init__(self, code: int, message: str, data: dict | None = None) -> None:
        super().__init__(message)
        self.code, self.data = code, data


class Server:
    """Answer MCP requests for the ledger under one project root."""

    def __init__(
        self, command: Callable[[Sequence[str]], int], root: Path, *, archive: bool = False
    ) -> None:
        self._command, self._root, self._archive = command, root, archive

    def serve(self, source: BinaryIO | None = None, sink: BinaryIO | None = None) -> int:
        """Answer one message per line until *source* ends."""
        source = sys.stdin.buffer if source is None else source
        sink = sys.stdout.buffer if sink is None else sink
        for line in source:
            reply = self.answer(line) if line.strip() else None
            if reply is not None:
                sink.write(json.dumps(reply, separators=(",", ":")).encode("ascii") + b"\n")
                sink.flush()
        return 0

    def answer(self, line: bytes) -> dict | None:
        """Return the reply to one message, or None when it takes none."""
        try:
            message = json.loads(line)
        except (ValueError, RecursionError):
            return {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}}
        if not isinstance(message, dict) or not isinstance(message.get("method"), str):
            error = {"code": -32600, "message": "expected one JSON-RPC request"}
            return {"jsonrpc": "2.0", "error": error}
        if "id" not in message:
            return None  # A notification, such as `notifications/cancelled`.
        reply: dict = {"jsonrpc": "2.0", "id": message["id"]}
        try:
            reply["result"] = self._result(message["method"], message.get("params", {}))
        except _Rejected as exc:
            reply["error"] = {"code": exc.code, "message": str(exc)}
            if exc.data is not None:
                reply["error"]["data"] = exc.data
        except Exception as exc:  # A fault in one call must not end the session.
            reply["error"] = {"code": -32603, "message": f"{type(exc).__name__}: {exc}"}
        return reply

    def _result(self, method: str, params: object) -> dict:
        if not isinstance(params, dict):
            raise _Rejected(-32602, "params must be an object")
        meta = params.get("_meta")
        meta = meta if isinstance(meta, dict) else {}
        # A request that names its protocol version is answered on its own;
        # one that does not belongs to a session opened with `initialize`.
        version = meta.get(f"{_META}protocolVersion")
        modern = version is not None
        versions = [MODERN, *LEGACY]
        if modern and version != MODERN:
            data = {"supported": versions, "requested": str(version)}
            raise _Rejected(-32022, "Unsupported protocol version", data)
        if modern and not isinstance(meta.get(f"{_META}clientCapabilities"), dict):
            raise _Rejected(-32602, f"_meta lacks {_META}clientCapabilities")
        cached = {"ttlMs": 300_000, "cacheScope": "public"} if modern else {}
        result: dict
        if method == "initialize" and not modern:
            asked = params.get("protocolVersion")
            result = {
                "protocolVersion": asked if asked in LEGACY else LEGACY[0],
                "capabilities": _CAPABILITIES,
                "serverInfo": _INFO,
                "instructions": _INSTRUCTIONS,
            }
        elif method == "ping" and not modern:
            result = {}
        elif method == "server/discover" and modern:
            result = {
                "supportedVersions": versions,
                "capabilities": _CAPABILITIES,
                "instructions": _INSTRUCTIONS,
                **cached,
            }
        elif method == "tools/list":
            result = {"tools": [{"name": name, **tool} for name, tool in _TOOLS.items()], **cached}
        elif method == "tools/call":
            result = self._call(params.get("name"), params.get("arguments", {}))
        else:
            raise _Rejected(-32601, f"Method not found: {method}")
        if modern:
            result = {"resultType": "complete", **result, "_meta": {f"{_META}serverInfo": _INFO}}
        return result

    def _call(self, name: object, arguments: object) -> dict:
        tool = _TOOLS.get(name) if isinstance(name, str) else None
        if tool is None:
            raise _Rejected(-32602, f"Unknown tool: {name}")
        if not isinstance(arguments, dict):
            raise _Rejected(-32602, "arguments must be an object")
        problem = _problem(tool["inputSchema"], arguments)
        if problem is not None:
            payload: dict = {"error": {"code": "invalid_input", "message": problem}}
            return _content(json.dumps(payload), failed=True)
        if self._archive and not tool["annotations"]["readOnlyHint"]:
            refused = {"code": "invalid_state", "message": "archive mode does not permit changes"}
            return _content(json.dumps({"error": refused}), failed=True)
        if name == "logs":
            arguments = {"tail": 100, **arguments}
        argv = [str(name), "--json-errors", f"--root={self._root}"]
        if self._archive:
            argv.append("--archive")
        for key, value in arguments.items():
            if key not in _POSITIONAL and value is not False:
                argv.append(f"--{key}" if value is True else f"--{key}={value}")
        given = [arguments[key] for key in ("run", "text") if key in arguments]
        if given:  # After `--` a value is never read as an option.
            argv += ["--", *given, *arguments.get("tags", [])]
        # The command prints to this capture, so stdout carries protocol messages only.
        captured = io.BytesIO()
        printed = io.TextIOWrapper(captured, encoding="utf-8", newline="\n")
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(io.StringIO()):
            status = self._command(argv)
            printed.flush()
        raw = captured.getvalue()
        if name == "logs" and status == 0:
            cut = len(raw) > _LOG_LIMIT
            text = raw[-_LOG_LIMIT:].decode("utf-8", errors="replace")
            return _content(f"[only the last {_LOG_LIMIT} bytes]\n{text}" if cut else text)
        payload = json.loads(raw)
        # `verify` reporting problems has done its work; only a refused call is an error.
        return _content(json.dumps(payload, separators=(",", ":")), failed="error" in payload)


def _problem(schema: dict, arguments: dict) -> str | None:
    """Say why *arguments* do not fit *schema*, or return None when they do."""
    for key in schema["required"]:
        if key not in arguments:
            return f"missing argument: {key}"
    for key, value in arguments.items():
        kind = schema["properties"].get(key, {}).get("type")
        if kind is None:
            return f"unknown argument: {key}"
        if type(value) is not _TYPES[kind]:
            return f"argument {key} must be of type {kind}"
        if isinstance(value, list) and any(type(item) is not str for item in value):
            return f"argument {key} must hold only strings"
    return None


def _content(text: str, *, failed: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": failed}
