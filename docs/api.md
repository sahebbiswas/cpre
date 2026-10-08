# Python API integration guide

Use the top-level `cpre` package as the supported Python compatibility boundary. This guide covers symbolic conditional analysis and the general integration contract. Concrete configuration selection and macro expansion live in the separate [preprocessing guide](preprocessing.md).

For command-line workflows, see the [CLI guide](cli.md). For process-to-process findings interchange, see [SARIF output](sarif.md).

## Supported import boundary

Import public symbols from `cpre`:

```python
import cpre

result = cpre.analyze_source(source_text, filename="src/example.c")
```

The names exported by `cpre.__all__` are the supported public boundary. Internal implementation modules such as `cpre.robdd`, `cpre.parser`, `cpre.model`, and `cpre.expressions` are not downstream APIs.

The public surface includes the symbolic expression model/algebra, symbolic analysis result/error model, source locations and edits, macro assumptions/configuration types, concrete preprocessing types, and macro Boolean simplification analysis (`analyze_macro()`, `analyze_macros()`, `MacroAnalysisResult`). This guide focuses on `analyze_source()` and the reusable symbolic expression surface; see [Macro Boolean simplification](macro-simplification.md) for object-like macro analysis, and [Concrete preprocessing](preprocessing.md) for `preprocess_source()`, `PreprocessResult`, `compact()`, macro environments, deterministic preprocessing context, opt-in include skipping (`skip_includes=True`, `SkippedInclude`), caller-resolved includes ([`include_resolver=`](include-resolution.md), `cpre.includes`), and pragma handling.

## Symbolic expression API

Downstream analyzers that need configuration-independent Boolean structure can construct and inspect expressions using top-level `cpre` imports only:

```python
import cpre

condition = cpre.conjunction(
    cpre.Variable("FEATURE"),
    cpre.DefinedVariable("CONFIG"),
    cpre.Predicate("VERSION >= 4"),
)

print(cpre.format_expression(condition))
```

The public node categories are:

- `Constant` / `TRUE` / `FALSE` for Boolean constants;
- `Variable(name)` for the truth/value of a macro;
- `DefinedVariable(name)` for macro definedness;
- `Predicate(text)` for an opaque C/preprocessor predicate that cpre does not interpret as an integer expression in this symbolic model;
- `Negation`, `Conjunction`, and `Disjunction` for Boolean structure;
- `Expression` and `BooleanAtom` as public typing aliases for those categories.

`Variable("A")`, `DefinedVariable("A")`, and `Predicate("A")` are intentionally different atoms even though two of them may render to the same text. Do not infer atom identity from rendered strings or class module paths.

### Deterministic algebra and formatting

Use the public helpers instead of depending on internal expression functions:

```python
a = cpre.Variable("A")
b = cpre.Variable("B")

expr = cpre.conjunction(a, a, b)
assert cpre.normalize(expr) == cpre.conjunction(a, b)
assert cpre.disjunction(a, cpre.negate(a)) == cpre.TRUE
```

`normalize()` and `simplify()` are equivalent public entry points for local Boolean normalization. They flatten associative nodes, remove identities and duplicates, detect complements, apply simple absorption, and order operands deterministically. They do **not** perform ROBDD/SAT proofs or evaluate opaque predicates.

`format_expression()` normalizes before rendering, so equivalent local structure has deterministic preprocessor-style output. `ordered_atoms()` returns every unique atom referenced by the original expression in deterministic semantic order; it intentionally inventories unsimplified input rather than dropping atoms eliminated by normalization. `expression_predicates()` returns the opaque predicate text set.

### Structured expression interchange

Use `expression_to_dict()` and `expression_from_dict()` when symbolic expressions need to cross cache/profile/artifact boundaries:

```python
encoded = cpre.expression_to_dict(condition)
restored = cpre.expression_from_dict(encoded)
assert restored == cpre.normalize(condition)
```

The representation is JSON-compatible and explicitly tagged by semantic node category (`constant`, `variable`, `defined`, `predicate`, `not`, `and`, `or`). Serialization is canonical: expressions are normalized first and operand order is deterministic. Deserialization rejects unknown kinds, missing/extra fields, and values with the wrong JSON shape rather than coercing them.

