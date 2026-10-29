# Ocura OSS 0.7 example catalog

Version 0.7.0.dev0.

The maintained `examples/0.7` branch holds the fuller demonstrations separately
from the primary walkthrough on `main`. These examples show how a caller uses
Ocura to retain execution evidence and explain a variation. They target the
unreleased 0.7 development interfaces. Follow the [catalog setup](../README.md#install-and-try)
to check out this branch, then install it with `python -m pip install .`, or install
its matching built wheel and copy the relevant example directories separately.
Example scripts and optional frameworks are not installed with the package.

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
or call an agent. Historical pilot measurements remain historical; the 0.7 refresh
does not establish new scientific results or compute savings.
