# Session changes — November 6, 2026

## Why this work started

The presentation grader described the original system as a standard agent loop:
read build logs, edit files, rebuild, and repeat. This session developed a more
specific research direction: trace architecture-dependent interfaces and check
whether a proposed repair covers connected source locations before validation.

The focus is Rust FFI C-character pointers. Hard-coding `i8` for an interface
that represents C `char` can expose type mismatches when changing target platforms.
The agent should derive a coherent repair from declarations and callers, preserve
unrelated signed-byte data, and avoid assuming every pointer mismatch is a C-string
problem. This changes the repair agent's evidence and review process, not model weights.

## Competition rules checked during the session

The live [submission guide](https://matrix.cstcloud.cn/build-bench/submission.html)
and [rules](https://matrix.cstcloud.cn/build-bench/rules.html) were inspected.
At the time of that check:

- Agents could not request official builds during a run. Additional rounds, if
  arranged by organizers, would supply updated logs to the same confirmed agent.
- Static analysis, source search and log processing were permitted alongside the LLM.
- Hosted agents used the injected organizer model configuration, with outbound
  networking disabled under the published interface.
- Repairs had to be applied to the permitted worktree. File creation/deletion
  was allowed within policy; exact replacement was our implementation choice.
- Case-specific answer tables, pre-generated patches and build-bypass changes
  were prohibited. Generic source analysis was the chosen approach.
- Official success still required policy checks, clean patch application, a
  successful target build and verified package artifacts.

These are session findings, not a substitute for the versioned official rules.

## Implemented changes

All agent paths below are relative to
`buildbench-starter-kit-0.1.0-rc.5/agents/full-agent/`.

| File | Change and purpose |
| --- | --- |
| `src/ffi_analysis.py` | New bounded source index, original repair obligations, coverage reports and offline inspection command. |
| `src/ffi_rust.py` | New v2 tokenizer/declaration reader and bounded resolution of explicit type aliases and imports. |
| `src/ffi_flows.py` | New v2 flow reader for direct calls, simple immutable local aliases and pointer-return relationships. |
| `src/model_adapter.py` | Supplies source-contract evidence to the model and refreshes coverage against current source. |
| `src/patterns.py` | Allows the old broad Rust signedness advice to be disabled when the new analysis is enabled; other recognizers remain available. |
| `src/loop.py` | Automatically enables edit review in FFI mode, checks simulated and actual edits, records final coverage, and rejects enabled in-run build feedback. |
| `src/proposal_review.py` | Explains coverage-driven review without promising an unavailable build. |
| `README.md` | Documents the opt-in mode, offline command and limitations. |

Other files:

- `tests/full-agent/test_ffi_analysis.py`: 37 tests covering positive examples,
  incomplete repairs, ambiguous/unsupported code, source preservation and loop integration.
- `docs/ffi-contract-analysis.md`: detailed usage, supported syntax, evidence,
  limits and evaluation plan.
- This file: session-wide summary.

## How the implementation evolved

The first version handled explicit foreign C-character parameters and direct
forwarding through simple wrappers and callers. It checked edits before approval,
after application and at exit. Its focused tests were included in a passing
530-test full-agent suite.

The follow-up expanded the method to `ffi-contracts-v2`:

- Grouped and renamed imports, module aliases, bounded reexports, and chained
  scalar/pointer type aliases, including supported cross-file resolution.
- Direct pointer returns, explicit returns and simple immutable local aliases.
- The known standard-library `CStr::from_ptr` C-character input contract, with
  source attribution and no assumption about arbitrary similarly named functions.
- Ordinary `let` initializers without discarding the entire function; conservative
  treatment of shadowing, reassignment and mutable borrows.
- Raw-string and character-literal masking, passive attributes, explicit missing
  generated-binding diagnostics, and additional source declaration provenance.

Saved Rust run records motivated the `CStr::from_ptr` work. Test examples of these
shapes are synthetic; they are not claimed to reproduce the entire package.

## Using the feature

```sh
export BB_FFI_ANALYSIS=1
export BB_AGENT_USE_BUILD_FEEDBACK=0
```

Use those settings with the existing full-agent harness invocation. Default
behavior remains unchanged when the feature is unset/off. Local exports do not
enable a hosted ZIP; that requires selecting and qualifying the submitted default.

From the agent directory, inspect unpacked source without a model or build:

```sh
python3 -m src.ffi_analysis --worktree /path/to/work/repo
```

The supplied worktree must contain `input/`. JSON output describes source coverage,
not a build verdict. `agent-result.json` records simulated review feedback plus
`ffi_applied` and `ffi_final` events, with hashes and `build_validated: false`.

## Validation and remaining work

- Latest full-agent suite: **551 tests passed**, including **37 FFI tests**.
- Local starter-kit submission validation: **valid**.
- The Docker-based check attempted earlier in the session could not run because
  the Docker daemon was unavailable. No hosted or official example build is claimed.
- No live model experiments, package repair success measurements, commits, pushes,
  or hosted submissions were performed in this session.

Still unresolved: general generics, methods, indirect calls, closures, nested
functions, macro expansion, target-dependent conditional compilation and missing
generated declarations. The full materials archive is a Git LFS pointer in this
checkout, so full `rust-subversion` coverage remains unverified.

The next empirical step is to run the offline report on an actual unpacked case,
inspect unresolved findings, and then compare pattern advice against FFI analysis
under identical models and budgets with in-run builds disabled. Only external
clean build validation can establish whether repair success improved.
