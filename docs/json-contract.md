# JSON output contract

Every `--json` document cpre writes follows the conventions on this page. Scripts can rely on what is documented here; anything not documented is not part of the contract.

For static-analysis interchange use SARIF ([sarif.md](sarif.md)). For Python integration use the Python API ([api.md](api.md)). The builders behind these documents are in `cpre.serialization` and return plain `dict`/`list` data.

## Identifying a document

Every top-level document has `schema` as its first key. The value is `cpre.<kind>/<major>`:

| `schema` | Written by |
| --- | --- |
| `cpre.analysis/1` | `cpre --json` |
| `cpre.simplify-macros/1` | `cpre simplify-macros --json` |
| `cpre.preprocess/1` | `cpre preprocess --json` |
| `cpre.unknown-macros/1` | `cpre preprocess --list-unknown-macros --json` |
| `cpre.expression/1` | `cpre test-input --json` |

`cpre --json` and `cpre simplify-macros --json` write one file record directly for a single file. For several files (or `--recursive`) they write `{"schema": ..., "files": [record, ...]}` with `files` in command-line order, and the per-file records do not repeat `schema`.

## Conventions

- **Always present.** Every documented field is present in every document of its kind. A field is never omitted to mean "nothing".
- **`[]` means none.** A list field that is `[]` was computed and is empty. For example `"unresolved": []` means no condition depends on an unknown macro.
- **`null` means not applicable or unknown.** `"source": null` means no output text was produced (the run was incomplete). `"complete": null` in an analysis record means conditional analysis did not run (`--no-conditionals`), so completeness is unknown rather than `true`.
- **`complete` is the incompleteness signal.** `complete: false` means cpre stopped short of a full answer (a resource limit, or a condition the current macro state cannot decide). The reasons are in `incomplete`/`diagnostics`. A document with `complete: false` is a valid result, not an error document.
- **Lines are 1-based** and refer to the file named by `path`/`file`, unless a diagnostic carries a `source_identity` naming an included file.
- **Ordering is deterministic.** Collections follow source order (`groups`, branches, `lines`), name order (`macros` by source position, `unknown_macros` sorted by name), or command-line order (`files`). The same input gives byte-identical output across runs and hash seeds.
- **Private details are excluded.** Output never contains BDD node identifiers, internal object ids, or memory addresses. Internal dataclass fields are not public because they happen to exist; only fields listed below are.

## Documents

### `cpre.analysis/1` (file record)

`path`, `groups`, `complete`, `partial`, `incomplete`, `incomplete_components`, `macros`, `semantics`, `symbolic_literals`.

- `groups`: conditional groups, each `{line, end_line, branches}`; a branch is `{directive, line, condition, status, children, ...}`. `status` is one of `reachable`, `dead`, `redundant`. Branches that were simplified also carry `simplified_condition`, `contextual_condition`, `effective_condition`, `reason`, and `opaque_predicates`.
- `partial`: `true` when some components finished and others did not.
- `incomplete`: diagnostics `{code, resource, limit, observed, message, line, component}`. `resource`, `limit`, `observed` and `component` are `null` where they do not apply.
- `incomplete_components`: `{index, groups, atoms, diagnostics}` per component that did not finish; `groups` entries are `{line, end_line, directive, condition}`.
- `macros`: `{name, line, column, candidate, simplified, original, replacement, equivalent, rewritable, reason, semantics, symbolic_literals}`.
- `semantics` is `ordinary` or `symbolic-literal`; `symbolic_literals` lists the integers treated as free atoms.

### `cpre.simplify-macros/1` (file record)

`path`, `rewritten`, `applied_count`, `verified`, `semantics`, `symbolic_literals`, `macros` (same macro records as above). `rewritten` means the file was written.

### `cpre.preprocess/1`

`file`, `complete`, `source`, `diagnostics`, `skipped_includes`.

- `diagnostics`: `{code, message, line, source_identity, condition, unresolved}`. `condition` is `null` unless the diagnostic is about one conditional directive. Resource-limit diagnostics use the `resource`/`limit`/`observed`/`component` fields of the analysis diagnostics in addition.
- `unresolved`: macros the condition depends on, `{name, uses, lines, suggestions}`; `uses` is a list of `definedness` and/or `value`.
- `skipped_includes`: `{directive, operand, line, source_identity}`.

### `cpre.unknown-macros/1`

`file`, `unknown_macros` (sorted by name; each `{name, uses, lines, suggestions}`).

### `cpre.expression/1`

`input`, `simplified`, `equivalent`, `changed`, `semantics`, `symbolic_literals`.

## Compatibility and versioning

The number after the slash is the **major version** of that document kind.

- **Compatible changes keep the major version** and may appear in any release: a new field, a new `code`/`status`/`directive` value, a new document kind, or a clarified description. Consumers must ignore fields they do not know and must tolerate new enumerated values.
- **Incompatible changes raise the major version** of the affected kind: removing or renaming a field, changing its type or meaning, or changing ordering guarantees. The change is announced in the changelog, and this page documents both versions during the transition.
- `cpre --version` is not part of the document. The `schema` value is the only version identifier a consumer needs.
- Text output, `--sarif` output, and the diagnostics in stderr are outside this contract.

## Opt-in diagnostic data

Resource telemetry and similar diagnostic extras (see issue #103) are not part of the compatibility contract: they appear only when requested, under a clearly named key, and may change in any release.

## Consumers

New JSON output reuses these conventions and the builders in `cpre.serialization`: `simplify-macros --check`/`--diff` (#141), `--dump-macros` (#101) and `cpre cover` (#126) are planned.
