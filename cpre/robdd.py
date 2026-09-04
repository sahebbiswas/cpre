"""Internal dependency-free ROBDD engine and exact/contextual simplification."""

from __future__ import annotations

from typing import Iterable

from .expressions import conjunction, disjunction, negate, simplify
from .model import (
    BooleanAtom, Conjunction, Constant, Disjunction, Expression, FALSE,
    Negation, Predicate, TRUE, Variable,
)


class BDD:
    def __init__(self, atoms: Iterable[BooleanAtom]):
        ordered_atoms = list(dict.fromkeys(atoms))
        self.order = {atom: index for index, atom in enumerate(ordered_atoms)}
        self.atoms = ordered_atoms
        self.nodes: list[tuple[int, int, int] | None] = [None, None]
        self.unique: dict[tuple[int, int, int], int] = {}
        self._apply_cache: dict[tuple[str, int, int], int] = {}
        self._not_cache: dict[int, int] = {0: 1, 1: 0}
        self._build_cache: dict[Expression, int] = {}
        self._expression_cache: dict[int, Expression] = {0: FALSE, 1: TRUE}

    def node_count(self, root: int) -> int:
        reachable: set[int] = set()
        pending = [root]
        while pending:
            node = pending.pop()
            if node < 2 or node in reachable:
                continue
            reachable.add(node)
            item = self.nodes[node]
            assert item is not None
            _, low, high = item
            pending.extend((low, high))
        return len(reachable)

    def _node(self, variable: int, low: int, high: int) -> int:
        if low == high:
            return low
        key = (variable, low, high)
        if key not in self.unique:
            self.unique[key] = len(self.nodes)
            self.nodes.append(key)
        return self.unique[key]

    def build(self, expression: Expression) -> int:
        expression = simplify(expression)
        cached = self._build_cache.get(expression)
        if cached is not None:
            return cached
        if isinstance(expression, Constant):
            result = int(expression.value)
        elif isinstance(expression, (Variable, Predicate)):
            result = self._node(self.order[expression], 0, 1)
        elif isinstance(expression, Negation):
            result = self.negate(self.build(expression.operand))
        elif isinstance(expression, Conjunction):
            result = 1
            for operand in expression.operands:
                result = self.apply("and", result, self.build(operand))
        else:
            result = 0
            for operand in expression.operands:
                result = self.apply("or", result, self.build(operand))
        self._build_cache[expression] = result
        return result

    def negate(self, node: int) -> int:
        if node in self._not_cache:
            return self._not_cache[node]
        item = self.nodes[node]
        assert item is not None
        variable, low, high = item
        result = self._node(variable, self.negate(low), self.negate(high))
        self._not_cache[node] = result
        return result

    def apply(self, operation: str, left: int, right: int) -> int:
        if left > right:
            left, right = right, left
        key = (operation, left, right)
        if key in self._apply_cache:
            return self._apply_cache[key]
        if operation == "and":
            if left == 0 or right == 0:
                return 0
            if left == 1:
                return right
            if left == right:
                return left
        elif operation == "or":
            if left == 1 or right == 1:
                return 1
            if left == 0:
                return right
            if left == right:
                return left
        else:
            raise ValueError(f"unknown BDD operation: {operation}")
        left_node = self.nodes[left]
        right_node = self.nodes[right]
        assert left_node is not None and right_node is not None
        variable = min(left_node[0], right_node[0])
        left_low, left_high = (left_node[1], left_node[2]) if left_node[0] == variable else (left, left)
        right_low, right_high = (right_node[1], right_node[2]) if right_node[0] == variable else (right, right)
        result = self._node(
            variable,
            self.apply(operation, left_low, right_low),
            self.apply(operation, left_high, right_high),
        )
        self._apply_cache[key] = result
        return result

    def satisfiable(self, expression: Expression) -> bool:
        return self.build(expression) != 0

    def to_expression(self, node: int) -> Expression:
        cached = self._expression_cache.get(node)
        if cached is not None:
            return cached
        item = self.nodes[node]
        assert item is not None
        variable_index, low_node, high_node = item
        variable = self.atoms[variable_index]
        low = self.to_expression(low_node)
        high = self.to_expression(high_node)
        if low == FALSE:
            result = conjunction(variable, high)
        elif high == FALSE:
            result = conjunction(negate(variable), low)
        elif low == TRUE:
            result = disjunction(negate(variable), high)
        elif high == TRUE:
            result = disjunction(variable, low)
        else:
            result = disjunction(
                conjunction(negate(variable), low),
                conjunction(variable, high),
            )
        self._expression_cache[node] = result
        return result

    def equivalent_under(self, context: Expression, left: Expression, right: Expression) -> bool:
        left_node = self.build(left)
        right_node = self.build(right)
        difference = self.apply(
            "or",
            self.apply("and", left_node, self.negate(right_node)),
            self.apply("and", self.negate(left_node), right_node),
        )
        return self.apply("and", self.build(context), difference) == 0


def _expression_size(expression: Expression) -> int:
    if isinstance(expression, (Constant, Variable, Predicate)):
        return 1
    if isinstance(expression, Negation):
        return 1 + _expression_size(expression.operand)
    return 1 + sum(_expression_size(operand) for operand in expression.operands)


def exact_simplify(expression: Expression, bdd: BDD) -> Expression:
    algebraic = simplify(expression)
    canonical = bdd.to_expression(bdd.build(algebraic))
    return canonical if _expression_size(canonical) < _expression_size(algebraic) else algebraic


def simplify_under(expression: Expression, context: Expression, bdd: BDD) -> Expression:
    expression = simplify(expression)
    if bdd.equivalent_under(context, expression, TRUE):
        return TRUE
    if bdd.equivalent_under(context, expression, FALSE):
        return FALSE
    if isinstance(expression, Negation):
        return negate(simplify_under(expression.operand, context, bdd))
    if not isinstance(expression, (Conjunction, Disjunction)):
        return expression
    operands = [simplify_under(item, context, bdd) for item in expression.operands]
    constructor = conjunction if isinstance(expression, Conjunction) else disjunction
    candidate = constructor(*operands)
    if not isinstance(candidate, (Conjunction, Disjunction)):
        return candidate
    operands = list(candidate.operands)
    changed = True
    while changed and len(operands) > 1:
        changed = False
        for index in range(len(operands)):
            trial = constructor(*(operands[:index] + operands[index + 1 :]))
            if bdd.equivalent_under(context, candidate, trial):
                candidate = trial
                operands.pop(index)
                changed = True
                break
    return candidate


__all__ = ["BDD", "exact_simplify", "simplify_under"]
