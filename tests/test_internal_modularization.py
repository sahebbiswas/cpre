"""Regression tests for the internal module boundaries introduced by issue #6."""

from cpre import cpre as compatibility
from cpre import analysis, discovery, expressions, model, parser, reporting, robdd


def test_core_subsystems_live_in_focused_modules():
    assert parser.parse_source.__module__ == "cpre.parser"
    assert expressions.parse_expression.__module__ == "cpre.expressions"
    assert robdd.BDD.__module__ == "cpre.robdd"
    assert analysis.analyze_tree.__module__ == "cpre.analysis"
    assert reporting.tree_to_dict.__module__ == "cpre.reporting"
    assert discovery.source_paths.__module__ == "cpre.discovery"
    assert model.ConditionalTree.__module__ == "cpre.model"


def test_legacy_internal_facade_delegates_without_reimplementing_subsystems():
    assert compatibility.parse_source is parser.parse_source
    assert compatibility.parse_expression is expressions.parse_expression
    assert compatibility._BDD is robdd.BDD
    assert compatibility.analyze_tree is analysis.analyze_tree
    assert compatibility.tree_to_dict is reporting.tree_to_dict
    assert compatibility._source_paths is discovery.source_paths
    assert compatibility.ConditionalTree is model.ConditionalTree


def test_compatibility_facade_all_is_explicit_and_private_helpers_stay_private():
    assert "ConditionalTree" in compatibility.__all__
    assert "analyze_source" in compatibility.__all__
    assert "_BDD" not in compatibility.__all__
    assert "_logical_lines" not in compatibility.__all__
