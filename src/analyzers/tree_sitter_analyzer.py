"""
Multi-language AST parser powered by tree-sitter.

Supports Python, SQL (tree-sitter-sql), YAML (tree-sitter-yaml),
and JavaScript/TypeScript.
Uses a LanguageRouter to dispatch to the correct grammar.

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
# tree-sitter core (required for all grammars)
# ---------------------------------------------------------------------------
try:
    from tree_sitter import Language as TSLanguage, Parser as TSParser
    TS_CORE_AVAILABLE = True
except ImportError as exc:
    TS_CORE_AVAILABLE = False
    TSLanguage = None  # type: ignore
    TSParser = None    # type: ignore
    logger.warning(f"tree-sitter core not available: {exc}")

# ---------------------------------------------------------------------------
# tree-sitter: Python + JavaScript grammars
# ---------------------------------------------------------------------------
try:
    import tree_sitter_python as tspython
    import tree_sitter_javascript as tsjavascript
    PY_LANGUAGE = TSLanguage(tspython.language())
    JS_LANGUAGE = TSLanguage(tsjavascript.language())
    TS_AVAILABLE = True
except Exception as exc:
    TS_AVAILABLE = False
    PY_LANGUAGE = None  # type: ignore
    JS_LANGUAGE = None  # type: ignore
    logger.warning(f"tree-sitter Python/JS grammars unavailable – falling back to regex: {exc}")

# ---------------------------------------------------------------------------
# tree-sitter: SQL grammar (tree-sitter-sql)
# ---------------------------------------------------------------------------
try:
    import tree_sitter_sql as tssql
    SQL_LANGUAGE = TSLanguage(tssql.language())
    TS_SQL_AVAILABLE = True
except Exception as exc:
    TS_SQL_AVAILABLE = False
    SQL_LANGUAGE = None  # type: ignore
    logger.warning(f"tree-sitter-sql unavailable – SQL AST parsing disabled: {exc}")

# ---------------------------------------------------------------------------
# tree-sitter: YAML grammar (tree-sitter-yaml)
# ---------------------------------------------------------------------------
try:
    import tree_sitter_yaml as tsyaml
    YAML_LANGUAGE = TSLanguage(tsyaml.language())
    TS_YAML_AVAILABLE = True
except Exception as exc:
    TS_YAML_AVAILABLE = False
    YAML_LANGUAGE = None  # type: ignore
    logger.warning(f"tree-sitter-yaml unavailable – YAML AST parsing disabled: {exc}")


# ---------------------------------------------------------------------------
# Data classes
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
class SQLTableRef:
    """A table reference extracted from a SQL AST."""
    name: str
    alias: Optional[str] = None
    line: int = 0
    ref_type: str = "input"   # "input" | "output"


@dataclass
class YAMLKeyRef:
    """A config key extracted from a YAML AST."""
    key: str
    value_preview: str = ""   # first 80 chars of the scalar value
    line: int = 0
    depth: int = 0


@dataclass
class ModuleAnalysisResult:
    path: str
    language: Language
    imports: list[ImportInfo] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    # SQL-specific structural elements
    sql_tables: list[SQLTableRef] = field(default_factory=list)
    # YAML-specific structural elements
    yaml_keys: list[YAMLKeyRef] = field(default_factory=list)
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
        if language == Language.SQL:
            return SQLASTAnalyzer()
        if language == Language.YAML:
            return YAMLASTAnalyzer()
        return RegexFallbackAnalyzer()


# ---------------------------------------------------------------------------
# Base analyzer
# ---------------------------------------------------------------------------


class BaseASTAnalyzer:
    def analyze(self, source: str, path: str, language: Language) -> ModuleAnalysisResult:
        raise NotImplementedError

    def _find_nodes(self, node, node_type: str) -> list:
        """Recursive DFS collector for a given node type."""
        results = []
        if node.type == node_type:
            results.append(node)
        for child in node.children:
            results.extend(self._find_nodes(child, node_type))
        return results

    def _node_bytes_text(self, node, source_bytes: bytes) -> str:
        return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Python AST Analyzer (tree-sitter-python)
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
            parser = TSParser(PY_LANGUAGE)
            tree = parser.parse(source.encode())
            root = tree.root_node
            result.imports = self._extract_imports(root, lines)
            result.functions = self._extract_functions(root, lines)
            result.classes = self._extract_classes(root, lines)
        except Exception as exc:
            result.parse_error = str(exc)
            logger.debug(f"tree-sitter Python parse error for {path}: {exc}")
            return self._regex_fallback(source, result)

        return result

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

    def _extract_imports(self, root, lines: list[str]) -> list[ImportInfo]:
        imports = []
        for node in self._find_nodes(root, "import_statement"):
            text = self._node_text(node, lines)
            m = re.match(r"import\s+([\w., ]+)", text)
            if m:
                for mod in m.group(1).split(","):
                    imports.append(ImportInfo(module=mod.strip(), line=node.start_point[0] + 1))

        for node in self._find_nodes(root, "import_from_statement"):
            text = self._node_text(node, lines)
            m = re.match(r"from\s+(\.*)(\S+)?\s+import\s+(.+)", text, re.DOTALL)
            if m:
                dots, mod, symbols_str = m.groups()
                module = (mod or "").strip()
                symbols = [s.strip().split(" as ")[0] for s in symbols_str.split(",")]
                imports.append(
                    ImportInfo(module=module, symbols=symbols, is_relative=bool(dots),
                               line=node.start_point[0] + 1)
                )
        return imports

    def _extract_functions(self, root, lines: list[str]) -> list[FunctionInfo]:
        functions = []
        for node in self._find_nodes(root, "function_definition"):
            if node.parent and node.parent.type == "block":
                if node.parent.parent and node.parent.parent.type == "class_definition":
                    continue
            name_node = node.child_by_field_name("name")
            name = self._node_text(name_node, lines) if name_node else "unknown"
            params_node = node.child_by_field_name("parameters")
            params = self._node_text(params_node, lines) if params_node else "()"
            functions.append(FunctionInfo(
                name=name, signature=f"def {name}{params}",
                start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                is_public=not name.startswith("_"),
                docstring=self._extract_docstring(node, lines),
            ))
        return functions

    def _extract_classes(self, root, lines: list[str]) -> list[ClassInfo]:
        classes = []
        for node in self._find_nodes(root, "class_definition"):
            name_node = node.child_by_field_name("name")
            name = self._node_text(name_node, lines) if name_node else "Unknown"
            bases: list[str] = []
            args_node = node.child_by_field_name("superclasses")
            if args_node:
                bases = [b.strip() for b in self._node_text(args_node, lines).strip("()").split(",") if b.strip()]

            methods: list[FunctionInfo] = []
            body = node.child_by_field_name("body")
            if body:
                for fn_node in self._find_nodes(body, "function_definition"):
                    fn_name_node = fn_node.child_by_field_name("name")
                    fn_name = self._node_text(fn_name_node, lines) if fn_name_node else "unknown"
                    params_node = fn_node.child_by_field_name("parameters")
                    params = self._node_text(params_node, lines) if params_node else "()"
                    methods.append(FunctionInfo(
                        name=fn_name, signature=f"def {fn_name}{params}",
                        start_line=fn_node.start_point[0] + 1,
                        end_line=fn_node.end_point[0] + 1,
                        is_public=not fn_name.startswith("_"),
                        docstring=self._extract_docstring(fn_node, lines),
                    ))

            classes.append(ClassInfo(name=name, bases=bases,
                                     start_line=node.start_point[0] + 1,
                                     end_line=node.end_point[0] + 1,
                                     methods=methods))
        return classes

    def _extract_docstring(self, node, lines: list[str]) -> Optional[str]:
        body = node.child_by_field_name("body")
        if not body:
            return None
        for child in body.children:
            if child.type == "expression_statement":
                for sub in child.children:
                    if sub.type in ("string", "concatenated_string"):
                        return self._node_text(sub, lines).strip("'\"").strip()
        return None

    def _regex_fallback(self, source: str, result: ModuleAnalysisResult) -> ModuleAnalysisResult:
        for m in re.finditer(r"^from\s+(\S+)\s+import\s+(.+)$", source, re.MULTILINE):
            result.imports.append(ImportInfo(module=m.group(1), symbols=[s.strip() for s in m.group(2).split(",")]))
        for m in re.finditer(r"^import\s+(.+)$", source, re.MULTILINE):
            result.imports.append(ImportInfo(module=m.group(1).strip()))
        for m in re.finditer(r"^def\s+(\w+)\s*\(([^)]*)\)", source, re.MULTILINE):
            result.functions.append(FunctionInfo(
                name=m.group(1), signature=f"def {m.group(1)}({m.group(2)})",
                start_line=source[: m.start()].count("\n") + 1,
                end_line=source[: m.start()].count("\n") + 1,
                is_public=not m.group(1).startswith("_"),
            ))
        return result


# ---------------------------------------------------------------------------
# SQL AST Analyzer (tree-sitter-sql)
# ---------------------------------------------------------------------------


class SQLASTAnalyzer(BaseASTAnalyzer):
    """
    Extracts table references from SQL files using the tree-sitter-sql grammar.

    Identifies:
    - FROM / JOIN table references → input tables (SQLTableRef with ref_type="input")
    - INSERT INTO / CREATE TABLE AS / CREATE VIEW AS → output tables (ref_type="output")
    - CTEs (WITH clause aliases) → tracked and excluded from input table list

    Results are stored in ModuleAnalysisResult.sql_tables and also mirrored
    into .imports so the existing Surveyor pipeline can read them transparently.
    """

    # Node types for statements that write tables
    _OUTPUT_STMT_TYPES = {"insert", "create_table", "create_view"}

    def analyze(self, source: str, path: str, language: Language) -> ModuleAnalysisResult:
        result = ModuleAnalysisResult(path=path, language=language, raw_source=source)
        lines = source.splitlines()
        result.lines_of_code = len([l for l in lines if l.strip() and not l.strip().startswith("--")])
        result.comment_lines = len([l for l in lines if l.strip().startswith("--")])

        if not TS_SQL_AVAILABLE:
            logger.debug(f"tree-sitter-sql unavailable, skipping AST parse for {path}")
            return result

        try:
            source_bytes = source.encode("utf-8", errors="replace")
            parser = TSParser(SQL_LANGUAGE)
            tree = parser.parse(source_bytes)
            root = tree.root_node

            cte_names: set[str] = set()
            input_tables: list[SQLTableRef] = []
            output_tables: list[SQLTableRef] = []

            # 1. Collect CTE names
            for node in self._find_nodes(root, "cte_name"):
                name = self._node_bytes_text(node, source_bytes).strip().strip('"').strip("'")
                if name:
                    cte_names.add(name.lower())

            # 2. Collect output table refs (INSERT / CREATE)
            for stmt_type in self._OUTPUT_STMT_TYPES:
                for stmt in self._find_nodes(root, stmt_type):
                    # We look for the first object_reference or relation in the head of the statement
                    for child in stmt.children:
                        if child.type in ("object_reference", "relation"):
                            name = self._node_bytes_text(child, source_bytes).strip().strip('"')
                            if name and name.lower() not in cte_names:
                                output_tables.append(
                                    SQLTableRef(name=name, line=child.start_point[0] + 1, ref_type="output")
                                )
                            break

            # 3. Collect input table references from containers
            for container_type in ("from", "join"):
                for container in self._find_nodes(root, container_type):
                    # Tables are generally in relation -> object_reference children
                    for child in self._find_nodes(container, "object_reference"):
                        name = self._node_bytes_text(child, source_bytes).strip().strip('"')
                        if name and name.lower() not in cte_names:
                            input_tables.append(
                                SQLTableRef(name=name, line=child.start_point[0] + 1, ref_type="input")
                            )
                        # Also check the parent relation node (some grammars label the identifier)
                        rel_nodes = self._find_nodes(container, "relation")
                        for rn in rel_nodes:
                            name = self._node_bytes_text(rn, source_bytes).strip().strip('"')
                            if name and name.lower() not in cte_names:
                                input_tables.append(
                                    SQLTableRef(name=name, line=rn.start_point[0] + 1, ref_type="input")
                                )

            # Deduplicate and store
            out_names = {r.name.lower() for r in output_tables}
            seen: set[str] = set()
            for ref in input_tables:
                lname = ref.name.lower()
                if lname not in seen and lname not in out_names:
                    seen.add(lname)
                    result.sql_tables.append(ref)
            for ref in output_tables:
                if ref.name.lower() not in {r.name.lower() for r in result.sql_tables}:
                    result.sql_tables.append(ref)

            # Mirror into imports for transparent pipeline compatibility
            for ref in result.sql_tables:
                result.imports.append(ImportInfo(module=ref.name, line=ref.line))

        except Exception as exc:
            result.parse_error = str(exc)
            logger.warning(f"SQL tree-sitter parse error for {path}: {exc}")

        return result

    def _collect_cte_names(self, root, source_bytes: bytes) -> set[str]:
        names: set[str] = set()
        for node in self._find_nodes(root, "cte_name"):
            names.add(self._node_bytes_text(node, source_bytes).strip().strip('"\'').lower())
        for node in self._find_nodes(root, "common_table_expression"):
            for child in node.children:
                if child.type in ("identifier", "name"):
                    names.add(self._node_bytes_text(child, source_bytes).strip().lower())
                    break
        return names

    def _collect_table_refs(
        self,
        node,
        source_bytes: bytes,
        cte_names: set[str],
        output: list[SQLTableRef],
    ) -> None:
        pass  # deprecated in favor of child-based extraction in analyze()


# ---------------------------------------------------------------------------
# YAML AST Analyzer (tree-sitter-yaml)
# ---------------------------------------------------------------------------


class YAMLASTAnalyzer(BaseASTAnalyzer):
    """
    Extracts structural config keys from YAML files using the tree-sitter-yaml grammar.

    Walks `block_mapping_pair` nodes recursively to build a list of YAMLKeyRef
    objects containing:
    - key name (e.g. "dag_id", "schedule_interval", "models", "sources")
    - value preview (first 80 chars of the scalar value)
    - line number in the file
    - nesting depth (0 = top-level)

    This allows the Surveyor to understand the *structural schema* of Airflow
    DAG configs and dbt schema.yml files from their AST — not just raw text.
    Results are mirrored into .imports for transparent pipeline compatibility.
    """

    def analyze(self, source: str, path: str, language: Language) -> ModuleAnalysisResult:
        result = ModuleAnalysisResult(path=path, language=language, raw_source=source)
        lines = source.splitlines()
        result.lines_of_code = len([l for l in lines if l.strip() and not l.strip().startswith("#")])
        result.comment_lines = len([l for l in lines if l.strip().startswith("#")])

        if not TS_YAML_AVAILABLE:
            logger.debug(f"tree-sitter-yaml unavailable, skipping {path}")
            return result

        try:
            source_bytes = source.encode("utf-8", errors="replace")
            parser = TSParser(YAML_LANGUAGE)
            tree = parser.parse(source_bytes)
            root = tree.root_node

            self._walk_mapping(root, source_bytes, result.yaml_keys, depth=0)

            # Mirror keys into imports for transparent pipeline compatibility
            for key_ref in result.yaml_keys:
                result.imports.append(ImportInfo(module=key_ref.key, line=key_ref.line))

        except Exception as exc:
            result.parse_error = str(exc)
            logger.warning(f"YAML tree-sitter parse error for {path}: {exc}")

        return result

    def _walk_mapping(
        self,
        node,
        source_bytes: bytes,
        output: list[YAMLKeyRef],
        depth: int,
    ) -> None:
        """Recursively walk mapping nodes to extract keys."""
        for child in node.children:
            if child.type == "block_mapping_pair":
                # In tree-sitter-yaml, mapping keys are usually the first node before ':'
                # We pick the child nodes that represent the key
                key_candidate = None
                val_candidate = None
                found_colon = False

                for grandchild in child.children:
                    if grandchild.type == ":":
                        found_colon = True
                        continue
                    if not found_colon and not key_candidate:
                        if grandchild.type in ("flow_node", "block_node", "plain_scalar"):
                            key_candidate = grandchild
                    elif found_colon and not val_candidate:
                        if grandchild.type in ("flow_node", "block_node", "plain_scalar", "block_sequence", "block_mapping"):
                            val_candidate = grandchild

                if key_candidate:
                    key_text = self._node_bytes_text(key_candidate, source_bytes).strip()
                    val_text = ""
                    if val_candidate:
                        raw = self._node_bytes_text(val_candidate, source_bytes).strip()
                        val_text = raw[:80] + ("…" if len(raw) > 80 else "")

                    output.append(
                        YAMLKeyRef(
                            key=key_text,
                            value_preview=val_text,
                            line=key_candidate.start_point[0] + 1,
                            depth=depth,
                        )
                    )

                    # Recurse into nested mapping if val is a mapping
                    if val_candidate:
                        self._walk_mapping(val_candidate, source_bytes, output, depth + 1)

            else:
                self._walk_mapping(child, source_bytes, output, depth)


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
            parser = TSParser(JS_LANGUAGE)
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
# Regex Fallback Analyzer (last resort for unknown file types)
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