"""Regression tests for issue #159: a self-referential macro in an #if condition.

A name that macro expansion leaves alone because it is already being replaced is a
plain identifier, and every identifier left in a controlling expression is 0
(C11 6.10.1). GCC and Clang evaluate such conditions; cpre used to report them as
unresolved.
"""

import pytest

import cpre

CLOSED = cpre.MacroConfiguration(unknown_names="undefined")
OPEN = cpre.MacroConfiguration()


def selected(source: str, configuration=CLOSED) -> list[str]:
    result = cpre.preprocess_source(source, configuration=configuration)
    assert result.complete, result.incomplete
    return result.source.split()


def branch(defines: str, condition: str, configuration=CLOSED, directive="#if") -> str:
    source = f"{defines}{directive} {condition}\nyes\n#else\nno\n#endif\n"
    if directive == "#elif":
        source = f"{defines}#if 0\nfirst\n#elif {condition}\nyes\n#else\nno\n#endif\n"
    (word,) = [w for w in selected(source, configuration) if w in ("yes", "no")]
    return word


@pytest.mark.parametrize("configuration", [CLOSED, OPEN], ids=["closed", "open"])
@pytest.mark.parametrize("directive", ["#if", "#elif"])
class TestSelfReference:
    def test_direct(self, configuration, directive):
        defines = "#define A A + 1\n"
        assert branch(defines, "A > 0", configuration, directive) == "yes"
        assert branch(defines, "A == 1", configuration, directive) == "yes"
        assert branch(defines, "A == 2", configuration, directive) == "no"

    def test_indirect(self, configuration, directive):
        defines = "#define B C + 1\n#define C B\n"
        assert branch(defines, "B > 0", configuration, directive) == "yes"
        assert branch(defines, "B == 1", configuration, directive) == "yes"
        assert branch(defines, "C == 1", configuration, directive) == "yes"

    def test_name_alone_is_zero(self, configuration, directive):
        assert branch("#define A A\n", "A", configuration, directive) == "no"
        assert branch("#define A A\n", "!A", configuration, directive) == "yes"

    def test_through_a_macro_argument(self, configuration, directive):
        defines = "#define G(x) x\n#define A G(A) + 1\n"
        assert branch(defines, "A == 1", configuration, directive) == "yes"

    def test_function_like(self, configuration, directive):
        defines = "#define F(x) x + F\n"
        assert branch(defines, "F(1) == 1", configuration, directive) == "yes"
        assert branch(defines, "F(2) == 1", configuration, directive) == "no"


@pytest.mark.parametrize("configuration", [CLOSED, OPEN], ids=["closed", "open"])
def test_defined_does_not_expand_the_operand(configuration):
    defines = "#define A A + 1\n"
    assert branch(defines, "defined(A) && defined A", configuration) == "yes"
    assert branch(defines, "defined(A) && A == 1", configuration) == "yes"


def test_self_reference_in_an_unevaluated_operand():
    defines = "#define A A + 1\n"
    assert branch(defines, "1 || A > 5") == "yes"
    assert branch(defines, "0 && A > 5") == "no"


def test_text_expansion_is_unchanged():
    result = cpre.preprocess_source("#define A A + 1\nint x = A;\n", configuration=CLOSED)
    assert result.complete and "int x =  A + 1 ;" in result.source


def test_other_unresolved_identifiers_still_stop_open_world_preprocessing():
    # Only a name that expansion itself left alone is known to be 0; any other
    # identifier might still be defined by the build, so the open world keeps asking.
    result = cpre.preprocess_source("#define A A + 1\n#if A + UNKNOWN > 0\nyes\n#endif\n")
    assert not result.complete
    (diagnostic,) = result.incomplete
    assert diagnostic.code is cpre.ErrorCode.UNRESOLVED_CONDITION
    assert [item.name for item in diagnostic.unresolved] == ["UNKNOWN"]
