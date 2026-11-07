"""Source-contract analysis and no-build integration; no model/network needed."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] /
                      'buildbench-starter-kit-0.1.0-rc.5/agents/full-agent'))
from src import ffi_analysis as ffi
from src.contracts import AgentConfig, Budget, ModelTurn
from src.types import CaseContext, Edit
from src.loop import run_loop
from src.model_adapter import ModelAdapter

ROOT = '''extern "C" {
    fn library_open(path: *const std::ffi::c_char);
}
pub unsafe fn wrapper(path: *const i8) { library_open(path); }
fn unrelated(samples: *const i8) {}
'''
CALLER = 'pub unsafe fn caller(path: *const i8) { crate::ffi::wrapper(path); }\n'


class FFIAnalysis(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write('ffi.rs', ROOT)
        self.write('caller.rs', CALLER)

    def write(self, name, text):
        path = self.root / 'input/src' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def fixed(self):
        return {
            'input/src/ffi.rs': ROOT.replace('wrapper(path: *const i8)',
                                            'wrapper(path: *const std::ffi::c_char)'),
            'input/src/caller.rs': CALLER.replace('*const i8', '*const std::ffi::c_char'),
        }

    def test_scope_includes_unreported_caller_excludes_byte_buffer(self):
        analysis = ffi.analyze(self.root)
        self.assertEqual({key[1] for key in analysis.obligations},
                         {'library_open', 'wrapper', 'caller'})
        self.assertEqual(analysis.report(analysis.original, 'original')['counts'],
                         {'satisfied': 1, 'remaining': 2, 'unknown': 0})

    def test_partial_and_complete_simulated_repair(self):
        analysis = ffi.analyze(self.root)
        fixed = self.fixed()
        partial = ffi.index_sources(self.root, {'input/src/ffi.rs': fixed['input/src/ffi.rs']})
        self.assertEqual(analysis.report(partial, 'partial')['counts']['remaining'], 1)
        report = analysis.report(ffi.index_sources(self.root, fixed), 'complete')
        self.assertEqual(report['counts'], {'satisfied': 3, 'remaining': 0, 'unknown': 0})
        self.assertFalse(report['build_validated'])
        self.assertEqual((self.root / 'input/src/ffi.rs').read_text(), ROOT)

    def test_deleting_call_or_changing_foreign_contract_is_not_success(self):
        analysis = ffi.analyze(self.root)
        fixed = self.fixed()
        fixed['input/src/ffi.rs'] = fixed['input/src/ffi.rs'].replace('library_open(path);', '')
        self.assertGreater(analysis.report(ffi.index_sources(self.root, fixed), '')['counts']['unknown'], 0)
        altered = {'input/src/ffi.rs': ROOT.replace('std::ffi::c_char', 'i8')}
        self.assertEqual(analysis.report(ffi.index_sources(self.root, altered), '')['counts']['unknown'], 1)
        (self.root / 'input/src/caller.rs').unlink()
        self.assertGreater(analysis.report(ffi.index_sources(self.root), '')['counts']['unknown'], 0)

    def test_comments_do_not_create_contracts(self):
        self.write('ffi.rs', '// ' + ROOT.replace('\n', '\n// '))
        self.assertFalse(ffi.analyze(self.root).obligations)

    def test_duplicate_declarations_are_unknown(self):
        self.write('ffi.rs', ROOT + ROOT)
        analysis = ffi.analyze(self.root)
        self.assertFalse(analysis.obligations)
        self.assertTrue(any('ambiguous' in note for note in analysis.original.unknown))

    def test_macro_shadowing_and_casts_do_not_make_confirmed_edges(self):
        for body in ('invoke!(path);', 'let path = other; library_open(path);',
                     'library_open(path as *const std::ffi::c_char);'):
            with self.subTest(body=body):
                self.write('ffi.rs', ROOT.replace('library_open(path);', body))
                analysis = ffi.analyze(self.root)
                self.assertEqual({key[1] for key in analysis.obligations}, {'library_open'})
                self.assertTrue(analysis.original.unknown)

    def test_cfg_and_unknown_alias_do_not_establish_contract(self):
        for source in ('#[cfg(target_arch="x86_64")]\n' + ROOT,
                       ROOT.replace('std::ffi::c_char', 'mystery::c_char')):
            self.write('ffi.rs', source)
            self.assertFalse(ffi.analyze(self.root).obligations)

    def test_explicit_c_char_import_supported(self):
        self.write('ffi.rs', 'use core::ffi::c_char;\n' + ROOT.replace('std::ffi::c_char', 'c_char'))
        self.assertEqual(len(ffi.analyze(self.root).obligations), 3)

    def test_function_local_import_does_not_resolve_foreign_alias(self):
        self.write('ffi.rs', ROOT.replace('std::ffi::c_char', 'c_char') +
                   '\nfn other() { use std::ffi::c_char; }\n')
        self.assertFalse(ffi.analyze(self.root).obligations)

    def test_explicit_signed_byte_foreign_interface_is_not_a_char_contract(self):
        self.write('ffi.rs', ROOT.replace('std::ffi::c_char', 'i8'))
        self.assertFalse(ffi.analyze(self.root).obligations)

    def test_assignment_cannot_create_false_parameter_flow(self):
        self.write('ffi.rs', ROOT.replace('library_open(path);',
                                         'path = other; library_open(path);'))
        analysis = ffi.analyze(self.root)
        self.assertEqual({key[1] for key in analysis.obligations}, {'library_open'})
        self.assertTrue(analysis.original.unknown)

    def test_symlink_and_scan_limits_report_unknown(self):
        outside = self.root / 'outside.rs'
        outside.write_text(ROOT)
        (self.root / 'input/src/link.rs').symlink_to(outside)
        self.assertTrue(any('nonregular' in s for s in ffi.analyze(self.root).original.unknown))
        with patch.object(ffi, 'MAX_FILES', 1):
            self.assertTrue(any('limit' in s for s in ffi.analyze(self.root).original.unknown))

    def test_actual_partial_source_is_measured(self):
        analysis = ffi.analyze(self.root)
        self.write('ffi.rs', self.fixed()['input/src/ffi.rs'])
        report = analysis.report(ffi.index_sources(self.root), 'actual')
        self.assertEqual(report['counts']['remaining'], 1)
        self.assertNotEqual(analysis.original.hashes['input/src/ffi.rs'],
                            report['source_hashes']['input/src/ffi.rs'])

    def test_grouped_renamed_and_chained_type_aliases(self):
        self.write('ffi.rs', '''
use std::ffi::{c_char as Char, CStr as Text};
type Character = Char;
type PathPtr = *const Character;
extern "C" { fn library_open(path: PathPtr); }
unsafe fn wrapper(path: *const i8) { library_open(path); }
''')
        analysis = ffi.analyze(self.root)
        self.assertEqual(len(analysis.obligations), 3)
        self.assertEqual(analysis.report(analysis.original, '')['counts']['remaining'], 2)

    def test_cross_file_type_and_function_import_aliases(self):
        self.write('types.rs', 'pub type Char = std::ffi::c_char;\npub type Ptr = *const Char;')
        self.write('ffi.rs', '''
use crate::types::Ptr;
extern "C" { pub fn library_open(path: Ptr); }
''')
        self.write('caller.rs', '''
use crate::ffi::{self as bindings, library_open as raw_open};
unsafe fn caller(path: *const i8) { raw_open(path); bindings::library_open(path); }
''')
        analysis = ffi.analyze(self.root)
        self.assertEqual({key[1] for key in analysis.obligations}, {'library_open', 'caller'})
        self.assertEqual(analysis.report(analysis.original, '')['counts']['remaining'], 1)
        self.assertTrue(any('input/src/types.rs:1' in evidence
                            for evidence in analysis.original.type_evidence))

    def test_duplicate_and_cyclic_aliases_do_not_establish_contracts(self):
        for prefix in ('type Char = Other; type Other = Char;',
                       'type Char = std::ffi::c_char; type Char = i8;',
                       'use std::ffi::c_char as Char; use other::Char;'):
            with self.subTest(prefix=prefix):
                self.write('ffi.rs', prefix + ROOT.replace('std::ffi::c_char', 'Char'))
                analysis = ffi.analyze(self.root)
                self.assertFalse(analysis.obligations)
                self.assertTrue(analysis.original.unknown)

    def test_alias_change_cannot_erase_original_contract(self):
        self.write('ffi.rs', 'type Char = std::ffi::c_char;\n' + ROOT.replace('std::ffi::c_char', 'Char'))
        analysis = ffi.analyze(self.root)
        original = (self.root / 'input/src/ffi.rs').read_text()
        current = ffi.index_sources(self.root, {'input/src/ffi.rs': original.replace(
            'type Char = std::ffi::c_char;', 'type Char = i8;')})
        self.assertEqual(analysis.report(current, '')['counts']['unknown'], 1)

    def test_pointer_returns_propagate_through_wrappers(self):
        source = '''
extern "C" { fn get_name() -> *const std::ffi::c_char; }
unsafe fn wrapper() -> *const i8 { get_name() }
unsafe fn caller() -> *const i8 { return wrapper(); }
fn unrelated() -> *const i8 { bytes() }
'''
        self.write('ffi.rs', source)
        self.write('caller.rs', '')
        analysis = ffi.analyze(self.root)
        self.assertEqual({key[1] for key in analysis.obligations}, {'get_name', 'wrapper', 'caller'})
        self.assertTrue(all(key[2] == -1 for key in analysis.obligations))
        self.assertEqual(analysis.report(analysis.original, '')['counts']['remaining'], 2)
        fixed = source.replace('wrapper() -> *const i8', 'wrapper() -> *const std::ffi::c_char')
        report = analysis.report(ffi.index_sources(self.root, {'input/src/ffi.rs': fixed}), '')
        self.assertEqual(report['counts']['remaining'], 1)
        fixed = fixed.replace('caller() -> *const i8', 'caller() -> *const std::ffi::c_char')
        self.assertEqual(analysis.report(ffi.index_sources(self.root, {'input/src/ffi.rs': fixed}),
                                         '')['counts'], {'satisfied': 3, 'remaining': 0, 'unknown': 0})

    def test_return_call_deletion_remains_unknown(self):
        self.write('ffi.rs', '''extern "C" { fn get_name() -> *const std::ffi::c_char; }
unsafe fn wrapper() -> *const i8 { get_name() }''')
        analysis = ffi.analyze(self.root)
        self.write('ffi.rs', '''extern "C" { fn get_name() -> *const std::ffi::c_char; }
unsafe fn wrapper() -> *const std::ffi::c_char { other() }''')
        self.assertEqual(analysis.report(ffi.index_sources(self.root), '')['counts']['unknown'], 1)

    def test_cstr_consumption_anchors_a_return_type(self):
        self.write('ffi.rs', '''
unsafe fn wrapper() -> *const i8 { unknown_external() }
unsafe fn caller() { std::ffi::CStr::from_ptr(wrapper()); }
''')
        self.write('caller.rs', '')
        analysis = ffi.analyze(self.root)
        self.assertIn(('input/src/ffi.rs', 'wrapper', -1), analysis.obligations)
        self.assertEqual(analysis.report(analysis.original, '')['counts']['remaining'], 1)

    def test_cstr_pattern_with_let_initializer_and_shadowing(self):
        # Synthetic fixture based on the CStr call shape in the archived record;
        # not a reconstruction of the full rust-subversion package.
        self.write('ffi.rs', '''
use std::ffi::{CStr as Text};
unsafe fn decode(path: *const i8) {
    let unrelated = 42;
    let path: &str = Text::from_ptr(path).to_str().unwrap();
    consume(path);
}
''')
        self.write('caller.rs', '')
        analysis = ffi.analyze(self.root)
        self.assertIn(('input/src/ffi.rs', 'decode', 0), analysis.obligations)
        report = analysis.report(analysis.original, '')
        self.assertEqual(report['counts']['remaining'], 1)
        self.assertEqual(len(report['standard_contracts']), 1)
        self.assertFalse(report['build_validated'])

    def test_cstr_is_not_guessed_from_an_unresolved_name(self):
        for prefix in ('', 'use mystery::CStr;', 'struct CStr;'):
            with self.subTest(prefix=prefix):
                self.write('ffi.rs', prefix + 'unsafe fn decode(path: *const i8) { CStr::from_ptr(path); }')
                self.write('caller.rs', '')
                self.assertFalse(ffi.analyze(self.root).obligations)

    def test_simple_immutable_local_aliases_follow_parameters_and_returns(self):
        self.write('ffi.rs', '''
extern "C" {
    fn library_open(path: *const std::ffi::c_char);
    fn get_name() -> *const std::ffi::c_char;
}
unsafe fn wrapper(path: *const i8) { let alias = path; library_open(alias); }
unsafe fn caller() -> *const i8 { let first = get_name(); let second = first; second }
''')
        self.write('caller.rs', '')
        analysis = ffi.analyze(self.root)
        self.assertIn(('input/src/ffi.rs', 'wrapper', 0), analysis.obligations)
        self.assertIn(('input/src/ffi.rs', 'caller', -1), analysis.obligations)

    def test_mutated_and_typed_locals_are_not_inferred(self):
        for body in ('let alias = path; alias = other; library_open(alias);',
                     'let alias: *const i8 = path; library_open(alias);',
                     'let mut alias = path; library_open(alias);'):
            with self.subTest(body=body):
                self.write('ffi.rs', ROOT.replace('library_open(path);', body))
                analysis = ffi.analyze(self.root)
                self.assertNotIn(('input/src/ffi.rs', 'wrapper', 0), analysis.obligations)

    def test_harmless_attributes_raw_strings_and_character_literals(self):
        source = '''
#[doc = "FFI"]
extern "C" { fn library_open(path: *const std::ffi::c_char); }
#[inline]
unsafe fn wrapper(path: *const i8) {
    let noise = r###"extern "C" { fn fake(p: *const std::ffi::c_char); }"###;
    let brace = '{';
    library_open(path);
}
'''
        self.write('ffi.rs', source)
        analysis = ffi.analyze(self.root)
        self.assertEqual({key[1] for key in analysis.obligations}, {'library_open', 'wrapper', 'caller'})

    def test_missing_generated_bindings_are_explicit(self):
        self.write('ffi.rs', '''include!(concat!(env!("OUT_DIR"), "/bindings.rs"));
unsafe fn wrapper(path: *const i8) { unknown_generated(path); }''')
        analysis = ffi.analyze(self.root)
        self.assertFalse(analysis.obligations)
        self.assertTrue(any('unavailable bindings' in note for note in analysis.original.unknown))

    def test_self_super_and_mod_rs_import_resolution(self):
        self.write('types/mod.rs', 'pub type Char = core::ffi::c_char;')
        self.write('types/ffi.rs', '''use super::Char;
extern "C" { pub fn open(path: *const Char); }
unsafe fn wrapper(path: *const i8) { self::open(path); }''')
        analysis = ffi.analyze(self.root)
        self.assertIn(('input/src/types/ffi.rs', 'wrapper', 0), analysis.obligations)

    def test_returned_parameter_receives_downstream_char_requirement(self):
        self.write('ffi.rs', '''
unsafe fn identity(path: *const i8) -> *const i8 { path }
unsafe fn caller(path: *const i8) { std::ffi::CStr::from_ptr(identity(path)); }
''')
        self.write('caller.rs', '')
        analysis = ffi.analyze(self.root)
        for key in [('identity', -1), ('identity', 0), ('caller', 0)]:
            self.assertIn(('input/src/ffi.rs', *key), analysis.obligations)

    def test_ambiguous_module_files_do_not_guess_import_target(self):
        self.write('ffi/mod.rs', ROOT)
        analysis = ffi.analyze(self.root)
        self.assertNotIn(('input/src/caller.rs', 'caller', 0), analysis.obligations)
        self.assertTrue(any('unresolved call' in note for note in analysis.original.unknown))

    def test_import_and_local_function_collision_is_unresolved(self):
        self.write('caller.rs', '''
use crate::ffi::wrapper;
fn wrapper(path: *const i8) {}
unsafe fn caller(path: *const i8) { wrapper(path); }
''')
        self.assertNotIn(('input/src/caller.rs', 'caller', 0), ffi.analyze(self.root).obligations)

    def test_cstr_cast_cannot_hide_an_existing_obligation(self):
        source = 'unsafe fn decode(path: *const i8) { std::ffi::CStr::from_ptr(path); }'
        self.write('ffi.rs', source)
        self.write('caller.rs', '')
        analysis = ffi.analyze(self.root)
        after = source.replace('from_ptr(path)', 'from_ptr(path as *const std::ffi::c_char)')
        report = analysis.report(ffi.index_sources(self.root, {'input/src/ffi.rs': after}), '')
        self.assertEqual(report['counts']['unknown'], 1)
        self.assertTrue(any('casts' in note for note in report['unknown']))

    def test_generated_source_present_in_worktree_is_indexed(self):
        self.write('generated.rs', '''extern "C" { pub fn raw(path: *const core::ffi::c_char); }''')
        self.write('caller.rs', '''unsafe fn caller(path: *const i8) { crate::generated::raw(path); }''')
        self.assertIn(('input/src/caller.rs', 'caller', 0), ffi.analyze(self.root).obligations)

    def test_body_limits_leave_explicit_unknowns(self):
        from src import ffi_flows
        with patch.object(ffi_flows, 'MAX_BODY_TOKENS', 1):
            analysis = ffi.analyze(self.root)
            self.assertTrue(any('flow scan limit' in note for note in analysis.original.unknown))
            self.assertNotIn(('input/src/caller.rs', 'caller', 0), analysis.obligations)

    def test_offline_cli_emits_a_report_without_modifying_source(self):
        import json
        import subprocess
        agent = Path(__file__).resolve().parents[2] / 'buildbench-starter-kit-0.1.0-rc.5/agents/full-agent'
        result = subprocess.run([sys.executable, '-m', 'src.ffi_analysis', '--worktree', str(self.root)],
                                cwd=agent, capture_output=True, text=True, check=True)
        report = json.loads(result.stdout)
        self.assertEqual(report['analysis_mode'], 'ffi-contracts-v2')
        self.assertEqual(report['counts']['remaining'], 2)
        self.assertFalse(report['build_validated'])
        self.assertEqual((self.root / 'input/src/ffi.rs').read_text(), ROOT)

    @patch.dict(os.environ, {'BB_FFI_ANALYSIS': '1'})
    def test_loop_reviews_applies_reports_and_never_builds(self):
        requests = []
        edits = [Edit(path, (ROOT if path.endswith('ffi.rs') else CALLER), text)
                 for path, text in self.fixed().items()]
        turns = iter([ModelTurn('edit', [], 'FFI', [], edits, ''),
                      ModelTurn('approve', [], '', [], [], 'consistent'),
                      ModelTurn('stop', [], '', [], [], 'submitted')])
        def next_turn(request):
            requests.append(request)
            return next(turns), {'input_tokens': 1, 'output_tokens': 1}
        class ForbiddenBuild:
            def request_build(self, *args):
                raise AssertionError('No build is allowed')
        result = run_loop(CaseContext('ffi', self.root, '', {}), next_turn=next_turn,
                          config=AgentConfig(use_build_feedback=False),
                          build_feedback=ForbiddenBuild(), max_turns=5)
        self.assertIn('SIMULATED proposed source', requests[1].proposal_review)
        self.assertIn('actual applied source', requests[2].feedback)
        final = result.review_events[-1]
        self.assertEqual(final['event'], 'ffi_final')
        self.assertEqual(final['analysis_mode'], 'ffi-contracts-v2')
        self.assertEqual(final['coverage']['counts']['satisfied'], 3)
        self.assertFalse(final['coverage']['build_validated'])

    @patch.dict(os.environ, {'BB_FFI_ANALYSIS': '1'})
    def test_adapter_supplies_current_contracts(self):
        from src.loop import LoopRequest
        context = CaseContext('ffi', self.root, '', {})
        adapter = ModelAdapter(lambda _: None, context, config=AgentConfig(use_build_feedback=False),
                               budget=Budget(), diagnostics=[], log_path=None)
        self.addCleanup(adapter.close)
        message = adapter.messages(LoopRequest(context))[1]['content']
        self.assertIn('caller parameter 0', message)
        self.assertIn('remaining', message)
        self.assertNotIn('with replace_all: true', message)

    @patch.dict(os.environ, {'BB_FFI_ANALYSIS': '1'})
    def test_enabled_build_feedback_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'USE_BUILD_FEEDBACK=0'):
            run_loop(CaseContext('ffi', self.root, '', {}), next_turn=lambda _: None,
                     build_feedback=object())


if __name__ == '__main__':
    unittest.main()
