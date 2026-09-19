"""Build the KnowledgeGraph from a ProjectSemantic model."""
import json
import re
from pathlib import Path
from typing import Dict, List, Set

from .classifier import Classifier
from .evidence import CERTAIN, EXTRACTED, Fact, detector, edge_subject, utc_now
from .model import Dependency, KnowledgeGraph, KnowledgeNode
from .semantic import FileSemantic, Import, ProjectSemantic



def _strip_jsonc(text: str) -> str:
    """Drop // and /* */ comments so a tsconfig can be parsed as JSON.

    Real tsconfigs are JSONC; json.loads raises on the first `/* Bundler mode */`.
    String literals are preserved so a comment marker inside a path is safe.
    """
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n and (text[j] != '"' or text[j - 1] == "\\"):
                j += 1
            out.append(text[i:j + 1]); i = j + 1
        elif text.startswith("//", i):
            i = text.find("\n", i)
            if i == -1:
                break
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
        else:
            out.append(ch); i += 1
    return "".join(out)

def _requirement_name(spec: str) -> str:
    """The distribution name out of a PEP 508 requirement string.

    "SQLAlchemy>=2.0" -> "sqlalchemy", "fastapi[all]==0.115" -> "fastapi".
    """
    match = re.match(r"\s*([A-Za-z0-9._-]+)", spec)
    return match.group(1).lower() if match else ""


