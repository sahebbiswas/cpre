# SARIF output

`cpre` can emit findings as SARIF 2.1.0 for static-analysis integrations and GitHub code scanning.

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

Dead and redundant branches are emitted at SARIF `warning` level. Simplification findings are emitted at `note` level. When a `Finding` contains a structured `SuggestedEdit`, the SARIF result includes a corresponding `fix` with the precise physical source range and replacement text.

If deterministic ROBDD resource limits stop exact analysis for a source file, cpre does not emit partial findings. The SARIF invocation is marked unsuccessful and the limit diagnostic is emitted as a tool execution notification. The CLI also exits with status 2, matching the existing incomplete-analysis behavior.

The generated document declares SARIF version `2.1.0` and the standard SARIF 2.1.0 JSON schema. Artifact locations use source paths supplied to the CLI, normalized as URI paths; paths under the current working directory are made relative where possible for code-scanning compatibility.

For GitHub Actions, write the output to a `.sarif` file and upload it with `github/codeql-action/upload-sarif`.
