"""Draft a test for a refusal the suite does not assert.

Deliberately narrow. The valuable gap is not "this class has no test" -- it is
the state measured at 134 of 403 codes on one real service: a test already
reaches the class that raises a refusal, and nothing checks that branch. The
fixture exists, the mocks exist, the file exists. What is missing is one
method, and a method is something that can be drafted from facts already in
the index rather than invented.

So this writes *into the style of the file the method would join*, read from
disk at generation time rather than guessed from a convention table: the same
imports, the same assertion library, the same annotations. A draft that does
not look like its neighbours does not get reviewed, it gets deleted.

Three rules hold it honest:

* Every symbol the draft names is grounded against the index first -- the
  class, the method, its parameters, the exception type. If one cannot be
  grounded the draft is refused rather than emitted with a plausible guess.
* It is written to `.eos/data/candidates/`, never into the source tree, and
  never near production code. Nothing here edits anything a build compiles.
* It is called a draft in every surface. It has not been compiled, run, or
  reviewed, and the states after those things happen are not this one.
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any

from core import index as _index
from core.lib.paths import is_test_path

STATUS_DRAFT = "draft"

_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)\s*;", re.MULTILINE)
_CLASS_OPEN = re.compile(r"\b(?:class|interface|enum|record)\s+(\w+)")
# `throw new X("CODE", ...)` and `throw X.of("CODE", ...)`, the two spellings
# the code detector already looks for, read back here for the exception type
# and for whether the code is an argument rather than part of the message.
_THROW_SITE = re.compile(
    r"throw\s+(?:new\s+)?(\w+)(?:\s*\.\s*of)?\s*\(\s*(?:\"(?P<literal>[A-Z0-9_]+)\")?", re.MULTILINE)


@dataclasses.dataclass
class Draft:
    code: str
    status: str
    thrown_by: str | None = None
    method: str | None = None
    source_ref: str | None = None
    test_path: str | None = None
    test_exists: bool = False
    style: dict[str, Any] = dataclasses.field(default_factory=dict)
    body: str | None = None
    grounded: list[str] = dataclasses.field(default_factory=list)
    refused: str | None = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def draft_for(project_root: str | Path, code: str) -> Draft:
    """Draft one test method for one behaviour code, or refuse and say why."""
    conn = _index.open_for_read(project_root)
    if conn is None:
        raise ValueError(f"No index at {_index.db_path(project_root)}. Run 'eos index' first.")
    try:
        if not _index.has_tables(conn, ("fact",)):
            raise ValueError(f"{_index.db_path(project_root)} predates provenance. "
                             "Run 'eos index' to rebuild it.")
        sites = conn.execute(
            "SELECT n.path, f.source_ref, f.object FROM fact f JOIN node n ON n.id = f.subject "
            "WHERE f.predicate = 'throws-code' AND f.object = ? ORDER BY n.path", (code,)).fetchall()
        detail = conn.execute(
            "SELECT object FROM fact WHERE predicate = 'throws-code-at' "
            "AND object LIKE ?", (f"{code}|%",)).fetchall()
        reached = _tests_reaching(conn, [row[0] for row in sites])
        class_files = _class_files(conn, project_root)
    finally:
        conn.close()

    if not sites:
        return Draft(code=code, status="refused",
                     refused=f"nothing in this project throws {code}")

    # The project's own file first: a draft for a class in a linked parent
    # would be written into a tree this project must not modify.
    own = [row for row in sites if not row[0].startswith("@parent:")]
    if not own:
        return Draft(code=code, status="refused", thrown_by=sites[0][0],
                     refused=f"{code} is thrown only in a linked parent, which this project "
                             "does not own; add the test where the parent lives")
    path, source_ref, _ = own[0]

    where = ""
    for (value,) in detail:
        _, _, rest = value.partition("|")
        candidate, _, _ = rest.partition("|")
        if candidate.split(".")[0] in Path(path).stem:
            where = candidate
            break
    where = where or (detail[0][0].split("|")[1] if detail else "")
    owner, _, method = where.rpartition(".")
    owner = owner or Path(path).stem

    root = Path(project_root).expanduser().resolve()
    tests = sorted(reached.get(path, ()))
    test_path = tests[0] if tests else _proposed_test_path(path)
    style = _style_of(root, test_path if tests else None, root / path)

    grounded, missing = _ground(root, path, owner, method, style)
    if missing:
        return Draft(code=code, status="refused", thrown_by=path, method=where,
                     source_ref=source_ref, test_path=test_path, test_exists=bool(tests),
                     style=style, grounded=grounded,
                     refused="; ".join(missing))

    # Can a test actually call the method that raises it? Private says no, and
    # so does protected or package-private from a test in another package. A
    # draft that names an inaccessible method does not compile, and a reviewer
    # who trusted the word "draft" finds that out only after wiring it up.
    subject = (root / path).read_text(encoding="utf-8", errors="replace")
    visibility = _declared_visibility(subject, method) if method else None
    call_method, note = method, None
    if visibility == "private" or (
            visibility in ("protected", "package")
            and style.get("package") and style["package"] != _package_of(subject)):
        entry = _public_entry(subject, exclude=method)
        if entry is None:
            return Draft(code=code, status="refused", thrown_by=path, method=where,
                         source_ref=source_ref, test_path=test_path, test_exists=bool(tests),
                         style=style, grounded=grounded,
                         refused=f"{method} is {visibility} and {owner} exposes no method a "
                                 f"test can call, so there is no way in from a test")
        call_method, note = entry, (
            f"{method} is {visibility}, so the branch is driven through {entry}")
        grounded.append(f"method {entry} is public in {path}")

    shape = _throw_shape(root, path, source_ref, code)
    accessor = _accessor_for(root, class_files, shape["exception"])
    if shape["exception"]:
        grounded.append(f"{code} is raised as {shape['exception']} at {source_ref}")
    if accessor:
        grounded.append(f"{shape['exception']}.{accessor}() returns the code")

    return Draft(
        code=code, status=STATUS_DRAFT, thrown_by=path, method=where, source_ref=source_ref,
        test_path=test_path, test_exists=bool(tests), style=style, grounded=grounded,
        body=_render(code, owner, call_method, style, bool(tests),
                     shape=shape, accessor=accessor, note=note),
    )


def _package_of(source: str) -> str | None:
    found = _PACKAGE.search(source)
    return found.group(1) if found else None


def _class_files(conn, project_root: str | Path) -> dict[str, Path]:
    """Class name -> the file declaring it, linked parents included.

    Only used to ground an exception's accessor, which is the one symbol a
    draft needs that is routinely defined outside the project: on an overlay
    codebase the exception type comes from the parent, and refusing to look
    there would mean never grounding it at all.
    """
    from core import links

    root = Path(project_root).expanduser().resolve()
    roots = {label: links.resolve_link_path(root, link)
             for label, link in links.read_links(root).items()}
    files: dict[str, Path] = {}
    try:
        rows = conn.execute("SELECT path FROM node WHERE path LIKE '%.java'").fetchall()
    except Exception:
        return files
    for (path,) in rows:
        name = Path(path).stem
        if name in files:
            continue
        if path.startswith(links.PARENT_PREFIX):
            label, _, rest = path[len(links.PARENT_PREFIX):].partition("/")
            parent_root = roots.get(label)
            if parent_root is None:
                continue
            files[name] = parent_root / rest
        else:
            files[name] = root / path
    return files


def _tests_reaching(conn, paths: list[str]) -> dict[str, set[str]]:
    if not paths:
        return {}
    placeholders = ",".join("?" * len(paths))
    rows = conn.execute(
        f"SELECT DISTINCT d.path, s.path FROM edge e "
        f"JOIN node s ON s.nid = e.src JOIN node d ON d.nid = e.dst "
        f"WHERE e.kind IN ('calls','new','field','import') AND d.path IN ({placeholders})",
        paths).fetchall()
    found: dict[str, set[str]] = {}
    for target, source in rows:
        if is_test_path(source):
            found.setdefault(target, set()).add(source)
    return found


def _proposed_test_path(path: str) -> str:
    """Where this project would put the test, by its own layout."""
    if "/src/main/" in path:
        return path.replace("/src/main/", "/src/test/").replace(".java", "Test.java")
    return path.replace(".java", "Test.java")


def _style_of(root: Path, test_path: str | None, subject: Path) -> dict[str, Any]:
    """The conventions of the file this method would join, read from it.

    Read rather than assumed. A generated method that imports a different
    assertion library than its neighbours is not reviewed, it is deleted -- and
    which library a project uses is not knowable from a table of defaults.
    """
    style: dict[str, Any] = {"package": None, "assertions": None, "runner": None,
                             "source": test_path or "(no existing test)"}
    text = ""
    if test_path:
        try:
            text = (root / test_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
    if not text:
        try:
            package = _PACKAGE.search(subject.read_text(encoding="utf-8", errors="replace"))
            style["package"] = package.group(1) if package else None
        except OSError:
            pass
        return style

    package = _PACKAGE.search(text)
    style["package"] = package.group(1) if package else None
    imports = set(_IMPORT.findall(text))
    style["imports"] = sorted(imports)
    if any(name.startswith("org.assertj") for name in imports) or "assertThatThrownBy" in text:
        style["assertions"] = "assertj"
    elif "assertThrows" in text or any(".junit.jupiter.api.Assertions" in n for n in imports):
        style["assertions"] = "junit"
    if "MockitoExtension" in text:
        style["runner"] = "mockito"
    style["nested"] = "@Nested" in text
    style["display_name"] = "@DisplayName" in text
    match = _CLASS_OPEN.search(text)
    style["test_class"] = match.group(1) if match else None
    return style


def _declared_visibility(source: str, method: str) -> str | None:
    """`public`, `protected`, `private` or `package` for a declared method.

    None when no declaration is found -- the method is called in this file but
    declared elsewhere, which is not something to guess about.
    """
    pattern = re.compile(
        r"^[ \t]*(?P<mods>(?:@\w+\s+)*(?:public|protected|private|static|final|synchronized|"
        r"abstract|native|default|\s)*?)(?:<[^>]+>\s+)?[\w.<>\[\],?]+\s+"
        rf"{re.escape(method)}\s*\(", re.MULTILINE)
    found = pattern.search(source)
    if not found:
        return None
    mods = found.group("mods")
    for level in ("private", "protected", "public"):
        if re.search(rf"\b{level}\b", mods):
            return level
    return "package"


def _public_entry(source: str, exclude: str) -> str | None:
    """A method a test in any package can call, preferring `execute`.

    The refusal being drafted for is raised somewhere; if the raising method
    cannot be called from a test, the branch has to be driven through one that
    can. Nothing else in the class is a candidate: a test that calls a method
    it has no access to does not compile, and a draft that does not compile is
    worse than no draft, because it reads as though someone checked.
    """
    public = re.findall(r"^[ \t]*public\s+(?:static\s+|final\s+|synchronized\s+)*"
                        r"(?:<[^>]+>\s+)?[\w.<>\[\],?]+\s+(\w+)\s*\(", source, re.MULTILINE)
    candidates = [name for name in public if name != exclude]
    if not candidates:
        return None
    return "execute" if "execute" in candidates else candidates[0]


def _throw_shape(root: Path, path: str, source_ref: str | None, code: str) -> dict[str, Any]:
    """The exception type at the throw site, and where the code actually is.

    `hasMessageContaining(code)` is only a correct assertion when the code is
    in the message. In the shape this detector finds most often it is the
    first argument -- `ValidationException.of("CODE", "human text")` -- and the
    message never contains it, so a draft asserting the message fails for a
    reason that has nothing to do with the behaviour under test.
    """
    shape: dict[str, Any] = {"exception": None, "code_in_message": True}
    line_no = 0
    if source_ref and ":" in source_ref:
        try:
            line_no = int(source_ref.rsplit(":", 1)[1])
        except ValueError:
            line_no = 0
    try:
        lines = (root / path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return shape
    if not line_no:
        return shape
    # The `throw` may sit a line or two above the line the fact records, and
    # the arguments below it; read a small window rather than one line.
    window = "\n".join(lines[max(0, line_no - 4):line_no + 4])
    found = _THROW_SITE.search(window)
    if found:
        shape["exception"] = found.group(1)
        if found.group("literal") == code:
            shape["code_in_message"] = False
    return shape


def _accessor_for(root: Path, paths: dict[str, Path], exception: str | None) -> str | None:
    """The no-argument accessor an exception exposes its code through.

    Grounded in the exception's own source, including a linked parent's, or
    not offered at all. Guessing `getCode()` on a class that does not have it
    is exactly the kind of plausible symbol this module refuses to emit.
    """
    seen: set[str] = set()
    current = exception
    # Three hops up the hierarchy. On this codebase the accessor is on the
    # exception's grandparent, not on the type the throw site names, so
    # stopping at the class itself grounds nothing and the draft falls back to
    # an assertion that passes for any exception of that type.
    for _ in range(3):
        if not current or current in seen:
            return None
        seen.add(current)
        source_path = paths.get(current)
        if source_path is None:
            return None
        try:
            source = source_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        for name in ("getCode", "getErrorCode", "code", "errorCode"):
            if re.search(rf"^[ \t]*public\s+[\w.<>\[\]]+\s+{name}\s*\(\s*\)", source, re.MULTILINE):
                return name
        # Lombok's @Getter is a fact in the source, not a convention: the class
        # carries the annotation and the field carries the name, and both are
        # checkable. Missing them means grounding nothing on a codebase that
        # generates most of its accessors.
        if "@Getter" in source:
            for field, name in (("code", "getCode"), ("errorCode", "getErrorCode")):
                if re.search(rf"^[ \t]*(?:private|protected|public)?\s*(?:final\s+)?"
                             rf"String\s+{field}\s*[;=]", source, re.MULTILINE):
                    return name
        extends = re.search(r"\bclass\s+\w+(?:<[^>]+>)?\s+extends\s+(\w+)", source)
        current = extends.group(1) if extends else None
    return None


def _ground(root: Path, path: str, owner: str, method: str,
            style: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Check every symbol the draft would name against the source itself."""
    grounded, missing = [], []
    try:
        source = (root / path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return grounded, [f"cannot read {path} ({exc})"]

    if re.search(rf"\b(?:class|record|enum)\s+{re.escape(owner)}\b", source):
        grounded.append(f"class {owner} is declared in {path}")
    else:
        missing.append(f"no class {owner} in {path}")
    if method:
        if re.search(rf"\b{re.escape(method)}\s*\(", source):
            grounded.append(f"method {method} exists")
        else:
            missing.append(f"no method {method} in {path}")
    if style.get("assertions") is None and style.get("source") != "(no existing test)":
        missing.append("cannot tell which assertion library the existing test uses")
    return grounded, missing


def _render(code: str, owner: str, method: str, style: dict[str, Any], joining: bool,
            shape: dict[str, Any] | None = None, accessor: str | None = None,
            note: str | None = None) -> str:
    """The draft itself: one test method, in the style of its neighbours."""
    assertions = style.get("assertions") or "assertj"
    shape = shape or {"exception": None, "code_in_message": True}
    thrown_type = shape.get("exception") or "Exception"
    in_message = shape.get("code_in_message", True)
    name = f"raises{_camel(code)}"
    display = f'    @DisplayName("raises {code}")\n' if style.get("display_name") else ""
    call = f"{_lower(owner)}.{method}(/* arrange the input that makes this fail */)" if method \
        else f"{_lower(owner)}.execute(/* arrange the input that makes this fail */)"

    if assertions == "junit":
        body = (f"        {thrown_type} thrown = assertThrows({thrown_type}.class, () ->\n"
                f"                {call});\n")
        if in_message:
            body += f'        assertTrue(thrown.getMessage().contains("{code}"));'
        elif accessor:
            body += f'        assertEquals("{code}", thrown.{accessor}());'
        else:
            body += (f"        // The code is an argument to {thrown_type}, not part of the\n"
                     f"        // message. Assert it with this type's own accessor:\n"
                     f'        // assertEquals("{code}", thrown.<accessor>());')
    else:
        body = (f"        assertThatThrownBy(() ->\n"
                f"                {call})\n")
        if in_message:
            body += f'                .hasMessageContaining("{code}");'
        elif accessor:
            body += (f"                .isInstanceOf({thrown_type}.class)\n"
                     f"                .extracting(thrown -> (({thrown_type}) thrown).{accessor}())\n"
                     f'                .isEqualTo("{code}");')
        else:
            body += (f"                .isInstanceOf({thrown_type}.class);\n"
                     f"        // The code is an argument to {thrown_type}, not part of the\n"
                     f"        // message. Assert it with this type's own accessor, or the\n"
                     f"        // test passes for any {thrown_type} at all.")

    why = (f"        // {note}.\n" if note else "")
    method_text = (f"{display}    @Test\n"
                   f"    void {name}() {{\n"
                   f"        // DRAFT -- not compiled, not run, not reviewed.\n"
                   f"        // The class is already reached by this test; what is missing is\n"
                   f"        // the arrangement that drives it down the branch raising {code}.\n"
                   f"{why}"
                   f"{body}\n"
                   f"    }}")
    if joining:
        return method_text
    package = style.get("package") or "com.example"
    return (f"package {package};\n\n"
            f"import org.junit.jupiter.api.Test;\n"
            f"import org.junit.jupiter.api.extension.ExtendWith;\n"
            f"import org.mockito.junit.jupiter.MockitoExtension;\n\n"
            f"@ExtendWith(MockitoExtension.class)\n"
            f"class {owner}Test {{\n\n"
            f"{method_text}\n"
            f"}}\n")


def _camel(code: str) -> str:
    return "".join(part.capitalize() for part in code.split("_") if part)


def _lower(name: str) -> str:
    return name[:1].lower() + name[1:] if name else name


def write_draft(project_root: str | Path, draft: Draft) -> Path:
    """Save a draft under .eos/data/candidates/. Never into the source tree."""
    target = (Path(project_root).expanduser().resolve() / ".eos" / "data" / "candidates"
              / f"{draft.code}.java.txt")
    target.parent.mkdir(parents=True, exist_ok=True)
    header = (f"// DRAFT for {draft.code} -- status: {draft.status}\n"
              f"// would join: {draft.test_path}\n"
              f"// thrown by:  {draft.source_ref}\n"
              f"// style read from: {draft.style.get('source')}\n"
              f"// Not compiled, not run, not reviewed. Copy it in, make it fail for the\n"
              f"// right reason, then make it pass.\n\n")
    target.write_text(header + (draft.body or ""), encoding="utf-8")
    return target
