# Managed Python Agent

Edit `src/main.py` to implement your repair strategy. The Agent reads task
metadata and the initial failure log from `/workspace/input`, modifies only
`/workspace/work/repo`, and writes `/workspace/output/agent-result.json`.

Run:

```bash
./bb check --agent ./agents/AGENT_NAME
./bb test --agent ./agents/AGENT_NAME
./bb package --agent ./agents/AGENT_NAME
```

For a model-dependent Agent, follow `model-access/guide.en.md`: read the three
platform settings, check and package locally, then test model access through
Hosted Smoke. Local `bb test` does not provide the official model channel.

