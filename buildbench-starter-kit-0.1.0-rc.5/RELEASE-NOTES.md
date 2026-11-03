# Starter Kit 0.1.0-rc.5

This patch release fixes generated diffs for added and deleted text files.

- Added the required Git file-mode headers when a text file is created or deleted, including newly added Debian patch files.
- Preserved executable permissions for added and deleted text files.
- Existing text-file edits and allowed-path, UTF-8 and binary-file checks are unchanged.

Update your local Starter Kit to use the fix. The corresponding patch generator has also been updated in Hosted Smoke. Previous run results are retained; run the test again to obtain a new result.

The Agent ZIP format, protocol version, model-access settings and competition limits are unchanged. The bundled model hello Agent remains version 0.2.2. Runtime images retain their independently versioned rc.3 digests. This release does not add an in-run target-build client.

See the model-access guides for configuration and the website for current model capabilities and competition rules.
