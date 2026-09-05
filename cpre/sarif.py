"""SARIF 2.1.0 rendering for structured cpre analysis results."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable
from urllib.parse import quote

from .api import AnalysisIncomplete, AnalysisResult, Finding, FindingKind, SourceRange

SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
SARIF_VERSION = "2.1.0"

_RULES: tuple[dict[str, object], ...] = (
    {
        "id": "CPRE001",
        "name": "dead-branch",
        "shortDescription": {"text": "Unreachable preprocessor branch"},
        "fullDescription": {
            "text": "A preprocessor branch cannot be selected under the modeled Boolean conditions."
        },
        "defaultConfiguration": {"level": "warning"},
    },
    {
        "id": "CPRE002",
        "name": "redundant-branch",
        "shortDescription": {"text": "Redundant preprocessor condition"},
        "fullDescription": {
            "text": "A preprocessor condition is always true in its reachable branch context."
        },
        "defaultConfiguration": {"level": "warning"},
    },
    {
        "id": "CPRE003",
        "name": "simplifiable-condition",
        "shortDescription": {"text": "Preprocessor condition can be simplified"},
        "fullDescription": {
            "text": "A preprocessor condition has a smaller globally equivalent Boolean form."
        },
        "defaultConfiguration": {"level": "note"},
    },
    {
        "id": "CPRE004",
        "name": "contextual-simplification",
        "shortDescription": {"text": "Preprocessor condition can be simplified in context"},
        "fullDescription": {
            "text": "A preprocessor condition has a smaller form equivalent in its reachable context."
        },
        "defaultConfiguration": {"level": "note"},
    },
)

_RULE_FOR_KIND = {
    FindingKind.DEAD_BRANCH: ("CPRE001", 0, "warning"),
    FindingKind.REDUNDANT_BRANCH: ("CPRE002", 1, "warning"),
    FindingKind.SIMPLIFIABLE_CONDITION: ("CPRE003", 2, "note"),
    FindingKind.CONTEXTUAL_SIMPLIFICATION: ("CPRE004", 3, "note"),
}


def _artifact_uri(filename: str | None) -> str:
    if filename is None:
        return "<memory>"
    path = Path(filename)
    if path.is_absolute():
        try:
            path = path.relative_to(Path.cwd())
        except ValueError:
            pass
    normalized = str(path).replace("\\", "/")
    return quote(normalized, safe="/:@")


def _region(source_range: SourceRange) -> dict[str, int]:
    region = {
        "startLine": source_range.start.line,
        "endLine": source_range.end.line,
    }
    if source_range.start.column is not None:
        region["startColumn"] = source_range.start.column
    if source_range.end.column is not None:
        region["endColumn"] = source_range.end.column
    return region


def _finding_region(finding: Finding) -> dict[str, int]:
    if finding.edit is not None:
        return _region(finding.edit.range)
    region = {"startLine": finding.location.line}
    if finding.location.column is not None:
        region["startColumn"] = finding.location.column
    return region


def _message(finding: Finding) -> str:
    replacement = None
    if finding.edit is not None:
        replacement = finding.edit.replacement
    elif finding.exact_simplification is not None:
        replacement = finding.exact_simplification.replacement
    elif finding.contextual_simplification is not None:
        replacement = finding.contextual_simplification.replacement
    if replacement is None:
        return finding.reason
    return f"{finding.reason}. Suggested condition: {replacement}"


def _fix(finding: Finding, uri: str) -> list[dict[str, object]] | None:
    if finding.edit is None:
        return None
    return [
        {
            "description": {"text": f"Replace condition with {finding.edit.replacement}"},
            "artifactChanges": [
                {
                    "artifactLocation": {"uri": uri},
                    "replacements": [
                        {
                            "deletedRegion": _region(finding.edit.range),
                            "insertedContent": {"text": finding.edit.replacement},
                        }
                    ],
                }
            ],
        }
    ]


def _finding_result(finding: Finding, filename: str | None) -> dict[str, object]:
    rule_id, rule_index, level = _RULE_FOR_KIND[finding.kind]
    uri = _artifact_uri(filename)
    result: dict[str, object] = {
        "ruleId": rule_id,
        "ruleIndex": rule_index,
        "level": level,
        "message": {"text": _message(finding)},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": uri},
                    "region": _finding_region(finding),
                }
            }
        ],
        "properties": {
            "kind": finding.kind.value,
            "directive": finding.directive,
            "dependsOnAssumptions": finding.depends_on_assumptions,
            "opaquePredicates": list(finding.opaque_predicates),
        },
    }
    if finding.original_condition is not None:
        result["properties"]["originalCondition"] = finding.original_condition  # type: ignore[index]
    if finding.edit is not None:
        result["properties"]["fixConfidence"] = finding.edit.confidence.value  # type: ignore[index]
        result["fixes"] = _fix(finding, uri)
    return result


def _notification(diagnostic: AnalysisIncomplete, filename: str | None) -> dict[str, object]:
    notification: dict[str, object] = {
        "level": "error",
        "message": {"text": diagnostic.message},
        "descriptor": {"id": diagnostic.code.value},
        "properties": {
            "resource": diagnostic.resource,
            "limit": diagnostic.limit,
            "observed": diagnostic.observed,
        },
    }
    if diagnostic.location is not None:
        notification["locations"] = [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": _artifact_uri(filename)},
                    "region": {"startLine": diagnostic.location.line},
                }
            }
        ]
    return notification


def sarif_log(results: Iterable[AnalysisResult], *, tool_version: str) -> dict[str, object]:
    """Return a deterministic SARIF 2.1.0 log for one cpre invocation."""

    analyses = tuple(results)
    sarif_results = [
        _finding_result(finding, analysis.filename)
        for analysis in analyses
        for finding in analysis.findings
    ]
    notifications = [
        _notification(diagnostic, analysis.filename)
        for analysis in analyses
        for diagnostic in analysis.incomplete
    ]
    invocation: dict[str, object] = {
        "executionSuccessful": not notifications,
    }
    if notifications:
        invocation["toolExecutionNotifications"] = notifications

    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "cpre",
                        "semanticVersion": tool_version,
                        "informationUri": "https://github.com/sahebbiswas/cpre",
                        "rules": list(_RULES),
                    }
                },
                "invocations": [invocation],
                "results": sarif_results,
            }
        ],
    }


__all__ = ["SARIF_SCHEMA", "SARIF_VERSION", "sarif_log"]
