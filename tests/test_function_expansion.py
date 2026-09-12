import re
import shutil
import subprocess

import pytest

from cpre import AnalysisOptions, ErrorCode, SourceLocation, preprocess_source
from cpre.expansion import tokenize


def spellings(source):
    return [token.text for token in tokenize(source) if token.kind not in {'space', 'comment'}]


CASES = [
    ('#define SUM(a,b) ((a)+(b))', 'SUM(2,3)', '((2)+(3))'),
    ('#define SET(x,y) do { (x) = (y); } while (0)', 'SET(a, f(1, 2));',
     'do { (a) = (f(1,2)); } while (0);'),
    ('#define F(x) (x+x)', 'F(F(1))', '((1+1)+(1+1))'),
    ('#define F(x) x\n#define A F', 'A /*c*/\n(3)', '3'),
    ('#define F(x) x\n#define A prefix F', 'A(3)', 'prefix 3'),
    ('#define F(x) x\n#define A F(3)', 'A', '3'),
    ('#define F(x) x\n#define OPEN (', 'F\nOPEN 1)', 'F(1)'),
    ('#define F(x) x\n#define VALUE 7', 'F(VALUE)', '7'),
    ('#define F(a,b) a b', 'F((1,2),g(3,(4,5)))', '(1,2) g(3,(4,5))'),
    ('#define F(a,b) a b', 'F(,)', ''),
    ('#define F(a,b) a b', 'F(,2)', '2'),
    ('#define F(x) x', 'F()', ''),
    ('#define F() 42', 'F(/* empty */)', '42'),
    ('#define F(x) F(x)', 'F(1)', 'F(1)'),
    ('#define F(x) G(x)\n#define G(x) F(x)', 'F(1) G(2)', 'F(1) G(2)'),
    ('#define F(x) x', 'F(F)(1)', 'F(1)'),
    ('#define F(x) x\n#define G F', 'G(G)(1)', 'F(1)'),
    ('#define F(x) x\n#define A A+1', 'F(A)', 'A+1'),
    ('#define F(x) x\n#define A F', 'F(A)(3)', 'F(3)'),
    ('#define F(x) x\n#define G() F', 'G()(3)', '3'),
    ('#define F(x) x\n#define PAIR 1,2', 'F(PAIR)', '1,2'),
    ('#define FIRST(a,b) a\n#define BAD(x) #x', 'FIRST(1,BAD(2))', '1'),
    ('#define ID(x) x\n#define L 9', 'ID(L"L")', 'L"L"'),
    ('#define F(x) x+x', 'F("a,b()")', '"a,b()"+"a,b()"'),
    ('#define F(x) x', "F(',')", "','"),
    ('#define F(x) x', 'F(R"tag(a,b(()))tag")', 'R"tag(a,b(()))tag"'),
    ('#define F(x) x', 'F', 'F'),
    ('#define F(...) call(__VA_ARGS__)', 'F(1,g(2,3),4)', 'call(1,g(2,3),4)'),
    ('#define F(...) call(__VA_ARGS__)', 'F()', 'call()'),
    ('#define F(x,...) call(x,__VA_ARGS__)', 'F(1,)', 'call(1,)'),
    ('#define F(x,...) call(x,__VA_ARGS__)\n#define N 2', 'F(1,N,3)', 'call(1,2,3)'),
    ('#define F(a,b) ((a) + \\\n (b))', 'F(1,\n2)', '((1)+(2))'),
    ('#define S(x) #x', 'S(hello   world)', '"hello world"'),
    ('#define S(x) %: x', 'S(a+b)', '"a+b"'),
    ('#define CAT(a,b) a ## b', 'CAT(pre,fix)', 'prefix'),
    ('#define CAT(a,b) a %:%: b', 'CAT(+,=)', '+='),
    ('#define CAT(a,b) a ## b', 'CAT(,tail) CAT(head,)', 'tail head'),
    ('#define AB a ## b', 'AB', 'ab'),
    ('#define X 7\n#define CAT(a,b) a##b\n#define X7 9', 'CAT(X,7)', '9'),
    ('#define X 7\n#define CAT(a,b) a##b\n#define XCAT(a,b) CAT(a,b)', 'XCAT(X,7)', '77'),
    ('#define S(x) #x\n#define X 7\n#define XS(x) S(x)', 'S(X) XS(X)', '"X" "7"'),
    ('#define V(...) #__VA_ARGS__', 'V(a,b,c)', '"a,b,c"'),
    ('#define V(prefix,...) prefix ## __VA_ARGS__', 'V(foo,bar) V(foo,)', 'foobar foo'),
]

