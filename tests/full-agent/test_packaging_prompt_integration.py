"""Verify evidence reaches the actual completion callback without live API calls."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] /
    'buildbench-starter-kit-0.1.0-rc.5/agents/full-agent'))
from src.main import run_agent
from src.contracts import AgentConfig, Budget
from src.types import CaseContext

class PackagingPromptIntegration(unittest.TestCase):
    def capture(self, enabled, evidence=True):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name,text in {
                'debian/rules':'override_dh_auto_build-indep:\n\tpython3 setup.py old_docs\n',
                'debian/example-doc.install':'manual/generated/html usr/share/doc/example\n',
                'setup.cfg':'[build_docs]\nsource-dir = manual\n',
                'manual/Makefile':'OUT = generated\nhtml:\n\tdocbuilder -b html . $(OUT)/html\n',
            }.items():
                path=root/'input'/name
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(text)
            log=root/'initial.log'
            log.write_text("error: invalid command 'old_docs'\n")
            captured=[]
            def complete(messages):
                captured.append(messages)
                return '{"action":"stop","reason":"integration probe"}', 'stop', {}
            with patch.dict(os.environ, {'BB_EXPERIMENT_PACKAGING_CONTEXT':str(enabled)}, clear=True):
                run_agent(CaseContext('synthetic',root,'',{'target_arch':'aarch64'}),
                          log,complete,config=AgentConfig(use_evidence_manager=evidence,
                          use_tools=True,use_build_feedback=False), budget=Budget())
            self.assertGreaterEqual(len(captured),1)
            return captured[0][1]['content']
    def test_enabled_context_reaches_completion_callback(self):
        message=self.capture(1)
        self.assertIn('input/debian/example-doc.install:1:',message)
        self.assertIn('manual/generated/html',message)
        self.assertIn('input/manual/Makefile:3:',message)
        self.assertIn('docbuilder -b html',message)
    def test_disabled_context_is_not_injected(self):
        self.assertNotIn('manual/generated/html',self.capture(0))
    def test_evidence_manager_off_does_not_inject_context(self):
        self.assertNotIn('Packaging output requirements',self.capture(1,False))
