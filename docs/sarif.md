# SARIF output

`cpre` can emit findings as SARIF 2.1.0 for static-analysis integrations and GitHub code scanning.

For direct Python embedding rather than process-level interchange, see the [Python API integration guide](api.md).

```bash
cpre --sarif source.c > cpre.sarif
cpre --recursive --sarif path/to/project > cpre.sarif
```

`--sarif` and `--json` are mutually exclusive. JSON remains the structural conditional-tree format; SARIF represents the stable findings produced by the public analysis API.

A single CLI invocation produces one SARIF run, including batch and recursive scans. Findings use these rule IDs:

| Rule | Finding |
| --- | --- |
| `CPRE001` | dead preprocessor branch |
| `CPRE002` | redundant preprocessor condition |
| `CPRE003` | globally simplifiable condition |
| `CPRE004` | contextually simplifiable condition |

Dead and redundant branches are emitted at SARIF `warning` level. Simplification findings are emitted at `note` level. Each result's `ruleIndex` is derived from the matching descriptor position in `tool.driver.rules`, so rule references remain consistent if descriptor ordering changes.

When a `Finding` contains a structured `SuggestedEdit`, the SARIF result includes a corresponding `fix` with the precise physical source range and replacement text. Dead and redundant branches do not receive synthesized deletion fixes.

## Tool notifications

Failures that prevent a clean analysis are represented as `toolExecutionNotifications` on the SARIF invocation. Their descriptor IDs come from cpre's stable `ErrorCode` enum and are declared in `tool.driver.notifications`, allowing SARIF consumers to resolve every emitted notification to a reporting descriptor.

This includes parser/directive failures, bounded-analysis exhaustion, supported analysis failures, invalid assumptions where applicable, and `source_read_error` for CLI file read/UTF-8 decoding failures.

If deterministic ROBDD resource limits stop exact analysis for a source file, cpre does not emit partial findings. The SARIF invocation is marked unsuccessful and the limit diagnostic is emitted as a tool execution notification. Parse/read failures likewise set `executionSuccessful` to `false`. The CLI exits with status 2 for these failure conditions.

## Artifact locations

The generated document declares SARIF version `2.1.0` and the standard SARIF 2.1.0 JSON schema. Artifact locations use source paths supplied to the CLI, normalized as URI paths; paths under the current working directory are made relative where possible for code-scanning compatibility.

## GitHub Actions

Write the output to a `.sarif` file and upload it with `github/codeql-action/upload-sarif`:

```yaml
- name: Run cpre
  run: cpre --recursive --sarif . > cpre.sarif

- name: Upload cpre SARIF
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: cpre.sarif
```

If cpre exits with status 2, treat the run as an analysis failure rather than a clean scan. The SARIF document still contains the successfully completed findings from other inputs plus structured tool notifications describing failed inputs.
