"""Reusable closed-world AST checks for the D68 EventLog authority."""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


APPROVED_EVENT_LOG_AUTHORITIES = frozenset(
    (
        ("vocab/corpus.py", "emit_scan", "ENCOUNTER"),
        ("vocab/forge/pipeline.py", "_log_event", "FORGE"),
        ("vocab/forge/recovery.py", "repair_evidence", "FORGE"),
        ("vocab/forge/recovery.py", "abandon_intent", "FORGE"),
        ("vocab/reconcile.py", "_append_state_event", "STATE"),
        ("vocab/assessment_producer.py", "_append_judge", "JUDGE"),
        ("vocab/assessment_producer.py", "_append_speak", "SPEAK"),
    )
)

APPROVED_LIFECYCLE_HISTORY_READS = frozenset(
    (
        (
            "vocab/reconcile.py",
            "_read_recovery_transactions",
            "read_strict",
            1,
        ),
        (
            "vocab/reconcile.py",
            "_load_event_history",
            "read_strict",
            1,
        ),
    )
)

APPROVED_STATE_MATERIALIZERS = frozenset(
    (
        (
            "vocab/reconcile.py",
            "_materialize_ungrouped_plan",
            "T9_STATE_PHASE_PREPARE",
            1,
        ),
        (
            "vocab/reconcile.py",
            "_materialize_ungrouped_plan",
            "T9_STATE_PHASE_COMMIT",
            1,
        ),
        (
            "vocab/reconcile.py",
            "_materialize_dormancy_plans",
            "T9_STATE_PHASE_PREPARE",
            1,
        ),
        (
            "vocab/reconcile.py",
            "_materialize_dormancy_plans",
            "T9_STATE_PHASE_COMMIT",
            1,
        ),
        (
            "vocab/reconcile.py",
            "_recover_ungrouped",
            "T9_STATE_PHASE_COMMIT",
            1,
        ),
        (
            "vocab/reconcile.py",
            "_recover_dormancy_group",
            "T9_STATE_PHASE_COMMIT",
            1,
        ),
        (
            "vocab/reconcile.py",
            "_abort_prepared_plans",
            "T9_STATE_PHASE_ABORT",
            1,
        ),
    )
)


@dataclass(frozen=True, slots=True)
class _LogUse:
    path: str
    scope: tuple[tuple[str, str], ...]
    qualified_scope: str
    line: int
    direct_call: bool
    actual: str
    literal: str | None


@dataclass(frozen=True, slots=True)
class _AttributeUse:
    path: str
    scope: tuple[tuple[str, str], ...]
    qualified_scope: str
    line: int
    attribute: str
    direct_call: bool


@dataclass(frozen=True, slots=True)
class _StateMaterializerUse:
    path: str
    scope: tuple[tuple[str, str], ...]
    qualified_scope: str
    line: int
    direct_call: bool
    phase_name: str | None
    actual: str


