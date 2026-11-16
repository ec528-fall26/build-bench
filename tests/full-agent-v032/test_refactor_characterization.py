"""Recorded against feaa571 before refactoring; no model or real builds.

Compare complete controller requests, outcomes, and final source bytes, not
just whether a scripted run reports success. The recording path is developer
only; ordinary test discovery always reads the committed fixture.
"""
from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / 'buildbench-starter-kit-0.1.0-rc.5/agents/full-agent-v032'
sys.path.insert(0, str(AGENT))
from src.contracts import AgentConfig, Budget, BuildResult, Diagnostic, ModelTurn, ToolRequest
from src.loop import run_loop
from src.tools import run_tool
from src.types import CaseContext, Edit

FIXTURE = Path(__file__).with_name('fixtures') / 'refactor_characterization.json'


def turn(action, edits=(), tools=(), reason='done'):
    return ModelTurn(action, list(tools), 'supported repair', ['input/a.c:1'], list(edits), reason)


def changed(old='int value = 1;', new='int value = 2;'):
    return turn('edit', [Edit('input/a.c', old, new)])


def scenario(name):
    steps = [changed(), turn('stop')]
    options = {}
    builds = []
    if name == 'no_build':
        options['config'] = AgentConfig(use_build_feedback=False)
    elif name == 'tools_then_edit':
        steps.insert(0, turn('tool', tools=[ToolRequest('read_file', {
            'path': 'input/a.c', 'start_line': 1, 'end_line': 2})]))
    elif name == 'correction':
        steps = [changed('absent'), changed(), turn('stop')]
    elif name == 'repeated_correction':
        steps = [changed('absent'), changed('absent'), turn('stop')]
    elif name == 'empty_batch':
        steps = [turn('edit'), turn('edit')]
    elif name == 'malformed':
        steps = [turn('malformed', reason='invalid JSON'), changed(), turn('stop')]
    elif name == 'review':
        options['review_edits'] = True
        steps = [changed(), turn('approve', reason='confirmed producer'), turn('stop')]
    elif name == 'review_revision':
        options['review_edits'] = True
        steps = [changed(), changed(new='int value = 3;'), turn('approve'), turn('stop')]
    elif name == 'unexpected_approval':
        steps = [turn('approve'), turn('stop')]
    elif name == 'token_budget':
        options['budget'] = Budget(max_tokens=3)
        steps = [turn('tool', tools=[]), turn('stop')]
    elif name == 'tool_budget':
        options['max_tool_calls'] = 0
        steps = [turn('tool', tools=[ToolRequest('list_dir', {'path': 'input'})])]
    elif name == 'rollback':
        steps = [changed(), changed('int value = 2;', 'int value = 3;'), turn('stop')]
        builds = [BuildResult(False, 'test', [], 1), BuildResult(False, 'build', [], 1)]
    elif name == 'repeated_diagnostic':
        steps = [changed(), changed('int value = 2;', 'int value = 3;')]
        diagnostic = Diagnostic('same failure', 'build', 'a.c', 1, [], None, 0)
        builds = [BuildResult(False, 'build', [diagnostic], 1)] * 2
    elif name == 'build_success':
        steps = [changed()]
        builds = [BuildResult(True, 'packaging', [], 1)]
    elif name == 'model_error':
        steps = [OSError('synthetic transport failure')]
    elif name == 'tools_disabled':
        options['config'] = AgentConfig(use_tools=False, use_history=False)
        steps = [turn('tool', tools=[]), changed(), turn('stop')]
    else:
        assert name == 'default'
    return steps, options, builds


SCENARIOS = (
    'default', 'no_build', 'tools_then_edit', 'correction', 'repeated_correction',
    'empty_batch', 'malformed', 'review', 'review_revision', 'unexpected_approval',
    'token_budget', 'tool_budget', 'rollback', 'repeated_diagnostic',
    'build_success', 'model_error', 'tools_disabled',
)


def capture(name):
    steps, options, builds = scenario(name)
    requests = []
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'input').mkdir()
        source = root / 'input/a.c'
        source.write_text('int value = 1;\n')
        context = CaseContext('characterization', root, 'original failure', {})

        def complete(request):
            data = asdict(request)
            data['context']['worktree'] = '<worktree>'
            requests.append(data)
            reply = steps.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply, {'input_tokens': 2, 'output_tokens': 1}

        class Feedback:
            def request_build(self, worktree):
                return builds.pop(0)

        result = run_loop(context, next_turn=complete, clock=lambda: 0,
                          build_feedback=Feedback() if builds else None, **options)
        record = asdict(result)
        # Signatures include filesystem identity. Preserve equality/order while
        # normalizing the per-temp-directory hashes, never the proposal text.
        signatures = {}
        for event in record['review_events']:
            value = event.get('signature')
            if value is not None:
                event['signature'] = signatures.setdefault(value, f'signature-{len(signatures) + 1}')
        return {'requests': requests, 'result': record, 'source': source.read_text()}


def capture_tools():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'input/debian/patches').mkdir(parents=True)
        (root / 'input/a.c').write_text('int value = 1;\n')
        (root / 'input/debian/patches/series').write_text('first.patch\n')
        log = root / 'original.log'
        log.write_text('one\nerror: failure\nthree\n')
        requests = [
            ToolRequest('read_file', {'path': 'input/a.c', 'start_line': 1, 'end_line': 3}),
            ToolRequest('find_files', {'name_glob': '*.c'}),
            ToolRequest('search', {'regex': 'value', 'path_glob': 'input/*.c'}),
            ToolRequest('patch_series', {}), ToolRequest('current_diff', {}),
            ToolRequest('read_log', {'start_line': 1, 'end_line': 2}),
            ToolRequest('search_log', {'regex': 'error'}),
            ToolRequest('read_file', {'path': '../outside', 'start_line': 1, 'end_line': 1}),
            ToolRequest('search', {'regex': '[', 'path_glob': '*'}),
            ToolRequest('unknown', {}), ToolRequest('list_dir', {'path': 7}),
        ]
        return [asdict(run_tool(request, root, log_path=log)) for request in requests]


class HistoricalBehavior(unittest.TestCase):
    def test_controller_requests_results_and_files_match_original(self):
        expected = json.loads(FIXTURE.read_text())
        for name in SCENARIOS:
            with self.subTest(scenario=name):
                self.assertEqual(capture(name), expected['loop'][name])

    def test_tool_responses_match_original(self):
        self.assertEqual(capture_tools(), json.loads(FIXTURE.read_text())['tools'])


if __name__ == '__main__' and '--record' in sys.argv:
    FIXTURE.write_text(json.dumps({'loop': {n: capture(n) for n in SCENARIOS},
                                   'tools': capture_tools()}, indent=2) + '\n')