The structured form, not dataclass `repr()`, implementation-module paths, or incidental hash/set ordering, is the supported interchange representation. Patch releases preserve these public categories and tagged semantics; intentional incompatible changes follow the downstream compatibility policy described below.

## Basic analysis

```python
import cpre

try:
    result = cpre.analyze_source(source_text, filename="src/example.c")
except cpre.CpreError as error:
    handle_error(error)
else:
    if not result.complete:
        handle_incomplete(result.incomplete)
    else:
        for finding in result.findings:
            handle_finding(finding)
```

`filename` is source identity metadata. Supplying it is recommended so findings and diagnostics remain associated with their originating file.

A successful `AnalysisResult` exposes:

- `findings`: ordered structured `Finding` values;
- `tree`: the analyzed conditional tree for consumers that need structural context;
- `filename`: caller-supplied source identity;
- `complete`: whether exact analysis completed within the configured contract and limits;
- `incomplete`: structured diagnostics explaining why exact analysis did not complete.

Do not infer success from an empty findings tuple. Always check `result.complete` before treating analysis as clean.

When analysis is incomplete, cpre deliberately does not expose findings based on partial proofs.

## Finding kinds

`Finding.kind` is a `FindingKind` enum. Match enum members rather than rendered messages:

```python
for finding in result.findings:
    match finding.kind:
        case cpre.FindingKind.DEAD_BRANCH:
            report_dead_branch(finding)
        case cpre.FindingKind.REDUNDANT_BRANCH:
            report_redundant_branch(finding)
        case cpre.FindingKind.SIMPLIFIABLE_CONDITION:
            report_exact_simplification(finding)
        case cpre.FindingKind.CONTEXTUAL_SIMPLIFICATION:
            report_contextual_simplification(finding)
```

Findings carry physical source location, directive/condition context, a human-readable reason, and structured simplification/edit fields where applicable. `depends_on_assumptions` identifies proofs that materially depend on caller-supplied macro assumptions.

Treat human-readable messages as presentation. Use enum values and structured fields for automation.

## Exact and contextual simplifications

cpre deliberately distinguishes two guarantees:

- `ExactSimplification` is Boolean-equivalent to the original condition under the active assumptions.
- `ContextualSimplification` is equivalent only inside that branch's reachable context, including enclosing and preceding branch conditions.

Prefer the typed fields:

```python
if finding.exact_simplification is not None:
    replacement = finding.exact_simplification.replacement

if finding.contextual_simplification is not None:
    replacement = finding.contextual_simplification.replacement
```

A consumer should preserve the distinction rather than presenting contextual replacements as globally equivalent expressions.

## Suggested edits and source ranges

When cpre can describe a safe direct source replacement, `finding.edit` is a `SuggestedEdit`:

```python
edit = finding.edit
if edit is not None:
    start = edit.range.start
    end = edit.range.end
    replacement = edit.replacement
    confidence = edit.confidence
```

`SourceRange.end` is exclusive. Locations are physical, one-based source positions. Backslash-continued directives retain physical line/column provenance.

Dead/redundant branch findings intentionally do not imply mechanical branch deletion. Do not synthesize destructive fixes merely because a branch is unreachable or redundant; apply automatic changes only when an explicit structured edit is present and suitable for the host tool's policy.

## Macro assumptions

Without assumptions, symbolic analysis remains configuration-independent. Callers with known build state may constrain the proof.

For simple Boolean values:

```python
result = cpre.analyze_source(
    source_text,
    filename=path,
    assumptions={
        "FEATURE_A": True,
        "FEATURE_B": False,
    },
)
```

Use `MacroAssumptions` when definedness and Boolean value must be represented separately:

```python
assumptions = cpre.MacroAssumptions(
    defined={"FEATURE_A", "FEATURE_ZERO"},
    undefined={"FEATURE_B"},
    values={
        "FEATURE_A": True,
        "FEATURE_ZERO": False,
    },
)

result = cpre.analyze_source(source_text, assumptions=assumptions)
```

Unmentioned macros remain symbolic. cpre does not silently treat unknown names as false in symbolic analysis.

Use `Finding.depends_on_assumptions` when the host needs to distinguish configuration-independent findings from findings proven only under supplied assumptions.

