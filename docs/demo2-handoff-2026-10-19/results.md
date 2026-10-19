# Demo 2: Rust budget experiments

These four exploratory runs used the full agent with history enabled,
based on commit `474ad0c`. They were not runs of the frozen one-shot baseline.

Case: `launchpad-plucky-amd64-arm64-rust-subversion-b3a4b8c35bb2`.

Each setting was tested once from a fresh run. None produced a verified repair.
The experiment driver prints 'Demo 3 comparison'; these results are retained
here as supporting development evidence for Demo 2.

| Token limit | Turn limit | Tokens used | Turns used | Attempts | Repair builds | Applied edits | Seconds | Loop stop |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 100,000 | 20 | 100803 | 11 | 1 | 1 | 2 | 398.1 | token_budget |
| 200,000 | 20 | 201597 | 18 | 2 | 2 | 4 | 627.6 | token_budget |
| 300,000 | 20 | 239567 | 20 | 2 | 1 | 23 | 667.1 | turn_budget |
| 300,000 | 30 | 208431 | 22 | 3 | 3 | 5 | 821.1 | build_budget |

Repair builds above count attempt records with a build result, excluding baseline builds.
Applied edits count successful replacement operations, not repaired files or retained final changes.

## Findings

- The 100k and 200k runs stopped at the token limit.
- The 300k/20-turn run stopped at the turn limit; its first attempt applied no edits.
- The 300k/30-turn run reached the three-build limit after 22 turns.
- In the final run, the first reported error moved through lines 81, 175, and 374.
- Three runs reached a remaining error at the `svn_client_checkout3` call.
- Approximately 89–92% of reported usage was input tokens.

The changing first error suggests local progress, but does not prove all earlier
errors were eliminated. Diagnostic counts are parsed summaries, not complete
compiler error counts. The recorded 'test' stage includes compilation invoked
by the package's test command; it does not establish successful compilation.

## Limitations

One run per setting cannot establish a causal or repeatable improvement.
Fresh runs produced different edits. Larger limits did not guarantee more
useful work or a repair. This is neither a hidden-case nor leaderboard result.

The archive includes logs, replies, diffs, run records, and recorded attempt
feedback. It does not establish that complete outgoing prompts and every
tool result were captured. Token-component attribution remains future work.

## Evidence provenance

- Archive: `demo2-budget-experiments-evidence.tar.gz`
- SHA256: `de679487f38d881d61cc2a08bf1592e17f0df0c1b7061de36c2ccbfff37c3ff3`
- The raw archive remains outside Git.
- `evidence.json` contains selected fields from the four archived run records.
- Budget settings come from the documented execution session; the archive
  also contains the final temporary budget/turn settings diff.
