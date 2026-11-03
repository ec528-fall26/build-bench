"""Native Chat access using platform-injected configuration; no build client."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from openai import OpenAI


def create_client(environment=None, *, transport=None):
    environment = os.environ if environment is None else environment
    base = environment['BB_MODEL_BASE_URL'].rstrip('/')
    alias = environment['BB_MODEL_NAME']
    credential = Path(environment['BB_MODEL_TOKEN_FILE'])
    parsed = urlsplit(base)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
            parsed.username or parsed.password or parsed.query or parsed.fragment or
            not parsed.path.endswith('/v1') or not alias.strip()):
        raise ValueError('Invalid injected model configuration')
    if credential.stat().st_size > 4096:
        raise ValueError('Invalid task credential file')
    token = credential.read_text(encoding='utf-8').strip()
    if not token or any(character.isspace() for character in token):
        raise ValueError('Invalid task credential')
    client = OpenAI(api_key=token, base_url=base, max_retries=0,
                    timeout=httpx.Timeout(620.0, connect=3.0),
                    http_client=httpx.Client(trust_env=False, transport=transport,
                                             headers={'Accept-Encoding': 'identity'}))
    try:
        if not any(model.id == alias for model in client.models.list().data):
            raise ValueError('Organizer model alias unavailable')
    except Exception:
        client.close()
        raise
    return client, alias


def request_edit(client, alias, source, evidence, spec):
    if len(source.encode()) > 8192 or len(evidence.encode()) > 12000:
        raise ValueError('Example input exceeds bounds')
    function = {
        'name': 'edit_text',
        'description': 'Delete one exact source region inside the public hello %build section. Do not execute commands.',
        'parameters': {'type': 'object', 'properties': {
            'path': {'type': 'string', 'enum': [spec]},
            'old_text': {'type': 'string'},
            'new_text': {'type': 'string', 'enum': ['']}},
            'required': ['path', 'old_text', 'new_text'], 'additionalProperties': False},
        'strict': True,
    }
    response = client.chat.completions.create(
        model=alias,
        messages=[{'role': 'user', 'content':
            'Repair the public Build-Bench hello RPM by one minimal deletion removing the intentional build failure. '
            'Use edit_text with new_text="". Preserve metadata, install and file sections; insert no commands or macros. '
            'The Agent will apply the edit and exit. The platform independently validates the final worktree.\n'
            'Source:\n' + source + '\nInitial failure evidence:\n' + evidence}],
        tools=[{'type': 'function', 'function': function}],
        tool_choice={'type': 'function', 'function': {'name': 'edit_text'}},
        parallel_tool_calls=False, max_completion_tokens=1024, store=False,
    )
    if response.model != alias or len(response.choices) != 1:
        raise ValueError('Unexpected model response')
    choice = response.choices[0]
    calls = choice.message.tool_calls or []
    if (choice.finish_reason != 'tool_calls' or choice.message.refusal or len(calls) != 1 or
            calls[0].type != 'function' or not calls[0].id or calls[0].function.name != 'edit_text'):
        raise ValueError('Expected one completed edit tool call')
    arguments = calls[0].function.arguments
    if len(arguments.encode()) > 8192 or not isinstance(json.loads(arguments), dict):
        raise ValueError('Invalid edit arguments')
    return arguments