VA_OPT_CASES = [
    ('#define F(...) f(0 __VA_OPT__(,) __VA_ARGS__)', 'F()', 'f(0)'),
    ('#define F(...) f(0 __VA_OPT__(,) __VA_ARGS__)', 'F(a,b)', 'f(0,a,b)'),
    ('#define F(x,...) f(x __VA_OPT__(,) __VA_ARGS__)', 'F(1,)', 'f(1)'),
    ('#define F(x,...) f(x __VA_OPT__(,) __VA_ARGS__)', 'F(1,2,3)', 'f(1,2,3)'),
    ('#define EMP\n#define F(...) f(0 __VA_OPT__(,) __VA_ARGS__)', 'F(EMP)', 'f(0)'),
    ('#define ID(x) x\n#define F(...) __VA_OPT__(ID(__VA_ARGS__))', 'F(7)', '7'),
    ('#define F(...) __VA_OPT__(((__VA_ARGS__)))', 'F(x)', '((x))'),
    ('#define F(...) pre ## __VA_OPT__(fix)', 'F() F(x)', 'pre prefix'),
    ('#define H2(X,Y,...) __VA_OPT__(X ## Y,) __VA_ARGS__', 'H2(a,b,c,d)', 'ab,c,d'),
    ('#define H3(X,...) #__VA_OPT__(X ## X X ## X)', 'H3(,0)', '""'),
    ('#define H4(X,...) __VA_OPT__(a X ## X) ## b', 'H4(,1)', 'a b'),
    ('#define F(...) __VA_OPT__(F(__VA_ARGS__))', 'F(1)', 'F(1)'),
    ('#define F(...) __VA_OPT__(a) __VA_OPT__(b)', 'F() F(x)', 'a b'),
]


@pytest.mark.parametrize('definitions, invocation, expected', CASES)
def test_function_expansion(definitions, invocation, expected):
    source = definitions + '\n' + invocation
    result = preprocess_source(source)
    assert result.complete, result.incomplete
    assert spellings(result.source) == spellings(expected)
    assert re.findall(r'\r\n|\r|\n', result.source) == re.findall(r'\r\n|\r|\n', source)


@pytest.mark.parametrize('definitions, invocation, expected', VA_OPT_CASES)
def test_va_opt_expansion(definitions, invocation, expected):
    source = definitions + '\n' + invocation
    result = preprocess_source(source)
    assert result.complete, result.incomplete
    assert spellings(result.source) == spellings(expected)
    assert re.findall(r'\r\n|\r|\n', result.source) == re.findall(r'\r\n|\r|\n', source)


@pytest.mark.skipif(shutil.which('gcc') is None, reason='optional GCC differential check')
@pytest.mark.parametrize('definitions, invocation, expected', CASES)
def test_supported_expansion_matches_gcc(definitions, invocation, expected):
    source = definitions + '\n' + invocation + '\n'
    compiler = subprocess.run(['gcc', '-E', '-P', '-x', 'c++', '-'], input=source,
                              text=True, capture_output=True, check=True)
    result = preprocess_source(source)
    assert result.complete, result.incomplete
    assert spellings(result.source) == spellings(compiler.stdout)


@pytest.mark.parametrize('compiler_name', ['gcc', 'clang'])
@pytest.mark.parametrize('definitions, invocation, expected', VA_OPT_CASES)
def test_va_opt_matches_standard_compilers(compiler_name, definitions, invocation, expected):
    compiler_path = shutil.which(compiler_name)
    if compiler_path is None:
        pytest.skip(f'optional {compiler_name} differential check')
    source = definitions + '\n' + invocation + '\n'
    compiler = subprocess.run(
        [compiler_path, '-std=c++20', '-E', '-P', '-x', 'c++', '-'],
        input=source, text=True, capture_output=True, check=True,
    )
    result = preprocess_source(source)
    assert result.complete, result.incomplete
    assert spellings(result.source) == spellings(compiler.stdout)


@pytest.mark.parametrize('definition, use', [
    ('#define F(a,b) a+b', 'F(1)'),
    ('#define F() 1', 'F(1)'),
    ('#define F(x) x', 'F(1,2)'),
    ('#define F(x) x', 'F('),
    ('#define F(x) x', 'F((1)'),
    ('#define F(x,...) x', 'F(1)'),
    ('#define F(x) x\n#define PAIR 1,2\n#define G(x) F(x)', 'G(PAIR)'),
    ('#define F(x) x', 'F(\n#define A 1\n1)'),
    ('#define F(x) x', 'F\n#if 1\n(1)\n#endif'),
])
def test_unsupported_and_malformed_calls_are_atomic(definition, use):
    result = preprocess_source(definition + '\n' + use)
    assert result.source is result.source_map is result.macros is None
    assert result.incomplete[0].code is ErrorCode.UNSUPPORTED_MACRO_EXPANSION
    inactive = preprocess_source(definition + '\n#if 0\n' + use + '\n#endif')
    assert inactive.complete, inactive.incomplete


