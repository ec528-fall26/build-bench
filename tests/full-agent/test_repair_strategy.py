"""Offline tests for selecting and presenting a small repair group."""
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
import os
import sys
import unittest

sys.path.insert(0, str(
    Path(__file__).resolve().parents[2]
    / "buildbench-starter-kit-0.1.0-rc.2/agents/full-agent"
))
from src import repair_strategy as strategy
from src.contracts import AgentConfig, Budget
from src.loop import LoopRequest
from src.model_adapter import ModelAdapter


def diagnostic(path, marker):
    return NS(resolved=[path], message=marker)


class StrategyTests(unittest.TestCase):
    def test_default_and_invalid_settings(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(strategy.configured_strategy(), "default")
        with patch.dict(os.environ, {"BB_REPAIR_STRATEGY": "typo"}):
            with self.assertRaises(ValueError):
                strategy.configured_strategy()

    def test_selects_three_in_one_file_without_mutation(self):
        items = [
            diagnostic("input/a.c", "a1"),
            diagnostic("input/b.c", "b1"),
            diagnostic("input/a.c", "a2"),
            diagnostic("input/a.c", "a3"),
            diagnostic("input/a.c", "a4"),
        ]
        self.assertEqual(
            [item.message for item in strategy.select_group(items)],
            ["a1", "a2", "a3"],
        )
        self.assertEqual(len(items), 5)

    def test_retained_build_replaces_initial_evidence(self):
        context = NS(log_tail="OLD LOG")
        fresh = diagnostic("input/new.c", "CURRENT ERROR")
        request = NS(
            context=context, current_build=NS(diagnostics=[fresh]),
            latest_attempt=None,
        )
        shown_context, selected, log, total = strategy.opening_inputs(
            request, [diagnostic("input/old.c", "OLD ERROR")], Path("old.log")
        )
        self.assertEqual(selected, [fresh])
        self.assertIsNone(log)
        self.assertEqual(shown_context.log_tail, "")
        self.assertEqual(context.log_tail, "OLD LOG")

    def test_initial_evidence_keeps_initial_log(self):
        context = NS(log_tail="initial")
        request = NS(context=context, current_build=None, latest_attempt=None)
        result = strategy.opening_inputs(request, [], Path("initial.log"))
        self.assertIs(result[0], context)
        self.assertEqual(result[2], Path("initial.log"))

    def test_discarded_attempt_is_not_presented_as_current(self):
        attempt = NS(number=2, applied=1, edits=[1], build=NS(marker="discarded"))
        text = strategy.attempt_summary(attempt, NS(marker="retained"))
        self.assertIn("not retained", text)

    def test_adapter_passes_selected_current_diagnostics(self):
        fresh = diagnostic("input/new.c", "CURRENT ERROR")
        context = NS(log_tail="old")
        request = LoopRequest(
            context=context, current_build=NS(diagnostics=[fresh])
        )
        with patch.dict(os.environ, {"BB_REPAIR_STRATEGY": "small-group"}):
            adapter = ModelAdapter(
                lambda messages: None, context,
                config=AgentConfig(), budget=Budget(),
                diagnostics=[diagnostic("input/old.c", "OLD ERROR")],
                log_path=Path("old.log"),
            )
        self.addCleanup(adapter.close)
        with (
            patch("src.model_adapter.prompts.opening_message", return_value="selected") as opening,
            patch("src.model_adapter.prompts.system_prompt", return_value="system"),
            patch("src.model_adapter.prompts.budget_note", return_value="budget"),
        ):
            messages = adapter.messages(request)
        self.assertEqual(opening.call_args.kwargs["diagnostics"], [fresh])
        self.assertIsNone(opening.call_args.kwargs["log_path"])
        self.assertIn("at most three", messages[0]["content"])
        self.assertEqual(adapter.repair_strategy, "small-group")



    def test_small_group_switches_tools_off_after_two_turns(self):
        from src.contracts import ToolRequest
        from src.model_adapter import TOOLS_OFF_FOLLOW_UP
        import json

        sent = []
        replies = iter([
            json.dumps({
                "action": "tool",
                "requests": [{"tool": "list_dir", "args": {"path": "input"}}],
            }),
            json.dumps({"action": "stop", "reason": "test complete"}),
        ])

        def complete(messages):
            sent.append(messages)
            return next(replies), "stop", {
                "input_tokens": 1, "output_tokens": 1
            }

        context = NS(log_tail="")
        with patch.dict(os.environ, {"BB_REPAIR_STRATEGY": "small-group"}):
            adapter = ModelAdapter(
                complete, context, config=AgentConfig(), budget=Budget(),
                diagnostics=[], log_path=None,
            )
        self.addCleanup(adapter.close)
        adapter._investigating = 2

        with (
            patch("src.model_adapter.prompts.opening_message", return_value=""),
            patch("src.model_adapter.prompts.system_prompt", return_value=""),
            patch("src.model_adapter.prompts.budget_note", return_value=""),
        ):
            result, usage = adapter(LoopRequest(context=context))

        self.assertEqual(result.action, "stop")
        self.assertEqual(len(sent), 2)
        self.assertIn("Tools are switched off", sent[0][-1]["content"])
        self.assertEqual(sent[1][-1]["content"], TOOLS_OFF_FOLLOW_UP)

    def test_default_strategy_keeps_six_tool_turns(self):
        context = NS(log_tail="")
        with patch.dict(os.environ, {"BB_REPAIR_STRATEGY": "default"}):
            adapter = ModelAdapter(
                lambda messages: None, context,
                config=AgentConfig(), budget=Budget(),
                diagnostics=[], log_path=None,
            )
        self.addCleanup(adapter.close)
        self.assertEqual(adapter._tool_turn_limit(), 6)

    def test_small_group_still_allows_tools_before_limit(self):
        import json

        def complete(messages):
            return json.dumps({
                "action": "tool",
                "requests": [{"tool": "list_dir", "args": {"path": "input"}}],
            }), "stop", {"input_tokens": 1, "output_tokens": 1}

        context = NS(log_tail="")
        with patch.dict(os.environ, {"BB_REPAIR_STRATEGY": "small-group"}):
            adapter = ModelAdapter(
                complete, context, config=AgentConfig(), budget=Budget(),
                diagnostics=[], log_path=None,
            )
        self.addCleanup(adapter.close)
        adapter._investigating = 1

        with (
            patch("src.model_adapter.prompts.opening_message", return_value=""),
            patch("src.model_adapter.prompts.system_prompt", return_value=""),
            patch("src.model_adapter.prompts.budget_note", return_value=""),
        ):
            result, usage = adapter(LoopRequest(context=context))

        self.assertEqual(result.action, "tool")
        self.assertEqual(adapter._investigating, 2)


if __name__ == "__main__":
    unittest.main()
