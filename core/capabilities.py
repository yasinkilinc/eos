"""What a project offers an agent instead of a raw command (ADR-026).

A host routes its external actions through wrappers -- a script that calls the
issue tracker, the database, the build -- because the raw command floods the
context, skips a safety check, or leaves no trace in the execution ledger.
Measured on one workspace: an issue search 40k-233k tokens raw against ~605
through its wrapper; a build ~13k tokens against five lines. The wrapper only
helps if the agent reaches for it, and a rule saying "use the wrapper" was
measured not to hold on its own.

So the wrappers are declared once, as data, in `capabilities.toml` beside the
notes (the knowledge directory is versioned; `.eos/` often is not), and every
surface that needs the list reads the same file:

- `eos capabilities` lists them, or the ones a task or a command calls for
- the task brief names the few a task's words point at, before the first call
- `eos hook post-tool` notices a raw command a capability covers, records it
  on the run as a bypass and answers it with one hint per session
- a host guard can refuse the `block` forms before they run

    [[capability]]
    name  = "tracker"
    run   = "scripts/tracker.sh"
    does  = "issue|search|comments <KEY>; writes need --confirm"
    words = ["issue", "ticket", "tracker"]
    block = []                                   # regexes a guard refuses
    hint  = ['curl\\s[^|;&]*tracker\\.example']  # allowed; answered with a hint

Nothing here runs a wrapper or decides that one was the right call: it states
what exists and recognises a command, and the agent decides (ADR-018).
"""
from __future__ import annotations

import dataclasses
import re
import shlex
from pathlib import Path

FILENAME = "capabilities.toml"
_FIELDS = ("name", "run", "does", "words", "block", "hint")
# A task names a capability by at most this many of them; more is a menu.
TASK_LIMIT = 3


@dataclasses.dataclass(frozen=True)
class Capability:
    name: str
    run: str
    does: str = ""
    words: tuple[str, ...] = ()
    block: tuple[str, ...] = ()
    hint: tuple[str, ...] = ()

    @property
    def program(self) -> str:
        """The name a command line uses to invoke it: `jira.sh` for `automation/jira.sh`.

        Empty when `run` is not a command -- "Edit tool", a capability whose
        answer is a harness tool -- so no command line is mistaken for it.
        """
        first = self.run.split()[0] if self.run.strip() else ""
        if "/" in first or re.fullmatch(r"[a-z0-9][\w.-]*", first):
            return Path(first).name
        return ""

    def line(self) -> str:
        return f"{self.name} → {self.run}" + (f"  {self.does}" if self.does else "")


def path_for(project_root: str | Path) -> Path:
    from core import notes

    return notes.notes_dir(project_root) / FILENAME


def load(project_root: str | Path) -> list[Capability]:
    """Every declared capability, in file order; [] when there is no file.

    A malformed entry is refused with its position named: a registry that
    silently dropped a typo would stop flagging the raw form it was written
    to catch, and nobody would notice the gap.
    """
    path = path_for(project_root)
    if not path.is_file():
        return []
    from core.lib.config_io import ConfigIO

    data = ConfigIO.read_toml(path)
    entries = data.get("capability", [])
    if not isinstance(entries, list):
        raise ValueError(f"{path}: `capability` must be an array of tables ([[capability]])")
    found: list[Capability] = []
    seen: set[str] = set()
    for number, entry in enumerate(entries, 1):
        where = f"{path.name} capability #{number}"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} is not a table")
        unknown = [key for key in entry if key not in _FIELDS]
        if unknown:
            raise ValueError(f"{where} has an unknown key {unknown[0]!r}; known: {', '.join(_FIELDS)}")
        name, run = entry.get("name"), entry.get("run")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{where} needs a name")
        if not isinstance(run, str) or not run.strip():
            raise ValueError(f"{where} ({name}) needs `run`: the command an agent should use")
        if name.strip() in seen:
            raise ValueError(f"{where}: the name {name!r} is used twice")
        seen.add(name.strip())
        lists = {}
        for key in ("words", "block", "hint"):
            value = entry.get(key, [])
            if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
                raise ValueError(f"{where} ({name}): {key} must be a list of non-empty strings")
            lists[key] = tuple(v.strip() for v in value)
        for key in ("block", "hint"):
            for pattern in lists[key]:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValueError(f"{where} ({name}): {key} pattern {pattern!r} is not a regex: {exc}")
        does = entry.get("does", "")
        if not isinstance(does, str):
            raise ValueError(f"{where} ({name}): does must be a string")
        found.append(Capability(name=name.strip(), run=run.strip(), does=does.strip(),
                                words=tuple(w.casefold() for w in lists["words"]),
                                block=lists["block"], hint=lists["hint"]))
    return found


def _invocation(program: str) -> re.Pattern[str]:
    return re.compile(r"(?:^|[\s;&|(/`$])" + re.escape(program) + r"(?=$|[\s;&|)`])")


def wrapper_in(command: str, capabilities: list[Capability]) -> Capability | None:
    """The capability this command already runs through, if any."""
    for capability in capabilities:
        if capability.program and _invocation(capability.program).search(command):
            return capability
    return None