@pytest.mark.parametrize('definition, use, message', [
    ('#define F(x) __VA_OPT__(x)', 'F(1)', 'requires a variadic'),
    ('#define F(...) __VA_OPT__ x', 'F(1)', 'must be followed'),
    ('#define F(...) __VA_OPT__((x)', 'F(1)', 'unterminated __VA_OPT__'),
    ('#define F(...) __VA_OPT__(__VA_OPT__(x))', 'F(1)', 'nested __VA_OPT__'),
    ('#define F(...) __VA_OPT__(##)', 'F(1)', 'cannot appear at an edge'),
    ('#define F(...) a ## __VA_OPT__(+)', 'F(1)', 'invalid token paste'),
    ('#define __VA_OPT__ 1', '__VA_OPT__', 'cannot be used as a macro name'),
    ('#define F(x) x', '__VA_OPT__(x)', 'only valid in a variadic macro'),
])
def test_invalid_va_opt_forms_are_structured_incomplete(definition, use, message):
    result = preprocess_source(definition + '\n' + use)
    assert result.source is result.source_map is result.macros is None
    diagnostic, = result.incomplete
    assert diagnostic.code is ErrorCode.UNSUPPORTED_MACRO_EXPANSION
    assert message in diagnostic.message


@pytest.mark.parametrize('definition, message', [
    ('#define F(x) __VA_OPT__(x)', 'requires a variadic'),
    ('#define F(...) __VA_OPT__ x', 'must be followed'),
    ('#define F(...) __VA_OPT__((x)', 'unterminated __VA_OPT__'),
    ('#define F(...) __VA_OPT__(__VA_OPT__(x))', 'nested __VA_OPT__'),
    ('#define F(...) __VA_OPT__(##)', 'cannot appear at an edge'),
    ('#define __VA_OPT__ 1', 'cannot be used as a macro name'),
    ('#define F(__VA_OPT__,...) x', 'cannot be a named parameter'),
])
def test_invalid_va_opt_definitions_are_atomic_without_invocation(definition, message):
    result = preprocess_source(definition + '\nint ok;')
    assert result.source is result.source_map is result.macros is None
    diagnostic, = result.incomplete
    assert diagnostic.code is ErrorCode.UNSUPPORTED_MACRO_EXPANSION
    assert diagnostic.location == SourceLocation(1, 1)
    assert message in diagnostic.message

    inactive = preprocess_source('#if 0\n' + definition + '\n#endif\nint ok;')
    assert inactive.complete, inactive.incomplete


@pytest.mark.parametrize('definition, use, message', [
    ('#define CAT(a,b) a ## b', 'CAT(x,+)', 'invalid token paste'),
    ('#define CAT(a,b) ## a b', 'CAT(x,y)', 'cannot appear at an edge'),
    ('#define BAD(x) # 1', 'BAD(x)', 'not followed by a parameter'),
])
def test_invalid_macro_operators_are_structured_incomplete(definition, use, message):
    result = preprocess_source(definition + '\n' + use)
    assert result.source is result.source_map is result.macros is None
    assert result.incomplete[0].code is ErrorCode.UNSUPPORTED_MACRO_EXPANSION
    assert message in result.incomplete[0].message


def test_stringification_escapes_quotes_and_backslashes():
    result = preprocess_source(r'#define S(x) #x' + '\n' + r'S("a\\b")')
    assert result.complete, result.incomplete
    assert spellings(result.source) == [r'"\"a\\\\b\""']


def test_source_order_redefinition_and_undef():
    result = preprocess_source('#define F(x) x+1\nF(1)\n#define F(x) x+2\nF(1)\n'
                               '#if 0\n#define F(x) BAD\n#endif\nF(1)\n#undef F\nF(1)')
    assert result.complete
    assert spellings(result.source) == spellings('1+1 1+2 1+2 F(1)')


