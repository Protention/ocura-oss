# Ocura OSS 0.8 example catalog

Version 0.8.0.

The `examples/0.8` branch holds the fuller demonstrations separately
from the primary demo and history walkthrough on
[`main`](https://github.com/Protention/ocura-oss/tree/main). These examples show how
a caller uses Ocura to retain execution evidence and explain a variation. They
use the 0.8.0 interfaces. Follow the [catalog setup](../README.md#install-and-try)
to check out this branch and install `ocura-oss==0.8.0` from PyPI.
`python -m pip install .` also works for package development. Keep the example
directories when using a separately installed package.
Example scripts and optional frameworks are not installed with the package.

Start new ledgers with 0.8. To inspect older work, keep the
[0.7 catalog](https://github.com/Protention/ocura-oss/tree/examples/0.7) and its
matching package; 0.8 does not convert or read earlier ledger formats.

| Workflow | Question it answers | Requirements |
| --- | --- | --- |
| [Fix a CI job reporter](../examples/ci_report/README.md) | What failed, why was it changed, and which evidence supports the handoff? | Python and Ocura |
| [Compare autoregressive training](../examples/autoregressive/README.md) | What changed between the baseline and a longer training run? | PyTorch or JAX; optional Ray |
| [Replay fixed training policies](../examples/dream_replay/README.md) | How can a caller select an allocation policy from recorded training history? | CPU PyTorch for training; no remote agent |
| [Dream-RSI adaptation](../examples/dream_rsi/README.md) | How can an application retain evidence for training exploration and policy replay? | CPU PyTorch for training and a signed-in Codex CLI for live agent calls |
| [Inspect an existing history](../examples/history/README.md) | What happened in this ledger, including failed or unfinished work? | Python and Ocura |

## A development handoff

A JSONL CI job reporter initially counts attempts as jobs. Its tests expose the
mistake. The corrected version selects each job's highest attempt, counts retries
and recovery, and rejects malformed input. The walkthrough records the original
failure and the correction as related runs:

```text
failing baseline
  -> declared reason for the correction
  -> passing child run
  -> later inspection of records, source fingerprints, and verified test output
```

From the checkout root:

```console
python examples/ci_report/workflow.py run --root ./ci-report-demo
python examples/ci_report/workflow.py report --root ./ci-report-demo
```

The destination must be new. Keep it after the run; the report reads that retained
work without running tests again. The example preserves its own source versions
and input so a maintainer can inspect them alongside the execution evidence.

The reason describes the developer's declared intent. Test output establishes
which checks passed or failed. Neither establishes every unstated motivation or
proves that the tests cover every requirement. An agent can use the same workflow
through its existing shell, with no agent-specific package integration.

## Training and replay

The autoregressive example compares two training budgets on a tiny included corpus.
Its report interprets verified workload metrics; Ocura does not score models.
An improved validation loss in that example describes that configuration and its
changed budget, not a general framework or learning-quality advantage.

Dream replay and Dream RSI are experimental entries in this branch's catalog.
Their code owns model checkpoints, policies, budgets, replay semantics, and agent access.
The fixed-recipe replay path needs no remote agent. The live RSI path is opt-in
and consumes the caller's agent usage. Retained replay/report commands do not train
or call an agent. Historical pilot measurements remain historical; the 0.8 refresh
does not establish new scientific results or compute savings.
