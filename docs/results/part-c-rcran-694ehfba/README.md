# Part C: local rcran integration checkpoint

Run: live-part-c-694ehfba
Date: 2026-10-14
Model: openai.gpt-oss-120b-1:0 through Amazon Bedrock.

The original package failed to compile because xmmintrin.h was unavailable.
The model guarded the x86-specific include. The repaired package passed the
supplied local validator, including artifact validation.

The loop made three model calls: malformed reply, tool request, edit.
One repair attempt and one build were recorded.
Reported usage: 24,804 input tokens and 1,846 output tokens.
Elapsed time: approximately 44 seconds.

This is a Part C local integration test, without Part E.
The temporary model adapter does not implement the evidence-manager switch;
the recorded use_evidence_manager setting is not proof of that integration.

Not yet demonstrated: revision after a failed edited candidate, or a live
history-disabled run. This is not a competition or Development Validation verdict.

repair.diff contains the single source-file change recorded in the attempt.
The JSON files are copied from the run and validator outputs.
The case manifest describes the environment; this folder is not a complete
runnable case bundle. Runtime images, dependencies, source archives, and
binary artifacts remain outside Git.

The standalone runner was executed from the terminal and is not yet saved
as a reusable script. These files preserve evidence, not full reproducibility.
