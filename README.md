# Ocura OSS 0.7 example catalog

This is the maintained example catalog on **`examples/0.7`**, a long-lived branch
for fuller demonstrations of **Ocura OSS 0.7.0**. Its package source matches the
[0.7.0 release](https://github.com/Protention/ocura-oss/releases/tag/v0.7.0).
[`main`](https://github.com/Protention/ocura-oss/tree/main) retains the primary
`ocura-oss demo` and history walkthrough; this catalog stays on its own branch.
Use the scripts from this checkout with the released package from PyPI.

| Example | What it demonstrates | Additional requirements |
| --- | --- | --- |
| [CI report](examples/ci_report/README.md) | Retain a failing check, a correction, and a later evidence-based handoff | None beyond Python and Ocura |
| [Autoregressive training](examples/autoregressive/README.md) | Compare a baseline with a longer training run and reconstruct the report | PyTorch or JAX; optional Ray |
| [Dream replay](examples/dream_replay/README.md) | Collect training history, replay fixed allocation policies, and run a selected policy | CPU PyTorch for training |
| [Dream RSI](examples/dream_rsi/README.md) | Explore training with agents, replay policies, and retain redeployment evidence | CPU PyTorch for training; signed-in Codex CLI for live agent calls |

Start with [installation](#install-and-try), then follow one guide. The
[use-case guide](docs/use-cases.md) compares the workflows and also links the
[history inspector](examples/history/README.md). Examples own their workloads,
checkpoints, metrics, policies, and agent access; Ocura supplies execution records,
lineage, and verified logs. Historical research evidence remains historical, and
this catalog refresh establishes no new training-quality or compute-savings result.

## Install and try

Use an activated virtual environment. The core package needs Python 3.11 or later;
the pinned Dream training setup uses Python 3.12 and CPU PyTorch.

```console
git clone --branch examples/0.7 --single-branch https://github.com/Protention/ocura-oss.git ocura-oss-examples
cd ocura-oss-examples
git rev-parse HEAD
python -m pip install ocura-oss==0.7.0
ocura-oss demo --root ./ocura-oss-demo
```

The demo destination must be new. Keep the checkout for its example scripts;
they and their optional frameworks are not installed with the package. Install
only the backend needed by the selected guide. To work on the package itself,
`python -m pip install .` also works: its source matches the final 0.7.0 release.

Record the commit printed by `git rev-parse HEAD` with retained results. The branch
can advance; keep the exact revision when sharing or reproducing a run.

## Start with a development handoff

The CI-report example needs no optional framework or agent account. It records
an expected test failure, a declared reason for the correction, and the passing
child run. Its report reconstructs that evidence without running tests again:

```console
python examples/ci_report/workflow.py run --root ./ci-report-run
python examples/ci_report/workflow.py report --root ./ci-report-run
```

Use a new destination. The [guide](examples/ci_report/README.md) explains the
retained source files, logs, run IDs, and limits of the evidence.

## Package interfaces

Ocura OSS is a local execution ledger for recording, branching, and comparing
command runs. It has no runtime dependencies. Version 0.7.0 adds descriptive
Python names, history discovery, verified log reads, structured errors, and the
combined branch-to-run call while preserving existing ledgers and callers.

- [Overview](docs/index.md): the complete CLI and Python workflow
- [Names and compatibility](docs/naming-migration.md): `Ledger`, `RunRecord`, and `BranchPoint`, with legacy aliases
- [Python API](docs/python-api.md): functions, typed results, and `Store`
- [CLI reference](docs/cli.md): commands, JSON fields, and error handling
- [Published documentation](https://protention.dev/): the released package reference

The [0.6.0 example snapshot](https://github.com/Protention/ocura-oss/tree/v0.6.0/examples/autoregressive)
remains available for the previous release.

## Validation and historical results

CI tests this branch's source and built distributions, plus the released PyPI
0.7.0 package with the PyTorch, JAX, and Dream CPU integrations. Live agent calls
and optional Ray execution are not part of CI. The Dream guides include the
opt-in training checks and live-run budgets.

The Dream RSI pilot and curated measurements remain September 2026 results from
Ocura OSS 0.4.0. Updating the adapter and rerunning compatibility checks does not
repeat that pilot or establish a new policy benefit. Each application owns its
checkpoints, source copies, metrics, and replay policy; Ocura supplies execution
records, lineage, and verified logs.

Ocura OSS 0.7.0 is Apache-2.0 licensed; the Dream examples retain their own
[MPL-2.0 license](examples/dream_replay/LICENSE). See [Contributing](CONTRIBUTING.md)
and the [security policy](SECURITY.md) for the core package.
