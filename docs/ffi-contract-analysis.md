# Rust FFI source-contract analysis

Updated November 6, 2026. Current analysis method: `ffi-contracts-v2`.

This opt-in feature gives the repair model source evidence about connected Rust
C-character interfaces and checks the proposed repair before final validation.
It does not train the model or execute a compiler. It is a bounded source reader,
not a complete Rust type checker or a proof that a patch builds.

## Enable the feature

Set these variables in the full-agent process environment before your existing
harness invocation:

```sh
export BB_FFI_ANALYSIS=1
export BB_AGENT_USE_BUILD_FEEDBACK=0
```

The mode automatically enables proposed-edit review. An actual in-run build
adapter with build feedback enabled is rejected. The external harness still
performs its final clean validation. Leaving `BB_FFI_ANALYSIS` unset, or setting
it to `0`, preserves the previous behavior for comparison.

Local environment exports do not configure an organizer-hosted ZIP. Hosted
activation requires choosing the default in the submitted code and qualifying
that exact immutable version. This work has not changed that default, the
manifest version, the model, or the dependency lockfile.

## What changed in v2

| Capability | First implementation | Current implementation |
| --- | --- | --- |
| Pointer parameters | Direct forwarding | Retained, with immutable local aliases |
| Pointer returns | Unresolved | Direct tail expressions, explicit returns, simple immutable local chains, and returned parameters |
| Type aliases | Mostly unresolved | Explicit scalar and pointer aliases, bounded chains, and cross-file aliases |
| Imports | A few exact C-character imports | Grouped imports, renamed imports, module aliases, and bounded explicit reexports |
| Module paths | Same file and limited `crate::` paths | Also `self::`, `super::`, and unambiguous `module/mod.rs` files |
| `CStr::from_ptr` | Unrecognized | Known standard-library C-character input contract, including explicit imported aliases |
| Ordinary `let` statements | Entire function skipped | Unrelated initializers retained; simple immutable aliases followed; shadowing tracked conservatively |
| Raw strings and documentation attributes | Could discard useful source | Literals are masked and a small allowlist of passive attributes is accepted |
| Generated bindings | Unresolved | Ordinary available source files can be indexed; missing declarations get an explicit diagnostic |
| Provenance | Locations and file hashes | Also alias/import declaration evidence and the standard-library contract source |

