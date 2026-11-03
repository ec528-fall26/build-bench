"""Actual pinned SDK, synthetic HTTP only; no model service or build execution."""
import copy
import contextlib
import io
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

AGENT = Path(__file__).resolve().parents[1] / 'agents/model-hello'
sys.path.insert(0, str(AGENT))
from src import adapter, main, edit

SOURCE = 'Name: buildbench-hello\n\n%build\necho "intentional Build-Bench demo failure" >&2\nexit 1\n\n%install\n# retained\n'
ARGS = {'path': edit.SPEC, 'old_text': 'echo "intentional Build-Bench demo failure" >&2\nexit 1\n', 'new_text': ''}


class ExampleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.repo = self.workspace / 'work/repo'
        (self.repo / 'input').mkdir(parents=True)
        (self.repo / edit.SPEC).write_text(SOURCE)
        (self.workspace / 'input').mkdir()
        (self.workspace / 'input/initial-build.log').write_text('initial failure')
        self.token = self.workspace / 'capability'
        self.token.write_text('synthetic-task-token')
        self.config = {'BB_MODEL_BASE_URL': 'http://localhost:9444/v1', 'BB_MODEL_NAME': 'synthetic-organizer-alias',
                       'BB_MODEL_TOKEN_FILE': str(self.token), 'BB_WORKSPACE': str(self.workspace)}
        self.calls = []

    def completion(self):
        return {'id': 'synthetic', 'object': 'chat.completion', 'created': 0,
                'model': self.config['BB_MODEL_NAME'], 'choices': [{'index': 0, 'finish_reason': 'tool_calls',
                'message': {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call-edit',
                'type': 'function', 'function': {'name': 'edit_text', 'arguments': json.dumps(ARGS)}}]}}]}

    def client(self, mutate=None, http_status=200):
        def handle(request):
            self.calls.append(request)
            if request.url.path == '/v1/models':
                return httpx.Response(200, json={'object': 'list', 'data': [{'id': self.config['BB_MODEL_NAME'],
                                                       'object': 'model', 'created': 0, 'owned_by': 'organizer'}]})
            self.assertEqual(request.url.path, '/v1/chat/completions')
            payload = self.completion()
            if mutate:
                mutate(payload)
            if http_status != 200:
                payload = {'error': {'message': 'sensitive synthetic-task-token', 'type': 'test_error'}}
            return httpx.Response(http_status, json=payload)
        client, alias = adapter.create_client(self.config, transport=httpx.MockTransport(handle))
        self.addCleanup(client.close)
        return client, alias

    def test_sdk_request_and_edit_without_build(self):
        client, alias = self.client()
        result = main.run_demo(client, alias, self.repo, 'initial failure')
        self.assertEqual(result['status'], 'completed')
        self.assertNotIn('exit 1', (self.repo / edit.SPEC).read_text())
        self.assertTrue((self.repo / edit.SPEC).read_text().endswith('\n'))
        self.assertEqual([r.url.path for r in self.calls], ['/v1/models', '/v1/chat/completions'])
        payload = json.loads(self.calls[-1].content)
        self.assertFalse(payload['store'])
        self.assertFalse(payload['parallel_tool_calls'])
        self.assertEqual(payload['max_completion_tokens'], 1024)
        self.assertNotIn('build_request', json.dumps(payload))
        self.assertNotIn('build_status', result)
        self.assertEqual(client.max_retries, 0)
        self.assertEqual(self.calls[-1].headers['accept-encoding'], 'identity')

    def test_failures_leave_worktree_unchanged(self):
        mutations = {
            'wrong_alias': lambda p: p.update(model='other'),
            'length': lambda p: p['choices'][0].update(finish_reason='length'),
            'refusal': lambda p: p['choices'][0]['message'].update(refusal='no'),
            'no_tool': lambda p: p['choices'][0]['message'].update(tool_calls=[]),
            'parallel_tools': lambda p: p['choices'][0]['message']['tool_calls'].append(copy.deepcopy(p['choices'][0]['message']['tool_calls'][0])),
            'wrong_name': lambda p: p['choices'][0]['message']['tool_calls'][0]['function'].update(name='shell'),
            'missing_id': lambda p: p['choices'][0]['message']['tool_calls'][0].update(id=''),
            'bad_json': lambda p: p['choices'][0]['message']['tool_calls'][0]['function'].update(arguments='{'),
            'traversal': lambda p: p['choices'][0]['message']['tool_calls'][0]['function'].update(arguments=json.dumps({**ARGS, 'path': '../outside'})),
            'insert_command': lambda p: p['choices'][0]['message']['tool_calls'][0]['function'].update(arguments=json.dumps({**ARGS, 'new_text': 'echo unsafe\n'})),
            'outside_section': lambda p: p['choices'][0]['message']['tool_calls'][0]['function'].update(arguments=json.dumps({**ARGS, 'old_text': 'Name: buildbench-hello'})),
            'no_match': lambda p: p['choices'][0]['message']['tool_calls'][0]['function'].update(arguments=json.dumps({**ARGS, 'old_text': 'not in source'})),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                client, alias = self.client(mutate)
                with self.assertRaises((ValueError, json.JSONDecodeError)):
                    main.run_demo(client, alias, self.repo, '')
                self.assertEqual((self.repo / edit.SPEC).read_text(), SOURCE)

    def test_http_failure_no_retries(self):
        client, alias = self.client(http_status=429)
        with self.assertRaises(Exception):
            main.run_demo(client, alias, self.repo, '')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual((self.repo / edit.SPEC).read_text(), SOURCE)

    def test_blank_credential_no_http(self):
        self.token.write_text(' ')
        with self.assertRaises(ValueError):
            adapter.create_client(self.config)
        self.assertEqual(self.calls, [])

    def test_bad_configuration_no_http(self):
        for value in ['http://localhost/v1?key=bad', 'http://name:secret@localhost/v1', 'file:///v1', 'http://localhost/chat/completions']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                adapter.create_client({**self.config, 'BB_MODEL_BASE_URL': value})

    def test_entrypoint_success(self):
        client, alias = self.client()
        captured = io.StringIO()
        with patch.dict(os.environ, self.config), patch.object(main, 'create_client', return_value=(client, alias)), contextlib.redirect_stdout(captured):
            self.assertEqual(main.main(), 0)
        result = json.loads((self.workspace / 'output/agent-result.json').read_text())
        self.assertEqual(result['status'], 'completed')
        self.assertNotIn('synthetic-task-token', captured.getvalue())
        self.assertNotIn('build_status', result)

    def test_initialization_safe_failure(self):
        captured = io.StringIO()
        with patch.dict(os.environ, self.config), patch.object(main, 'create_client', side_effect=ValueError('sensitive synthetic-task-token')), contextlib.redirect_stdout(captured):
            self.assertEqual(main.main(), 1)
        result = json.loads((self.workspace / 'output/agent-result.json').read_text())
        self.assertEqual(result['failure_stage'], 'model_initialization')
        self.assertEqual(result['status'], 'no_fix')
        self.assertNotIn('sensitive', captured.getvalue())
        self.assertNotIn('synthetic-task-token', captured.getvalue())

    def test_symlink_target_rejected(self):
        target = self.repo / edit.SPEC
        target.unlink()
        outside = self.workspace / 'other.spec'
        outside.write_text(SOURCE)
        target.symlink_to(outside)
        with self.assertRaises(ValueError):
            edit.apply_edit(self.repo, json.dumps(ARGS))
        self.assertEqual(outside.read_text(), SOURCE)

    def test_public_source_has_no_build_or_responses_client(self):
        for path in AGENT.rglob('*'):
            if path.is_file():
                self.assertFalse(path.is_symlink())
                if path.suffix in ('.pyc', '.pyo'):
                    continue
                text = path.read_text()
                for forbidden in ['subprocess', 'client.responses', 'feedback_receipt', 'build_request']:
                    self.assertNotIn(forbidden, text)

    def test_missing_platform_configuration_safe_failure(self):
        captured = io.StringIO()
        with patch.dict(os.environ, {'BB_WORKSPACE': str(self.workspace)}, clear=True), contextlib.redirect_stdout(captured):
            self.assertEqual(main.main(), 1)
        result = json.loads((self.workspace / 'output/agent-result.json').read_text())
        self.assertEqual(result['failure_stage'], 'model_initialization')
        self.assertEqual(result['error_type'], 'KeyError')
        self.assertEqual(self.calls, [])

    def test_configuration_snippet_safe_failure(self):
        path = Path(__file__).resolve().parent / 'example.py'
        spec = importlib.util.spec_from_file_location('configuration_example', path)
        example = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(example)
        captured = io.StringIO()
        with patch.object(example, 'execute', side_effect=ValueError('sensitive synthetic-task-token')), contextlib.redirect_stdout(captured):
            self.assertEqual(example.main(), 1)
        self.assertEqual(json.loads(captured.getvalue())['error_type'], 'ValueError')
        self.assertNotIn('sensitive', captured.getvalue())
        self.assertNotIn('synthetic-task-token', captured.getvalue())


if __name__ == '__main__':
    unittest.main(verbosity=2)
