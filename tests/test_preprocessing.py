import pytest

from cpre import (
    AnalysisError, AnalysisOptions, ErrorCode, MacroAssumptions, ParseError,
    PreprocessResult, SourceLocation, compact, preprocess_source,
)


def selected(source, **kwargs):
    result = preprocess_source(source, **kwargs)
    assert result.complete, result.incomplete
    assert isinstance(result, PreprocessResult)
    assert len(result.source) == len(source)
    assert [i for i, char in enumerate(result.source) if char in '\r\n'] == [
        i for i, char in enumerate(source) if char in '\r\n']
    return result.source


def test_nested_elif_and_columns():
    source = ('before\n#if A\nno\n#elif B\n#if C\nno2\n#else\n'
              '  retained();\n#endif\n#else\nno3\n#endif\nafter')
    output = selected(source, assumptions={'A': False, 'B': True, 'C': False})
    assert [line.strip() for line in output.splitlines() if line.strip()] == [
        'before', 'retained();', 'after']
    assert output.splitlines()[7] == '  retained();'


@pytest.mark.parametrize('ending', ['\n', '\r\n', '\r'])
def test_continuations_and_line_endings(ending):
    source = ending.join(['#if A && \\', ' B', 'yes', '#else', 'no', '#endif', ''])
    assert 'yes' in selected(source, assumptions={'A': True, 'B': True})
    assert 'no' not in selected(source, assumptions={'A': True, 'B': True})


def test_empty_last_continuation_is_removed():
    assert selected('#if 1\nx\n#endif \\\n\n').splitlines()[1] == 'x'


@pytest.mark.parametrize('directive', ['ifdef', 'ifndef', 'if defined(X)'])
def test_defined_zero_is_distinct_from_false(directive):
    head = f'#{directive} X' if directive != 'if defined(X)' else '#if defined(X)'
    output = selected(head + '\nyes\n#else\nno\n#endif\n',
                      assumptions=MacroAssumptions(defined=['X'], values={'X': False}))
    assert ('yes' in output) == (directive != 'ifndef')
    assert 'no' in selected('#if X\nyes\n#else\nno\n#endif',
                            assumptions=MacroAssumptions(undefined=['X']))


@pytest.mark.parametrize('condition', ['X', 'defined(X)', 'VERSION >= 4'])
def test_unknown_is_atomic_and_located(condition):
    result = preprocess_source(f'prefix\n#if {condition}\nyes\n#endif', filename='x.c')
    assert not result.complete
    assert result.source is None
    assert result.filename == 'x.c'
    assert result.incomplete[0].code is ErrorCode.UNRESOLVED_CONDITION
    assert result.incomplete[0].location == SourceLocation(2)


def test_defined_only_does_not_assume_nonzero():
    assert not preprocess_source('#if X\nx\n#endif',
                                 assumptions=MacroAssumptions(defined=['X'])).complete


def test_unknown_unreachable_conditions_do_not_block():
    source = '#if 0\n#if UNKNOWN\nx\n#endif\n#elif 1\ny\n#elif UNKNOWN\nz\n#endif'
    assert 'y' in selected(source)


def test_tautology_and_c_macro_semantics():
    assert 'yes' in selected('#if X || !X\nyes\n#endif')
    assert 'no' in selected('#if X && !defined(X)\nyes\n#else\nno\n#endif')


def test_c23_branches():
    source = '#if 0\nx\n#elifdef X\ny\n#elifndef Y\nz\n#endif'
    assert 'z' in selected(source, assumptions=MacroAssumptions(undefined=['X', 'Y']))


@pytest.mark.parametrize('source, code', [
    ('#if (\n#endif', ErrorCode.EXPRESSION_SYNTAX),
    ('#else', ErrorCode.UNMATCHED_DIRECTIVE),
    ('#if 1', ErrorCode.UNTERMINATED_CONDITIONAL),
    ('#ifdef X Y\n#endif', ErrorCode.MALFORMED_MACRO_DIRECTIVE),
    ('#if 0\n#else\n#elif 1\n#endif', ErrorCode.MISPLACED_DIRECTIVE),
])
def test_malformed_raises_structured_error(source, code):
    with pytest.raises(ParseError) as caught:
        preprocess_source(source, filename='bad.c')
    assert caught.value.code is code
    assert caught.value.filename == 'bad.c'
    assert caught.value.location is not None


@pytest.mark.parametrize('directive', ['#include "x.h"', '#include_next <x.h>', '#import "x.h"'])
def test_includes_are_explicitly_unsupported(directive):
    result = preprocess_source(directive + '\n#if X\nx\n#endif', assumptions={'X': True})
    assert not result.complete
    assert result.source is None
    assert result.incomplete[0].code is ErrorCode.UNSUPPORTED_PREPROCESSING_DIRECTIVE
    assert 'ok' in selected('#if 0\n' + directive + '\n#endif\nok')


def test_other_text_is_unchanged_and_empty_input_is_complete():
    for source in ['', 'int x;', '// comment\n#pragma once\nint x;\n']:
        assert selected(source) == source