`CStr::from_ptr` is a general library rule, not a package-specific answer.
Its input represents a pointer to C `char`, as described in the
[Rust CStr documentation](https://doc.rust-lang.org/std/ffi/struct.CStr.html#method.from_ptr).
The analyzer does not substitute a particular architecture's signed byte type.
It does not infer a C-string contract merely because a user-defined function or
type happens to be named `CStr` or `c_char`.

## Example

```rust
use std::ffi::CStr as Text;
type Byte = i8;

unsafe fn decode(path: *const Byte) {
    let description = "unrelated work";
    let path: &str = Text::from_ptr(path).to_str().unwrap();
    consume(path);
}
```

The reader resolves the imported `Text`, recognizes `from_ptr`'s C-character
requirement, resolves `Byte` to `i8`, and identifies `decode`'s parameter as an
obligation. The new binding named `path` does not hide the original parameter
inside its own initializer. Later uses are not assumed to refer to the original
pointer. Calls such as `consume` or methods whose types cannot be resolved stay
unknown; this example is illustrative, not a complete compilable program.

For a return wrapper such as `fn name() -> *const i8 { foreign_name() }`, an
available foreign declaration returning `*const c_char` can establish the return
obligation. A downstream `CStr::from_ptr(name())` can also constrain `name`'s
return type, even if its implementation calls an unavailable function. That
unavailable implementation remains explicitly unresolved.

## Runtime flow and reports

1. Read bounded source under the worktree's `input/` directory.
2. Resolve supported declarations, aliases, calls and return relationships.
3. Anchor obligations in foreign C-character declarations or the known standard
   library contract. Preserve those original obligations throughout the run.
4. Show the model affected locations, relationships, declaration evidence and
   unresolved findings. Replace the older broad Rust replacement advice while
   retaining other recognizers.
5. Check the existing edit simulator's candidate before approval, then check the
   actual worktree after edits and again at exit.
6. Allow the model to revise or stop within the existing turn/token/time budgets.
   There is no additional unbounded retry loop and no automatic build request.

| Status | Interpretation |
| --- | --- |
| `satisfied` | An indexed slot has the expected portable pointer type and its required original relationships remain. |
| `remaining` | An affected slot still uses a hard-coded byte type. |
| `unknown` | A declaration or relationship disappeared, changed incompatibly, or could not be resolved. |

Parameter positions are zero-based; internal slot `-1` denotes a return value.
Deleting a call, changing a foreign declaration or hiding a pointer behind a cast
cannot erase an existing obligation. Unrelated signed-byte data is not selected
solely because it contains `i8`. Coverage is advisory: it cannot establish memory
safety, string termination, semantic equivalence, or successful compilation.

Existing `agent-result.json` review events include `ffi_applied` and `ffi_final`
reports. These record `ffi-contracts-v2`, counts, original/current source hashes,
relationships, declaration evidence, unknowns and `build_validated: false`.
Original line numbers remain provenance anchors if edits move current lines.
Unknowns from original and current indexing remain visible so narrowing the
analyzed source cannot silently become a claim of full coverage.

## Inspect a worktree without a model or build

From the full-agent directory:

```sh
python3 -m src.ffi_analysis --worktree /path/to/work/repo
```

The supplied directory must contain `input/` with the unpacked package source.
The command prints a JSON report to stdout. It does not edit files, contact a
model, run build scripts or download dependencies. A successful command exit
means the report was produced, not that the source builds.

## Explicit limits

Still unresolved: generic functions/type parameters, method receiver types,
indirect/function-pointer calls, closures, nested functions, arbitrary transformed
arguments, casts, procedural/declarative macro expansion, and active target/feature
configuration. Functions containing unsupported binding scopes are skipped.
Simple typed or mutable local aliases are not followed. Local assignments and
mutable borrows invalidate subsequent inferred flows conservatively.

Conditional or unknown active attributes leave a file unresolved; passive
attributes such as documentation and `inline` are allowed. Inline modules and
`impl` blocks are not expanded. Module-file mapping is not Cargo's module/config
resolution. Ambiguous `.rs` versus `mod.rs` paths, cycles and conflicting imports
are not guessed. `libc` and other external aliases need available declarations;
the reader does not assume external names have a particular meaning.

`include!` does not run a generator or load arbitrary paths. A generated Rust file
already in the scanned source tree can participate through supported explicit
paths. Declarations absent from the worktree remain unavailable. A `CStr` rule
cannot replace missing arbitrary foreign-function signatures.

Bounds: 300 Rust files; 256 KiB per file; 4 MiB total; 40,000 lexical tokens per
file; 10,000 directory entries; a three-second traversal deadline checked between
directories; 8,000 body tokens and 256 local bindings per function; 2,000 flow
edges; 200 obligations; and 16-step alias/import resolution chains. These are
bounded scans, not a hard end-to-end parser timeout. Symlinks, `.git`, `.pc`,
`target` and `vendor` directories are excluded. Unknown and declaration displays
are capped at 80 entries each.

## Evidence, verification and evaluation

The archived `rust-subversion` record contains repeated `CStr::from_ptr` errors
and source excerpts involving `let path = ...` initializers. This motivated v2.
The tests use explicitly synthetic examples of those shapes. The full downloaded
materials archive in this checkout is only a Git LFS pointer, so no complete
`rust-subversion` source analysis or build validation was performed here. Nested
callbacks and other unsupported constructs in that package may still prevent
substantial coverage.

```sh
python3 -m unittest discover -s tests/full-agent -p test_ffi_analysis.py
python3 -m unittest discover -s tests/full-agent
```

Current verification: 37 focused FFI tests, 551 full-agent tests passing. The local
starter-kit submission validator accepts the bundle. These checks establish
implementation behavior, not better repair success. No live model run or official
Hosted Smoke qualification was performed for this change.

For an ablation compare existing pattern advice (`BB_FFI_ANALYSIS=0`) against
contracts (`BB_FFI_ANALYSIS=1`), with `BB_REVIEW_EDITS=1` and build feedback off in
both arms. Keep cases, model, edit mechanism and budgets identical. Freeze held-out
packages, repeat runs and use external clean validation as the success criterion.
A generic-agent control additionally requires consistently disabling pattern advice;
this change does not introduce that third experiment configuration.
