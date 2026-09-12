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
]


@pytest.mark.parametrize('definitions, invocation, expected', CASES)
def test_function_expansion(definitions, invocation, expected):
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


@pytest.mark.parametrize('definition, use', [
    ('#define F(a,b) a+b', 'F(1)'),
    ('#define F() 1', 'F(1)'),
    ('#define F(x) x', 'F(1,2)'),
    ('#define F(x) x', 'F('),
    ('#define F(x) x', 'F((1)'),
    ('#define F(x) #x', 'F(1)'),
    ('#define F(x,y) x ## y', 'F(a,b)'),
    ('#define F(x,y) x %:%: y', 'F(a,b)'),
    ('#define F(...) __VA_OPT__(,) __VA_ARGS__', 'F(1)'),
    ('#define F(x,...) x', 'F(1)'),
    ('#define F(x) x\n#define BAD(a) #a', 'F(BAD(1))'),
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
    assert source[span.source_start:span.source_end] == ('F /*arg*/ (\n 3\n)').replace('\n', ending)
    assert span.start == SourceLocation(3, 7)
    assert span.end == SourceLocation(5, 2)
    assert '// before' in result.source and '// after' in result.source
    assert re.findall(r'\r\n|\r|\n', result.source) == re.findall(r'\r\n|\r|\n', source)
    assert result.source_map[0].output_start == 0
    assert result.source_map[-1].output_end == len(result.source)
    for left, right in zip(result.source_map, result.source_map[1:]):
        assert left.output_end == right.output_start


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


def test_diagnostics_point_to_invocations_when_flushed_by_later_directive():
    result = preprocess_source('#define F(x) #x\nint n=F(1);\n#define LATER 1')
    assert result.incomplete[0].location == SourceLocation(2, 7)
    source = '#define F(x) x x\n' + 'F(' * 15 + '1' + ')' * 15 + '\n#define LATER 1'
    result = preprocess_source(source, options=AnalysisOptions(max_work=100))
    assert result.incomplete[0].location == SourceLocation(2)
