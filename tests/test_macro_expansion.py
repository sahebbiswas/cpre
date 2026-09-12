import re

import pytest

from cpre import (AnalysisOptions, ErrorCode, MacroAssumptions, SourceLocation,
                  SourceMapping, preprocess_source)


def output(source, **kwargs):
    result = preprocess_source(source, **kwargs)
    assert result.complete, result.incomplete
    return result.source


def test_numeric_alias_expression_redefinition_undef_and_inactive_state():
    source = ('BEFORE\n#define N 42\n#define ALIAS N\n#define EXPR (ALIAS + 2)\n'
              'int x = EXPR;\n#define N 7\nALIAS\n#if 0\n#define N 9\n'
              '#undef ALIAS\nN\n#endif\nALIAS\n#undef N\nALIAS N\n')
    assert [line.strip() for line in output(source).splitlines() if line.strip()] == [
        'BEFORE', 'int x =  ( 42 + 2 ) ;', '7', '7', 'N  N']


@pytest.mark.parametrize('definitions, text, expected', [
    ('#define A A', 'A A', 'A A'),
    ('#define A B\n#define B A', 'A B', 'A B'),
    ('#define A A + 1', 'A', 'A + 1'),
    ('#define A B + B\n#define B 2', 'A A', '2 + 2 2 + 2'),
    ('#define E', '+E+', '+ +'),
    ('#define P +', 'P+', '+ +'),
    ('#define P ++', 'P', '++'),
    ('#define A (x << 2) != 0 && y->z', 'A', '( x << 2 ) != 0 && y -> z'),
])
def test_recursive_empty_and_token_boundaries(definitions, text, expected):
    assert ' '.join(output(definitions + '\n' + text).split()) == expected


def test_comments_literals_numbers_and_identifier_boundaries():
    text = ('/* X\n X */ "X \\\" X" \'X\' // X\n'
            'L"X" u8"X" R"tag(X " X)tag" Xsuffix 0xX 1e+X 1\'024 X')
    rendered = output('#define X 17\n#define L 3\n#define u8 4\n' + text)
    assert rendered.endswith(text[:-1] + ' 17 ')


def test_replacement_literals_and_comments():
    rendered = output('#define S "http://example/*X*/"\n#define X 9\n#define E X/**/+ X\nS E')
    assert rendered.strip() == '"http://example/*X*/"   9 + 9'


@pytest.mark.parametrize('ending', ['\n', '\r\n', '\r'])
def test_continuations_and_invocation_source_mapping(ending):
    source = '#define LONG 123456\nint x = LO\\\nNG + LONG;\n'.replace('\n', ending)
    result = preprocess_source(source, filename='test.c')
    assert result.complete
    assert re.findall(r'\r\n|\r|\n', result.source) == re.findall(r'\r\n|\r|\n', source)
    cursor = 0
    expansions = []
    for mapping in result.source_map:
        assert isinstance(mapping, SourceMapping)
        assert mapping.output_start == cursor
        cursor = mapping.output_end
        if mapping.expanded:
            expansions.append(mapping)
            assert '123456' in result.source[mapping.output_start:mapping.output_end]
        else:
            assert mapping.output_end - mapping.output_start == mapping.source_end - mapping.source_start
    assert cursor == len(result.source)
    assert len(expansions) == 2
    assert expansions[0].start == SourceLocation(2, 9)
    assert expansions[0].end == SourceLocation(3, 3)
    assert source[expansions[0].source_start:expansions[0].source_end] == 'LO\\' + ending + 'NG'
    assert expansions[1].start == SourceLocation(3, 6)


def test_line_comment_splicing_does_not_expand_hidden_identifier():
    assert output('#define X 7\n// comment \\\nX\nX').endswith('// comment \\\nX\n 7 ')


@pytest.mark.parametrize('definition, use, expected', [
    ('#define A x ## y', 'A', 'xy'),
    ('#define A #x', 'A', '# x'),
    ('#define A x %:%: y', 'A', 'xy'),
])
def test_object_macro_operator_tokens_expand(definition, use, expected):
    assert output(definition + '\n' + use).strip() == expected
    assert output(definition + '\n#if 0\n' + use + '\n#endif').strip() == ''


def test_bare_function_macro_and_inactive_invocation_are_safe():
    assert output('#define F(x) x\nF\n#if 0\n(1)\n#endif').strip() == 'F'


@pytest.mark.parametrize('assumptions', [{'A': True}, {'A': False}, MacroAssumptions(defined=['A'])])
def test_assumptions_do_not_supply_replacement_tokens(assumptions):
    result = preprocess_source('A', assumptions=assumptions)
    assert result.incomplete[0].code is ErrorCode.UNSUPPORTED_MACRO_EXPANSION
    assert output('#undef A\nA', assumptions=assumptions).strip() == 'A'
    assert output('#define A 0\nA', assumptions=assumptions).strip() == '0'


def test_no_python_recursion_limit_and_bounded_exponential_expansion():
    source = ''.join(f'#define A{i} A{i+1}\n' for i in range(1100)) + '#define A1100 1\nA0'
    assert output(source).strip() == '1'
    source = ''.join(f'#define A{i} A{i+1} A{i+1}\n' for i in range(25)) + '#define A25 1\nA0'
    first = preprocess_source(source, options=AnalysisOptions(max_work=1000))
    second = preprocess_source(source, options=AnalysisOptions(max_work=1000))
    assert first == second
    assert first.source is first.source_map is first.macros is None
    assert first.incomplete[0].code is ErrorCode.ANALYSIS_LIMIT_EXCEEDED


def test_empty_and_unexpanded_maps():
    assert preprocess_source('').source_map == ()
    source = 'int unchanged;\n'
    result = preprocess_source(source)
    assert result.source_map == (SourceMapping(0, len(source), 0, len(source),
                                              SourceLocation(1, 1), SourceLocation(2, 1)),)


def test_replacement_token_splicing_does_not_insert_spaces():
    assert output('#define N 1\\\n2\n#define A LO\\\nNG\n#define LONG N\nA').strip() == '12'


def test_large_repeated_replacement_is_work_bounded():
    source = '#define A "' + 'x' * 2000 + '"\nA A A'
    result = preprocess_source(source, options=AnalysisOptions(max_work=3000))
    assert result.source is None
    assert result.incomplete[0].code is ErrorCode.ANALYSIS_LIMIT_EXCEEDED