@pytest.mark.parametrize('ending', ['\n', '\r\n', '\r'])
def test_multiline_invocation_mapping_and_untouched_comments(ending):
    source = ('#define F(x) x+x\n// before\nint n=F /*arg*/ (\n 3\n); // after\n').replace('\n', ending)
    result = preprocess_source(source, filename='mapped.c')
    assert result.complete
    span, = [span for span in result.source_map if span.expanded]
    assert source[span.source_start:span.source_end] == ('F /*arg*/ (\n 3\n').replace('\n', ending)
    assert span.start == SourceLocation(3, 7)
    assert span.end == SourceLocation(5, 2)
    assert '// before' in result.source and '// after' in result.source
    assert re.findall(r'\r\n|\r|\n', result.source) == re.findall(r'\r\n|\r|\n', source)
    assert result.source_map[0].output_start == 0
    assert result.source_map[-1].output_end == len(result.source)
    for left, right in zip(result.source_map, result.source_map[1:]):
        assert left.output_end == right.output_start


def test_va_opt_source_mapping_is_invocation_oriented():
    source = '#define F(...) __VA_OPT__(value)\nint n=F(1);\n'
    result = preprocess_source(source, filename='mapped.c')
    assert result.complete, result.incomplete
    expanded = [span for span in result.source_map if span.expanded]
    assert len(expanded) == 1
    span = expanded[0]
    assert source[span.source_start:span.source_end] == 'F(1)'
    assert span.start == SourceLocation(2, 7)
    assert span.end == SourceLocation(2, 11)
    assert result.source[span.output_start:span.output_end].strip() == 'value'


def test_recursion_depth_and_work_limits_are_explicit():
    source = '#define F(x) x\n' + 'F(' * 1100 + '1' + ')' * 1100
    result = preprocess_source(source, options=AnalysisOptions(max_work=3_000_000))
    assert result.complete, result.incomplete
    assert result.source.strip() == '1'
    source = '#define F(x) x x\n' + 'F(' * 25 + '1' + ')' * 25
    first = preprocess_source(source, options=AnalysisOptions(max_work=1000))
    second = preprocess_source(source, options=AnalysisOptions(max_work=1000))
    assert first == second
    assert first.source is first.source_map is first.macros is None
    assert first.incomplete[0].code is ErrorCode.ANALYSIS_LIMIT_EXCEEDED


def test_va_opt_resource_limit_failure_is_deterministic():
    source = '#define F(...) __VA_OPT__(__VA_ARGS__ __VA_ARGS__)\n' + 'F(' * 25 + '1' + ')' * 25
    first = preprocess_source(source, options=AnalysisOptions(max_work=1000))
    second = preprocess_source(source, options=AnalysisOptions(max_work=1000))
    assert first == second
    assert first.source is first.source_map is first.macros is None
    assert first.incomplete[0].code is ErrorCode.ANALYSIS_LIMIT_EXCEEDED


def test_diagnostics_point_to_invocations_when_flushed_by_later_directive():
    result = preprocess_source('#define F(x) # 1\nint n=F(1);\n#define LATER 1')
    assert result.incomplete[0].location == SourceLocation(2, 7)
    source = '#define F(x) x x\n' + 'F(' * 15 + '1' + ')' * 15 + '\n#define LATER 1'
    result = preprocess_source(source, options=AnalysisOptions(max_work=100))
    assert result.incomplete[0].location == SourceLocation(2)


@pytest.mark.parametrize('middle', ['EMPTY', 'DROP(1)', 'ALIAS'])
@pytest.mark.parametrize('separate_buffer', [False, True])
def test_intervening_empty_expansion_clears_pending_invocation(middle, separate_buffer):
    source = ('#define F(x) x\n#define EMPTY\n#define DROP(x)\n#define ALIAS EMPTY\n'
              'F\n#if 1\n' + middle + ('\n#endif\n' if separate_buffer else '\n') + '(1)\n'
              + ('' if separate_buffer else '#endif\n'))
    result = preprocess_source(source)
    assert result.complete, result.incomplete
    assert spellings(result.source) == spellings('F(1)')


@pytest.mark.parametrize('middle', ['', '/* comment */'])
def test_whitespace_only_buffer_preserves_directive_invocation_guard(middle):
    source = '#define F(x) x\nF\n#if 1\n' + middle + '\n#endif\n(1)\n'
    result = preprocess_source(source)
    assert result.source is None
    assert result.incomplete[0].code is ErrorCode.UNSUPPORTED_MACRO_EXPANSION


def test_generated_open_parenthesis_does_not_invoke_preceding_buffer():
    result = preprocess_source('#define F(x) x\n#define OPEN (\nF\n#if 1\nOPEN 1)\n#endif')
    assert result.complete, result.incomplete
    assert spellings(result.source) == spellings('F(1)')
