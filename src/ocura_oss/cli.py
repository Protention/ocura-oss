# SPDX-License-Identifier: Apache-2.0

"""Argparse wiring, exit-code mapping, concise text views, and JSON summaries."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import NoReturn

from ocura_oss import __version__, api, branching, demo, model, runner, store, supervise
from ocura_oss.errors import ExecutionState, StoreError, recovery_action, with_execution
from ocura_oss.model import ModelError

EXIT_OK = 0
EXIT_COMMAND_FAILED = 1
EXIT_INVALID = 2
EXIT_LAUNCH_FAILED = 3


class _ParserError(Exception):
    """An already printed argument error in opt-in structured mode."""


class _ArgumentParser(argparse.ArgumentParser):
    structured_errors = False

    def error(self, message: str) -> NoReturn:
        if not self.structured_errors:
            super().error(message)
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise _ParserError(message)


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    separator = raw.index("--") if "--" in raw else len(raw)
    options = raw[:separator]
    json_errors = "--json-errors" in options
    # Never interpret a child command's options. Accept the opt-in before or
    # after the subcommand without letting a subparser default overwrite it.
    raw = [token for token in options if token != "--json-errors"] + raw[separator:]
    parser = _build_parser(json_errors=json_errors)
    command_tail: list[str] = []
    is_run = bool(raw and raw[0] == "run")
    try:
        if is_run:
            separator = raw.index("--") if "--" in raw else len(raw)
            head = raw[:separator]
            wants_help = "-h" in head or "--help" in head
            if "--" not in raw and not wants_help:
                message = (
                    "put the command after -- so its flags are not"
                    " interpreted by ocura-oss; for example:"
                )
                print(f"error: {message}", file=sys.stderr)
                print("  ocura-oss run -- python script.py", file=sys.stderr)
                if json_errors:
                    _emit_error(StoreError(message, code="invalid_input", execution="not_started"))
                return EXIT_INVALID
            command_tail = raw[separator + 1 :]
            args = parser.parse_args(head)
        else:
            args = parser.parse_args(raw)
    except _ParserError as exc:
        _emit_error(
            StoreError(
                str(exc),
                code="invalid_input",
                execution="not_started" if is_run else "not_applicable",
            )
        )
        return EXIT_INVALID
    json_errors = json_errors or args.json_errors
    # For logs, --json selects metadata instead of the log bytes.
    if json_errors and args.command_name != "logs":
        args.json = True
    retained = False
    try:
        return _dispatch(args, command_tail)
    except (StoreError, ModelError, OSError) as exc:
        error = exc if isinstance(exc, StoreError) else with_execution(exc, "not_applicable", {})
    except RecursionError:
        message = "state lineage is too deeply nested to verify; the state directory may be corrupt"
        error = StoreError(message, code="integrity_error")
    except demo.DemoError as exc:
        error = StoreError(f"demo step '{exc.step}' failed: {exc.message}", execution="unknown")
        retained = True
    print(f"error: {error}", file=sys.stderr)
    if retained:
        print("the demo directory was retained", file=sys.stderr)
    if json_errors:
        _emit_error(error, command=args.command_name)
    return EXIT_INVALID


def _build_parser(*, json_errors: bool = False) -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="ocura-oss",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "A local execution ledger: record a baseline, branch, rerun, and "
            "compare. Use the CLI from your terminal, scripts, or an AI agent. "
            "Commands inherit your execution environment's permissions."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    init_parser = subparsers.add_parser(
        "init",
        help="create a project ledger and a default pathway",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Creates a project ledger and its default line of work (pathway)"
            " under ROOT/.ocura-oss/."
            " Returns exit code 2 if state already exists."
        ),
    )
    init_parser.add_argument(
        "--root",
        default=None,
        metavar="PATH",
        help="project root directory (default: current directory)",
    )
    init_parser.add_argument(
        "--name",
        default="ocura-oss",
        metavar="NAME",
        help="name recorded in the project ledger (default: ocura-oss)",
    )
    init_parser.add_argument(
        "--json", action="store_true", help="print project paths and IDs as JSON"
    )

    run_parser = subparsers.add_parser(
        "run",
        help="run one command and record evidence",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Runs COMMAND directly with shell=False, the project root as working"
            " directory, and captured stdout/stderr logs. Everything after -- is"
            " the command; put run's own options before it.\n"
            "Example: ocura-oss run --pathway ID --param batch=2 -- python script.py --batch 2\n"
            "To create and run a variation in one step, use --from BRANCH_POINT_ID"
            " --reason TEXT instead of --pathway. --param then sets the new"
            " pathway's labels, inherited by its run.\n"
            "Parameters are recorded labels; pass actual inputs to COMMAND. A run"
            " also records its pathway's parameters, so a run on a branch does"
            " not repeat them.\n"
            "While the command runs, its output streams to your terminal and is"
            " recorded under .ocura-oss/logs/. If you press Ctrl+C, or this"
            " process is asked to stop (SIGTERM, SIGHUP, or Ctrl+Break), the"
            " command is stopped and the partial attempt is still recorded as"
            " interrupted evidence; a second"
            " Ctrl+C exits immediately and leaves the attempt unfinished until"
            " `ocura-oss recover` closes it. With --json, command output"
            " stays in the logs and stdout contains one result object.\n"
            "Use --no-capture when output must not be retained, and --mask-arg"
            " for a command token that must not be recorded. With --compress,"
            " captured logs of at least 1 MiB are stored as gzip after finalization"
            " when that saves at least 10%; `ocura-oss logs ID` reads either form.\n"
            "With --substitute, write {KEY} in COMMAND to pass a parameter's value"
            " and record it from one declaration: --param batch=4 --substitute --"
            ' python train.py --batch "{batch}". Quote the placeholder in PowerShell.\n'
            "Exit codes: 0 passed, 1 command failed or was interrupted,"
            " 3 could not launch; each finalized attempt has a branch point."
        ),
    )
    run_parser.add_argument(
        "--root",
        default=None,
        metavar="PATH",
        help="project root directory (default: current directory)",
    )
    run_parser.add_argument(
        "--pathway",
        default=None,
        metavar="ID",
        help=(
            "pathway to attach evidence to, as a full id or a unique prefix"
            " (default: the project's default pathway)"
        ),
    )
    run_parser.add_argument(
        "--from",
        dest="source_branch_point",
        default=None,
        metavar="ID",
        help="create a child from this verified branch point (full ID or unique prefix)",
    )
    run_parser.add_argument(
        "--reason",
        default=None,
        metavar="TEXT",
        help="reason for the new variation; required with --from",
    )
    run_parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "record a parameter label (not passed to COMMAND); replaces the"
            " pathway's value for the same key; with --from, sets the new"
            " pathway's labels; repeatable"
        ),
    )
    run_parser.add_argument(
        "--quiet",
        action="store_true",
        help="do not stream the command's output to the terminal",
    )
    run_parser.add_argument(
        "--json",
        action="store_true",
        help="print one result as JSON; command output is retained in logs without streaming",
    )
    run_parser.add_argument(
        "--no-capture",
        action="store_true",
        help="retain no stdout or stderr; the record states that output was not captured",
    )
    run_parser.add_argument(
        "--compress",
        action="store_true",
        help="store this run's large captured logs as gzip after finalization",
    )
    run_parser.add_argument(
        "--compress-min-bytes",
        type=int,
        default=None,
        metavar="N",
        help="minimum original log size to compress (default: 1048576); implies --compress",
    )
    run_parser.add_argument(
        "--mask-arg",
        action="append",
        default=[],
        type=int,
        metavar="POSITION",
        help=(
            "record a placeholder instead of the COMMAND token at this zero-based"
            " position; COMMAND still receives the real value; repeatable"
        ),
    )
    run_parser.add_argument(
        "--substitute",
        action="store_true",
        help=(
            "replace {KEY} in COMMAND tokens with that parameter's value, from --param"
            " and then the pathway; write {{ or }} for a literal brace"
        ),
    )
    run_parser.add_argument(
        "--context",
        action="store_true",
        help="record the platform and the git revision and dirty state at launch",
    )
    run_parser.add_argument(
        "--context-file",
        action="append",
        default=[],
        metavar="PATH",
        help="record the size and SHA-256 of this file at launch; repeatable",
    )
    run_parser.add_argument(
        "command",
        nargs="*",
        metavar="COMMAND",
        help="command tokens after -- (required); executed verbatim",
    )

    runs_parser = subparsers.add_parser("runs", help="page recorded runs without reading logs")
    runs_parser.add_argument("--pathway", metavar="ID", help="only runs on this pathway")
    runs_parser.add_argument("--outcome", choices=[item.value for item in model.Outcome])
    runs_parser.add_argument(
        "--after", metavar="ID", help="continue after this branch point: a page's next_after"
    )
    page_size = runs_parser.add_mutually_exclusive_group()
    page_size.add_argument(
        "--limit", type=int, default=100, help="page size, 1 to 1000 (default: 100)"
    )
    page_size.add_argument("--all", action="store_true", help="list every match")
    runs_parser.add_argument("--root", default=None, metavar="PATH", help="project root directory")
    runs_parser.add_argument(
        "--json", action="store_true", help="print a JSON page and next_after ID"
    )

    show_parser = subparsers.add_parser(
        "show", help="inspect a run, its reason and log identities without reading logs"
    )
    show_parser.add_argument("atom", metavar="ID", help="run ID (atom-...) or unique prefix")
    show_parser.add_argument(
        "--command", action="store_true", help="also include recorded arguments"
    )
    show_parser.add_argument("--root", default=None, metavar="PATH", help="project root directory")
    show_parser.add_argument("--json", action="store_true", help="print machine-readable JSON")

    pathways_parser = subparsers.add_parser(
        "pathways",
        help="list pathways oldest first",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Lists each line of work (pathway): parent, source branch point, creation"
            " time, reason, declared parameters, and whether terminal evidence"
            " is attached."
        ),
    )
    pathways_parser.add_argument(
        "--root",
        default=None,
        metavar="PATH",
        help="project root directory (default: current directory)",
    )
    pathways_parser.add_argument(
        "--json", action="store_true", help="print machine-readable JSON instead of text"
    )

    chokepoints_parser = subparsers.add_parser(
        "branch-points",
        aliases=["chokepoints"],
        help="list branch points newest first (legacy command: chokepoints)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Lists terminal evidence that can identify the source of a variation."
            " Existing chokepoint-... IDs remain valid; a branch point does not"
            " restore files, processes, or model state. Never includes commands or log contents."
        ),
    )
    chokepoints_parser.add_argument(
        "--root",
        default=None,
        metavar="PATH",
        help="project root directory (default: current directory)",
    )
    chokepoints_parser.add_argument(
        "--json", action="store_true", help="print machine-readable JSON instead of text"
    )

    branch_parser = subparsers.add_parser(
        "branch",
        help="create a variation from a branch point",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Records a child pathway whose effective parameters are the parent's"
            " plus your overrides. Metadata only: no workspace, artifact, or"
            " process is copied or rewound. Returns exit code 2 for missing,"
            " checksum-mismatched, nonterminal, nonbranchable, or unverifiable sources.\n"
            "An id may be shortened to a unique prefix of at least 4 hexadecimal"
            " characters, such as chokepoint-447c or 447c."
        ),
    )
    branch_parser.add_argument(
        "--from",
        dest="source",
        required=True,
        metavar="BRANCH_POINT_ID",
        help=(
            "source branch point: an existing chokepoint-... ID or unique prefix"
            " (see branch-points)"
        ),
    )
    branch_parser.add_argument(
        "--reason", required=True, metavar="TEXT", help="why this branch exists (nonblank)"
    )
    branch_parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="parameter override added to the parent's effective parameters; repeatable",
    )
    branch_parser.add_argument(
        "--root",
        default=None,
        metavar="PATH",
        help="project root containing the source branch point (default: current directory)",
    )
    branch_parser.add_argument(
        "--json", action="store_true", help="print machine-readable JSON instead of text"
    )

    compare_parser = subparsers.add_parser(
        "compare",
        help="compare a source run with its child runs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Compares a branch point's source run with every child pathway"
            " created from it. State is ready when every child has terminal"
            " evidence, partial when at least one child lacks evidence, and"
            " no_branch when there are no children. Returns exit code 2 if"
            " required records or logs fail verification."
        ),
    )
    compare_parser.add_argument(
        "--root",
        default=None,
        metavar="PATH",
        help="project root directory (default: current directory)",
    )
    compare_parser.add_argument(
        "--from",
        dest="source",
        default=None,
        metavar="BRANCH_POINT_ID",
        help=(
            "source branch point, as a chokepoint-... ID or unique prefix (default: newest"
            " source with child pathways, or newest terminal branch point when no"
            " branches exist)"
        ),
    )
    compare_parser.add_argument(
        "--json", action="store_true", help="print machine-readable JSON instead of text"
    )

    verify_parser = subparsers.add_parser(
        "verify",
        help="re-check every state record and every log referenced by a recorded run",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Validates record envelopes, checksums, references, and semantic"
            " consistency; verifies each referenced log's size and SHA-256;"
            " reports orphaned files under .ocura-oss/logs/, runs without a"
            " branch point, and abandoned attempts. Attempts another process is"
            " still recording are listed and are not problems. Exits 0 when"
            " intact and 2 with an explicit problem list otherwise."
        ),
    )
    verify_parser.add_argument(
        "--root",
        default=None,
        metavar="PATH",
        help="project root directory (default: current directory)",
    )
    verify_parser.add_argument(
        "--json", action="store_true", help="print a JSON report including the problems list"
    )
    verify_parser.add_argument(
        "--against",
        default=None,
        metavar="FILE",
        help=(
            "manifest retained from an earlier `ocura-oss manifest`; every record it"
            " lists must still be present and unchanged"
        ),
    )

    manifest_parser = subparsers.add_parser(
        "manifest",
        help="print one checksum line per verified record",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Verifies the complete state, then prints one `KIND ID CHECKSUM` line"
            " for every record. Keep the output somewhere this state's writers"
            " cannot change, and pass it to `verify --against` later to detect"
            " records that were rewritten together with their checksums. Exits 2"
            " without printing a manifest when the state does not verify."
        ),
    )
    manifest_parser.add_argument(
        "--root",
        default=None,
        metavar="PATH",
        help="project root directory (default: current directory)",
    )
    manifest_parser.add_argument(
        "--json", action="store_true", help="print the digest and entries as JSON"
    )

    attempts_parser = subparsers.add_parser(
        "attempts",
        help="list unfinished attempts oldest first",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Lists attempts that have started and are not yet finalized. An"
            " attempt is running while a live process is recording it and"
            " abandoned once that process has stopped. Never includes commands"
            " or log contents."
        ),
    )
    attempts_parser.add_argument(
        "--root",
        default=None,
        metavar="PATH",
        help="project root directory (default: current directory)",
    )
    attempts_parser.add_argument(
        "--json", action="store_true", help="print machine-readable JSON instead of text"
    )

    recover_parser = subparsers.add_parser(
        "recover",
        help="close attempts whose recording process stopped",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Closes every abandoned attempt. One with no recorded outcome"
            " becomes an abandoned run with a branch point: its captured"
            " output is retained, and its outcome, finish time, and duration"
            " stay unknown. Running attempts are left alone. Run this only"
            " after the abandoned command itself has stopped."
        ),
    )
    recover_parser.add_argument(
        "--root",
        default=None,
        metavar="PATH",
        help="project root directory (default: current directory)",
    )
    recover_parser.add_argument(
        "--json", action="store_true", help="print machine-readable JSON instead of text"
    )

    logs_parser = subparsers.add_parser(
        "logs",
        help="read verified original log bytes or inspect storage metadata",
        description=(
            "Writes the selected log's verified original bytes to stdout, transparently"
            " decoding compressed storage. --info or --json emits metadata instead."
            " The run ID (atom-...) may be a unique prefix of at least four hexadecimal characters."
        ),
    )
    logs_parser.add_argument("atom", metavar="ID", help="run ID (atom-...) or unique prefix")
    logs_parser.add_argument(
        "--stream", choices=("stdout", "stderr"), default="stdout", help="log stream"
    )
    logs_parser.add_argument(
        "--info", action="store_true", help="print verified metadata instead of log contents"
    )
    logs_parser.add_argument(
        "--json", action="store_true", help="print verified metadata as JSON; implies --info"
    )
    logs_parser.add_argument("--root", default=None, metavar="PATH", help="project root directory")

    logs_parser.add_argument(
        "--offset", type=int, default=0, metavar="N", help="start at original byte N"
    )
    logs_parser.add_argument(
        "--length", type=int, metavar="N", help="return at most N original bytes"
    )

    for name, verb in (("compress", "compress"), ("decompress", "restore")):
        storage_parser = subparsers.add_parser(
            name,
            help=f"{verb} finalized captured logs without changing recorded evidence",
            description=(
                "Changes only physical log storage after verifying the original bytes."
                " Defaults to all finalized runs; --run accepts a full ID or unique prefix."
                " The legacy --atom selector remains supported."
                " --dry-run previews changes without replacing files."
            ),
        )
        storage_parser.add_argument(
            "--run",
            dest="run_id",
            default=None,
            metavar="ID",
            help="only this run ID (atom-...) or unique prefix",
        )
        storage_parser.add_argument(
            "--atom", default=None, metavar="ID", help="legacy selector for --run"
        )
        if name == "compress":
            storage_parser.add_argument(
                "--min-bytes",
                type=int,
                default=1_048_576,
                metavar="N",
                help="minimum original log size to compress (default: 1048576)",
            )
        storage_parser.add_argument(
            "--dry-run", action="store_true", help="preview storage changes without writing"
        )
        storage_parser.add_argument(
            "--json", action="store_true", help="print storage changes as JSON"
        )
        storage_parser.add_argument(
            "--root", default=None, metavar="PATH", help="project root directory"
        )

    demo_parser = subparsers.add_parser(
        "demo",
        help="run the complete workflow in a new retained directory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Performs init, a baseline run, a branch point, an explained"
            " branch, an explicit rerun, full verification, and a ready"
            " comparison. Creates and retains DESTINATION, which must not exist;"
            " on any failure the directory is kept and the failing step is named."
        ),
    )
    demo_parser.add_argument(
        "--root",
        required=True,
        metavar="PATH",
        help="destination directory, which must not exist",
    )
    demo_parser.add_argument(
        "--json", action="store_true", help="print a JSON report of steps and comparison"
    )

    for read_parser in (
        pathways_parser,
        chokepoints_parser,
        compare_parser,
        verify_parser,
        manifest_parser,
        attempts_parser,
        logs_parser,
        runs_parser,
        show_parser,
    ):
        read_parser.add_argument(
            "--archive",
            action="store_true",
            help="read a stable archive without ledger writes or writer coordination",
        )

    for command_parser in (parser, *dict.fromkeys(subparsers.choices.values())):
        command_parser.add_argument(
            "--json-errors",
            action="store_true",
            help="emit JSON results and structured errors; preserve human diagnostics on stderr",
        )
        if isinstance(command_parser, _ArgumentParser):
            command_parser.structured_errors = json_errors
    return parser


def _dispatch(args: argparse.Namespace, command_tail: list[str]) -> int:
    name = args.command_name
    if name == "init":
        return _init(args)
    if name == "run":
        return _run(args, command_tail)
    if name == "runs":
        return _runs(args)
    if name == "show":
        return _show(args)
    if name == "pathways":
        return _pathways(args)
    if name in ("branch-points", "chokepoints"):
        return _chokepoints(args)
    if name == "branch":
        return _branch(args)
    if name == "compare":
        return _compare(args)
    if name == "verify":
        return _verify(args)
    if name == "manifest":
        return _manifest(args)
    if name == "attempts":
        return _attempts(args)
    if name == "recover":
        return _recover(args)
    if name == "logs":
        return _logs(args)
    if name in ("compress", "decompress"):
        return _change_log_storage(args)
    if name == "demo":
        return _demo(args)
    raise StoreError(f"unknown command: {name}", code="invalid_input")


def _open_store(
    root_value: str | None, *, require_state: bool = True, archive: bool = False
) -> store.Store:
    root = store.resolve_root(root_value)
    state = store.Store(root, archive=archive)
    if require_state:
        state.require()
    return state


def _collect_parameters(tokens: Sequence[str]) -> dict:
    parameters: dict[str, str] = {}
    for token in tokens:
        key, value = model.parse_parameter(token)
        if key in parameters:
            raise model.ValidationError(f"duplicate parameter declaration: {key}")
        parameters[key] = value
    return parameters


def _emit_json(payload: dict) -> None:
    # Escaped Unicode round-trips through JSON even on legacy Windows stdout encodings.
    print(json.dumps(payload, ensure_ascii=True, indent=2))


def _emit_error(error: StoreError, *, command: str | None = None) -> None:
    execution: ExecutionState = error.execution
    if command == "run" and execution == "not_applicable":
        # Only an execution boundary can establish that launch never happened.
        execution = "unknown"
    _emit_json(
        {
            "error": {
                "code": error.code,
                "message": str(error),
                "execution": execution,
                "ids": error.ids,
                "recovery": error.recovery or recovery_action(error.code, execution),
            }
        }
    )


def _init(args: argparse.Namespace) -> int:
    state = _open_store(args.root, require_state=False)
    den, pathway = state.initialize_state(name=args.name)
    if args.json:
        _emit_json(
            {
                "root": str(state.root),
                "state_dir": str(state.state_dir),
                "den_id": den.id,
                "default_pathway_id": pathway.id,
            }
        )
        return EXIT_OK
    print(f"project ledger: {den.id}")
    print(f"default pathway: {pathway.id}")
    print(f"state: {state.state_dir}")
    return EXIT_OK


def _run(args: argparse.Namespace, command_tail: Sequence[str]) -> int:
    try:
        if not command_tail:
            raise model.ValidationError("run requires at least one command token after --")
        if args.command:
            raise model.ValidationError("unexpected tokens before --; put the command after --")
        state = _open_store(args.root)
        declared = _collect_parameters(args.param)
        source_id = None
        if args.source_branch_point is not None:
            if args.pathway is not None:
                raise model.ValidationError("--from conflicts with --pathway")
            if args.reason is None or not args.reason.strip():
                raise model.ValidationError("--from requires a nonempty --reason")
            source_id = state.resolve_id(args.source_branch_point, "chokepoint")
            pathway_id = None
        elif args.reason is not None:
            raise model.ValidationError("--reason requires --from")
        elif args.pathway:
            pathway_id = state.resolve_id(args.pathway, "pathway")
        else:
            pathway_id = state.load_den().default_pathway_id
        if pathway_id is not None:
            state.load_pathway(pathway_id)
    except (StoreError, ModelError, OSError) as exc:
        raise with_execution(exc, "not_started", {})  # noqa: B904 - it sets the cause
    mirror = not (args.quiet or args.json)
    if not args.json:
        print(f"recording under {state.state_dir}")
    with supervise.stop_requests_interrupt():
        execution = api.run(
            list(command_tail),
            root=state.root,
            pathway_id=pathway_id,
            source_branch_point_id=source_id,
            reason=args.reason,
            parameters=declared,
            mirror=mirror,
            capture=not args.no_capture,
            compress=args.compress or args.compress_min_bytes is not None,
            compression_min_bytes=(
                1_048_576 if args.compress_min_bytes is None else args.compress_min_bytes
            ),
            masked_arguments=args.mask_arg,
            substitute=args.substitute,
            context=args.context,
            context_files=args.context_file,
        )
    atom = execution.atom
    files = state.log_files(atom)
    for message in execution.storage_warnings:
        print(f"warning: {message}", file=sys.stderr)
    if args.json:
        payload: dict[str, object] = {
            "atom_id": atom.id,
            "chokepoint_id": execution.chokepoint.id,
            "pathway_id": atom.pathway_id,
            "outcome": atom.outcome.value,
            "duration_seconds": atom.duration_seconds,
            "return_code": atom.return_code,
            "launch_error_category": atom.launch_error_category,
            "output_capture": atom.output_capture.value,
            "stdout_log": atom.stdout_log,
            "stderr_log": atom.stderr_log,
            "log_files": files,
        }
        if execution.storage_warnings:
            payload["storage_warnings"] = list(execution.storage_warnings)
        _emit_json(payload)
    else:
        _print_run(execution, files)
    if atom.outcome is model.Outcome.LAUNCH_FAILED:
        return EXIT_LAUNCH_FAILED
    if atom.outcome in (model.Outcome.FAILED, model.Outcome.INTERRUPTED):
        return EXIT_COMMAND_FAILED
    return EXIT_OK


def _print_run(execution: runner.RunExecution, files: dict[str, str]) -> None:
    atom = execution.atom
    print(f"run: {atom.id}")
    print(f"branch point: {execution.chokepoint.id}")
    print(f"pathway: {atom.pathway_id}")
    print(f"outcome: {atom.outcome.value}")
    print(f"duration: {atom.duration_seconds:.6f}s")
    if atom.output_capture is model.OutputCapture.FULL:
        print(f"stdout log: {files['stdout']}")
        print(f"stderr log: {files['stderr']}")
        print(f"read: ocura-oss logs {atom.id}")
    else:
        print("output: not captured")
    if atom.return_code is not None:
        print(f"return code: {atom.return_code}")
    if atom.launch_error_category is not None:
        if atom.launch_error_category == "interrupted":
            print("interrupted: partial output retained in the logs above")
        else:
            print(f"launch error: {atom.launch_error_category}")


def _pathway_summaries(state: store.Store) -> list[model.PathwaySummary]:
    summaries = []
    recorded_pathways = {atom.pathway_id for atom in state.list_atoms()}
    for pathway in state.list_pathways():
        summaries.append(
            model.PathwaySummary(
                id=pathway.id,
                parent_pathway_id=pathway.parent_pathway_id,
                source_chokepoint_id=pathway.source_chokepoint_id,
                created_at=pathway.created_at,
                reason=pathway.reason,
                parameters=pathway.parameters,
                has_terminal_evidence=pathway.id in recorded_pathways,
            )
        )
    return summaries


def _pathways(args: argparse.Namespace) -> int:
    state = _open_store(args.root, archive=args.archive)
    summaries = _pathway_summaries(state)
    if args.json:
        _emit_json({"pathways": [item.to_dict() for item in summaries]})
        return EXIT_OK
    for item in summaries:
        parent = item.parent_pathway_id or "-"
        source = item.source_chokepoint_id or "-"
        evidence = "yes" if item.has_terminal_evidence else "no"
        parameter_text = ", ".join(
            f"{key}={value}" for key, value in sorted(item.parameters.items())
        )
        print(f"pathway {item.id}")
        print(f"  parent: {parent}")
        print(f"  source: {source}")
        print(f"  created: {item.created_at}")
        print(f"  reason: {item.reason}")
        print(f"  parameters: {parameter_text or '(none)'}")
        print(f"  terminal evidence: {evidence}")
    return EXIT_OK


def _chokepoints(args: argparse.Namespace) -> int:
    state = _open_store(args.root, archive=args.archive)
    summaries = [
        model.ChokepointSummary(
            id=item.id,
            pathway_id=item.pathway_id,
            atom_id=item.atom_id,
            outcome=item.outcome,
            created_at=item.created_at,
            branchable=item.branchable,
        )
        for item in state.list_chokepoints()
    ]
    if args.json:
        _emit_json({"chokepoints": [item.to_dict() for item in summaries]})
        return EXIT_OK
    run_label = "atom" if args.command_name == "chokepoints" else "run"
    for item in summaries:
        branchable = "yes" if item.branchable else "no"
        print(
            f"{item.created_at}  {item.id}  pathway={item.pathway_id}"
            f"  {run_label}={item.atom_id}  outcome={item.outcome.value}"
            f"  branchable={branchable}"
        )
    return EXIT_OK


def _runs(args: argparse.Namespace) -> int:
    state = _open_store(args.root, archive=args.archive)
    page = state.list_runs(
        pathway_id=None if args.pathway is None else state.resolve_id(args.pathway, "pathway"),
        outcome=None if args.outcome is None else model.Outcome(args.outcome),
        after=None if args.after is None else state.resolve_id(args.after, "chokepoint"),
        limit=None if args.all else args.limit,
    )
    if args.json:
        _emit_json(page.to_dict())
    else:
        for item in page.runs:
            run = item.run
            print(
                f"{run.started_at}  {run.id}  branch_point={item.chokepoint_id}"
                f"  pathway={run.pathway_id}  {_run_phrase(run)}"
            )
        if page.next_after is not None:
            print(f"next after: {page.next_after}")
        if page.unfinished_attempts:
            print(f"unfinished attempts: {page.unfinished_attempts} (may finish behind this page)")
        print("verification: records checked; logs not checked")
    return EXIT_OK


def _show(args: argparse.Namespace) -> int:
    state = _open_store(args.root, archive=args.archive)
    inspected = state.inspect_run(state.resolve_id(args.atom, "atom"))
    files = state.log_files(inspected.atom)
    if args.json:
        _emit_json({**inspected.to_dict(include_command=args.command), "log_files": files})
        return EXIT_OK
    atom, pathway = inspected.atom, inspected.pathway
    print(f"run: {atom.id}")
    print(f"result: {_run_phrase(model.run_summary_from_atom(atom))}")
    print(f"pathway: {pathway.id}")
    print(f"reason: {pathway.reason}")
    print(f"source branch point: {pathway.source_chokepoint_id or '-'}")
    chokepoint = inspected.chokepoint
    print(f"branch point: {'- (unfinished)' if chokepoint is None else chokepoint.id}")
    for child in inspected.children:
        print(f"child pathway: {child.id}  reason: {child.reason}")
    print(f"parameters: {json.dumps(dict(atom.declared_parameters), ensure_ascii=True)}")
    for stream in ("stdout", "stderr"):
        print(f"{stream}: {files.get(stream, 'not captured')}")
        if atom.output_capture is model.OutputCapture.FULL:
            print(f"  bytes: {getattr(atom, f'{stream}_bytes')}")
            print(f"  sha256: {getattr(atom, f'{stream}_sha256')}")
    if args.command:
        print(f"command: {json.dumps(list(atom.command), ensure_ascii=True)}")
    print("verification: record and lineage checked; logs not checked")
    return EXIT_OK


def _branch(args: argparse.Namespace) -> int:
    overrides = _collect_parameters(args.param)
    state = _open_store(args.root)
    chokepoint, _atom, pathway = branching.load_verified_source(
        state, state.resolve_id(args.source, "chokepoint")
    )
    child = branching.create_child_pathway(
        state,
        source_chokepoint=chokepoint,
        source_pathway=pathway,
        reason=args.reason,
        overrides=overrides,
    )
    summary = model.PathwaySummary(
        id=child.id,
        parent_pathway_id=child.parent_pathway_id,
        source_chokepoint_id=child.source_chokepoint_id,
        created_at=child.created_at,
        reason=child.reason,
        parameters=child.parameters,
        has_terminal_evidence=False,
    )
    if args.json:
        _emit_json(summary.to_dict())
        return EXIT_OK
    print(f"pathway: {child.id}")
    print(f"parent: {child.parent_pathway_id}")
    print(f"source branch point: {child.source_chokepoint_id}")
    return EXIT_OK


def _compare(args: argparse.Namespace) -> int:
    state = _open_store(args.root, archive=args.archive)
    source = None if args.source is None else state.resolve_id(args.source, "chokepoint")
    result = branching.compare(state, source)
    if args.json:
        _emit_json(result.to_dict())
        return EXIT_OK
    print(f"comparison: {result.state.value}")
    print(f"source branch point: {result.source_chokepoint_id}")
    print(f"source pathway: {result.source_pathway_id}")
    print(f"source run: {_run_phrase(result.source_run)}")
    for child in result.children:
        print(f"child pathway: {child.pathway_id}")
        print(f"  reason: {child.reason}")
        print(f"  parameters: {child.parameters.phrase()}")
        print(f"  source run: {_run_phrase(child.source_run)}")
        print(f"  child run: {_run_phrase(child.child_run)}")
        if child.run_parameters is not None:
            print(f"  run parameters: {child.run_parameters.phrase()}")
    return EXIT_OK


def _run_phrase(summary: model.RunSummary | None) -> str:
    if summary is None:
        return "missing evidence"
    if summary.duration_seconds is None:
        return f"{summary.outcome.value} (duration unknown)"
    return f"{summary.outcome.value} {summary.duration_seconds:.6f}s"


def _verify(args: argparse.Namespace) -> int:
    state = _open_store(args.root, archive=args.archive)
    retained = None if args.against is None else _read_manifest(args.against)
    report = state.verify_state(against=retained)
    den = state.load_den()
    status = "ok" if report.ok else "failed"
    if args.json:
        _emit_json(
            {
                "den": den.id,
                "status": status,
                "digest": report.digest,
                "counts": {
                    "pathways": report.pathways,
                    "atoms": report.atoms,
                    "chokepoints": report.chokepoints,
                    "logs_checked": report.logs_checked,
                },
                "attempts": {
                    "running": list(report.running_attempts),
                    "abandoned": list(report.abandoned_attempts),
                },
                "problems": [
                    {"record": record, "problem": problem} for record, problem in report.problems
                ],
            }
        )
        return EXIT_OK if report.ok else EXIT_INVALID
    print(f"project ledger: {den.id}")
    print(
        f"verified: {report.pathways} pathways, {report.atoms} runs,"
        f" {report.chokepoints} branch points, {report.logs_checked} logs"
    )
    if report.running_attempts:
        print(f"running attempts: {len(report.running_attempts)}")
    for record, problem in report.problems:
        print(f"problem: {record}: {problem}")
    if report.digest is not None:
        print(f"digest: {report.digest}")
    print(f"integrity: {status}")
    return EXIT_OK if report.ok else EXIT_INVALID


def _read_manifest(location: str) -> list[str]:
    raw = Path(location).read_bytes()
    # Windows PowerShell writes redirected output as UTF-16, or as UTF-8 with a mark.
    encoding = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    try:
        return raw.decode(encoding).splitlines()
    except UnicodeDecodeError as exc:
        raise StoreError(
            f"manifest {location} is not UTF-8 or UTF-16 text", code="invalid_input"
        ) from exc


def _manifest(args: argparse.Namespace) -> int:
    state = _open_store(args.root, archive=args.archive)
    report = state.verify_state()
    if not report.ok:
        record, problem = report.problems[0]
        raise StoreError(
            f"a manifest requires a fully verified state ({record}: {problem})",
            code="integrity_error",
        )
    if args.json:
        _emit_json({"digest": report.digest, "entries": list(report.manifest)})
        return EXIT_OK
    for entry in report.manifest:
        print(entry)
    return EXIT_OK


def _attempts(args: argparse.Namespace) -> int:
    state = _open_store(args.root, archive=args.archive)
    attempts = state.list_attempts()
    if args.json:
        _emit_json(
            {
                "attempts": [
                    {
                        "id": attempt.id,
                        "pathway_id": attempt.pathway_id,
                        "started_at": attempt.started_at,
                        "state": attempt_state.value,
                    }
                    for attempt, attempt_state in attempts
                ]
            }
        )
        return EXIT_OK
    for attempt, attempt_state in attempts:
        print(
            f"{attempt.started_at}  {attempt.id}  pathway={attempt.pathway_id}"
            f"  state={attempt_state.value}"
        )
    return EXIT_OK


def _recover(args: argparse.Namespace) -> int:
    state = _open_store(args.root)
    recovered = state.recover()
    if args.json:
        _emit_json(
            {
                "recovered": [
                    {
                        "atom_id": item.atom_id,
                        "chokepoint_id": item.chokepoint_id,
                        "pathway_id": item.pathway_id,
                        "action": item.action.value,
                    }
                    for item in recovered
                ]
            }
        )
        return EXIT_OK
    for item in recovered:
        print(
            f"{item.action.value}: run {item.atom_id}  branch point {item.chokepoint_id}"
            f"  pathway={item.pathway_id}"
        )
    print(f"recovered: {len(recovered)}")
    return EXIT_OK


def _demo(args: argparse.Namespace) -> int:
    report = demo.run_demo(Path(args.root))
    if args.json:
        _emit_json(report.to_dict())
        return EXIT_OK
    print(f"demo completed: {report.root}")
    for step in report.steps:
        label = "branch-point" if step.step == "chokepoint" else step.step
        print(f"  {label}: {step.detail}")
    print(f"comparison: {report.comparison.state.value}")
    print("retained state under .ocura-oss/")
    return EXIT_OK


def _logs(args: argparse.Namespace) -> int:
    if (args.info or args.json) and (args.offset or args.length is not None):
        raise StoreError(
            "log byte ranges cannot be combined with --info or --json", code="invalid_input"
        )
    state = _open_store(args.root, archive=args.archive)
    atom_id = state.resolve_id(args.atom, "atom")
    if args.info or args.json:
        info = state.log_info(atom_id, stream=args.stream)
        if args.json:
            _emit_json(asdict(info))
            return EXIT_OK
        print(f"fingerprint: {info.fingerprint}")
        print(f"run: {info.atom_id}")
        print(f"stream: {info.stream}")
        print(f"outcome: {info.outcome}")
        print(f"started: {info.started_at}")
        print(f"encoding: {info.encoding}")
        print(f"original bytes: {info.original_bytes}")
        print(f"stored bytes: {info.stored_bytes}")
        print(f"sha256: {info.sha256}")
        print(f"storage path: {info.path}")
        return EXIT_OK
    output = getattr(sys.stdout, "buffer", None)
    if output is None:
        raise StoreError(
            "reading log bytes requires binary stdout; use --info or --json", code="invalid_input"
        )
    with state.open_verified_log(
        atom_id, stream=args.stream, offset=args.offset, length=args.length
    ) as source:
        while chunk := source.read(1024 * 1024):
            output.write(chunk)
    output.flush()
    return EXIT_OK


def _change_log_storage(args: argparse.Namespace) -> int:
    state = _open_store(args.root)
    run_id = None if args.run_id is None else state.resolve_id(args.run_id, "atom")
    atom_id = None if args.atom is None else state.resolve_id(args.atom, "atom")
    if run_id is not None and atom_id is not None and run_id != atom_id:
        raise StoreError("--run and --atom must identify the same run", code="invalid_input")
    atom_id = run_id if run_id is not None else atom_id
    if args.command_name == "compress":
        changes = state.compress_logs(
            atom_id=atom_id, min_bytes=args.min_bytes, dry_run=args.dry_run
        )
    else:
        changes = state.decompress_logs(atom_id=atom_id, dry_run=args.dry_run)
    if args.json:
        _emit_json({"dry_run": args.dry_run, "changes": [asdict(item) for item in changes]})
        return EXIT_OK
    if args.dry_run:
        print("dry run: no storage changes written")
    for item in changes:
        print(
            f"{item.action}: run {item.atom_id}  stream={item.stream}"
            f"  original={item.original_bytes}  stored={item.stored_bytes}  path={item.path}"
        )
    print(f"logs considered: {len(changes)}")
    return EXIT_OK
