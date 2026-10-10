import json
import os
import subprocess
import sys

import pytest

import cpre


def test_ordinary_expression_completes_below_default_limits():
    result = cpre.analyze_source("#if A && B\n#endif\n")

    assert result.complete
    assert result.incomplete == ()


def test_atom_limit_returns_structured_incomplete_result():
    result = cpre.analyze_source(
        "#if A && B && C\n#endif\n",
        filename="atoms.c",
        options=cpre.AnalysisOptions(max_atoms=2),
    )

    assert not result.complete
    assert result.findings == ()
    assert len(result.incomplete) == 1
    diagnostic = result.incomplete[0]
    assert diagnostic.code is cpre.ErrorCode.ANALYSIS_LIMIT_EXCEEDED
    assert diagnostic.resource == "atoms"
    assert diagnostic.limit == 2
    assert diagnostic.observed == 3
    assert diagnostic.location is None
    assert result.filename == "atoms.c"


def test_higher_atom_limit_allows_same_source_to_complete():
    source = "#if A && B && C\n#endif\n"

    limited = cpre.analyze_source(source, options=cpre.AnalysisOptions(max_atoms=2))
    allowed = cpre.analyze_source(source, options=cpre.AnalysisOptions(max_atoms=3))

    assert not limited.complete
    assert allowed.complete


def test_bdd_node_limit_is_deterministic_and_located():
    options = cpre.AnalysisOptions(max_bdd_nodes=1)
    source = "#if A && B\n#endif\n"

    first = cpre.analyze_source(source, options=options)
    second = cpre.analyze_source(source, options=options)

    assert first.incomplete == second.incomplete
    diagnostic = first.incomplete[0]
    assert diagnostic.resource == "bdd_nodes"
    assert diagnostic.limit == 1
    assert diagnostic.observed == 2
    assert diagnostic.location == cpre.SourceLocation(line=1)
    assert first.findings == ()


def test_work_limit_returns_no_partial_finding():
    source = "#if A || B\n#elif A\n#endif\n"
    result = cpre.analyze_source(source, options=cpre.AnalysisOptions(max_work=1))

    assert not result.complete
    assert result.incomplete[0].resource == "work"
    assert result.incomplete[0].location == cpre.SourceLocation(line=1)
    assert result.findings == ()


def test_higher_node_and_work_limits_allow_dead_branch_proof():
    source = "#if A || B\n#elif A\n#endif\n"
    result = cpre.analyze_source(
        source,
        options=cpre.AnalysisOptions(max_bdd_nodes=100, max_work=10_000),
    )

    assert result.complete
    assert [finding.kind for finding in result.findings] == [cpre.FindingKind.DEAD_BRANCH]


def test_large_realistic_nested_fixture_completes_with_defaults():
    lines = []
    for index in range(24):
        lines.append(f"#if FEATURE_{index % 8} || PLATFORM_{index % 4}\n")
    lines.append("int enabled;\n")
    lines.extend("#endif\n" for _ in range(24))

    result = cpre.analyze_source("".join(lines))

    assert result.complete


def test_limit_diagnostic_is_stable_across_python_hash_seeds():
    script = """
import json
import cpre
result = cpre.analyze_source(
    '#if A && B && C\\n#endif\\n',
    options=cpre.AnalysisOptions(max_atoms=2),
)
d = result.incomplete[0]
print(json.dumps([d.resource, d.limit, d.observed, d.location.line if d.location else None]))
"""
    outputs = []
    for seed in ("1", "7"):
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        outputs.append(json.loads(completed.stdout))

    assert outputs == [["atoms", 2, 3, None], ["atoms", 2, 3, None]]


@pytest.mark.parametrize("name", ["max_atoms", "max_bdd_nodes", "max_work"])
def test_analysis_options_require_positive_integer_limits(name):
    values = {"max_atoms": 64, "max_bdd_nodes": 100_000, "max_work": 500_000}
    values[name] = 0

    with pytest.raises(cpre.AnalysisError) as caught:
        cpre.AnalysisOptions(**values)

    assert caught.value.code is cpre.ErrorCode.ANALYSIS_FAILURE


def _feature_groups(count):
    return "".join(f"#ifdef F{i}\nint a{i};\n#endif\n" for i in range(count))


@pytest.mark.parametrize("count", [40, 300])
def test_preprocessing_many_independent_conditionals_completes_under_default_limits(count):
    # Regression for #124: the atom and node limits used to cap the whole file.
    configuration = cpre.MacroConfiguration(
        presence=[f"F{i}" for i in range(0, count, 3)], unknown_names="undefined"
    )

    result = cpre.preprocess_source(_feature_groups(count), configuration=configuration)

    assert result.complete
    kept = [line for line in result.source.splitlines() if line.strip()]
    assert kept == [f"int a{i};" for i in range(0, count, 3)]


def test_preprocessing_applies_atom_limit_per_condition():
    source = "#if A\n#endif\n#if B\n#endif\n#if C && D\nint x;\n#endif\n"
    options = cpre.AnalysisOptions(max_atoms=2)
    configuration = cpre.MacroConfiguration(unknown_names="undefined")

    result = cpre.preprocess_source(source, configuration=configuration, options=options)

    # A and B fit alone (value and definedness atoms); C && D needs four atoms.
    assert not result.complete
    assert result.source is None
    [diagnostic] = result.incomplete
    assert diagnostic.resource == "atoms"
    assert (diagnostic.limit, diagnostic.observed) == (2, 4)
    assert diagnostic.location == cpre.SourceLocation(5)


def test_preprocessing_unreached_condition_does_not_count_against_limits():
    source = "#if 0\n#if A || B || C\n#endif\n#endif\nint x;\n"

    result = cpre.preprocess_source(source, options=cpre.AnalysisOptions(max_atoms=2))

    assert result.complete
    assert result.source.strip() == "int x;"
