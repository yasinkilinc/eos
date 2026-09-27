"""Which project a run is about, from the workspace root (2.x roadmap C5, part 2).

Sessions in a workspace of many projects start one directory above them, so a
run about one project used to land in the workspace's own ledger and that
project's ledger stayed empty. `<workspace>/.eos/projects.toml` names the
projects and the words that mean each:

    [[project]]
    root = "../services/order-capture"      # relative to the workspace root
    name = "order-capture"                  # defaults to the root's directory name
    aliases = ["ordercapture", "oc"]        # the name is always one

`run start` from the workspace records the run in the project its title names
-- exactly one, by a whole alias. Near misses are the alias blocker's
candidates (roadmap R15: a digit guard, then edit distance within half the
shorter word, capped at one): printed as a suggestion, never acted on.
Without the file, nothing changes.
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

FILENAME = "projects.toml"
# A title word joined with its neighbours: "order capture" is an alias too.
MAX_ALIAS_WORDS = 3
# Shorter words are too common to be a near miss of anything.
MIN_NEAR_LENGTH = 5
MAX_NEAR_EDITS = 1

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*[A-Za-z0-9]|[A-Za-z0-9]")


@dataclasses.dataclass(frozen=True)
class Project:
    name: str
    root: Path
    aliases: tuple[str, ...]


def path_for(workspace_root: str | Path) -> Path:
    return Path(workspace_root).expanduser().resolve() / ".eos" / FILENAME


def _key(text: str) -> str:
    """`Order-Capture`, `order_capture` and `ordercapture` are one alias."""
    return re.sub(r"[-_. ]+", "", text.casefold())


def load(workspace_root: str | Path) -> list[Project]:
    """The projects the workspace names; [] without the file.

    Raises ValueError on a file that cannot be read as projects -- a routing
    table that silently routes nothing is worse than one that says it is broken.
    """
    import tomllib

    source = path_for(workspace_root)
    if not source.is_file():
        return []
    try:
        data = tomllib.loads(source.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"{source}: {exc}") from exc
    entries = data.get("project", [])
    if not isinstance(entries, list):
        raise ValueError(f"{source}: [[project]] must be an array of tables")
    base = source.parent.parent
    found: list[Project] = []
    for number, entry in enumerate(entries, 1):
        if not isinstance(entry, dict) or not isinstance(entry.get("root"), str) or not entry["root"].strip():
            raise ValueError(f"{source}: project {number} has no root")
        root = (base / entry["root"]).resolve()
        name = entry.get("name") or root.name
        aliases = entry.get("aliases", [])
        if not isinstance(name, str) or not isinstance(aliases, list) \
                or not all(isinstance(a, str) and a.strip() for a in aliases):
            raise ValueError(f"{source}: project {number} ({entry['root']}): name is a string, "
                             f"aliases a list of strings")
        if not root.is_dir():
            continue  # a project not cloned on this machine routes nothing here
        found.append(Project(name=name, root=root, aliases=tuple(dict.fromkeys([name, *aliases]))))
    return found


def find(projects: list[Project], word: str) -> Project | None:
    """The project a name or alias means, or None."""
    key = _key(word)
    for project in projects:
        if any(_key(alias) == key for alias in project.aliases):
            return project
    return None


def _spans(text: str) -> list[str]:
    words = _WORD.findall(text)
    return [" ".join(words[i:i + n]) for n in range(1, MAX_ALIAS_WORDS + 1)
            for i in range(len(words) - n + 1)]


def named_in(projects: list[Project], text: str) -> list[tuple[Project, str]]:
    """Each project the text names by a whole alias, with the words that named it."""
    found: dict[str, tuple[Project, str]] = {}
    for span in _spans(text):
        project = find(projects, span)
        if project is not None and project.name not in found:
            found[project.name] = (project, span)
    return list(found.values())


def _distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _near(a: str, b: str) -> bool:
    """The alias blocker (R15): candidates only, for a person to confirm."""
    if a == b or min(len(a), len(b)) < MIN_NEAR_LENGTH:
        return False
    # Digit guard: `crm-batch2` and `crm-batch3` are two things, not a typo.
    if re.findall(r"\d+", a) != re.findall(r"\d+", b):
        return False
    # R15's half-the-word bound was made for entity names; over a free-text
    # title it paired `commit` with `common` on the host's run titles.
    return _distance(a, b) <= min(MAX_NEAR_EDITS, min(len(a), len(b)) // 2)


def near_in(projects: list[Project], text: str) -> list[tuple[Project, str]]:
    """Projects a word of the text almost names -- never used to route."""
    found: dict[str, tuple[Project, str]] = {}
    for span in _spans(text):
        key = _key(span)
        for project in projects:
            if project.name not in found and any(_near(key, _key(alias)) for alias in project.aliases):
                found[project.name] = (project, span)
    return list(found.values())


def ledger_holding(workspace_root: str | Path, execution: str) -> Path | None:
    """The workspace project's run ledger that holds this execution, if any."""
    from core import executions

    try:
        projects = load(workspace_root)
    except ValueError:
        return None
    for project in projects:
        ledger = executions.path_for(project.root)
        if ledger.is_file() and execution in {r.id for r in executions.load_path(ledger)}:
            return ledger
    return None
