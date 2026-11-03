# Example Cases

The self-contained hello Case includes a frozen package dependency snapshot,
so it is distributed in the immutable image below
rather than stored in Git.

```text
ghcr.io/terriyyy/buildbench-example-assets@sha256:98bdf0f445cf0296f67ffc8b4601ab1cb1f82b957f84bd9739276f5659cdde54
```

This is the independently versioned rc.3 runtime-image release, retained by Starter Kit rc.5. The public development dataset is a separate resource; it does not include frozen local build environments for every Case.

`./bb demo` extracts the Case automatically. Participants do not need to
download or prepare RPM dependencies manually.