class KnowledgeBuilder:
    """Transform language-agnostic semantic model into project knowledge."""

    def __init__(self) -> None:
        self.classifier = Classifier()
        self._root: Path | None = None

    def build(self, project: ProjectSemantic, root: Path | None = None) -> KnowledgeGraph:
        """Build the graph. `root` is the project directory on disk.

        Without it, import resolution tested candidate paths against the
        process working directory instead of the project, so the same command
        run from a different place produced a different graph -- measured at 18
        import edges instead of 60, exit 0, no warning.
        """
        self._root = Path(root) if root is not None else None
        self._build_module_index(project)
        self._load_js_aliases()
        graph = KnowledgeGraph(languages=project.detected_languages)
        node_ids: Set[str] = set()
        symbol_index: Dict[str, str] = {}  # symbol key -> file node id
        folder_ids: Set[str] = set()

        # 1. Create a node for every file.
        for file in project.files:
            node_id = self._file_node_id(file.path)
            node_type, tags = self.classifier.classify_file(file)
            node = KnowledgeNode(
                id=node_id,
                type=node_type,
                label=Path(file.path).stem,
                path=file.path,
                language=file.language,
                tags=tags,
                metadata={
                    "exports": [e.name for e in file.exports],
                    "top_symbols": [s.name for s in file.symbols[:8]],
                },
                doc=file.doc,
            )
            graph.add_node(node)
            node_ids.add(node_id)

            # Index exports/symbols by name for later linking.
            for exp in file.exports:
                symbol_index[self._symbol_key(file.path, exp.name)] = node_id
            for sym in file.symbols:
                symbol_index[self._symbol_key(file.path, sym.name)] = node_id

            if node_type == "entry-point":
                graph.entry_points.append(node_id)

            # Folder-hierarchy edges: link this file to its parent folder node.
            self._link_to_folder(file.path, graph, folder_ids)

        # 2. Tech stack detection (minimal heuristic).
        graph.tech_stack = self._detect_tech_stack(project)

        # Folder-hierarchy edges are a projection of the path, not a detection,
        # so they carry no per-edge fact: on a real service they are 47,425 of
        # 64,479 edges and a provenance row saying "read from the path" for each
        # would be megabytes of noise. The coverage row exists so their absence
        # from the fact table is a stated decision rather than a silent gap.
        folder_edges: Dict[str, int] = {}
        for edge in graph.edges:
            if edge.kind == "folder-hierarchy":
                folder_edges[edge.source_id] = folder_edges.get(edge.source_id, 0) + 1
        folder_detector = detector("folders")
        for file in project.files:
            project.report.note_coverage(
                folder_detector, "folder-hierarchy",
                folder_edges.get(self._file_node_id(file.path), 0))

        # 3. Edges: imports between files.
        import_detector = detector("imports")
        observed = utc_now()
        for file in project.files:
            source_id = self._file_node_id(file.path)
            resolved_here = 0
            for imp in file.imports:
                kind = "relative" if imp.is_relative else "absolute"
                if imp.is_relative:
                    if file.language == "python":
                        target_path = self._resolve_python_relative(file.path, imp)
                    elif file.language in ("javascript", "typescript"):
                        target_path = self._resolve_js_relative(file.path, imp.module)
                    else:
                        target_path = self._resolve_relative_import(file.path, imp.module)
                elif file.language in ("javascript", "typescript"):
                    # A tsconfig `paths` alias is neither relative nor a package.
                    target_path = self._resolve_js_alias(imp.module, file.path)
                    if target_path is not None:
                        kind = "alias"
                    elif self._looks_like_alias(imp.module):
                        kind = "alias"
                    # Anything left is a bare specifier, which is a package by
                    # definition: not starting with '.' or '/' means it resolves
                    # through node_modules, never to a project file. Matching it
                    # against file stems produced 26 false edges on the CSR
                    # frontend, one of them src/config/dayjs.ts importing itself
                    # because it configures the npm package it is named after.
                else:
                    target_path = self._resolve_absolute_import(imp.module, project)
                    # Language plugins split a dotted import into a module and a
                    # trailing name, which is right for "from x import y" but
                    # inverts the Java case: "import com.acme.svc.Thing" yields
                    # module="com.acme.svc" (a *package*, i.e. a directory) and
                    # name="Thing" (the class the file is actually named after).
                    # Resolving the module alone can therefore only ever hit a
                    # directory, so the edge is silently dropped -- which reads
                    # downstream as "this file has no dependents". Try the
                    # fully-qualified path too, after the module, so the
                    # from-import case (name is a symbol inside the module)
                    # keeps winning where it already resolved.
                    if target_path is None and imp.name and imp.name.isidentifier():
                        target_path = self._resolve_absolute_import(
                            f"{imp.module}.{imp.name}", project
                        )

                if target_path:
                    target_id = self._file_node_id(target_path)
                    if target_id in node_ids:
                        graph.add_edge(
                            Dependency(
                                source_id=source_id,
                                target_id=target_id,
                                kind="import",
                                weight=1,
                                metadata={"imported": imp.name},
                            )
                        )
                        resolved_here += 1
                        # Provenance for the edge just added. The subject is the
                        # edge's own identity, not a surrogate id: the index
                        # renumbers node ids on every rebuild, so a stored
                        # number would silently come to mean another file.
                        graph.add_fact(Fact(
                            subject_kind="edge",
                            subject=edge_subject(source_id, target_id, "import", imp.name or ""),
                            predicate="import-edge",
                            object=imp.module,
                            origin=EXTRACTED,
                            confidence=CERTAIN,
                            detector=import_detector,
                            source_ref=f"{file.path}:{imp.line}" if imp.line else file.path,
                            observed_at=file.parsed_at or observed,
                        ))
                    # Resolved to a real file EOS does not parse -- 401 .scss
                    # imports on the CSR frontend. That is already reported as
                    # an unindexed language; counting it again as an unresolved
                    # import would say the file is missing when it is not.
                    continue

                # Nothing on disk answered this specifier. Counting it by kind is
                # what would have caught the alias gap: "0 of 6,511 `@/…`
                # specifiers resolved" printed exactly like a project that uses
                # no aliases at all. A bare package specifier is expected not to
                # resolve, so it is not counted.
                if kind != "absolute" or file.language not in ("javascript", "typescript"):
                    project.report.note_unresolved(kind)

            # Every parsed file is eligible, including the ones that produced
            # nothing: "no import edges anywhere" and "the import detector never
            # ran" must not read the same.
            project.report.note_coverage(import_detector, "import-edge", resolved_here)

        return graph

    @staticmethod
    def _looks_like_alias(module: str) -> bool:
        """A specifier that is neither relative nor a plausible package name.

        `@/foo` and `~/foo` are alias conventions; `@scope/pkg` is a real npm
        scope and must not be counted as a failed alias.
        """
        return module.startswith(("@/", "~/", "#"))

    @staticmethod
    def _file_node_id(path: str) -> str:
        # Stable URL-safe ID based on relative path.
        return "file:" + path.replace(" ", "_").replace("\\", "/")

    @staticmethod
    def _symbol_key(path: str, name: str) -> str:
        return f"{path}::{name}"

    @staticmethod
    def _folder_node_id(folder_path: str) -> str:
        return "folder:" + folder_path.replace(" ", "_").replace("\\", "/")

    def _link_to_folder(
        self,
        file_path: str,
        graph: KnowledgeGraph,
        folder_ids: Set[str],
    ) -> None:
        """Create folder nodes and folder-hierarchy edges for a file's ancestors.

        Adds a ``folder`` node per ancestor directory and links file -> folder
        and folder -> parent-folder with ``kind='folder-hierarchy'`` edges.
        The root ('.') is skipped (it is the project boundary, not a module).
        """
        from .model import KnowledgeNode

        parts = Path(file_path).parts
        # parts[:-1] are the ancestor dirs; skip the filename.
        for i in range(1, len(parts)):
            folder_path = str(Path(*parts[:i])).replace("\\", "/")
            folder_id = self._folder_node_id(folder_path)
            if folder_id not in folder_ids:
                folder_ids.add(folder_id)
                graph.add_node(
                    KnowledgeNode(
                        id=folder_id,
                        type="folder",
                        label=parts[i - 1],
                        path=folder_path,
                    )
                )

            # Edge from this node (file or child folder) to its parent folder.
            if i == 1:
                child_id = self._file_node_id(file_path)
            else:
                child_path = str(Path(*parts[: i - 1])).replace("\\", "/")
                child_id = self._folder_node_id(child_path)
            graph.add_edge(
                Dependency(
                    source_id=child_id,
                    target_id=folder_id,
                    kind="folder-hierarchy",
                    weight=1,
                )
            )

    _MANIFESTS = {
        "pyproject.toml": "python",
        "setup.py": "python",
        "requirements.txt": "python",
        "package.json": "node",
        "pom.xml": "java",
        "build.gradle": "java",
        "build.gradle.kts": "java",
        "Cargo.toml": "rust",
        "go.mod": "go",
    }

    _FRAMEWORK_TOKENS = (
        "fastapi", "flask", "django", "pydantic", "sqlalchemy", "celery", "pytest",
        "react", "vue", "svelte", "express", "next", "vite", "typescript",
        "spring-boot", "spring-boot-starter-web", "hibernate", "kafka", "quarkus",
    )

    def _detect_tech_stack(self, project: ProjectSemantic) -> List[str]:
        stack: Set[str] = set()
        for file in project.files:
            if file.language == "python":
                stack.add("python")
            if file.language in ("javascript", "typescript"):
                stack.add("node")
                if file.language == "typescript":
                    stack.add("typescript")
                else:
                    stack.add("javascript")
            if file.language == "java":
                stack.add("java")
        stack.update(self._stack_from_manifests())
        return sorted(stack)

    def _stack_from_manifests(self) -> Set[str]:
        """Read the frameworks a project declares about itself.

        Mapping language to language made TechStack.md byte-identical -- 48
        bytes -- for two entirely unrelated projects, while the manifests
        naming their actual stack sat next to it unread.

        Read from disk rather than from project.files: Scanner._process_file
        returns early when no plugin claims the extension, so pom.xml,
        pyproject.toml and requirements.txt never enter the semantic model at
        all. Only the root and its direct children are consulted; a manifest
        deeper than that describes a subproject, not this one.
        """
        found: Set[str] = set()
        if self._root is None:
            return found

        candidates: List[Path] = [self._root / name for name in self._MANIFESTS]
        try:
            children = sorted(p for p in self._root.iterdir() if p.is_dir())
        except OSError:
            children = []
        for child in children:
            if child.name.startswith("."):
                continue
            candidates.extend(child / name for name in self._MANIFESTS)

        for path in candidates:
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            found.add(self._MANIFESTS[path.name])
            declared = self._declared_dependencies(path.name, text)
            found.update(
                token
                for token in self._FRAMEWORK_TOKENS
                if any(self._names_token(token, name) for name in declared)
            )
        return found

    @staticmethod
    def _names_token(token: str, name: str) -> bool:
        """True when `name` contains `token` as a whole dotted/hyphenated word.

        A bare substring test made `react` match io.projectreactor and
        reactor-core, and `spring-boot-starter-web` match
        spring-boot-starter-webflux -- reporting the reactive stack as the
        servlet one it deliberately excludes.
        """
        return (
            re.search(r"(?:^|[^0-9a-z])" + re.escape(token) + r"(?:$|[^0-9a-z])", name)
            is not None
        )

    @staticmethod
    def _declared_dependencies(filename: str, text: str) -> Set[str]:
        """Dependency names a manifest actually declares, lowercased.

        Grepping the raw text instead reported react for a package whose only
        mention of it was a description, spring-boot for a pom that explicitly
        EXCLUDED it, and django for a requirements comment saying it is not
        used. A framework claim in TechStack.md is exactly the kind of
        confident wrong answer this indexer is supposed to stop producing, so
        each format is parsed rather than searched.
        """
        names: Set[str] = set()
        try:
            if filename == "package.json":
                import json as _json

                data = _json.loads(text)
                if isinstance(data, dict):
                    for key in ("dependencies", "devDependencies", "peerDependencies"):
                        section = data.get(key)
                        if isinstance(section, dict):
                            names.update(str(k).lower() for k in section)

            elif filename == "pyproject.toml":
                import tomllib

                data = tomllib.loads(text)
                project = data.get("project", {}) if isinstance(data, dict) else {}
                for spec in project.get("dependencies", []) or []:
                    names.add(_requirement_name(str(spec)))
                for group in (project.get("optional-dependencies", {}) or {}).values():
                    for spec in group or []:
                        names.add(_requirement_name(str(spec)))
                poetry = (
                    data.get("tool", {}).get("poetry", {}).get("dependencies", {})
                    if isinstance(data, dict)
                    else {}
                )
                if isinstance(poetry, dict):
                    names.update(str(k).lower() for k in poetry)

            elif filename == "requirements.txt":
                for line in text.splitlines():
                    line = line.split("#", 1)[0].strip()
                    if line and not line.startswith("-"):
                        names.add(_requirement_name(line))

            elif filename == "pom.xml":
                import xml.etree.ElementTree as ET

                root = ET.fromstring(text)
                for dependency in root.iter():
                    if dependency.tag.rsplit("}", 1)[-1] != "dependency":
                        continue
                    # Direct children only. An <exclusion> names a dependency
                    # that must NOT be pulled in and carries the same
                    # artifactId/groupId tags, but it lives one level deeper
                    # under <exclusions>, so never iterating into children keeps
                    # it out without a special case.
                    for child in dependency:
                        if child.tag.rsplit("}", 1)[-1] in ("artifactId", "groupId") and child.text:
                            names.add(child.text.strip().lower())

            elif filename in ("build.gradle", "build.gradle.kts"):
                for line in text.splitlines():
                    line = line.split("//", 1)[0].strip()
                    for match in re.finditer(r"""['"]([A-Za-z0-9_.\-]+:[A-Za-z0-9_.\-]+)""", line):
                        names.update(part.lower() for part in match.group(1).split(":"))
        except Exception:
            # A manifest that does not parse tells us nothing; it must not stop
            # the scan, and guessing from its text is what this replaces.
            return names
        return names

    def _resolve_python_relative(self, source_path: str, imp: Import) -> str | None:
        """Resolve `from ..pkg import name` the way Python itself does.

        level=1 is the file's own directory and each extra level walks up one.
        The imported *name* is tried as a submodule before the package
        __init__, because `from . import helper` means helper.py when that file
        exists -- resolving to the package instead produced self-edges and
        pointed `from .sub import leaf` at sub/__init__.py rather than leaf.py.
        """
        base = Path(source_path).parent
        for _ in range(max(0, imp.level - 1)):
            # Clamping at the root re-anchors an escaping import onto whatever
            # lives there -- on scikit-learn's utils/ subtree that invented six
            # edges, including an apparent cycle with the package __init__.
            if base == Path("."):
                return None
            base = base.parent

        module_parts = imp.module.split(".") if imp.module else []
        pkg_dir = base.joinpath(*module_parts) if module_parts else base

        candidates: List[Path] = []
        if imp.name:
            candidates.append(pkg_dir / f"{imp.name}.py")
            candidates.append(pkg_dir / imp.name / "__init__.py")
        if module_parts:
            candidates.append(pkg_dir.with_suffix(".py"))
        candidates.append(pkg_dir / "__init__.py")

        for candidate in candidates:
            resolved = candidate.as_posix()
            if resolved != source_path and self._exists(candidate):
                return resolved
        return None

    _JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".d.ts")

    def _resolve_js_relative(self, source_path: str, module: str) -> str | None:
        """Resolve a JS/TS specifier as a POSIX path, never as a dotted module.

        './types' and '../shared/x' are paths. Splitting them on '.' the way a
        Java or Python module name is split is why a 225-file TypeScript
        project produced 3 import edges where the compiler finds 883 -- and why
        all 3 pointed at the wrong file.
        """
        base = Path(source_path).parent
        for segment in module.split("/"):
            if segment in ("", "."):
                continue
            if segment == "..":
                # Path('.').parent is Path('.'), so walking past the root used
                # to be a silent no-op that re-anchored the specifier at the
                # root and matched it against whatever happened to live there --
                # a fabricated dependency between two unrelated files, which is
                # worse than no edge at all.
                if base == Path("."):
                    return None
                base = base.parent
            else:
                base = base / segment

        return self._resolve_js_path(base, source_path)

    def _resolve_js_path(self, base: Path, source_path: str) -> str | None:
        """Node/TS resolution for an already-resolved project-relative path.

        Shared by relative specifiers and by tsconfig `paths` aliases so both
        obey one extension and index policy.
        """
        candidates: List[Path] = []
        if base.name:
            # NodeNext/ESM requires writing the EMITTED specifier, so './auth.js'
            # is the only legal way to name auth.ts. Probe the literal path
            # first, then the sources it could have been emitted from, and only
            # then treat the specifier as extensionless.
            candidates.append(base)
            if base.suffix in (".js", ".mjs", ".cjs", ".jsx"):
                candidates.extend(
                    base.with_suffix(ext) for ext in (".ts", ".tsx", ".mts", ".cts")
                )
            candidates.extend(base.with_name(base.name + ext) for ext in self._JS_EXTENSIONS)
        # '..' from src/x.ts means the root's own index file; base is Path('.')
        # there and has no name to extend, so only this loop applies.
        candidates.extend(base / f"index{ext}" for ext in self._JS_EXTENSIONS)

        for candidate in candidates:
            resolved = candidate.as_posix()
            if resolved != source_path and self._exists(candidate):
                return resolved
        return None

    def _load_js_aliases(self) -> None:
        """tsconfig/jsconfig `paths` prefixes, longest first.

        `@/foo/Bar` is not a package and not a relative path, so it reached the
        dotted-module resolver and could never match. On the CSR frontend the
        alias form is the majority style -- 6,766 alias specifiers against
        6,185 relative ones -- and none of them produced an edge.

        Globbed rather than read from tsconfig.json alone: in that project
        tsconfig.json is a references-only stub and `paths` lives in
        tsconfig.app.json. These files are JSONC, so comments are stripped
        before parsing.
        """
        self._js_aliases: List[tuple[str, str]] = []
        if self._root is None:
            return
        for pattern in ("tsconfig*.json", "jsconfig*.json"):
            for config in sorted(self._root.glob(pattern)):
                try:
                    data = json.loads(_strip_jsonc(config.read_text(encoding="utf-8", errors="replace")))
                except (OSError, ValueError):
                    continue
                options = data.get("compilerOptions", {}) if isinstance(data, dict) else {}
                base_url = str(options.get("baseUrl", "."))
                paths = options.get("paths", {})
                if not isinstance(paths, dict):
                    continue
                for alias, targets in paths.items():
                    if not (alias.endswith("/*") and isinstance(targets, list) and targets):
                        continue
                    target = str(targets[0])
                    if not target.endswith("/*"):
                        continue
                    prefix = alias[:-1]  # "@/*" -> "@/"
                    directory = (Path(base_url) / target[:-2]).as_posix().lstrip("./")
                    self._js_aliases.append((prefix, directory))
        self._js_aliases.sort(key=lambda pair: len(pair[0]), reverse=True)

    def _resolve_js_alias(self, module: str, source_path: str) -> str | None:
        for prefix, directory in getattr(self, "_js_aliases", []):
            if module.startswith(prefix):
                base = Path(directory) / module[len(prefix):] if directory else Path(module[len(prefix):])
                return self._resolve_js_path(base, source_path)
        return None

    def _resolve_relative_import(self, source_path: str, module: str) -> str | None:
        source_dir = Path(source_path).parent
        parts = module.lstrip(".").split(".") if module.lstrip(".") else []
        dots = len(module) - len(module.lstrip("."))
        base = source_dir
        for _ in range(dots - 1):
            if base == Path("."):
                return None
            base = base.parent
        candidate_dir = base.joinpath(*parts)
        for ext in [".py", ".js", ".ts", "/__init__.py", "/index.js", "/index.ts"]:
            candidate = candidate_dir.with_suffix(ext) if not ext.startswith("/") else candidate_dir / ext.lstrip("/")
            if self._exists(candidate):
                return str(candidate).replace("\\", "/")
        return None

    def _exists(self, candidate: Path) -> bool:
        """Test a project-relative candidate against the project root.

        `candidate` is relative to the project, so a bare `.is_file()` resolves
        it against the process working directory instead -- which is why the
        same scan produced a different graph depending on where it was run
        from. A build with no root cannot resolve paths at all, and says so by
        returning False rather than by consulting an unrelated directory.
        """
        if self._root is None:
            return False
        return (self._root / candidate).is_file()

    def _build_module_index(self, project: ProjectSemantic) -> None:
        """Index every file by every dotted-path suffix of its path, once.

        Resolving each import by walking all files -- and rebuilding
        Path(...).parts for every (import, file) pair -- was 98% of a
        321-second scan of a 2,140-file project, and 275 of the 277 seconds
        spent on a 4,108-file TypeScript tree. The work is identical for every
        import, so it belongs in a dict built once.

        First writer wins per key, which preserves the previous behaviour of
        returning the first matching file in scan order.
        """
        self._module_index: Dict[tuple, str] = {}
        self._stem_index: Dict[str, List[str]] = {}
        for file in project.files:
            file_parts = Path(file.path).with_suffix("").parts
            for size in range(1, len(file_parts) + 1):
                self._module_index.setdefault(file_parts[-size:], file.path)
            self._stem_index.setdefault(Path(file.path).stem, []).append(file.path)

    def _resolve_absolute_import(self, module: str, project: ProjectSemantic) -> str | None:
        # Map a dotted module name to a project file. Prefer a full dotted-path
        # tail match (e.g. ``import foo.bar`` -> src/foo/bar.py); fall back to a
        # stem match only when unambiguous to avoid false edges on common names.
        if not module:
            return None
        parts = tuple(module.split("."))
        hit = self._module_index.get(parts)
        if hit is not None:
            return hit
        if len(parts) == 1:
            # Accept a stem-only match only when exactly one candidate exists.
            candidates = self._stem_index.get(parts[0], [])
            if len(candidates) == 1:
                return candidates[0]
        return None
