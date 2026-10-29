# Choose an example

Version 0.7.0.dev0.

These examples show how a caller uses Ocura to retain execution evidence and
explain a variation. They target the unreleased 0.7 development interfaces. Install
this checkout with `python -m pip install .`, or install its built wheel and copy
the relevant example directory separately. Example scripts and optional frameworks
are not installed with the package.

| Workflow | Question it answers | Requirements |
| --- | --- | --- |
| [Fix a CI job reporter](../examples/ci_report/README.md) | What failed, why was it changed, and which evidence supports the handoff? | Python and Ocura |
| [Compare autoregressive training](../examples/autoregressive/README.md) | What changed between the baseline and a longer training run? | PyTorch or JAX; optional Ray |
| [Inspect an existing history](../examples/history/README.md) | What happened in this ledger, including failed or unfinished work? | Python and Ocura |
| [Dream-RSI adaptation](https://github.com/Protention/ocura-oss/tree/feat/0.7-dream-examples/examples/dream_rsi) | How can an application retain evidence for training exploration and policy replay? | Separate experimental branch; CPU PyTorch for training and a signed-in Codex CLI for live agent calls |

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

Dream replay and Dream RSI remain on a separate experimental branch. Their code
owns model checkpoints, policies, budgets, replay semantics, and agent access.
The fixed-recipe replay path needs no remote agent. The live RSI path is opt-in
and consumes the caller's agent usage. Retained replay/report commands do not train
or call an agent. Historical pilot measurements remain historical; the 0.7 refresh
does not establish new scientific results or compute savings.
