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


def worktree_dirs(workspace_root: str | Path) -> list[Path]:
    """Directories holding extra checkouts of the projects (`worktrees = [...]`
    at the top of projects.toml): a checkout `<dir>/<project name>-<label>/` is
    that project's. [] without the file or the key."""
    import tomllib

    source = path_for(workspace_root)
    try:
        data = tomllib.loads(source.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    entries = data.get("worktrees", [])
    if not isinstance(entries, list):
        return []
    return [(source.parent.parent / entry).resolve() for entry in entries if isinstance(entry, str) and entry.strip()]


def owner(workspace_root: str | Path, projects: list[Project], path: str | Path,
          worktrees: list[Path] | None = None) -> tuple[str, Path, str] | None:
    """(project name, project root, path relative to it) for a file, or None.

    The deepest project root holding the file wins, the workspace root itself
    being one; a file in a worktree checkout belongs to the project whose name
    the checkout's directory starts with, and is relative to the checkout --
    a note's scope is project-relative, so a path that kept the checkout's
    directory would match no note."""
    import os

    root = Path(workspace_root).expanduser().resolve()
    try:
        target = Path(os.path.realpath(Path(path).expanduser()))
    except (OSError, ValueError):
        return None
    for directory in worktrees or []:
        try:
            parts = target.relative_to(directory).parts
        except ValueError:
            continue
        if len(parts) < 2:
            return None
        for project in sorted(projects, key=lambda p: len(p.root.name), reverse=True):
            if parts[0] == project.root.name or parts[0].startswith(project.root.name + "-"):
                return project.name, project.root, "/".join(parts[1:])
        return None
    best = None
    for name, project_root in [(root.name, root)] + [(p.name, p.root) for p in projects]:
        try:
            relative = target.relative_to(project_root)
        except ValueError:
            continue
        if relative.parts and (best is None or len(project_root.parts) > len(best[1].parts)):
            best = (name, project_root, relative.as_posix())
    return best


def of(project_root: str | Path) -> Path | None:
    """The workspace a project belongs to (`[workspace] root` in the project's
    config, relative to the project), when that workspace's projects.toml
    lists it. A session can start in the project as well as at the root; what
    the workspace recorded -- its procedures above all -- must reach it there
    too."""
    from core.lib.config_io import ConfigIO

    project = Path(project_root).expanduser().resolve()
    try:
        table = ConfigIO.read_toml(project / ".eos" / "config.toml").get("workspace")
        value = table.get("root") if isinstance(table, dict) else None
        if not isinstance(value, str) or not value.strip():
            return None
        root = (project / value).resolve()
        if root != project and any(p.root == project for p in load(root)):
            return root
    except Exception:  # noqa: BLE001 - a broken link briefs the project alone
        return None
    return None


def load_quiet(workspace_root: str | Path) -> list[Project]:
    """`load` for a hook: a broken file briefs no project rather than failing a session."""
    try:
        return load(workspace_root)
    except ValueError:
        return []


def where(workspace_root: str | Path, project: Project) -> str:
    """The project's path as a command typed at the workspace root takes it."""
    import os

    return os.path.relpath(project.root, Path(workspace_root).expanduser().resolve())


# `eos <verb> [<verb>] .` -- a brief names the project it was built for as `.`,
# which from the workspace root is the wrong store.
_HERE = re.compile(r"(\beos (?:[a-z][\w-]* ){1,2})\.(?=[\s\"'`)]|$)", re.M)


def relocate(text: str, project_name: str, path: str) -> str:
    """A project's brief, readable from the workspace root: every command names
    the project's path instead of `.`, and the header names the path too. The
    fresh-session eval followed a service block's `eos note show .` into the
    workspace's own store twice before it thought to `cd`."""
    head, newline, rest = text.partition("\n")
    head = head.replace(f"— {project_name}", f"— {path}", 1)
    return _HERE.sub(lambda match: match.group(1) + path, head + newline + rest)


def live(project_root: Path) -> bool:
    """Whether a project has something a session should hear about unasked:
    an open run or work in flight. Files checked first -- most projects of a
    workspace have neither ledger, and those cost a stat, not a parse."""
    from core import executions, work

    try:
        if executions.path_for(project_root).is_file() and any(r.open for r in executions.load(project_root)):
            return True
        return work.path_for(project_root).is_file() and bool(work.items(project_root))
    except Exception:  # noqa: BLE001 - a ledger that cannot be read is not live
        return False


# NOTES ELSEWHERE: a task note lives in the store of the project it is about,
# and a prompt like "1588 where are we" names no project, so no brief reaches
# it. Title and tags only decide; the full text only measures rarity.
_KEY = re.compile(r"(?<![\w-])([^\W\d_][^\W_]*)-(\d+)(?![\w-])")
_BARE_NUMBER = re.compile(r"(?<![\w-])(\d{4,})(?![\w-])")
ELSEWHERE_LIMIT = 3
# A word is the prompt's subject when at most this share of every store's
# notes contains it anywhere (and never fewer than SUBJECT_MIN_DOCS). Measured
# on a 508-note workspace: a subject word in 1-2 notes, task words ("scenario",
# "push") in 13-17; a fixed count lost the subject when a bulk import doubled
# the store, a share did not.
SUBJECT_MAX_SHARE = 0.01
SUBJECT_MIN_DOCS = 3


def notes_elsewhere(workspace_root: str | Path, projects: list[Project], prompt: str,
                    briefed: set[Path]) -> str:
    """Notes in the stores no brief covered whose title or tags hold the issue
    key (or its number) the prompt names, or a word rare enough across every
    store to be the prompt's subject; '' when there are none."""
    from core import notes

    root = Path(workspace_root).expanduser().resolve()
    keys = {f"{m.group(1)}-{m.group(2)}".casefold() for m in _KEY.finditer(prompt)}
    numbers = {m.group(1) for m in _BARE_NUMBER.finditer(prompt)}
    prompt_words = notes._words(prompt) - keys - numbers
    stores = [(root, ".")] + [(p.root, where(root, p)) for p in projects]
    corpus = []
    for project_root, path in stores:
        try:
            loaded = notes.load_notes(project_root)
        except Exception:  # noqa: BLE001 - one unreadable store costs its notes
            continue
        corpus += [(project_root, path, note) for note in loaded if note.title]
    if not corpus:
        return ""
    texts = [" ".join([note.title, *note.tags, note.body]).casefold() for _, _, note in corpus]
    most = max(SUBJECT_MIN_DOCS, int(len(corpus) * SUBJECT_MAX_SHARE))
    subjects = {word for word in prompt_words if 0 < sum(word in text for text in texts) <= most}
    scored = []
    for project_root, path, note in corpus:
        if project_root in briefed or notes.is_bulk_index(note) or note.kind == "procedure":
            continue
        head_text = " ".join([note.title, *note.tags])
        head = notes._words(head_text)
        if keys & head or any(f"-{number}" in head_text.casefold() for number in numbers):
            scored.append((2, note.path.name, path, note))
        elif subjects & head:
            scored.append((1, note.path.name, path, note))
    if not scored:
        return ""
    # YYYYMMDD-slug file names: newest first among equals.
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    lines = ["NOTES ELSEWHERE — named by this prompt, in a store no brief above covered"]
    lines += [f"  - {note.title}  ({Path(path).name if path != '.' else root.name})"
              for _, _, path, note in scored[:ELSEWHERE_LIMIT]]
    lines.append(f'  Read one: eos note show {scored[0][2]} "<title>"')
    return "\n".join(lines)


def ledger_holding(workspace_root: str | Path, execution: str) -> Path | None:
    """The workspace project's run ledger that holds this execution, if any."""
    from core import executions

    try:
        projects = load(workspace_root)
    except ValueError:
        return None
    for project in projects:
        ledger = executions.path_for(project.root)
        if ledger.is_file() and executions.load_path(ledger, only=execution):
            return ledger
    return None
