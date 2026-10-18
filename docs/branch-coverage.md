# Branch-covering configurations

`cpre.cover_branches()` generates a small, deterministic set of concrete macro configurations that together exercise every reachable conditional-branch outcome in a source. Use it to build or test each configuration of a file, or to find branches no external configuration can reach.

```python
import cpre

source = """\
#if defined(USE_SSL) && !LEGACY
int tls;
#elif LEGACY
int legacy;
#endif
"""

result = cpre.cover_branches(source, filename="net.c")
assert result.complete

for number, item in enumerate(result.configurations):
    defined = {d.name: d.replacement for d in item.configuration.definitions}
    print(number, defined, sorted(item.configuration.undefined), item.covers)
# 0 {'USE_SSL': '1'} ['LEGACY'] (0,)
# 1 {'LEGACY': '1'} ['USE_SSL'] (1,)
# 2 {} ['LEGACY', 'USE_SSL'] (2,)

for outcome in result.outcomes:
    print(outcome.group_line, outcome.branch_line, outcome.status.value, outcome.configuration)
# 1 1 covered 0
# 1 3 covered 1
# 1 None covered 2
```

Each `item.configuration` is an ordinary closed-world `MacroConfiguration`. You can pass it to `cpre.preprocess_source(source, configuration=...)` or translate it into compiler `-D`/`-U` flags.

## Outcomes

An *outcome* is one branch of a conditional group in the primary source being selected, including `#else`. A group without `#else` has one more outcome, in which no branch is selected; it has `branch_line=None` and `directive=None`. Outcomes are listed in source order, with nested groups after the branch that contains them.

Each `BranchOutcome` has a `status`:

| Status | Meaning |
| --- | --- |
| `COVERED` | Concrete preprocessing selected this outcome under configuration `configuration`, an index into `result.configurations`. |
| `UNSATISFIABLE` | The outcome's path condition (the enclosing branches, earlier sibling conditions, and its own condition) is unsatisfiable, for example `#if A && !A`, a branch inside `#if 0`, or `#else` after `#if 1`. No configuration can reach it. |
| `NOT_COVERED` | Satisfiable in the Boolean model, but none of the generated configurations reached it. `reason` explains why. |

## How configurations are generated

1. Each outcome's path condition is built from the conditional structure, together with the C rule that a true macro value implies the macro is defined.
2. Top-level groups that share no macro form independent components with separate Boolean managers. As in `analyze_source()`, `max_atoms` and `max_bdd_nodes` apply per component and `max_work` is shared.
3. Within each component, plans are built greedily in source order. The first unplanned outcome is conjoined with every later unplanned outcome that stays jointly satisfiable. The k-th plans of all components are combined into one configuration.
4. A deterministic false-first witness of each plan becomes the configuration. Every conditional macro is either defined as `1` (defined and true), defined as `0` (defined and false), or explicitly undefined, under `unknown_names="undefined"`; a macro compared with integer literals instead receives the integer solved for it (see [Integer comparisons](#integer-comparisons)).
5. **Every configuration is verified** with `cpre.preprocess_source()`, and credited only with the outcomes preprocessing actually selected, including outcomes it was not generated for.
6. Each outcome still uncovered gets one more attempt with a configuration that targets it alone.
7. Configurations whose outcomes are all covered by other configurations are dropped.

The result is **sufficient** for every `COVERED` outcome: re-running the listed configurations reproduces the claimed coverage. It is **greedy, not minimal**. A smaller set may exist.

Each `CoverageConfiguration` also reports:

- `required`: a reduced set of Boolean assignments (`WitnessAssignment`) that by itself guarantees, in the Boolean model, the outcomes the configuration was generated for.
- `dont_care`: conditional macros that set leaves free. `configuration` still pins each of them to one concrete choice.
- `covers`: indices into `result.outcomes` of every outcome preprocessing selected.

## Integer comparisons

A comparison of a macro with an integer literal, `NAME op INT` or `INT op NAME` with `op` one of `==`, `!=`, `<`, `<=`, `>`, `>=` (decimal or hex, optional leading minus), is solved rather than left opaque. For each macro, cpre chooses an integer that satisfies every comparison the plan requires on it together: the candidates are `0`, `1` and each literal with its neighbours (`INT - 1`, `INT`, `INT + 1`), tried in ascending order, so `VERSION >= 3` gets `VERSION=3`, `V >= 3 && V < 5` gets `V=3`, and `V > 3` gets `V=4`. The Boolean model is told which combinations of the comparisons on one macro are possible, so it never proposes `V >= 5` true with `V >= 3` false. Truthiness (`#if V`) and definedness (`defined(V)`) of the same macro are kept consistent with the chosen value. Macros that the preprocessing context fixes are never assigned, and a macro with more than eight distinct comparisons falls back to opaque handling. Every configuration is still verified by concrete preprocessing, so a value is credited only if it really selects the outcome.

## Why an outcome can be `NOT_COVERED`

Path conditions use the Boolean model of `analyze_source()`, which treats each macro as an independent external input. Concrete preprocessing can disagree, and verification catches that instead of reporting false coverage:

- **Source-order `#define`/`#undef`.** In `#define A 1` / `#if A` / `#else`, the `#else` is unreachable from outside the file. The reason names the overriding macro.
- **Opaque expressions.** Anything but a comparison of a macro with an integer literal, such as `#if VERSION % 2 == 1`, is a predicate the Boolean model cannot solve, and cpre does not invent integer values for it. The reason names the predicate.
- **Contradictory comparisons.** `#if V > 3 && V < 2` requires one value of `V` to satisfy both, which none does; the outcome is `NOT_COVERED` with that reason, never reported as covered.
- **Names fixed by `context`.** Standard macros supplied through `PreprocessingContext` are never configured, so outcomes that need a different value stay uncovered.
- **Incomplete preprocessing.** An unresolved `#include`, an unsupported directive, or an unanswered `__has_include` yields `preprocessing was incomplete: line N: ...`. Pass `skip_includes`, `include_resolver`, `max_include_depth`, `include_query`, `has_include_from_resolver`, or `pragma_handler` to `cover_branches()`; they are forwarded to each verification run, together with `context`.

## Incomplete results

If exact analysis, or concrete preprocessing of a generated configuration, exceeds a resource limit, the result is atomic: `complete` is `False`, `configurations` and `outcomes` are `None`, and `incomplete` is an `AnalysisIncomplete` naming the limit. Raise limits with `options=cpre.AnalysisOptions(...)`. The same options are used for verification. Malformed conditionals raise `ParseError`, as in `analyze_source()`.

## Determinism

For the same input and options, `cover_branches()` returns the same configurations, outcomes, and order, regardless of `PYTHONHASHSEED`.

## Current boundaries

- Only conditionals in the primary source are targets. Included headers are preprocessed during verification but their branches are not counted.
- There is no CLI command yet ([#126](https://github.com/sahebbiswas/cpre/issues/126)).
- Macro values are `1`, `0`, undefined, or an integer chosen for simple comparisons (see [Integer comparisons](#integer-comparisons)). A macro in an opaque expression such as `VERSION % 2 == 1` still gets only `1`, `0` or undefined.
