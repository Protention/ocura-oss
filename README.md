# Ocura OSS 0.8 example catalog

This is the example catalog on **`examples/0.8`**, a long-lived branch
for fuller demonstrations of **Ocura OSS 0.8.0**. It includes the package source
from the [0.8.0 release](https://github.com/Protention/ocura-oss/releases/tag/v0.8.0).
[`main`](https://github.com/Protention/ocura-oss/tree/main) retains the primary
`ocura-oss demo` and history walkthrough; this catalog stays on its own branch.
Install the matching release from PyPI as shown below.

| Example | What it demonstrates | Additional requirements |
| --- | --- | --- |
| [CI report](https://github.com/Protention/ocura-oss/blob/examples/0.8/examples/ci_report/README.md) | Retain a failing check, a correction, and a later evidence-based handoff | None beyond Python and Ocura |
| [Autoregressive training](https://github.com/Protention/ocura-oss/blob/examples/0.8/examples/autoregressive/README.md) | Compare a baseline with a longer training run and reconstruct the report | PyTorch or JAX; optional Ray |
| [Dream replay](https://github.com/Protention/ocura-oss/blob/examples/0.8/examples/dream_replay/README.md) | Collect training history, replay fixed allocation policies, and run a selected policy | CPU PyTorch for training |
| [Dream RSI](https://github.com/Protention/ocura-oss/blob/examples/0.8/examples/dream_rsi/README.md) | Explore training with agents, replay policies, and retain redeployment evidence | CPU PyTorch for training; signed-in Codex CLI for live agent calls |

Start with [installation](#install-and-try), then follow one guide. The
[use-case guide](https://github.com/Protention/ocura-oss/blob/examples/0.8/docs/use-cases.md) compares the workflows and also links the
[history inspector](https://github.com/Protention/ocura-oss/blob/examples/0.8/examples/history/README.md). Examples own their workloads,
checkpoints, metrics, policies, and agent access; Ocura supplies execution records,
lineage, and verified logs. Historical research evidence remains historical, and
this catalog refresh establishes no new training-quality or compute-savings result.

## Install and try

Use an activated virtual environment. The core package needs Python 3.11 or later;
the pinned Dream training setup uses Python 3.12 and CPU PyTorch.

```console
git clone --branch examples/0.8 --single-branch https://github.com/Protention/ocura-oss.git ocura-oss-examples
cd ocura-oss-examples
git rev-parse HEAD
python -m pip install ocura-oss==0.8.0
ocura-oss demo --root ./ocura-oss-demo
```

The demo destination must be new. Keep the checkout for its example scripts;
they and their optional frameworks are not installed with the package. Install
only the backend needed by the selected guide. To work on the package itself,
`python -m pip install .` also works, or build and install this checkout's wheel:

```console
python -m pip wheel --no-deps --wheel-dir dist .
python -m pip install --no-index --no-deps dist/ocura_oss-0.8.0-py3-none-any.whl
```

The wheel build may install build-time requirements; the resulting package has
no runtime dependencies.

Record the commit printed by `git rev-parse HEAD` with retained results. The branch
can advance; keep the exact revision when sharing or reproducing a run.

Use a new ledger for 0.8. It does not read or convert ledgers from 0.7 or earlier.
Keep the matching older package and example checkout when inspecting older work;
see the [upgrade notes](https://github.com/Protention/ocura-oss/blob/examples/0.8/docs/state-and-verification.md#upgrading-from-earlier-versions).

## Start with a development handoff

The CI-report example needs no optional framework or agent account. It records
an expected test failure, a declared reason for the correction, and the passing
child run. Its report reconstructs that evidence without running tests again:

```console
python examples/ci_report/workflow.py run --root ./ci-report-run
python examples/ci_report/workflow.py report --root ./ci-report-run
```

Use a new destination. The [guide](https://github.com/Protention/ocura-oss/blob/examples/0.8/examples/ci_report/README.md) explains the
retained source files, logs, run IDs, and limits of the evidence.

## Package interfaces

Ocura OSS is a local execution ledger for recording, branching, and comparing
command runs. It has no runtime dependencies. Version 0.8 uses `Ledger`,
`RunRecord`, and `BranchPoint` throughout Python, JSON and stored state. It
removes the legacy aliases and uses schema 3 for newly created ledgers.

- [Overview](https://github.com/Protention/ocura-oss/blob/examples/0.8/docs/index.md): the complete CLI and Python workflow
- [Names and compatibility](https://github.com/Protention/ocura-oss/blob/examples/0.8/docs/naming-migration.md): renamed interfaces, IDs and JSON fields
- [Python API](https://github.com/Protention/ocura-oss/blob/examples/0.8/docs/python-api.md): functions, typed results, and `Store`
- [CLI reference](https://github.com/Protention/ocura-oss/blob/examples/0.8/docs/cli.md): commands, JSON fields, and error handling
- [Published documentation](https://protention.dev/): the released package reference

The [comparison walkthrough](https://github.com/Protention/ocura-oss/blob/examples/0.8/docs/index.md#compare-two-runs) records a `count=1`
baseline and a `count=2` variation. For those runs, `ocura-oss compare` shows:

```text
comparison: ready
source branch point: branch-point-5352cadd53fb419b916e3c22c741a7d8
source pathway: pathway-6ae2f30d9f4045949c42489948af5a79
source run: passed 0.110000s
child pathway: pathway-53ca2781d584487abb72bd320f7ad81b
  reason: try count 2
  parameters: added count=2
  source run: passed 0.110000s
  child run: passed 0.125000s
  run parameters: changed count (1 -> 2)
```

The [0.7 catalog](https://github.com/Protention/ocura-oss/tree/examples/0.7)
remains available with its tested 0.7.0 package pin, alongside the
[0.6.0 example snapshot](https://github.com/Protention/ocura-oss/tree/v0.6.0/examples/autoregressive).

## Validation and historical results

CI tests this branch's source and built distributions, including its installed
wheel with the PyTorch, JAX, and Dream CPU integrations. Live agent calls
and optional Ray execution are not part of CI. The Dream guides include the
opt-in training checks and live-run budgets.

The Dream RSI pilot and curated measurements remain September 2026 results from
Ocura OSS 0.4.0. Updating the adapter and rerunning compatibility checks does not
repeat that pilot or establish a new policy benefit. Each application owns its
checkpoints, source copies, metrics, and replay policy; Ocura supplies execution
records, lineage, and verified logs.

Ocura OSS 0.8.0 is Apache-2.0 licensed; the Dream examples retain their own
[MPL-2.0 license](https://github.com/Protention/ocura-oss/blob/examples/0.8/examples/dream_replay/LICENSE). See [Contributing](https://github.com/Protention/ocura-oss/blob/examples/0.8/CONTRIBUTING.md)
and the [security policy](https://github.com/Protention/ocura-oss/blob/examples/0.8/SECURITY.md) for the core package.
