"""
Multi-language AST parser powered by tree-sitter.

Supports Python, SQL (basic), YAML (structural), and JavaScript/TypeScript.
Uses a LanguageRouter to dispatch to the right grammar.

Design principles:
- Never crash on unparseable files – log and skip.
- Return rich structural data; leave semantic interpretation to the Semanticist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.models.nodes import Language
from src.utils.logging_utils import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# tree-sitter import with graceful fallback
# ---------------------------------------------------------------------------
try:
    import tree_sitter_python as tspython
    import tree_sitter_javascript as tsjavascript
    from tree_sitter import Language as TSLanguage, Parser

    PY_LANGUAGE = TSLanguage(tspython.language())
    JS_LANGUAGE = TSLanguage(tsjavascript.language())
    TS_AVAILABLE = True
except Exception as exc:
    TS_AVAILABLE = False
    logger.warning(f"tree-sitter not available – falling back to regex analysis: {exc}")


# ---------------------------------------------------------------------------
# Data classes returned by the analyzer
# ---------------------------------------------------------------------------


@dataclass
class ImportInfo:
    module: str
    symbols: list[str] = field(default_factory=list)
    is_relative: bool = False
    line: int = 0


@dataclass
class FunctionInfo:
    name: str
    signature: str
    start_line: int
    end_line: int
    is_public: bool = True
    decorators: list[str] = field(default_factory=list)
    docstring: Optional[str] = None


@dataclass
class ClassInfo:
    name: str
    bases: list[str]
    start_line: int
    end_line: int
    methods: list[FunctionInfo] = field(default_factory=list)


@dataclass
class ModuleAnalysisResult:
    path: str
    language: Language
    imports: list[ImportInfo] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    lines_of_code: int = 0
    comment_lines: int = 0
    raw_source: str = ""
    parse_error: Optional[str] = None


# ---------------------------------------------------------------------------
# Language Router
# ---------------------------------------------------------------------------


class LanguageRouter:
    """Select the correct tree-sitter grammar and analysis strategy per file."""

    def route(self, path: Path, language: Language) -> "BaseASTAnalyzer":
        if language == Language.PYTHON:
            return PythonASTAnalyzer()
        if language in (Language.JAVASCRIPT, Language.TYPESCRIPT):
            return JavaScriptASTAnalyzer()
        # SQL and YAML are handled by dedicated analyzers; fall through to regex
        return RegexFallbackAnalyzer()


# ---------------------------------------------------------------------------
# Base analyzer interface
# ---------------------------------------------------------------------------


class BaseASTAnalyzer:
    def analyze(self, source: str, path: str, language: Language) -> ModuleAnalysisResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Python AST Analyzer (tree-sitter)
# ---------------------------------------------------------------------------


class PythonASTAnalyzer(BaseASTAnalyzer):
    """Full structural analysis of Python files using tree-sitter."""

    def analyze(self, source: str, path: str, language: Language) -> ModuleAnalysisResult:
        result = ModuleAnalysisResult(path=path, language=language, raw_source=source)

        lines = source.splitlines()
        result.lines_of_code = len([l for l in lines if l.strip() and not l.strip().startswith("#")])
        result.comment_lines = len([l for l in lines if l.strip().startswith("#")])

        if not TS_AVAILABLE:
            return self._regex_fallback(source, result)

        try:
            parser = Parser(PY_LANGUAGE)
            tree = parser.parse(source.encode())
            root = tree.root_node

            result.imports = self._extract_imports(root, lines)
            result.functions = self._extract_functions(root, lines)
            result.classes = self._extract_classes(root, lines)
        except Exception as exc:
            result.parse_error = str(exc)
            logger.debug(f"tree-sitter parse error for {path}: {exc}")
            return self._regex_fallback(source, result)

        return result

    # --- tree-sitter helpers ------------------------------------------------

    def _node_text(self, node, source_lines: list[str]) -> str:
        start_row, start_col = node.start_point
        end_row, end_col = node.end_point
        if start_row == end_row:
            return source_lines[start_row][start_col:end_col]
        parts = [source_lines[start_row][start_col:]]
        for row in range(start_row + 1, end_row):
            parts.append(source_lines[row])
        parts.append(source_lines[end_row][:end_col])
        return "\n".join(parts)

    def _find_nodes(self, node, node_type: str) -> list:
        results = []
        if node.type == node_type:
            results.append(node)
        for child in node.children:
            results.extend(self._find_nodes(child, node_type))
        return results

    def _extract_imports(self, root, lines: list[str]) -> list[ImportInfo]:
        imports = []

        for node in self._find_nodes(root, "import_statement"):
            text = self._node_text(node, lines)
            # "import os", "import os.path"
            m = re.match(r"import\s+([\w., ]+)", text)
            if m:
                for mod in m.group(1).split(","):
                    imports.append(
                        ImportInfo(module=mod.strip(), line=node.start_point[0] + 1)
                    )

        for node in self._find_nodes(root, "import_from_statement"):
            text = self._node_text(node, lines)
            m = re.match(r"from\s+(\.*)(\S+)?\s+import\s+(.+)", text, re.DOTALL)
            if m:
                dots, mod, symbols_str = m.groups()
                module = (mod or "").strip()
                symbols = [s.strip().split(" as ")[0] for s in symbols_str.split(",")]
                imports.append(
                    ImportInfo(
                        module=module,
                        symbols=symbols,
                        is_relative=bool(dots),
                        line=node.start_point[0] + 1,
                    )
                )

        return imports

    def _extract_functions(self, root, lines: list[str]) -> list[FunctionInfo]:
        functions = []
        for node in self._find_nodes(root, "function_definition"):
            # Avoid nested functions inside classes (handled separately)
            if node.parent and node.parent.type in ("block",):
                if node.parent.parent and node.parent.parent.type == "class_definition":
                    continue  # will be captured via class extraction

            name_node = node.child_by_field_name("name")
            name = self._node_text(name_node, lines) if name_node else "unknown"
            params_node = node.child_by_field_name("parameters")
            params = self._node_text(params_node, lines) if params_node else "()"
            signature = f"def {name}{params}"
            is_public = not name.startswith("_")
            docstring = self._extract_docstring(node, lines)

            functions.append(
                FunctionInfo(
                    name=name,
                    signature=signature,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    is_public=is_public,
                    docstring=docstring,
                )
            )
        return functions

    def _extract_classes(self, root, lines: list[str]) -> list[ClassInfo]:
        classes = []
        for node in self._find_nodes(root, "class_definition"):
            name_node = node.child_by_field_name("name")
            name = self._node_text(name_node, lines) if name_node else "Unknown"

            # Base classes
            bases: list[str] = []
            args_node = node.child_by_field_name("superclasses")
            if args_node:
                bases_text = self._node_text(args_node, lines)
                bases = [b.strip() for b in bases_text.strip("()").split(",") if b.strip()]

            # Methods
            methods: list[FunctionInfo] = []
            body = node.child_by_field_name("body")
            if body:
                for fn_node in self._find_nodes(body, "function_definition"):
                    fn_name_node = fn_node.child_by_field_name("name")
                    fn_name = self._node_text(fn_name_node, lines) if fn_name_node else "unknown"
                    params_node = fn_node.child_by_field_name("parameters")
                    params = self._node_text(params_node, lines) if params_node else "()"
                    methods.append(
                        FunctionInfo(
                            name=fn_name,
                            signature=f"def {fn_name}{params}",
                            start_line=fn_node.start_point[0] + 1,
                            end_line=fn_node.end_point[0] + 1,
                            is_public=not fn_name.startswith("_"),
                            docstring=self._extract_docstring(fn_node, lines),
                        )
                    )

            classes.append(
                ClassInfo(
                    name=name,
                    bases=bases,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    methods=methods,
                )
            )
        return classes

    def _extract_docstring(self, node, lines: list[str]) -> Optional[str]:
        """Extract first string literal from a function/class body as docstring."""
        body = node.child_by_field_name("body")
        if not body:
            return None
        for child in body.children:
            if child.type == "expression_statement":
                for sub in child.children:
                    if sub.type in ("string", "concatenated_string"):
                        raw = self._node_text(sub, lines)
                        return raw.strip("'\"").strip()
        return None

    # --- regex fallback -------------------------------------------------------

    def _regex_fallback(self, source: str, result: ModuleAnalysisResult) -> ModuleAnalysisResult:
        # Simple import extraction via regex when tree-sitter is unavailable
        for m in re.finditer(r"^from\s+(\S+)\s+import\s+(.+)$", source, re.MULTILINE):
            result.imports.append(
                ImportInfo(module=m.group(1), symbols=[s.strip() for s in m.group(2).split(",")])
            )
        for m in re.finditer(r"^import\s+(.+)$", source, re.MULTILINE):
            result.imports.append(ImportInfo(module=m.group(1).strip()))

        for m in re.finditer(r"^def\s+(\w+)\s*\(([^)]*)\)", source, re.MULTILINE):
            result.functions.append(
                FunctionInfo(
                    name=m.group(1),
                    signature=f"def {m.group(1)}({m.group(2)})",
                    start_line=source[: m.start()].count("\n") + 1,
                    end_line=source[: m.start()].count("\n") + 1,
                    is_public=not m.group(1).startswith("_"),
                )
            )
        return result


# ---------------------------------------------------------------------------
# JavaScript / TypeScript Analyzer
# ---------------------------------------------------------------------------


class JavaScriptASTAnalyzer(BaseASTAnalyzer):
    def analyze(self, source: str, path: str, language: Language) -> ModuleAnalysisResult:
        result = ModuleAnalysisResult(path=path, language=language, raw_source=source)
        lines = source.splitlines()
        result.lines_of_code = len([l for l in lines if l.strip()])

        if not TS_AVAILABLE:
            return result

        try:
            parser = Parser(JS_LANGUAGE)
            tree = parser.parse(source.encode())
            # Basic import extraction
            for node in self._find_import_nodes(tree.root_node):
                text = lines[node.start_point[0]] if node.start_point[0] < len(lines) else ""
                m = re.search(r"from\s+['\"]([^'\"]+)['\"]", text)
                if m:
                    result.imports.append(ImportInfo(module=m.group(1), line=node.start_point[0] + 1))
        except Exception as exc:
            result.parse_error = str(exc)
        return result

    def _find_import_nodes(self, node) -> list:
        results = []
        if node.type in ("import_statement", "import_declaration"):
            results.append(node)
        for child in node.children:
            results.extend(self._find_import_nodes(child))
        return results


# ---------------------------------------------------------------------------
# Regex Fallback Analyzer (used for YAML, plain text, etc.)
# ---------------------------------------------------------------------------


class RegexFallbackAnalyzer(BaseASTAnalyzer):
    def analyze(self, source: str, path: str, language: Language) -> ModuleAnalysisResult:
        result = ModuleAnalysisResult(path=path, language=language, raw_source=source)
        result.lines_of_code = len([l for l in source.splitlines() if l.strip()])
        return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

_router = LanguageRouter()


def analyze_file(path: Path, source: str, language: Language) -> ModuleAnalysisResult:
    """
    Dispatch to the right analyzer and return a ModuleAnalysisResult.
    Never raises – logs errors and returns a partial result.
    """
    try:
        analyzer = _router.route(path, language)
        return analyzer.analyze(source, str(path), language)
    except Exception as exc:
        logger.warning(f"Analysis failed for {path}: {exc}")
        return ModuleAnalysisResult(
            path=str(path), language=language, raw_source=source, parse_error=str(exc)
        )