def assert_t12_ast_invariants(repository_root: Path) -> None:
    """Assert the exact D68 matrix, import allowlist, and producer rules."""
    root = Path(repository_root)
    production = root / "vocab"
    log_uses: list[_LogUse] = []
    concrete_importers: list[tuple[str, str, int]] = []
    constant_getattrs: list[tuple[str, str, int]] = []
    lifecycle_reads: list[_AttributeUse] = []
    tolerant_reconcile_reads: list[_AttributeUse] = []
    state_materializers: list[_StateMaterializerUse] = []
    eventlog_constructors: list[tuple[str, str, int]] = []

    for path in sorted(production.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        visitor = _ProductionVisitor(relative)
        visitor.visit(tree)
        log_uses.extend(visitor.log_uses)
        concrete_importers.extend(visitor.concrete_importers)
        constant_getattrs.extend(visitor.constant_log_getattrs)
        lifecycle_reads.extend(visitor.lifecycle_reads)
        tolerant_reconcile_reads.extend(visitor.tolerant_reconcile_reads)
        state_materializers.extend(visitor.state_materializers)
        eventlog_constructors.extend(visitor.eventlog_constructors)

    failures: list[str] = []
    observed: Counter[tuple[str, str, str]] = Counter()
    approved_by_location = {
        (path, (("function", scope),)): (scope, event_type)
        for path, scope, event_type in APPROVED_EVENT_LOG_AUTHORITIES
    }
    for use in log_uses:
        authority = approved_by_location.get((use.path, use.scope))
        expected = authority[1] if authority is not None else None
        detail = (
            f"{use.path}:{use.line} scope={use.qualified_scope} "
            f"expected={expected!r} actual={use.actual}"
        )
        if not use.direct_call:
            failures.append(f"captured .log authority: {detail}")
            continue
        if expected is None or use.literal != expected:
            failures.append(f"unapproved EventLog authority: {detail}")
            continue
        observed[(use.path, authority[0], use.literal)] += 1

    for approved in sorted(APPROVED_EVENT_LOG_AUTHORITIES):
        count = observed[approved]
        if count != 1:
            failures.append(
                f"approved EventLog authority count is {count}, expected 1: "
                f"path={approved[0]} scope={approved[1]} event={approved[2]!r}"
            )

    for path, scope, line in constant_getattrs:
        failures.append(
            f"getattr(..., 'log') captures authority: {path}:{line} scope={scope}"
        )
    for path, imported, line in concrete_importers:
        if path != "vocab/assessment_producer.py":
            failures.append(
                f"concrete EventLog import is not allowed: {path}:{line} "
                f"scope=<module> import={imported}"
            )

    for use in tolerant_reconcile_reads:
        failures.append(
            f"tolerant .read is forbidden in reconcile: {use.path}:{use.line} "
            f"scope={use.qualified_scope}"
        )

    approved_reads = {
        (path, (("function", scope),), attribute): count
        for path, scope, attribute, count in APPROVED_LIFECYCLE_HISTORY_READS
    }
    observed_reads: Counter[tuple[str, str, str]] = Counter()
    for use in lifecycle_reads:
        expected = approved_reads.get((use.path, use.scope, use.attribute))
        detail = (
            f"{use.path}:{use.line} scope={use.qualified_scope} "
            f"attribute={use.attribute!r}"
        )
        if not use.direct_call:
            failures.append(f"captured lifecycle history read authority: {detail}")
            continue
        if expected is None:
            failures.append(f"unapproved lifecycle history read: {detail}")
            continue
        observed_reads[(use.path, use.scope[0][1], use.attribute)] += 1
    for path, scope, attribute, count in sorted(
        APPROVED_LIFECYCLE_HISTORY_READS
    ):
        actual_count = observed_reads[(path, scope, attribute)]
        if actual_count != count:
            failures.append(
                f"approved lifecycle history read count is {actual_count}, "
                f"expected {count}: path={path} scope={scope} "
                f"attribute={attribute!r}"
            )

    approved_state = {
        (path, (("function", scope),), phase): count
        for path, scope, phase, count in APPROVED_STATE_MATERIALIZERS
    }
    observed_state: Counter[tuple[str, str, str]] = Counter()
    for use in state_materializers:
        expected = (
            None
            if use.phase_name is None
            else approved_state.get((use.path, use.scope, use.phase_name))
        )
        detail = (
            f"{use.path}:{use.line} scope={use.qualified_scope} "
            f"phase={use.actual}"
        )
        if not use.direct_call:
            failures.append(f"captured STATE materializer authority: {detail}")
            continue
        if expected is None:
            failures.append(f"unapproved STATE materializer authority: {detail}")
            continue
        observed_state[(use.path, use.scope[0][1], use.phase_name)] += 1
    for path, scope, phase, count in sorted(APPROVED_STATE_MATERIALIZERS):
        actual_count = observed_state[(path, scope, phase)]
        if actual_count != count:
            failures.append(
                f"approved STATE materializer count is {actual_count}, "
                f"expected {count}: path={path} scope={scope} phase={phase}"
            )

    for path, scope, line in eventlog_constructors:
        failures.append(
            f"production EventLog construction is forbidden: "
            f"{path}:{line} scope={scope}"
        )

    failures.extend(_producer_failures(root / "vocab" / "assessment_producer.py"))
    if failures:
        raise AssertionError("\n".join(failures))


def _literal_assignment(tree: ast.Module, name: str) -> ast.AST | None:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return node.value
    return None


def _load_d58_inventory(root: Path) -> tuple[ast.Module, dict[str, object]]:
    path = root / "tests/d58_probe_inventory.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename="tests/d58_probe_inventory.py")
    namespace: dict[str, object] = {
        "__file__": str(path),
        "__name__": "_d58_probe_inventory_invariant_copy",
    }
    exec(compile(tree, str(path), "exec"), namespace)
    return tree, namespace


def _semantic_anchor_fingerprints(
    root: Path,
    failures: list[str],
) -> tuple[tuple[object, ...], ...]:
    relative = "tests/test_t11_invariant_probes.py"
    tree = ast.parse(
        (root / relative).read_text(encoding="utf-8"),
        filename=relative,
    )
    value = _literal_assignment(tree, "SEMANTIC_ANCHORS")
    if not isinstance(value, (ast.Tuple, ast.List)):
        failures.append("SEMANTIC_ANCHORS is not an explicit literal sequence")
        return ()

    outcome_names = {
        "ASSESSMENT_OUTCOME_PASS": "PASS",
        "ASSESSMENT_OUTCOME_FAIL": "FAIL",
        "ASSESSMENT_OUTCOME_OMITTED": "OMITTED",
        "ASSESSMENT_OUTCOME_ABSTAIN": "ABSTAIN",
    }
    fingerprints: list[tuple[object, ...]] = []
    for index, row in enumerate(value.elts):
        if not isinstance(row, (ast.Tuple, ast.List)) or len(row.elts) != 6:
            failures.append(f"SEMANTIC_ANCHORS row {index} has invalid shape")
            continue
        channel, _task_content, outcome, failure_code, reason_code, target_present = (
            row.elts
        )
        if not (
            isinstance(channel, ast.Constant)
            and isinstance(channel.value, str)
            and isinstance(outcome, ast.Name)
            and outcome.id in outcome_names
            and isinstance(failure_code, ast.Constant)
            and isinstance(failure_code.value, str)
            and isinstance(reason_code, ast.Constant)
            and isinstance(reason_code.value, str)
            and isinstance(target_present, ast.Constant)
            and isinstance(target_present.value, bool)
        ):
            failures.append(
                f"SEMANTIC_ANCHORS row {index} cannot be statically fingerprinted"
            )
            continue
        fingerprints.append(
            (
                channel.value,
                outcome_names[outcome.id],
                failure_code.value,
                reason_code.value,
                target_present.value,
            )
        )
    return tuple(fingerprints)


def assert_d58_probe_invariants(repository_root: Path) -> None:
    """Assert the frozen registry is complete, resolvable, and unskippable."""
    from vocab.contracts import T12_LIFECYCLE_ENABLED_CHANNELS

    root = Path(repository_root)
    failures: list[str] = []
    inventory_tree, inventory = _load_d58_inventory(root)
    D58_CLAUSES = inventory["D58_CLAUSES"]
    D58_PROBE_INVENTORY = inventory["D58_PROBE_INVENTORY"]
    D58_SEMANTIC_ANCHOR_CASES = inventory["D58_SEMANTIC_ANCHOR_CASES"]
    REQUIRED_CLAUSES_BY_CHANNEL = inventory["REQUIRED_CLAUSES_BY_CHANNEL"]
    REQUIRED_D58_SELECTORS = inventory["REQUIRED_D58_SELECTORS"]

    clauses_node = _literal_assignment(inventory_tree, "D58_CLAUSES")
    if not (
        isinstance(clauses_node, ast.Tuple)
        and all(
            isinstance(element, ast.Constant) and isinstance(element.value, str)
            for element in clauses_node.elts
        )
    ):
        failures.append("D58_CLAUSES must be an explicit literal tuple of strings")
    anchor_cases_node = _literal_assignment(
        inventory_tree, "D58_SEMANTIC_ANCHOR_CASES"
    )
    try:
        literal_anchor_cases = ast.literal_eval(anchor_cases_node)
    except (TypeError, ValueError):
        literal_anchor_cases = None
    if not isinstance(anchor_cases_node, ast.Tuple) or literal_anchor_cases is None:
        failures.append(
            "D58_SEMANTIC_ANCHOR_CASES must be an explicit independent literal tuple"
        )
    if len(D58_CLAUSES) != len(set(D58_CLAUSES)):
        failures.append("D58_CLAUSES contains duplicate mandatory clause names")
    if set(D58_PROBE_INVENTORY) != set(D58_CLAUSES):
        failures.append("D58 inventory keys do not equal the frozen clause set")
    for clause in D58_CLAUSES:
        if not D58_PROBE_INVENTORY.get(clause):
            failures.append(f"D58 clause has no registered selector: {clause}")
    inventory_selectors = frozenset(
        selector
        for selectors in D58_PROBE_INVENTORY.values()
        for selector in selectors
    )
    if inventory_selectors != REQUIRED_D58_SELECTORS:
        failures.append(
            "D58 required selector set does not equal the clause registry"
        )
    actual_anchor_cases = _semantic_anchor_fingerprints(root, failures)
    if len(D58_SEMANTIC_ANCHOR_CASES) != len(set(D58_SEMANTIC_ANCHOR_CASES)):
        failures.append("frozen semantic-anchor inventory contains duplicates")
    if (
        len(actual_anchor_cases) != len(D58_SEMANTIC_ANCHOR_CASES)
        or set(actual_anchor_cases) != set(D58_SEMANTIC_ANCHOR_CASES)
    ):
        failures.append(
            "SEMANTIC_ANCHORS does not exactly equal the frozen semantic-anchor cases"
        )
    if set(REQUIRED_CLAUSES_BY_CHANNEL) != set(T12_LIFECYCLE_ENABLED_CHANNELS):
        failures.append("D58 channel inventory does not match enabled channels")
    for channel, clauses in REQUIRED_CLAUSES_BY_CHANNEL.items():
        missing = set(clauses).difference(D58_PROBE_INVENTORY)
        if missing:
            failures.append(
                f"D58 channel {channel} has unknown required clauses: "
                f"{tuple(sorted(missing))}"
            )
        semantic_clauses = {
            clause
            for clause in D58_CLAUSES
            if clause.startswith(f"semantic_anchor_{channel}_")
        }
        if not semantic_clauses or not semantic_clauses.issubset(clauses):
            failures.append(
                f"D58 channel {channel} lacks its complete semantic anchors"
            )

    modules: dict[str, ast.Module] = {}
    registered_names: dict[str, set[str]] = {}
    for selector in sorted(REQUIRED_D58_SELECTORS):
        parts = selector.split("::")
        if len(parts) != 2 or "[" in selector:
            failures.append(f"invalid base pytest selector: {selector}")
            continue
        relative, test_name = parts
        path = root / relative
        if not path.is_file():
            failures.append(f"registered probe module is missing: {relative}")
            continue
        tree = modules.get(relative)
        if tree is None:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            modules[relative] = tree
            registered_names[relative] = {
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
        if test_name not in registered_names[relative]:
            failures.append(f"registered probe node is missing: {selector}")

    forbidden_attributes = {"skip", "skipif", "xfail", "importorskip"}
    for relative, tree in modules.items():
        forbidden_aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in (
                "pytest",
                "unittest",
            ):
                for imported in node.names:
                    if (
                        imported.name in forbidden_attributes
                        or imported.name.startswith("skip")
                    ):
                        forbidden_aliases.add(imported.asname or imported.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "pytestmark":
                failures.append(
                    f"registered probe module declares pytestmark: "
                    f"{relative}:{node.lineno}"
                )
            if isinstance(node, ast.Attribute) and (
                node.attr in forbidden_attributes or node.attr.startswith("skip")
            ):
                failures.append(
                    f"registered probe module contains forbidden skip/xfail "
                    f"authority: {relative}:{node.lineno} attr={node.attr}"
                )
            if isinstance(node, ast.Name) and node.id in forbidden_attributes:
                failures.append(
                    f"registered probe module contains aliased skip/xfail "
                    f"authority: {relative}:{node.lineno} name={node.id}"
                )
            if isinstance(node, ast.Name) and node.id in forbidden_aliases:
                failures.append(
                    f"registered probe module contains imported skip/xfail "
                    f"alias: {relative}:{node.lineno} name={node.id}"
                )
    if failures:
        raise AssertionError("\n".join(failures))


class _ProductionVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.scope: list[tuple[str, str]] = []
        self.parents: list[ast.AST] = []
        self.log_uses: list[_LogUse] = []
        self.concrete_importers: list[tuple[str, str, int]] = []
        self.constant_log_getattrs: list[tuple[str, str, int]] = []
        self.lifecycle_reads: list[_AttributeUse] = []
        self.tolerant_reconcile_reads: list[_AttributeUse] = []
        self.state_materializers: list[_StateMaterializerUse] = []
        self.eventlog_constructors: list[tuple[str, str, int]] = []

    def visit(self, node: ast.AST) -> None:
        self.parents.append(node)
        super().visit(node)
        self.parents.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, kind="function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, kind="async-function")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)
        self.scope.append(("class", node.name))
        for statement in node.body:
            self.visit(statement)
        self.scope.pop()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit(node.args)
        self.scope.append(("lambda", f"<lambda@{node.lineno}>"))
        self.visit(node.body)
        self.scope.pop()

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        parent = self.parents[-2] if len(self.parents) >= 2 else None
        direct = isinstance(parent, ast.Call) and parent.func is node
        if self.path == "vocab/reconcile.py" and node.attr == "read":
            self.tolerant_reconcile_reads.append(
                _AttributeUse(
                    self.path,
                    tuple(self.scope),
                    self._qualified_scope(),
                    node.lineno,
                    node.attr,
                    direct,
                )
            )
        if self.path == "vocab/reconcile.py" and node.attr == "read_strict":
            self.lifecycle_reads.append(
                _AttributeUse(
                    self.path,
                    tuple(self.scope),
                    self._qualified_scope(),
                    node.lineno,
                    node.attr,
                    direct,
                )
            )
        if node.attr == "log":
            literal: str | None = None
            actual = "<captured attribute>"
            if direct:
                call = parent
                event_keywords = [item for item in call.keywords if item.arg == "event"]
                if event_keywords:
                    actual = "event=" + ast.dump(
                        event_keywords[0].value,
                        include_attributes=False,
                    )
                elif call.args:
                    argument = call.args[0]
                    if isinstance(argument, ast.Constant) and type(argument.value) is str:
                        literal = argument.value
                        actual = repr(argument.value)
                    else:
                        actual = ast.dump(argument, include_attributes=False)
                else:
                    actual = "<missing event argument>"
            self.log_uses.append(
                _LogUse(
                    self.path,
                    tuple(self.scope),
                    self._qualified_scope(),
                    node.lineno,
                    direct,
                    actual,
                    literal,
                )
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "EventLog"
            or isinstance(node.func, ast.Attribute)
            and node.func.attr == "EventLog"
        ):
            self.eventlog_constructors.append(
                (self.path, self._qualified_scope(), node.lineno)
            )
        if (
            (
                isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                or isinstance(node.func, ast.Attribute)
                and node.func.attr == "getattr"
            )
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "log"
        ):
            self.constant_log_getattrs.append(
                (self.path, self._qualified_scope(), node.lineno)
            )
        if (
            self.path == "vocab/reconcile.py"
            and (
                isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                or isinstance(node.func, ast.Attribute)
                and node.func.attr == "getattr"
            )
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "read_strict"
        ):
            self.lifecycle_reads.append(
                _AttributeUse(
                    self.path,
                    tuple(self.scope),
                    self._qualified_scope(),
                    node.lineno,
                    "read_strict",
                    False,
                )
            )
        if (
            self.path == "vocab/reconcile.py"
            and (
                isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                or isinstance(node.func, ast.Attribute)
                and node.func.attr == "getattr"
            )
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "_append_state_event"
        ):
            self._record_indirect_state_materializer(
                node,
                "getattr(..., '_append_state_event')",
            )
        if (
            self.path == "vocab/reconcile.py"
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "_append_state_event"
            and self._is_reflective_mapping(node.func.value)
        ):
            self._record_indirect_state_materializer(
                node,
                "reflective mapping .get('_append_state_event')",
            )
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if (
            self.path == "vocab/reconcile.py"
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "_append_state_event"
            and self._is_reflective_mapping(node.value)
        ):
            self._record_indirect_state_materializer(
                node,
                "reflective mapping['_append_state_event']",
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if self.path == "vocab/reconcile.py" and node.id == "_append_state_event":
            parent = self.parents[-2] if len(self.parents) >= 2 else None
            direct = isinstance(parent, ast.Call) and parent.func is node
            phase_name: str | None = None
            actual = "<captured name>"
            if direct:
                phase_keywords = [item for item in parent.keywords if item.arg == "phase"]
                if phase_keywords:
                    actual = "phase=" + ast.dump(
                        phase_keywords[0].value,
                        include_attributes=False,
                    )
                elif len(parent.args) >= 4:
                    phase = parent.args[3]
                    actual = ast.dump(phase, include_attributes=False)
                    if isinstance(phase, ast.Name):
                        phase_name = phase.id
                else:
                    actual = "<missing phase argument>"
            self.state_materializers.append(
                _StateMaterializerUse(
                    self.path,
                    tuple(self.scope),
                    self._qualified_scope(),
                    node.lineno,
                    direct,
                    phase_name,
                    actual,
                )
            )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for name in node.names:
            if name.name in ("events", "vocab.events"):
                self.concrete_importers.append((self.path, name.name, node.lineno))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        resolved = self._resolve_import_from(module, node.level)
        imports_concrete = resolved in ("events", "vocab.events") or any(
            f"{resolved}.{item.name}" in ("events", "vocab.events")
            for item in node.names
        )
        if imports_concrete:
            imported_names = ", ".join(item.name for item in node.names)
            prefix = "." * node.level
            self.concrete_importers.append(
                (
                    self.path,
                    f"from {prefix}{module} import {imported_names}",
                    node.lineno,
                )
            )

    def _resolve_import_from(self, module: str, level: int) -> str:
        if level == 0:
            return module
        package = list(Path(self.path).with_suffix("").parts[:-1])
        keep = len(package) - (level - 1)
        base = package[: max(keep, 0)]
        if module:
            base.extend(module.split("."))
        return ".".join(base)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        kind: str,
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)
        self.scope.append((kind, node.name))
        for statement in node.body:
            self.visit(statement)
        self.scope.pop()

    def _visit_comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
    ) -> None:
        kind = type(node).__name__
        self.scope.append(("comprehension", f"<{kind}@{node.lineno}>"))
        self.generic_visit(node)
        self.scope.pop()

    @staticmethod
    def _is_reflective_mapping(node: ast.AST) -> bool:
        if isinstance(node, ast.Attribute) and node.attr == "__dict__":
            return True
        return (
            isinstance(node, ast.Call)
            and (
                isinstance(node.func, ast.Name)
                and node.func.id in {"globals", "locals", "vars"}
                or isinstance(node.func, ast.Attribute)
                and node.func.attr in {"globals", "locals", "vars"}
            )
        )

    def _record_indirect_state_materializer(
        self,
        node: ast.AST,
        actual: str,
    ) -> None:
        self.state_materializers.append(
            _StateMaterializerUse(
                self.path,
                tuple(self.scope),
                self._qualified_scope(),
                node.lineno,
                False,
                None,
                actual,
            )
        )

    def _qualified_scope(self) -> str:
        return (
            ".".join(f"{kind}:{name}" for kind, name in self.scope)
            if self.scope
            else "<module>"
        )


def _producer_failures(path: Path) -> list[str]:
    relative = "vocab/assessment_producer.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
    failures: list[str] = []
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    forbidden_segments = {"anki", "reconcile", "session", "lifecycle"}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and node.id == "getattr"
            or isinstance(node, ast.Attribute)
            and node.attr == "getattr"
        ):
            failures.append(
                f"producer getattr is forbidden: {relative}:{node.lineno}"
            )
        if isinstance(node, ast.ImportFrom):
            for imported in node.names:
                if imported.name == "getattr" or (
                    node.module == "builtins" and imported.name == "*"
                ):
                    failures.append(
                        f"producer getattr import is forbidden: "
                        f"{relative}:{node.lineno}"
                    )
        if isinstance(node, ast.Attribute) and node.attr in ("log", "read"):
            parent = parents.get(node)
            if not isinstance(parent, ast.Call) or parent.func is not node:
                failures.append(
                    f"producer .{node.attr} must be a direct callee: "
                    f"{relative}:{node.lineno}"
                )
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [item.name for item in node.names]
                if isinstance(node, ast.Import)
                else [node.module or "", *(item.name for item in node.names)]
            )
            for module in modules:
                if forbidden_segments.intersection(module.split(".")):
                    failures.append(
                        f"producer forbidden import {module!r}: {relative}:{node.lineno}"
                    )
    return failures
