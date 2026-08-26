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


@dataclass(frozen=True, slots=True)
class _LogUse:
    path: str
    scope: tuple[tuple[str, str], ...]
    qualified_scope: str
    line: int
    direct_call: bool
    actual: str
    literal: str | None


def assert_t12_ast_invariants(repository_root: Path) -> None:
    """Assert the exact D68 matrix, import allowlist, and producer rules."""
    root = Path(repository_root)
    production = root / "vocab"
    log_uses: list[_LogUse] = []
    concrete_importers: list[tuple[str, str, int]] = []
    constant_getattrs: list[tuple[str, str, int]] = []

    for path in sorted(production.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        visitor = _ProductionVisitor(relative)
        visitor.visit(tree)
        log_uses.extend(visitor.log_uses)
        concrete_importers.extend(visitor.concrete_importers)
        constant_getattrs.extend(visitor.constant_log_getattrs)

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

    failures.extend(_producer_failures(root / "vocab" / "assessment_producer.py"))
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
        if node.attr == "log":
            parent = self.parents[-2] if len(self.parents) >= 2 else None
            direct = isinstance(parent, ast.Call) and parent.func is node
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
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for name in node.names:
            if name.name in ("events", "vocab.events"):
                self.concrete_importers.append((self.path, name.name, node.lineno))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        imports_concrete = (
            module in ("events", "vocab.events")
            and any(item.name in ("EventLog", "*") for item in node.names)
        ) or (
            module in ("", "vocab")
            and any(item.name == "events" for item in node.names)
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
