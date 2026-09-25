"""
Static code analysis unit tests to ensure no functions assign to module-level globals
without an explicit 'global' declaration, preventing UnboundLocalError regressions.
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest

from tests.base import IsolatedCollectorTestCase


class TestGlobalScopingAndSafety(IsolatedCollectorTestCase):
    """
    Analyzes Python source files to ensure no module-level global variables
    are assigned within functions without an explicit 'global' declaration,
    which would cause Python to treat the variable as local and trigger
    UnboundLocalError when referenced before assignment.
    """

    def _check_file_scoping(self, file_path: Path, critical_globals: set[str]) -> None:
        self.assertTrue(file_path.exists(), f"{file_path} does not exist")

        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()

        tree = ast.parse(source, filename=str(file_path))

        # Collect top-level global variable assignments (regular and type-annotated)
        top_level_globals: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        top_level_globals.add(target.id)
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name):
                    top_level_globals.add(node.target.id)

        self.assertTrue(
            critical_globals.issubset(top_level_globals),
            f"Expected critical globals not found in {file_path.name}: {critical_globals - top_level_globals}",
        )

        issues: list[str] = []

        class FunctionScopeChecker(ast.NodeVisitor):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._check_scope(node)
                self.generic_visit(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self._check_scope(node)
                self.generic_visit(node)

            def _check_scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
                declared_globals: set[str] = set()
                assigned_locals: set[str] = set()

                for subnode in ast.walk(node):
                    if isinstance(subnode, ast.Global):
                        declared_globals.update(subnode.names)

                for subnode in ast.walk(node):
                    if isinstance(subnode, (ast.FunctionDef, ast.AsyncFunctionDef)) and subnode != node:
                        continue
                    if isinstance(subnode, ast.Assign):
                        for target in subnode.targets:
                            if isinstance(target, ast.Name):
                                assigned_locals.add(target.id)
                    elif isinstance(subnode, ast.AugAssign):
                        if isinstance(subnode.target, ast.Name):
                            assigned_locals.add(subnode.target.id)

                for name in assigned_locals:
                    if name in critical_globals and name not in declared_globals:
                        issues.append(
                            f"Function '{node.name}' (line {node.lineno}) assigns to '{name}' "
                            f"without declaring 'global {name}'. This will cause UnboundLocalError."
                        )

        checker = FunctionScopeChecker()
        checker.visit(tree)

        self.assertEqual(
            issues,
            [],
            f"Scoping violations detected in {file_path.name}:\n" + "\n".join(issues),
        )

    def test_collector_no_unbound_globals(self):
        collector_path = Path(__file__).resolve().parent.parent / "collector" / "collector.py"
        critical_globals = {"NOTIFY_CHAR", "WRITE_CHAR", "DB_PATH", "GOAL_ML", "cmd_queue", "ha_conn", "ha_api"}
        self._check_file_scoping(collector_path, critical_globals)

    def test_server_no_unbound_globals(self):
        server_path = Path(__file__).resolve().parent.parent / "server" / "server.py"
        critical_globals = {"db", "DB_PATH", "API_TOKEN", "WATERH_TZ"}
        self._check_file_scoping(server_path, critical_globals)


if __name__ == "__main__":
    unittest.main()
