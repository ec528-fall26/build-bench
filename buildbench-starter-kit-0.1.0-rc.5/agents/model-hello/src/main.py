"""One model-selected hello edit, then exit for independent platform validation."""
import json
import os
from pathlib import Path

from src.adapter import create_client, request_edit
from src.edit import SPEC, apply_edit


def run_demo(client, alias, repo, evidence):
    source = (repo / SPEC).read_text(encoding='utf-8')
    arguments = request_edit(client, alias, source, evidence, SPEC)
    apply_edit(repo, arguments)
    return {'schema_version': '0.1', 'status': 'completed', 'modified_paths': [SPEC],
            'message': 'Model-selected repair applied; independent platform validation is required.'}


def main():
    workspace = Path(os.environ.get('BB_WORKSPACE', '/workspace'))
    client, stage = None, 'model_initialization'
    try:
        client, alias = create_client()
        stage = 'input_loading'
        initial = workspace / 'input/initial-build.log'
        with initial.open('rb') as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 6000))
            evidence = handle.read(6000).decode('utf-8', errors='replace')
        stage = 'model_repair'
        result = run_demo(client, alias, workspace / 'work/repo', evidence)
    except Exception as exc:
        result = {'schema_version': '0.1', 'status': 'no_fix',
                  'failure_stage': stage, 'error_type': type(exc).__name__}
        status = getattr(exc, 'status_code', None)
        if type(status) is int:
            result['http_status'] = status
    finally:
        if client is not None:
            client.close()
    output = workspace / 'output'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'agent-result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result), flush=True)
    return 0 if result['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
