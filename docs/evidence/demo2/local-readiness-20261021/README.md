# Local readiness checks — October 21, 2026

Both one-shot and full-agent passed the Starter Kit hello example:
1/1 cases passed, readiness exit code 0, final validation succeeded,
and submission packages were generated.

Configuration:
- Starter Kit rc.2 with published rc.3 validator/example-assets images.
- Local runner modified to forward model credentials/settings.
- BB_LOCAL_AGENT_NETWORK=bridge enabled access to our model endpoint.
- No agent repair-code or validator success-criteria changes were made
  as part of this connectivity fix.
- These are modified local readiness tests, not Hosted Smoke qualification.
- Hosted model integration and Hosted Smoke remain pending.

Package SHA-256:
- one-shot: 294442a5bc2f6e81a9440291425253a21316223bb20d1260a2f6c022d05bd9b8
- full-agent: 153413bed2249a2eeb423beef9c895eb602c36986765669f90d1fa5d13454095

Recorded paths may refer to the original EC2 checkout.
