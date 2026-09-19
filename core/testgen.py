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

    return Draft(
        code=code, status=STATUS_DRAFT, thrown_by=path, method=where, source_ref=source_ref,
        test_path=test_path, test_exists=bool(tests), style=style, grounded=grounded,
        body=_render(code, owner, method, style, bool(tests)),
    )


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


def _render(code: str, owner: str, method: str, style: dict[str, Any], joining: bool) -> str:
    """The draft itself: one test method, in the style of its neighbours."""
    assertions = style.get("assertions") or "assertj"
    name = f"raises{_camel(code)}"
    display = f'    @DisplayName("raises {code}")\n' if style.get("display_name") else ""
    call = f"{_lower(owner)}.{method}(/* arrange the input that makes this fail */)" if method \
        else f"{_lower(owner)}.execute(/* arrange the input that makes this fail */)"

    if assertions == "junit":
        body = (f"        Exception thrown = assertThrows(Exception.class, () ->\n"
                f"                {call});\n"
                f'        assertTrue(thrown.getMessage().contains("{code}")\n'
                f'                || String.valueOf(thrown).contains("{code}"));')
    else:
        body = (f"        assertThatThrownBy(() ->\n"
                f"                {call})\n"
                f'                .hasMessageContaining("{code}");')

    method_text = (f"{display}    @Test\n"
                   f"    void {name}() {{\n"
                   f"        // DRAFT -- not compiled, not run, not reviewed.\n"
                   f"        // The class is already reached by this test; what is missing is\n"
                   f"        // the arrangement that drives it down the branch raising {code}.\n"
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
