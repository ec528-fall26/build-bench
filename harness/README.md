# Harness (Part 5)

The local test setup around the agent: unpack a case, run the agent, repack the
source package, build it with the organizers' validator, and record the result.

**Never part of the submission.** The agent ZIP is built from
`buildbench-starter-kit-0.1.0-rc.2/agents/one-shot/`; this folder sits outside
the starter kit so it cannot be packaged by mistake, and so a newer starter-kit
release can replace that folder without touching our code.

Background and measured results: [`docs/part5-findings.md`](../docs/part5-findings.md).

## Contents

| File | Purpose | Status |
| --- | --- | --- |
| `record.py` | Read a validator result folder into one outcome; append JSONL rows | Done |
| `tests/` | Unit tests, using real `build-result.json` files from the rcran runs | Done |
| loop script | unpack → agent edits → `dpkg-source --auto-commit -b` → `run.sh` → record | Next |

## Repaired means

`status == "succeeded"` **and** `artifact_validation_passed == true`.

`patch_applied` is ignored on purpose. It reports the validator's own `--patch`
option, which we never use — our repair is inside the rebuilt source package —
so it stays `false` even on a successful repair.

## Usage

```bash
# from the repository root
python3 -m unittest discover -s harness/tests -t .
python3 -m harness.record results/baseline-1 results/fix-1
```

```text
baseline-1           failed          not repaired    89s  build failed; expected binary artifact pattern ...
fix-1                succeeded       REPAIRED        87s  build completed successfully
```

Standard library only; Python 3.10 or newer.
