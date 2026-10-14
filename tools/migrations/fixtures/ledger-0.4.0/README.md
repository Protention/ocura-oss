# Frozen Ocura OSS 0.4 ledger

`state/` was written and verified using the Python modules from tag `v0.4.0`,
commit `240843038a744b906ed6dffa8836dd827ea50691`. It has 2 pathways, 5 atoms,
5 chokepoints, and 10 logs, including binary output and passed, failed,
launch-failed, and interrupted outcomes.

The fixture writer used deterministic IDs and a fixed advancing test clock.
Logical `python` commands were routed to the fixture writer's Python interpreter
without recording a machine-specific executable path. The interrupted run used
a one-shot `KeyboardInterrupt` in the real child process's wait method; the 0.4
runner stopped that child and wrote its normal interrupted records. This is test
evidence, not a historical research run.

The standard-library migration tool's canonical inventory digest of `state/` is:

```text
8b41c4c3435266fba7b428dcccb2d32c1dee33ab12157027530cbca0dec88b39
```

Do not regenerate or edit the fixture. New cases belong in separate fixtures or
temporary copies in tests. Everything in `tools/migrations/` is excluded from
the pip distributions.
