# Experiments

Run from the repository root:

```bash
python3 experiments/check-demo1-evidence.py
```

This checks archived Demo 1 evidence in a few seconds with Python 3.9+ and no external packages. It prints two `PASS` lines and a scope statement. It does not execute a new build or an LLM evaluation. See [the design document](../docs/design-document.md) for the supported claims and limitations.

Future evaluation scripts must record case and agent versions, budgets, all per-case outcomes and the command needed to reproduce each reported result.
