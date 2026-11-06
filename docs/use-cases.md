# Choose an example

Version 0.8.0.

The core repository keeps one primary example: record a small demonstration, then
inspect its retained history. The fuller demonstrations live together on the
semi-permanent [`examples/0.8` branch](https://github.com/Protention/ocura-oss/tree/examples/0.8).
That branch is maintained separately and is not intended to merge into `main`.
Example scripts and optional frameworks are not installed with the package.

## Start here

The inspection script lives in the repository and is not installed with the package.
From this checkout with Ocura OSS 0.8 installed:

```console
ocura-oss demo --root ./ocura-oss-demo
python examples/history/inspect_history.py --root ./ocura-oss-demo --archive
```

The demo destination must be new. If you already ran it, skip that command. The [history guide](../examples/history/README.md) explains the report and
how to inspect your own ledger. No optional dependencies are needed.

## Fuller demonstrations

These examples use **Ocura OSS 0.8.0**. Use a separate checkout and an activated
virtual environment, then install the matching release:

```console
git clone --branch examples/0.8 --single-branch https://github.com/Protention/ocura-oss.git ocura-oss-examples
cd ocura-oss-examples
git rev-parse HEAD
python -m pip install ocura-oss==0.8.0
```

Follow the selected guide for any optional dependencies. The branch's package
source also matches 0.8.0, so `python -m pip install .` works for package development.

The examples branch has its own CI checks, including PyTorch and JAX CPU workflows.
Record the checked-out commit when retaining results you need to reproduce.
The [0.7 catalog](https://github.com/Protention/ocura-oss/tree/examples/0.7) and
[0.6.0 example snapshot](https://github.com/Protention/ocura-oss/tree/v0.6.0/examples/autoregressive)
remain available with their matching package versions. Start new ledgers with
0.8; it does not read or convert earlier ledger formats.

| Workflow | What it demonstrates | Additional requirements |
| --- | --- | --- |
| [CI-report development](https://github.com/Protention/ocura-oss/blob/examples/0.8/examples/ci_report/README.md) | A failing baseline, the declared reason for a correction, and later inspection of verified evidence | None beyond Python and Ocura |
| [Autoregressive training](https://github.com/Protention/ocura-oss/blob/examples/0.8/examples/autoregressive/README.md) | Compare a baseline with a longer training run and reconstruct the result | PyTorch or JAX; optional Ray |
| [Dream replay](https://github.com/Protention/ocura-oss/blob/examples/0.8/examples/dream_replay/README.md) | Checkpointed training and policy replay over recorded branches | CPU PyTorch for training; no remote agent |
| [Dream RSI](https://github.com/Protention/ocura-oss/blob/examples/0.8/examples/dream_rsi/README.md) | Experimental agent-directed training exploration and policy revision | CPU PyTorch and a signed-in Codex CLI for live agent calls |

Dream replay and Dream RSI are experimental examples of what callers can build
with Ocura. Each application owns its source copies, checkpoints, metrics, and
replay policy; Ocura supplies execution records, lineage, and verified logs.
