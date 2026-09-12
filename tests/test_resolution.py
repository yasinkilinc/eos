"""Import resolution correctness across languages.

Every test here scans a copy of tests/fixtures/polyglot_project, which is the
smallest tree that can express the defects the previous fixture could not: a
two-file, single-absolute-import Python project cannot fail at relative
imports, at JS specifiers, at Java annotations, or at a working directory.
"""
import shutil
from collections import Counter
from pathlib import Path

from core.knowledge.builder import KnowledgeBuilder
from core.lib.cache_store import CacheStore
from core.scanner import Scanner

FIXTURE = Path(__file__).parent / "fixtures" / "polyglot_project"


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "polyglot"
    shutil.copytree(FIXTURE, root)
    return root


def _scan(root: Path):
    cache = CacheStore(root / ".eos" / "data" / "cache")
    project = Scanner(root, cache).scan(full=True)
    return KnowledgeBuilder().build(project, root=root)


def _import_edges(graph) -> set:
    return {(e.source_id, e.target_id) for e in graph.edges if e.kind == "import"}


def _import_edge_count(graph) -> int:
    return Counter(e.kind for e in graph.edges)["import"]


def test_import_edges_identical_from_any_cwd(tmp_path, monkeypatch):
    root = _copy(tmp_path)

    monkeypatch.chdir(root)
    from_inside = _import_edge_count(_scan(root))

    monkeypatch.chdir(tmp_path)
    from_outside = _import_edge_count(_scan(root))

    assert from_inside > 0, "fixture produced no import edges at all"
    assert from_inside == from_outside, (
        f"scan is CWD-dependent: {from_inside} edges from the project root, "
        f"{from_outside} from outside"
    )


def test_python_relative_imports_resolve_to_the_right_files(tmp_path, monkeypatch):
    root = _copy(tmp_path)
    monkeypatch.chdir(root)
    edges = _import_edges(_scan(root))

    # `from .. import helper` in pkg/sub/leaf.py must reach pkg/helper.py, not
    # collapse onto the subpackage's own __init__.py.
    assert ("file:pkg/sub/leaf.py", "file:pkg/helper.py") in edges

    # `from .sub import leaf` in pkg/__init__.py must reach the submodule, not
    # the subpackage __init__.
    assert ("file:pkg/__init__.py", "file:pkg/sub/leaf.py") in edges

    # `from . import helper` must reach the sibling module.
    assert ("file:pkg/__init__.py", "file:pkg/helper.py") in edges

    assert not [s for s, t in edges if s == t], "a file was recorded as importing itself"


def test_typescript_specifiers_resolve_as_paths(tmp_path, monkeypatch):
    root = _copy(tmp_path)
    monkeypatch.chdir(root)
    edges = _import_edges(_scan(root))

    # './components/Button' is a path, not a dotted module name.
    assert ("file:web/src/index.ts", "file:web/src/components/Button.tsx") in edges

    # `import type { Theme } from './theme'` is an import like any other.
    assert ("file:web/src/index.ts", "file:web/src/theme.ts") in edges


def _resolve(root, source, module, language="typescript"):
    """One resolver call with the root bound, as build() does."""
    builder = KnowledgeBuilder()
    builder._root = root
    if language == "python":
        from core.knowledge.semantic import Import

        return builder._resolve_python_relative(source, Import(module=module, name=None, is_relative=True, level=1))
    return builder._resolve_js_relative(source, module)