def test_resource_limit_never_exposes_partial_source():
    result = preprocess_source('#if A || B\nx\n#endif', options=AnalysisOptions(max_atoms=1))
    assert not result.complete
    assert result.source is None
    assert result.incomplete[0].code is ErrorCode.ANALYSIS_LIMIT_EXCEEDED


def test_invalid_options_and_assumptions():
    with pytest.raises(AnalysisError):
        preprocess_source('', assumptions={'X': 1})
    with pytest.raises(AnalysisError):
        preprocess_source('', options=False)


def test_comments_crossing_selection_boundaries_remain_balanced():
    source = '#if 1 /* explanation\ncontinued */ int kept;\n#endif\n'
    output = selected(source)
    assert '/* explanation\ncontinued */ int kept;' in output
    source = '/* explanation\n*/ #if 0\nint removed;\n#endif\nint kept;'
    output = selected(source)
    assert '/* explanation\n*/' in output
    assert 'int removed;' not in output
    assert 'int kept;' in output


@pytest.mark.parametrize('ending', ['\n', '\r\n', '\r'])
@pytest.mark.parametrize('comment', ['/* dead */', '/* dead\ncontinued */'])
def test_comments_wholly_in_discarded_regions_stay_masked(ending, comment):
    source = ('#if 0\n' + comment + '\n#else /* directive only */\n'
              '/* retained */ int kept;\n#endif\n').replace('\n', ending)
    output = selected(source)
    assert 'dead' not in output
    assert 'continued' not in output
    assert 'directive only' not in output
    assert '/* retained */ int kept;' in output
    assert output.count('/*') == output.count('*/') == 1


def test_comments_wholly_in_nested_discarded_branches_stay_masked():
    source = ('#if 1\n#if 0\n/* nested dead */\n#endif\n'
              '/* retained */\n#else\n/* other dead */\n#endif')
    output = selected(source)
    assert 'dead' not in output
    assert output.count('/*') == output.count('*/') == 1
    assert '/* retained */' in output


def test_line_comment_does_not_hide_directives_after_carriage_return():
    source = '// heading\r#if 0\r/* dead */\r#endif\rint kept;'
    output = selected(source)
    assert output.startswith('// heading\r')
    assert 'dead' not in output
    assert '#if' not in output
    assert output.endswith('int kept;')


def test_removed_lines_are_immutable_and_only_mark_wholly_masked_lines():
    source = '#if 0\ndead\n#else\nkept\n#endif\n'
    result = preprocess_source(source)
    assert result.complete
    assert result.removed_lines == frozenset({1, 2, 3, 5})
    assert isinstance(result.removed_lines, frozenset)


def test_compact_is_explicit_and_preserves_retained_blank_lines():
    source = '#if 0\ndead\n#endif\n\nkept\n'
    result = preprocess_source(source)
    assert result.complete
    assert result.source == '     \n    \n      \n\nkept\n'
    assert compact(result) == '\nkept\n'
    assert compact(result, max_consecutive_blank_lines=1) == '\n\nkept\n'


def test_retained_blank_line_breaks_removed_line_collapse_run():
    source = '#if 0\ndead\n#endif\n\n#if 0\ndead2\n#endif\nkept\n'
    result = preprocess_source(source)
    assert result.complete
    assert compact(result, max_consecutive_blank_lines=1) == '\n\n\nkept\n'


@pytest.mark.parametrize('ending', ['\n', '\r\n', '\r'])
def test_compact_preserves_kept_line_endings_and_no_final_newline(ending):
    source = ending.join(['#if 0', 'dead', '#endif', 'kept'])
    result = preprocess_source(source)
    assert result.complete
    assert compact(result) == 'kept'
    assert compact(result, max_consecutive_blank_lines=1) == ending + 'kept'


def test_compact_rejects_negative_limit_and_incomplete_result():
    result = preprocess_source('#if 0\ndead\n#endif\nkept\n')
    with pytest.raises(ValueError, match='non-negative'):
        compact(result, max_consecutive_blank_lines=-1)

    incomplete = preprocess_source('#if UNKNOWN\nmaybe\n#endif\n')
    assert not incomplete.complete
    assert incomplete.removed_lines is None
    with pytest.raises(ValueError, match='complete PreprocessResult'):
        compact(incomplete)


def test_restored_block_comment_lines_are_not_reported_as_removed():
    source = '#if 1 /* explanation\ncontinued */ int kept;\n#endif\n'
    result = preprocess_source(source)
    assert result.complete
    assert 1 not in result.removed_lines
    assert 2 not in result.removed_lines
    assert 3 in result.removed_lines
    assert compact(result) == ''.join(result.source.splitlines(keepends=True)[:2])


def test_compact_leaves_expanded_retained_output_unchanged():
    source = '#define VALUE 123\n\nint value = VALUE;\n'
    result = preprocess_source(source)
    assert result.complete
    assert result.removed_lines == frozenset({1})
    assert compact(result) == '\nint value = 123;\n'


def test_canonical_and_compact_forms_have_equivalent_nonblank_content():
    source = '#if 0\ndead\n#else\nkept\n#endif\n\nnext\n'
    result = preprocess_source(source)
    canonical = [line for line in result.source.splitlines() if line.strip()]
    compacted = [line for line in compact(result).splitlines() if line.strip()]
    assert canonical == compacted == ['kept', 'next']
