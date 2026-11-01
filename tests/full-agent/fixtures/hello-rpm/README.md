# Regression fixture: the starter kit's RPM `hello` case

Copied unchanged from the starter kit's demo workspace
(`buildbench-starter-kit-0.1.0-rc.2/runs/demo/.internal/workspace/`), laid out
as the platform presents it: `input/` (task.json, initial-build.log) and the
worktree `work/repo/` (manifest.json, input/buildbench-hello.spec).

`hello` is an RPM package. The agent treated it as Debian and sometimes gave up
without editing (docs/design-document.md, local readiness notes;
docs/full_submission_plan.md, Part A1). `test_package_format.py` checks that
Part A now reports it as RPM, from its metadata and, with the manifest removed,
from its files.