@dataclasses.dataclass(frozen=True)
class Match:
    capability: Capability
    tier: str        # "block" or "hint"
    pattern: str


def match(command: str, capabilities: list[Capability]) -> Match | None:
    """The first capability whose raw form this command is -- block before hint.

    A command that already runs a registered wrapper is never a raw form:
    `tracker.sh search X | grep Y` is the wrapper doing its job.
    """
    if not command or wrapper_in(command, capabilities) is not None:
        return None
    for tier in ("block", "hint"):
        for capability in capabilities:
            for pattern in getattr(capability, tier):
                if re.search(pattern, command):
                    return Match(capability, tier, pattern)
    return None


def for_task(task: str, capabilities: list[Capability], limit: int = TASK_LIMIT) -> list[Capability]:
    """The capabilities a task's words point at, most words first, then file order."""
    from core import notes

    words = notes._words(task or "")
    scored = []
    for order, capability in enumerate(capabilities):
        overlap = len(words & set(capability.words)) + (1 if capability.name.casefold() in words else 0)
        if overlap:
            scored.append((-overlap, order, capability))
    return [capability for _, _, capability in sorted(scored)[:limit]]


def remedy(found: Match) -> str:
    """One line an agent can act on, naming what to run instead."""
    capability = found.capability
    return (f"`{capability.name}` has a wrapper: {capability.run}"
            + (f" ({capability.does})" if capability.does else "")
            + " -- it keeps the output small and records the call on the run.")


# --- a command line, reduced to what the ledger may hold -------------------------

# Programs that only look. Recording each `ls` or `grep` as something a run
# did buries the actions worth reading, and says nothing a transcript lacks.
READ_ONLY = frozenset((
    "ls", "cat", "head", "tail", "wc", "echo", "printf", "pwd", "which", "type", "file", "stat",
    "du", "df", "find", "grep", "egrep", "fgrep", "sort", "uniq", "cut", "tr", "date", "true",
    "false", "sleep", "test", "[", "cd", "sed", "awk", "jq", "less", "more", "tree", "basename",
    "dirname", "realpath", "readlink", "env", "export", "set", "command", "xargs", "diff", "cmp",
    "column", "nl", "od", "strings", "shasum", "md5", "sha256sum", "whoami", "hostname", "uname",
    "id", "ps", "lsof", "time", "timeout", "exit", "source", ".", "unset", "printenv", "tee",
))
GIT_READ_VERBS = frozenset((
    "status", "log", "diff", "show", "branch", "remote", "rev-parse", "ls-files", "blame", "grep",
    "describe", "config", "shortlog", "ls-remote", "rev-list", "cat-file", "merge-base", "reflog",
    "for-each-ref", "name-rev", "check-ignore", "worktree", "help", "version", "--version",
))
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SEPARATORS = ("&&", "||", ";", "|", "&")
# A here-document's body is input to its command, not commands of its own.
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n.*?^\s*\2\s*$", re.S | re.M)


def programs(command: str) -> list[tuple[str, str | None]]:
    """(program, git verb) for each simple command in a command line, in order.

    Only names leave here -- never an argument, a path or a value -- so what
    a hook records from a command line cannot carry what a person typed.
    """
    command = _HEREDOC.sub(" ", command).replace("\n", " ; ")
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        tokens = command.split()
    found: list[tuple[str, str | None]] = []
    expect = True
    words: list[str] = []
    for token in tokens + [";"]:
        # An empty token is an argument (`sed -i ''`), not a separator: the
        # empty set is a subset of every set, and treating it as one split
        # `sed -i '' 's/a/b/' f` and named the program "b" (seen live).
        if token in _SEPARATORS or (token and set(token) <= set("&|;()")):
            if words:
                found.append(_simple(words))
            words, expect = [], True
            continue
        if expect and _ASSIGNMENT.match(token):
            continue
        expect = False
        words.append(token)
    return [item for item in found if item[0]]


def _simple(words: list[str]) -> tuple[str, str | None]:
    head = Path(words[0]).name
    if head in ("sudo", "nohup", "time", "exec", "command", "env"):
        rest = [w for w in words[1:] if not w.startswith("-") and not _ASSIGNMENT.match(w)]
        return _simple(rest) if rest else (head, None)
    if head in ("bash", "sh", "zsh") and len(words) > 1 and not words[1].startswith("-"):
        head = Path(words[1]).name
    verb = None
    if head == "git":
        rest = words[1:]
        while rest and rest[0].startswith("-"):
            rest = rest[2:] if rest[0] in ("-C", "-c", "--git-dir", "--work-tree") else rest[1:]
        verb = rest[0] if rest else None
    return head, verb


# EOS's own commands record themselves (a run's start, its decisions, notes);
# an event saying "eos ran" inside the run it opened is noise.
SELF = frozenset(("eos", "eos-event"))


def worth_recording(program: str, verb: str | None) -> bool:
    if program == "git":
        return bool(verb) and verb not in GIT_READ_VERBS
    return program not in READ_ONLY and program not in SELF