For exact external macro replacement definitions and open/closed unknown-name policy during concrete preprocessing, use `MacroConfiguration` as described in [Concrete macro configuration](concrete-configuration.md) and [Concrete preprocessing](preprocessing.md).

## Deterministic resource limits

ROBDD reasoning is bounded deterministically. Callers may provide explicit limits:

```python
options = cpre.AnalysisOptions(
    max_atoms=64,
    max_bdd_nodes=100_000,
    max_work=500_000,
)

result = cpre.analyze_source(
    source_text,
    filename=path,
    options=options,
)
```

If a limit is exceeded, `analyze_source()` returns an incomplete `AnalysisResult` rather than raising solely because the configured proof budget was exhausted. Findings are never derived from a partial proof; when only some independent components hit a limit, the result is *partial* (see [Partial results and incomplete components](#partial-results-and-incomplete-components)).

```python
if not result.complete:
    for diagnostic in result.incomplete:
        print(
            diagnostic.code,
            diagnostic.resource,
            diagnostic.limit,
            diagnostic.observed,
            diagnostic.location,
        )
```

Downstream tools should preserve this distinction. Converting incomplete analysis into "no findings" creates false confidence.

### Independent components and budget semantics

`analyze_source()` does not build one Boolean model for the whole file. It partitions the analysis into **independent components** and applies the limits as follows:

- **What is coupled.** A top-level conditional group (`#if`/`#ifdef`/`#ifndef` with its `#elif` and `#else` branches) and every conditional nested inside it are analyzed together, because branch reachability depends on earlier branches and on enclosing branches. Two groups belong to the same component when they share a Boolean atom. When macro assumptions are supplied, `defined(X)` and the bare value `X` are distinct atoms that are coupled by C semantics, so groups that mention either form of the same macro are in one component. Assumed macros that no condition mentions are independent of every component.
- **Per component.** `max_atoms` and `max_bdd_nodes` apply to each component separately. Many unrelated conditionals no longer compete for one file-wide atom budget: a file with hundreds of independent `#ifdef FEATURE_n` blocks completes under the default `max_atoms=64`, while any single connected component with more than `max_atoms` atoms is still incomplete.
- **Global.** `max_work` is one cap shared by every component (and by the extra baseline pass performed when assumptions are supplied). Partitioning never gives each component a fresh work budget, and it never costs more work than analyzing the same groups one after another. Because creating a BDD node always consumes work, the total number of nodes across components is also bounded by `max_work`.
- **Diagnostics.** When a component exceeds `max_atoms`, `AnalysisIncomplete.observed` is that component's atom count, not the file total, and `location` stays `None`. Every too-large component is reported, in source order of its first group, so `result.incomplete[0]` is the one whose first atom appears earliest in the source. `bdd_nodes` and `work` diagnostics are located at the branch being analyzed, as before.
- **Unchanged results.** Within each component, atoms keep the order in which they first appear in the file, so findings, simplifications, and edits for a source that fit the previous file-wide limits are identical. Sources that were incomplete only because unrelated conditionals (or unused assumptions) exhausted the atom budget now complete.

```python
source = "".join(f"#if FEATURE_{n}\n#endif\n" for n in range(500))

cpre.analyze_source(source).complete                        # True: 500 components of one atom each
cpre.analyze_source(
    "#if A && B\n#endif\n#if B && C\n#endif\n",
    options=cpre.AnalysisOptions(max_atoms=2),
).complete                                                  # False: A, B, C form one component
cpre.analyze_source(
    "#if A && B\n#endif\n#if C && D\n#endif\n",
    options=cpre.AnalysisOptions(max_atoms=2),
).complete                                                  # True: two independent components
```

Partitioning applies to `analyze_source()`. The exact Boolean queries and macro simplification reason about a single expression at a time and apply `max_atoms` to that whole expression.

### Partial results and incomplete components

When one component exceeds `max_atoms` or `max_bdd_nodes`, the other components are still analyzed. The result is then **partial**:

| Field | Complete | Partial | Curtailed as a whole |
| --- | --- | --- | --- |
| `complete` | `True` | `False` | `False` |
| `partial` | `False` | `True` | `False` |
| `findings` | all findings | findings of the components that completed | `()` |
| `incomplete` | `()` | one `AnalysisIncomplete` per incomplete component, each with `component` set | one `AnalysisIncomplete` with `component=None` |
| `incomplete_components` | `()` | one `IncompleteComponent` per incomplete component | `()` |

The whole analysis is curtailed (no findings) when the shared `max_work` budget is exhausted, or when a limit is hit while deciding whether the supplied assumptions are satisfiable at all, because every component's results depend on that.

Each `IncompleteComponent` describes one set of coupled conditional groups:

- `index`: its position in `incomplete_components`; `AnalysisIncomplete.component` refers to it.
- `groups`: the top-level conditional groups (`IncompleteGroup`) in source order, each with the opening directive's `location`, the matching `#endif`'s `end_line`, the `directive` (`if`, `ifdef`, `ifndef`), and its `condition` text. Nested conditionals belong to their top-level group.
- `atoms`: the component's Boolean atoms (`"A"`, `"defined(A)"`, opaque predicate text) in first-occurrence order.
- `diagnostics`: the component-local `AnalysisIncomplete` diagnostics (the limit that was exceeded and its `resource`, `limit`, `observed`, `location`). They also appear in `result.incomplete`. `resource` is a shortcut for the first diagnostic's resource.
- `contains_line(line)`: whether a source line lies inside one of the component's groups.

No finding is reported for any line inside an incomplete component, and the branches of those groups in `result.tree` carry `analysis is None`. In particular, the absence of a dead-branch finding there means "not analyzed", not "reachable".

```python
source = (
    "#if A\n#if A\nx;\n#endif\n#endif\n"   # lines 1-5: redundant nested #if A
    "#if B && C && D\n#endif\n"              # lines 6-7: three atoms
)
result = cpre.analyze_source(source, options=cpre.AnalysisOptions(max_atoms=2))

result.complete                                   # False
result.partial                                    # True
[f.location.line for f in result.findings]        # [2]
component = result.incomplete_components[0]
[g.location.line for g in component.groups]       # [6]
component.atoms                                   # ('B', 'C', 'D')
component.resource                                # 'atoms'
result.incomplete[0].component                    # 0
```

Interpret results as follows:

1. `result.complete`: every finding is proven and every conditional was analyzed. An empty `findings` means the file is clean.
2. `result.partial`: every finding is proven and can be acted on, but the file is not clean. Report each `incomplete_components` entry as unanalyzed (for example, as a tool notification), and do not treat its lines as having no findings.
3. Otherwise the analysis was curtailed as a whole, and `findings` is empty.

`complete` keeps its meaning: it is `True` exactly when `incomplete` is empty, so code written before partial results existed still treats a partial result as incomplete.

## Unknown macro dependencies

Open-world preprocessing fails when a reachable condition depends on a macro the configuration does not fix. Instead of parsing diagnostic text, query the dependencies directly:

```python
import cpre

cpre.unknown_macros("__GNUC__ >= 4")
# (UnknownMacro(name='__GNUC__', uses=(MacroUse.VALUE,), locations=()),)

cpre.unknown_macros("defined(FEAT)")[0].suggestions
# ('-D FEAT', '-U FEAT')            -- definedness never suggests a value

for item in cpre.unknown_macros_in_source(source_text, configuration=configuration):
    print(item.name, [use.value for use in item.uses], [loc.line for loc in item.locations])
```

- `unknown_macros(expression, *, configuration=None)` inspects one `#if`/`#elif` expression. Configured definitions are expanded, so a name reached only through a configured macro's replacement list is reported.
- `unknown_macros_in_source(source, *, filename=None, configuration=None)` inspects every conditional directive in the source, reachable or not, because reachability itself depends on the unknown names. The result is a sound over-approximation suitable for seeding a configuration. A name is not unknown when the configuration fixes the state a use needs, or when an earlier `#define`/`#undef` in the same or an enclosing branch assigns it on every path to the use (so include-guarded bodies behave as expected). A definition in some other branch keeps the name unknown, and its replacement list is followed. Included headers are not read.

Each `UnknownMacro` has a `name`, `uses` (`MacroUse.DEFINEDNESS` for `defined`/`#ifdef`/`#ifndef` tests and `MacroUse.VALUE` for evaluated identifiers; definedness first), `locations` of the dependent directives in source order, and `suggestions` with command-line forms that would fix the state (`-D NAME=<value>` only for value uses). Results are sorted by name. Integer and character literals, `defined`, `__has_include`, `__LINE__`, and `__FILE__` are never unknown. Under `UnknownNamePolicy.UNDEFINED`, no name is unknown.

The same data appears on preprocessing failures: a `PreprocessDiagnostic` with `ErrorCode.UNRESOLVED_CONDITION` carries `condition` (the expression as written, or the macro name for `#ifdef`-style directives) and `unresolved`, the `UnknownMacro` entries that keep that condition undetermined under the exact macro state at that point.

```python
result = cpre.preprocess_source("#if __GNUC__ >= 4\n#endif\n")
diagnostic = result.incomplete[0]
diagnostic.condition                       # '__GNUC__ >= 4'
[item.name for item in diagnostic.unresolved]  # ['__GNUC__']
```

The unknown-macro API does not depend on the CLI and is intended for reuse by configuration-generation tooling.

## Structured errors

Malformed conditional source and invalid public API input use the `CpreError` hierarchy:

```python
try:
    result = cpre.analyze_source(source_text, filename=path)
except cpre.CpreError as error:
    handle_error(
        code=error.code,
        message=error.message,
        location=error.location,
        filename=error.filename,
    )
```

Use `ErrorCode` values and structured locations rather than parsing `str(error)` or terminal text. Unexpected programming errors are intentionally not collapsed into `CpreError`.

`analyze_source()` accepts source text and does not open files. File I/O policy belongs to the host application. `SOURCE_READ_ERROR` is primarily used by cpre's CLI/SARIF ingestion layer; library callers should map their own I/O failures into the diagnostic model appropriate for their host.

## Recommended downstream pattern

A static-analysis host should keep cpre focused on conditional reasoning:

```python
import cpre


def analyze_preprocessor_conditions(path: str, source: str):
    try:
        result = cpre.analyze_source(source, filename=path)
    except cpre.CpreError as error:
        return {
            "status": "error",
            "code": error.code.value,
            "location": error.location,
            "message": error.message,
        }

    if result.partial:
        return {
            "status": "partial",
            "findings": result.findings,
            "unanalyzed": result.incomplete_components,
        }

    if not result.complete:
        return {
            "status": "incomplete",
            "diagnostics": result.incomplete,
        }

    return {
        "status": "complete",
        "findings": result.findings,
    }
```

Recommended integration rules:

1. Let the host own file reading, build-system discovery, and policy.
2. Call `cpre.analyze_source()` once per source/configuration being analyzed.
3. Catch `CpreError` for supported cpre failures.
4. Check `result.complete` before treating a source as clean; on a `result.partial` result, consume the findings but also report the `incomplete_components`.
5. Map `FindingKind` through structured values, not messages.
6. Preserve `depends_on_assumptions` when configuration-specific reasoning is enabled.
7. Apply only explicit `SuggestedEdit` objects according to host policy.
8. Import supported symbols from `cpre`, not implementation modules.

For a host that instead needs one selected translation-unit representation, use the [preprocessing integration checklist](preprocessing.md#downstream-integration-checklist).

## Choosing an interchange surface

The CLI's JSON output is a structural conditional-tree report. It should not be treated as a substitute for the Python object model.

Use:

- the Python API when both components run in Python and need the richest structured contract;
- `expression_to_dict()` / `expression_from_dict()` for stable symbolic-expression interchange;
- [SARIF](sarif.md) when findings need to cross a process/tool boundary;
- CLI text for human-facing terminal workflows;
- [concrete preprocessing](preprocessing.md) when a downstream parser/analyzer needs selected source and provenance.

## Compatibility expectations

cpre is in Beta. The documented top-level API is intended for real downstream integrations, and compatibility-sensitive changes should be deliberate and documented.

Evergreen integration code should depend on documented types, enum values, result fields, symbolic expression categories/tagged interchange, and `cpre.__all__`, not private helpers or implementation-specific ROBDD/parser details.

For the broader transformation and downstream-compatibility boundary, see [Downstream compatibility](downstream-compatibility.md).
