"""Model configuration example. Run only inside an enabled hosted sandbox.

Illustrative 128-token/620-second settings, not official competition limits.
This does not repair a Case or implement the full Agent output contract.
"""
import json
import os
from pathlib import Path

import httpx
from openai import OpenAI


def execute():
    base_url = os.environ["BB_MODEL_BASE_URL"]
    model = os.environ["BB_MODEL_NAME"]
    task_token = Path(os.environ["BB_MODEL_TOKEN_FILE"]).read_text().strip()
    if not task_token:
        raise RuntimeError("Task model credential is missing")

    with OpenAI(
        base_url=base_url,
        api_key=task_token,
        max_retries=0,
        timeout=httpx.Timeout(620.0, connect=3.0),
        http_client=httpx.Client(
            trust_env=False,
            headers={"Accept-Encoding": "identity"},
        ),
    ) as client:
        result = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with OK."}],
            max_completion_tokens=128,
            store=False,
        )
        if len(result.choices) != 1 or result.choices[0].finish_reason != "stop":
            raise RuntimeError("Model response did not complete normally")
        if not result.choices[0].message.content:
            raise RuntimeError("Model response was empty")
        print(result.choices[0].message.content)


def main():
    try:
        execute()
        return 0
    except Exception as exc:
        error = {'status': 'failed', 'failure_stage': 'model_access', 'error_type': type(exc).__name__}
        http_status = getattr(exc, 'status_code', None)
        if type(http_status) is int:
            error['http_status'] = http_status
        print(json.dumps(error), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
