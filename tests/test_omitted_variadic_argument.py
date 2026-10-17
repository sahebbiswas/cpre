"""#151: a variadic macro call may omit the variadic argument entirely, comma included."""

import pytest

import cpre
from cpre.lexer import tokenize

CONFIGURATION = cpre.MacroConfiguration(unknown_names="undefined")


def tokens(text: str) -> list[str]:
    return [t.text for t in tokenize(text) if t.kind not in {"space", "comment"}]


def expand(definition: str, use: str) -> list[str]:
    result = cpre.preprocess_source(f"{definition}\n{use}\n", configuration=CONFIGURATION)
    assert result.complete, result.incomplete
    return tokens(result.source)


LOG = "#define LOG(fmt, ...) printf(fmt __VA_OPT__(,) __VA_ARGS__)"


def test_issue_example():
    assert expand(LOG, 'LOG("plain");') == tokens('printf("plain") ;')


@pytest.mark.parametrize("use", ['LOG("plain")', 'LOG("plain", )', 'LOG("plain",)'])
def test_omitted_and_empty_variadic_argument_agree(use):
    assert expand(LOG, use) == tokens('printf("plain" )')


def test_variadic_argument_still_binds_when_present():
    assert expand(LOG, 'LOG("x", 1, 2)') == tokens('printf("x" , 1, 2)')


def test_omitted_argument_in_va_args_and_stringification():
    definition = "#define M(a, ...) [a|__VA_ARGS__|#__VA_ARGS__]"
    assert expand(definition, "M(1)") == tokens('[ 1 | | ""]')
    assert expand(definition, "M(1, )") == tokens('[ 1 | | ""]')
    assert expand(definition, "M(1, x y)") == tokens('[ 1 | x y | "x y" ]')


def test_omitted_argument_with_va_opt_content():
    definition = "#define M(a, ...) a __VA_OPT__(<__VA_ARGS__> #a a ## a) end"
    assert expand(definition, "M(1)") == tokens("1 end")
    assert expand(definition, "M(1, z)") == tokens('1 <z> "1" 11 end')


def test_omitted_argument_with_paste_operands_left_unexpanded():
    definition = "#define E\n#define P(a, ...) a ## __VA_ARGS__ ## a\n"
    assert expand(definition, "P(x)") == tokens("xx")
    assert expand(definition, "P(x, E)") == tokens("xEx")  # ## operands are not expanded


def test_omitted_argument_after_several_named_parameters():
    definition = "#define M(a, b, ...) a-b-__VA_ARGS__-"
    assert expand(definition, "M(1, 2)") == tokens("1 - 2 - -")
    assert expand(definition, "M(1, 2, 3)") == tokens("1 - 2 - 3 -")


def test_empty_call_of_a_macro_with_one_named_parameter():
    assert expand("#define M(a, ...) [a|__VA_ARGS__]", "M()") == tokens("[|]")


def test_variadic_only_macro_is_unchanged():
    assert expand("#define V(...) <__VA_ARGS__>", "V()") == tokens("<>")


def test_too_few_arguments_are_still_rejected():
    result = cpre.preprocess_source("#define M(a, b, ...) a\nM(1)\n", configuration=CONFIGURATION)
    assert not result.complete
    assert "expects at least 2 arguments, got 1" in result.incomplete[0].message


def test_omitted_argument_in_an_if_condition():
    source = "#define M(a, ...) (a __VA_OPT__(+ __VA_ARGS__))\n#if M(2) == 2 && M(1, 4) == 5\nyes\n#endif\n"
    result = cpre.preprocess_source(source, configuration=CONFIGURATION)
    assert result.complete and result.source.split() == ["yes"]
