"""Installed-wheel contract for lossless conditional structure consumers."""

import cpre


def test_structural_top_level_imports_are_available():
    symbols = (
        cpre.ConditionalBlock,
        cpre.ConditionalBranch,
        cpre.ConditionalDirective,
        cpre.ConditionalStructureTree,
        cpre.DirectiveToken,
        cpre.StructuralSourceLocation,
        cpre.StructuralSourceRange,
        cpre.StructureDiagnostic,
        cpre.StructureDiagnosticCode,
        cpre.parse_conditionals,
    )
    assert all(symbol is not None for symbol in symbols)


def test_structural_ranges_and_recovery_survive_installed_package_boundary():
    source = "#if FLAG && \\\n FLAG\nbody\n#else bad\nfallback\n#endif\n"
    tree = cpre.parse_conditionals(source, filename="contract.c")

    assert isinstance(tree, cpre.ConditionalStructureTree)
    assert tree.filename == "contract.c"
    assert [directive.kind for directive in tree.directives] == ["if", "else", "endif"]
    first = tree.blocks[0].branches[0]
    condition_range = first.directive.condition_range
    assert isinstance(condition_range, cpre.StructuralSourceRange)
    assert condition_range.text(source) == "FLAG && \\\n FLAG"
    assert condition_range.start == cpre.StructuralSourceLocation(
        offset=4, line=1, column=5
    )
    assert condition_range.end.offset == source.index("FLAG\nbody") + 4
    assert first.body_range.text(source) == "body\n"
    assert [diagnostic.code for diagnostic in tree.diagnostics] == [
        cpre.StructureDiagnosticCode.TRAILING_DIRECTIVE_TEXT
    ]


def test_structural_parser_exposes_public_symbolic_nodes_without_private_imports():
    tree = cpre.parse_conditionals(
        "#ifdef FEATURE\n#elifndef FALLBACK\n#endif\n"
    )
    first, second = tree.blocks[0].branches
    assert first.directive.condition == cpre.DefinedVariable("FEATURE")
    assert second.directive.condition == cpre.Negation(cpre.DefinedVariable("FALLBACK"))