def test_a_specifier_reaching_the_project_root_does_not_crash(tmp_path):
    """`export * from '..'` in src/index.ts, and `require('..')` in a test
    directory, are ordinary Node idioms. Walking `..` landed base on Path('.'),
    and with_name('' + ext) raises ValueError -- taking the whole scan down
    with exit 1 and no brain written."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "index.ts").write_text("export const root = 1;\n", encoding="utf-8")
    (root / "src" / "reexport.ts").write_text("export { root } from '..';\n", encoding="utf-8")

    assert _resolve(root, "src/reexport.ts", "..") == "index.ts"
    assert _resolve(root, "src/reexport.ts", ".") is None


def test_a_specifier_escaping_the_project_root_resolves_to_nothing(tmp_path):
    """Path('.').parent is Path('.'), so every level past the root was a no-op
    and the specifier was re-anchored at the root -- inventing an edge to
    whatever happened to live there."""
    root = tmp_path / "proj"
    (root / "a" / "b").mkdir(parents=True)
    (root / "victim.ts").write_text("export const x = 1;\n", encoding="utf-8")
    (root / "a" / "b" / "deep.ts").write_text("import '../../../victim';\n", encoding="utf-8")

    assert _resolve(root, "a/b/deep.ts", "../../../victim") is None


def test_an_emitted_js_specifier_resolves_to_its_typescript_source(tmp_path):
    """Under moduleResolution Node16/NodeNext -- mandatory for ESM TypeScript
    -- './auth.js' is the only legal way to import auth.ts."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "auth.ts").write_text("export const a = 1;\n", encoding="utf-8")
    (root / "src" / "main.ts").write_text("import { a } from './auth.js';\n", encoding="utf-8")

    assert _resolve(root, "src/main.ts", "./auth.js") == "src/auth.ts"
    assert _resolve(root, "src/main.ts", "./auth") == "src/auth.ts"


def test_a_bare_js_specifier_never_resolves_to_a_project_file(tmp_path, monkeypatch):
    """A specifier not starting with '.' or '/' resolves through node_modules.
    Matching it against file stems made src/config/dayjs.ts -- which configures
    the npm package it is named after -- import itself."""
    root = tmp_path / "proj"
    (root / "src" / "config").mkdir(parents=True)
    (root / "package.json").write_text('{"dependencies":{"dayjs":"^1"}}', encoding="utf-8")
    (root / "src" / "config" / "dayjs.ts").write_text(
        "import dayjs from 'dayjs';\nexport default dayjs;\n", encoding="utf-8"
    )
    monkeypatch.chdir(root)

    edges = _import_edges(_scan(root))

    assert not [s for s, t in edges if s == t], f"self-edge produced: {edges}"
    assert edges == set(), f"a bare npm specifier became a project edge: {edges}"


def test_tsconfig_path_aliases_resolve(tmp_path, monkeypatch):
    """'@/foo/Bar' does not start with '.', so it went to the dotted-module
    resolver, which splits on '.' and can never match a path. On the CSR
    frontend the alias form is the majority style: 6,766 alias specifiers
    against 6,185 relative ones, and none of them produced an edge."""
    root = tmp_path / "proj"
    (root / "src" / "components").mkdir(parents=True)
    # Real tsconfigs are JSONC, and in this codebase `paths` lives in a
    # referenced file rather than in tsconfig.json itself.
    (root / "tsconfig.json").write_text(
        '{ "references": [{ "path": "./tsconfig.app.json" }] }', encoding="utf-8"
    )
    (root / "tsconfig.app.json").write_text(
        """{
  /* Bundler mode */
  "compilerOptions": {
    "baseUrl": ".",
    "paths": { "@/*": ["src/*"] }  // alias
  }
}""",
        encoding="utf-8",
    )
    (root / "src" / "components" / "Button.tsx").write_text(
        "export const Button = 1;\n", encoding="utf-8"
    )
    (root / "src" / "app.ts").write_text(
        "import { Button } from '@/components/Button';\n", encoding="utf-8"
    )
    monkeypatch.chdir(root)

    edges = _import_edges(_scan(root))

    assert ("file:src/app.ts", "file:src/components/Button.tsx") in edges, edges


def test_unresolved_imports_are_reported(tmp_path, monkeypatch):
    """Zero of 6,511 alias specifiers resolving looked exactly like a project
    with no aliases at all, which is why this shipped unnoticed."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "package.json").write_text('{"name":"x"}', encoding="utf-8")
    (root / "src" / "app.ts").write_text(
        "import { A } from '@/nowhere/A';\nimport { B } from './missing';\n", encoding="utf-8"
    )
    monkeypatch.chdir(root)

    cache = CacheStore(root / ".eos" / "data" / "cache")
    project = Scanner(root, cache).scan(full=True)
    KnowledgeBuilder().build(project, root=root)

    assert project.report.unresolved_imports.get("alias", 0) >= 1
    assert project.report.unresolved_imports.get("relative", 0) >= 1
