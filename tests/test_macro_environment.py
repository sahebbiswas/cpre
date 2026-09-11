import pytest

from cpre import (MacroAssumptions, MacroDefinition, MacroEnvironment,
                  MacroState, SourceLocation, preprocess_source)


def run(source, **kwargs):
    result = preprocess_source(source, **kwargs)
    assert result.complete, result.incomplete
    assert len(result.source) == len(source)
    assert [(i, c) for i, c in enumerate(source) if c in '\r\n'] == [
        (i, c) for i, c in enumerate(result.source) if c in '\r\n']
    return result


def test_source_order_nested_redefinition_undef_and_elif():
    source = ('#define X 0\n#ifdef X\n#if X\ndead1\n#else\n#define X 2\n#endif\n'
              '#elif 1\n#undef X\n#endif\n#if X\nfirst\n#endif\n'
              '#undef X\n#if X\ndead2\n#elif defined(X)\ndead3\n#else\nsecond\n#endif')
    result = run(source)
    assert [s.strip() for s in result.source.splitlines() if s.strip()] == ['first', 'second']
    assert result.macros['X'] == MacroState(False, False)


def test_inactive_definitions_and_undefs_do_not_leak():
    result = run('#define X 1\n#if 0\n#undef X\n#define Y 1\n#if 1\n#define Z 2\n#endif\n#endif\n#if X\nkept\n#endif')
    assert 'kept' in result.source
    assert set(result.macros) == {'X'}


def test_seeded_definedness_and_values_are_overridden_independently():
    assumptions = MacroAssumptions(defined=['ZERO'], undefined=['X'], values={'ZERO': False, 'FLAG': False})
    result = run('#define X 3\n#undef ZERO\n#define FLAG 0\n#if X && defined(FLAG) && !FLAG && !defined(ZERO)\nkept\n#endif', assumptions=assumptions)
    assert 'kept' in result.source
    assert assumptions.undefined == frozenset({'X'})
    env = MacroEnvironment(assumptions)
    assert env.get('FLAG') == MacroState(None, False)
    assert env.get('ZERO') == MacroState(True, False)
    assert env.get('UNKNOWN') == MacroState()
    assert MacroEnvironment({'X': True}).get('X') == MacroState(True, True)


@pytest.mark.parametrize('literal, number', [('0', 0), ('42', 42), ('0x10UL', 16), ('077', 63), ('(-2)', -2), ('((0))', 0)])
def test_numeric_definitions_keep_numeric_value_and_boolean_truth(literal, number):
    result = run(f'#define X {literal}\n#if X\nnonzero\n#else\nzero\n#endif')
    assert result.macros['X'].definition.numeric_value == number
    assert ('nonzero' in result.source) == bool(number)
    assert result.macros['X'].defined is True


def test_function_macros_empty_macros_and_object_whitespace():
    result = run('#define F(x, ...) x + __VA_ARGS__\n#define G() 1\n#define EMPTY\n#define OBJECT (x)\n#if defined(F) && defined(G) && defined(EMPTY) && !F\nF(1, 2);\n#endif')
    assert 'F(1, 2);' in result.source
    definition = result.macros['F'].definition
    assert definition.parameters == ('x',)
    assert definition.variadic
    assert definition.replacement == 'x + __VA_ARGS__'
    assert definition.location == SourceLocation(1)
    assert result.macros['G'].definition.parameters == ()
    assert result.macros['OBJECT'].definition.parameters is None
    assert result.macros['EMPTY'].definition.replacement == ''


@pytest.mark.parametrize('replacement', ['OTHER', '1 + 2', '', '"text"'])
def test_unsupported_replacement_values_remain_unknown(replacement):
    result = preprocess_source(f'#define X {replacement}\n#if X\nx\n#endif')
    assert not result.complete
    assert result.source is None and result.macros is None
    assert result.incomplete[0].location == SourceLocation(2)
    assert run(f'#define X {replacement}\n#ifdef X\ny\n#endif').complete


@pytest.mark.parametrize('directive', ['#define', '#undef X extra', '#define 1X', '#define F(x,x) x', '#define F(x', '#define F(,x) x'])
def test_invalid_active_macro_directives_are_atomic(directive):
    result = preprocess_source('#define OK 1\n' + directive)
    assert result.source is None and result.macros is None
    assert result.incomplete[0].location == SourceLocation(2)
    assert run('#if 0\n' + directive + '\n#endif').complete


def test_unknown_branch_and_include_do_not_expose_partial_state():
    for tail in ['#if UNKNOWN\n#define Y 2\n#endif', '#include "x.h"']:
        result = preprocess_source('#define X 1\n' + tail)
        assert result.source is None and result.macros is None


def test_snapshots_are_detached_immutable_and_deterministically_ordered():
    env = MacroEnvironment({'Z': True})
    env.define(MacroDefinition('A', '0'))
    snapshot = env.snapshot()
    env.undef('A')
    assert list(snapshot) == ['A', 'Z']
    assert snapshot['A'].defined is True
    with pytest.raises(TypeError):
        snapshot['B'] = MacroState()
    assert run('').macros == {}


@pytest.mark.parametrize('ending', ['\n', '\r\n', '\r'])
def test_continued_definitions_comments_and_quoted_replacement(ending):
    source = '#define X \\\n  0x2 /* comment */\n#define URL "https://example.test/a/*b*/"\n#if X\nX;\n#endif'
    result = run(source.replace('\n', ending))
    assert result.macros['X'].definition.numeric_value == 2
    assert result.macros['URL'].definition.replacement == '"https://example.test/a/*b*/"'
    assert 'X;' in result.source


def test_no_retroactive_change_and_no_state_leak_across_calls():
    result = preprocess_source('#if X\nx\n#endif\n#define X 1')
    assert not result.complete
    assert result.incomplete[0].location == SourceLocation(1)
    run('#define X 1')
    assert not preprocess_source('#if X\nx\n#endif').complete


def test_digit_separators_do_not_hide_later_macro_directives():
    result = run("int count = 1'024;\n#define X 0\n#if X\ndead\n#endif\n#define TEXT \"'text'\"\n")
    assert result.macros['X'].value is False
    assert 'dead' not in result.source
