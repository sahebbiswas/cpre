# Exact Boolean queries and witnesses

`cpre` exposes exact symbolic Boolean reasoning through the top-level package without exposing ROBDD managers, node identifiers, unique tables, or atom-order maps. These APIs operate on the public symbolic expression model documented in [Python API integration](api.md).

## Public queries

```python
import cpre

A = cpre.Variable("A")
B = cpre.Variable("B")

sat = cpre.satisfiable(cpre.conjunction(A, B))
implication = cpre.implies(cpre.conjunction(A, B), A)
equivalence = cpre.equivalent(
    cpre.Disjunction((
        cpre.Conjunction((A, B)),
        cpre.Conjunction((A, cpre.Negation(B))),
    )),
    A,
)
simplified = cpre.exact_simplify(
    cpre.Disjunction((
        cpre.Conjunction((A, B)),
        cpre.Conjunction((A, cpre.Negation(B))),
    ))
)
witness = cpre.witness_assignment(cpre.conjunction(A, B))
```

The result types are `SatisfiabilityResult`, `ProofResult`, `SimplificationResult`, and `WitnessResult`. All have a `complete` property. A completed query is an exact proof over the Boolean atoms modeled by the expression; cpre does not return a guessed or truncated logical result.

For `ProofResult`, `holds=True` means the implication/equivalence was proven and `holds=False` means a counterexample exists. For `SatisfiabilityResult`, `satisfiable=False` means unsatisfiability was proven. For `SimplificationResult`, a non-`None` `expression` is globally equivalent to the input expression.

## Incomplete analysis and resource limits

All query functions accept the existing `AnalysisOptions` type. The defaults and semantics are therefore aligned with `analyze_source()`:

```python
options = cpre.AnalysisOptions(
    max_atoms=64,
    max_bdd_nodes=100_000,
    max_work=500_000,
)
result = cpre.implies(A, B, options=options)
```

If a deterministic resource limit is exceeded, `complete` is false and the logical result is `None`. The `incomplete` field is an `AnalysisIncomplete` value containing `resource`, `limit`, `observed`, and `code=ErrorCode.ANALYSIS_LIMIT_EXCEEDED`.

This distinction is intentional:

- incomplete satisfiability never masquerades as UNSAT;
- incomplete implication/equivalence never masquerades as a false or true proof;
- incomplete simplification returns `expression=None`, never an unproven rewrite;
- incomplete witness generation returns `satisfiable=None` and `assignment=None`.

Callers may choose their own conservative fallback after inspecting the structured metadata.

## Witness contract

`witness_assignment()` returns a deterministic satisfying assignment when one exists. `WitnessAssignment.kind` preserves the semantic atom category:

- `WitnessAtomKind.MACRO_VALUE` for `Variable(name)`;
- `WitnessAtomKind.MACRO_DEFINED` for `DefinedVariable(name)`;
- `WitnessAtomKind.PREDICATE` for `Predicate(text)`.

Macro truth/value and macro definedness are independent facts even when they use the same macro name. Opaque predicates are assigned only a Boolean truth value; cpre does not invent an integer value for predicate text.

Assignments are returned in deterministic semantic order. When an input atom is a don't-care for the selected satisfying path, cpre fills it with `False`. This policy includes atoms eliminated by normalization (for example, the `A` in `A || !A`) so repeated calls produce a stable complete assignment over the atoms referenced by the original input.

A completed UNSAT result has `satisfiable=False` and `assignment=None`. An incomplete result has `satisfiable=None` and `assignment=None`.

## Exactness boundary

These APIs reason over cpre's public Boolean atom model. `Predicate("VERSION >= 4")` is an opaque Boolean fact; the exact Boolean engine does not evaluate the underlying C integer expression. Exactness therefore means exact Boolean reasoning over the modeled atoms, not integer theorem proving.

`exact_simplify()` may return the normalized input unchanged when the internal exact form is not structurally smaller. It never requires callers to inspect implementation-specific BDD state.

## Determinism and compatibility

For the same public expression and `AnalysisOptions`, query results, witness ordering, don't-care choices, and limit behavior are deterministic and do not depend on Python hash seed.

The top-level query functions and result/witness types are part of the documented downstream compatibility contract. Consumers should import them from `cpre`, not from `cpre.proofs` or `cpre.robdd`. ROBDD implementation details remain private and may change without notice.
