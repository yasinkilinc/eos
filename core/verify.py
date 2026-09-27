"""Which work is verified: scopes changed after their last passing check (ADR-028).

A project declares, in `verify.toml` beside its notes, which files belong to
which scope and which command counts as that scope's check. A change to a
file in a scope is verified once a command matching the scope's `passes`
exits 0 after it. Everything here is pure: paths and commands in, instances
out. A missing or malformed file declares nothing, and nothing is gated.
"""
from __future__ import annotations

import dataclasses
import os
import re
from pathlib import Path

from core import notes

FILENAME = "verify.toml"
MAX_SHOWN = 3
MAX_REASON = 600
_PLACEHOLDER = re.compile(r"\{([A-Za-z_]\w*)\}")
# Words that run the next word as the command and pass its exit status through.
_LEAD = (r"(?:(?:/\S*/)?(?:bash|sh|env|command|exec|time|nice|nohup)\s+|(?:/\S*/)?timeout\s+\S+\s+"
         r"|\w+=\S*\s+)*")
# After the check, `&&` may run only these (and read-only git): none of them changes
# a file or the outcome.
_HARMLESS = {"echo", "printf", "true", ":", "exit", "cd", "pwd", "ls", "cat", "head", "tail", "wc"}
_GIT_READS = {"status", "diff", "log", "show", "rev-parse"}
_PIPEFAIL = re.compile(r"\s*set\b[^;\n]*\s-\w*o\s+pipefail\b")
# `set -e`, `set -euo pipefail`, `set -o errexit`; `set +e` turns it off.
_ERREXIT = re.compile(r"\s*set\b[^;\n]*(?:\s-[a-z]*e[a-z]*\b|\s-o\s+errexit\b)")
_NO_ERREXIT = re.compile(r"\s*set\b[^;\n]*(?:\s\+[a-z]*e[a-z]*\b|\s\+o\s+errexit\b)")
_NO_PIPEFAIL = re.compile(r"\s*set\b[^;\n]*\s\+o\s+pipefail\b")
# Where errexit does not apply, or text that does not run where it stands:
# conditions, loop and case heads, `!`, a function body or a `{ }` group.
_COMPOUND = re.compile(r"\s*(?:if|elif|while|until|for|case|select|do|then|else|function|!)(?:\s|$)"
                       r"|.*\w\s*\(\)|(?:.*\s)?\{(?:\s|$)")
# A block's opening and closing words, where a command starts: what runs between
# them may not run at all. Words that may stand before a command's first word.
_OPEN_WORDS = {"if", "while", "until", "for", "case", "select", "{"}
_CLOSE_WORDS = {"fi", "done", "esac", "}"}
_BEFORE_WORDS = {"!", "time", "then", "do", "else", "elif", "nice", "command", "exec", "coproc"}
MAX_NESTING = 20
_SUBSHELL = re.compile(r"\s*\((?P<inner>.*)\)\s*$", re.S)


@dataclasses.dataclass(frozen=True)
class Scope:
    name: str
    paths: tuple[str, ...]
    passes: tuple[str, ...]
    run: str


def path_for(project_root: str | Path) -> Path:
    return notes.notes_dir(project_root) / FILENAME


def load(project_root: str | Path) -> list[Scope]:
    """The declared scopes; [] when the file is absent or malformed."""
    path = path_for(project_root)
    if not path.is_file():
        return []
    try:
        from core.lib.config_io import ConfigIO

        data = ConfigIO.read_toml(path)
        scopes = []
        for entry in data.get("scope") or []:
            paths = tuple(p for p in entry.get("paths") or [] if isinstance(p, str))
            passes = tuple(p for p in entry.get("passes") or [] if isinstance(p, str))
            name, run = str(entry.get("name") or ""), str(entry.get("run") or "")
            if not (name and paths and passes):
                continue
            for pattern in passes:
                re.compile(_fill(pattern, {key: "x" for key in _PLACEHOLDER.findall(pattern)}))
            for pattern in paths:
                _glob(Path(project_root), pattern)
            scopes.append(Scope(name, paths, passes, run))
        return scopes
    except Exception:  # noqa: BLE001 - a broken file declares nothing
        return []


def _fill(pattern: str, values: dict, escape: bool = True) -> str:
    """Placeholders filled. In a regex the value is escaped; where nothing in the
    pattern follows it, it is bounded, so `billing` does not match the start of
    `billing-batch`. A placeholder with no value (a file matched by a pattern
    without one) stands for any single word."""
    if not escape:
        return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), pattern)

    def one(found: re.Match) -> str:
        if found.group(1) not in values:
            return r"[^\s/]+"
        after = pattern[found.end():]
        bounded = not after or after[0] in " )$" or after.startswith((r"\s", r"\b"))
        return re.escape(values[found.group(1)]) + (r"(?![\w-])" if bounded else "")

    return _PLACEHOLDER.sub(one, pattern)


def _real(root: Path, path: str) -> str:
    full = path if os.path.isabs(path) else os.path.join(str(root), path)
    return os.path.realpath(full).replace(os.sep, "/")


def _glob(root: Path, pattern: str) -> re.Pattern:
    full = pattern if os.path.isabs(pattern) else os.path.join(str(root), pattern)
    full = os.path.normpath(full).replace(os.sep, "/")
    literal = re.split(r"[*{]", full, maxsplit=1)[0]
    head = literal.rsplit("/", 1)[0] if "/" in literal else literal
    full = os.path.realpath(head).replace(os.sep, "/") + full[len(head):]
    out, index = [], 0
    while index < len(full):
        if full.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif full.startswith("**", index):
            out.append(".*")
            index += 2
        elif full[index] == "*":
            out.append("[^/]*")
            index += 1
        elif (found := _PLACEHOLDER.match(full, index)) is not None:
            name = found.group(1)
            out.append(f"(?P={name})" if f"(?P<{name}>" in "".join(out) else f"(?P<{name}>[^/]+)")
            index = found.end()
        else:
            out.append(re.escape(full[index]))
            index += 1
    return re.compile("".join(out))


def _names(scope: Scope) -> list[str]:
    return sorted({name for pattern in scope.paths for name in _PLACEHOLDER.findall(pattern)})


def _key(scope: Scope, values: dict) -> str:
    """`scope`, `scope:value` when the scope has one placeholder name, else
    `scope:name=value,…` -- a value always travels with its name."""
    if not values:
        return scope.name
    if len(_names(scope)) == 1:
        return f"{scope.name}:{next(iter(values.values()))}"
    return scope.name + ":" + ",".join(f"{name}={values[name]}" for name in sorted(values))


def instance(scopes: list[Scope], root: str | Path, path: str) -> str | None:
    """The instance key (`_key`) for the first scope whose paths match."""
    if not path:
        return None
    full = _real(Path(root), path)
    for scope in scopes:
        for pattern in scope.paths:
            found = _glob(Path(root), pattern).fullmatch(full)
            if found is not None:
                return _key(scope, found.groupdict())
    return None


def _values(scope: Scope, key: str) -> dict:
    rest = key.split(":", 1)[1] if ":" in key else ""
    if not rest:
        return {}
    names = _names(scope)
    if len(names) == 1:
        return {names[0]: rest}
    return dict(part.split("=", 1) for part in rest.split(",") if "=" in part)


def _commands(command: str) -> list[str]:
    """The command's top-level commands, split at unquoted `;` and newlines
    outside subshells, with comments and heredoc bodies removed. Quoted text stays
    as written, so a regex still sees it; a separator inside quotes is not one."""
    text = command.replace("\\\n", " ")
    commands, current, quote, index, pending, depth = [], [], "", 0, [], 0
    while index < len(text):
        char = text[index]
        if quote:
            current.append(char)
            if char == "\\" and quote == '"' and index + 1 < len(text):
                current.append(text[index + 1])
                index += 1
            elif char == quote:
                quote = ""
        elif char == "\\" and index + 1 < len(text):
            current += [char, text[index + 1]]
            index += 1
        elif char in "'\"":
            quote = char
            current.append(char)
        elif char == "#" and (not current or current[-1] in " \t;&|("):
            while index + 1 < len(text) and text[index + 1] != "\n":
                index += 1
        elif text.startswith("<<<", index):
            current.append("<<<")
            index += 2
        elif text.startswith("<<", index):
            found = re.match(r"<<-?\s*(['\"]?)([\w.-]+)\1", text[index:])
            if found is None:
                current.append("<<")
                index += 1
            else:
                pending.append(found.group(2))
                current.append("<<" + found.group(2))
                index += found.end() - 1
        elif char in ";\n":
            if depth:  # inside a subshell: its own separator, not the command's
                current.append(";")
            else:
                commands.append("".join(current))
                current = []
            if char == "\n":
                for tag in pending:  # the bodies start on the next line and end at their tag
                    end = re.compile(r"^[ \t]*" + re.escape(tag) + r"[ \t]*$", re.M).search(text, index + 1)
                    index = end.end() if end else len(text)
                pending = []
        elif char in "()":
            depth = depth + 1 if char == "(" else max(depth - 1, 0)
            current.append(char)
        else:
            current.append(char)
        index += 1
    commands.append("".join(current))
    return [c.strip() for c in commands if c.strip()]


def _split(command: str, separator: str) -> list[str]:
    """`command` split at unquoted `separator` (`&&` or `|`); `||` is never a pipe."""
    parts, current, quote, index, depth = [], [], "", 0, 0
    while index < len(command):
        char = command[index]
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char in "()":
            depth = depth + 1 if char == "(" else max(depth - 1, 0)
        elif depth == 0 and command.startswith(separator, index) and not (separator == "|" and (
                command.startswith("||", index) or command[index - 1:index] == "|")):
            parts.append("".join(current))
            current = []
            index += len(separator)
            if separator == "|" and command.startswith("&", index):  # `|&`
                index += 1
            continue
        current.append(char)
        index += 1
    parts.append("".join(current))
    return [part.strip() for part in parts]


def _masked(command: str) -> str:
    """Every `$(...)` and backtick substitution replaced by `$_`: a check inside
    one never decides the command's exit status (`echo $(false; make test)`
    exits 0), and its separators are not the command's (third review)."""
    out, quote, index = [], "", 0
    while index < len(command):
        char = command[index]
        if quote == "'":
            quote = "" if char == "'" else quote
        elif char == "\\" and index + 1 < len(command):
            out.append(command[index:index + 2])
            index += 2
            continue
        elif char == "'" and not quote:
            quote = "'"
        elif char == '"':
            quote = "" if quote == '"' else '"'
        elif command.startswith("$(", index) or char == "`":
            depth, index = 1, index + (2 if char == "$" else 1)
            while index < len(command) and depth:
                if char == "`":
                    depth = 0 if command[index] == "`" else 1
                elif command.startswith("$(", index):
                    depth += 1
                    index += 1
                elif command[index] == ")":
                    depth -= 1
                index += 1
            out.append("$_")
            continue
        out.append(char)
        index += 1
    return "".join(out)


_DASH_C = re.compile(r"(?:\w+=\S*\s+)*(?:bash|sh|zsh)\s+(?:-\w+\s+)*-\w*c\s+(['\"])(?P<inner>.*)\1\s*$", re.S)


def _unquoted(command: str) -> str:
    return re.sub(r"'[^']*'|\"(?:\\.|[^\"\\])*\"", "\"\"", command)


def _block_words(bare: str) -> tuple[int, bool]:
    """(opens - closes, whether any block word starts a command) for one
    top-level fragment, looking at every command start in it -- after `&&`,
    `||`, `|` and words such as `!`, `time`, `then` (seventh review)."""
    delta, touched = 0, False
    pieces = [p for part in _split(bare, "&&") for q in _split(part, "||") for p in _split(q, "|")]
    for piece in pieces:
        words = piece.split()
        while words and (words[0] in _BEFORE_WORDS or re.fullmatch(r"\w+=\S*|-p", words[0])):
            touched = touched or words[0] in {"then", "do", "else", "elif"}
            words = words[1:]
        if not words:
            continue
        if re.fullmatch(r"\w[\w.-]*\(\)", words[0]) or words[0] == "function":
            touched = True
            delta += words.count("{")
        elif words[0] in _OPEN_WORDS:
            delta, touched = delta + 1, True
        elif words[0] in _CLOSE_WORDS or words[0].startswith("}"):
            delta, touched = delta - 1, True
    return delta, touched


def _harmless(segment: str) -> bool:
    """A command after the check that changes no file and no outcome."""
    words = segment.split()
    if not words:
        return True
    return words[0] in _HARMLESS or (words[0] == "git" and len(words) > 1 and words[1] in _GIT_READS)


def _ends_shell(command: str) -> bool:
    """Whether a command may end the shell before what follows it: `exit`,
    `return` or `exec <program>` starting one of its `&&`/`||` commands (a
    pipeline stage runs in a subshell, so its `exit` ends only that)."""
    for part in _split(_unquoted(command), "&&"):
        for piece in _split(part, "||"):
            if "|" in piece:
                continue
            words = piece.split()
            while words and words[0] in _BEFORE_WORDS - {"exec"}:
                words = words[1:]
            if words and (words[0] in {"exit", "return"} or (words[0] == "exec" and len(words) > 1
                                                               and not words[1].startswith(("<", ">", "2>")))):
                return True
    return False


def _stage_hit(segment: str, regex: str, pipefail: bool, level: int = 0) -> int | None:
    """Where the check is among the segment's pipeline stages, when its status
    reaches the segment's: the last stage, or any under pipefail."""
    stages = _split(segment, "|")
    for n, stage in enumerate(stages):
        if (re.match(_LEAD + regex, stage)
                or ((inner := _DASH_C.match(stage)) and _runs(inner.group("inner"), regex, level + 1))
                or ((inner := _SUBSHELL.match(stage)) and _runs(inner.group("inner"), regex, level + 1))):
            return n if n == len(stages) - 1 or pipefail else None
    return None


def _safe(command: str) -> bool:
    bare = _unquoted(command)
    return "||" not in bare and not re.search(r"(?<![&>|<])&(?![&>])", bare)


def _runs(command: str, regex: str, level: int = 0) -> bool:
    """Whether the command exits 0 only if the check passed.

    In the last top-level command: no `||` or background `&`; after the check
    `&&` runs only harmless commands (`echo PASS`). Under an earlier `set -e`,
    also any top-level command whose last `&&` segment is the check: a failure
    there ends the shell (a failing `&&` head does not). A pipe after the check
    counts only under `set -o pipefail`."""
    if level > MAX_NESTING:
        return False
    commands = _commands(_masked(command))
    if len(commands) >= 2 and re.fullmatch(r'\s*exit\s+"?\$\?"?\s*', commands[-1]):
        commands = commands[:-1]  # `check; exit $?` exits with the check's own status
    errexit = pipefail = False
    block = 0
    for index, current in enumerate(commands):
        last = index == len(commands) - 1
        # Inside an if/loop/case/function/group -- or opening one -- a check may
        # never run, so it never counts (sixth review); a block's close ends it.
        delta, touched = _block_words(_unquoted(current))
        if touched or block:
            block = max(block + delta, 0)
            continue
        if _ends_shell(current) and not last:
            return False  # what follows never runs (differential fuzz, 20,000 commands)
        if _safe(current) and not _COMPOUND.match(current):
            segments = _split(current, "&&")
            local_pipefail = pipefail
            for position, segment in enumerate(segments):
                if _PIPEFAIL.match(segment):
                    local_pipefail = True
                if _stage_hit(segment, regex, local_pipefail, level) is None:
                    continue
                after = segments[position + 1:]
                if last and all(_harmless(s) for s in after):
                    return True
                if errexit and not after:
                    return True
        if _NO_ERREXIT.match(current):
            errexit = False
        elif _ERREXIT.match(current):
            errexit = True
        if _NO_PIPEFAIL.match(current):
            pipefail = False
        elif _PIPEFAIL.match(current):
            pipefail = True
    return False


def cleared(scopes: list[Scope], command: str, keys: list[str]) -> list[str]:
    """The instances among `keys` that this command (which exited 0) checks."""
    by_name = {scope.name: scope for scope in scopes}
    done = []
    for key in keys:
        scope = by_name.get(key.split(":", 1)[0])
        if scope is None:
            continue
        values = _values(scope, key)
        if any(_runs(command, _fill(p, values)) for p in scope.passes):
            done.append(key)
    return done


_COMMAND_START = r"(?:^|[;&|(]\s*|\n\s*)" + _LEAD


def attempted(scopes: list[Scope], command: str, keys: list[str]) -> list[str]:
    """The instances among `keys` whose check this command runs -- it starts a
    command somewhere in it -- without the exit status counting (`| tail`, `;`)."""
    by_name = {scope.name: scope for scope in scopes}
    masked, counted = _unquoted(_masked(command)), set(cleared(scopes, command, keys))
    found = []
    for key in keys:
        scope = by_name.get(key.split(":", 1)[0])
        if scope is None or key in counted:
            continue
        values = _values(scope, key)
        if any(re.search(_COMMAND_START + _fill(p, values), masked) for p in scope.passes):
            found.append(key)
    return found


def dirty(scopes: list[Scope], root: str | Path, events: list[tuple[str, str]]) -> list[tuple[str, int]]:
    """Instances whose last change comes after their last pass, with that change's index."""
    last_change: dict[str, int] = {}
    last_pass: dict[str, int] = {}
    for index, (kind, value) in enumerate(events):
        if kind == "changed":
            key = instance(scopes, root, value)
            if key is not None:
                last_change[key] = index
        elif kind == "passed":
            last_pass[value] = index
    return [(key, at) for key, at in last_change.items() if last_pass.get(key, -1) < at]


def signature(found: list[tuple[str, int]]) -> str:
    return "|".join(f"{key}@{at}" for key, at in sorted(found))


def run_hint(scopes: list[Scope], key: str) -> str:
    scope = next((s for s in scopes if s.name == key.split(":", 1)[0]), None)
    return _fill(scope.run, _values(scope, key), escape=False) if scope else ""


def reason(scopes: list[Scope], found: list[tuple[str, int]]) -> str:
    lines = ["EOS: changed after its last passing check, so not verified yet:"]
    lines += [f"- {key}: {run_hint(scopes, key)}" for key, _ in found[:MAX_SHOWN]]
    if len(found) > MAX_SHOWN:
        lines.append(f"- (+{len(found) - MAX_SHOWN} more)")
    lines.append("Run the check as the last command, with no `||` after it and no `| tail` (unless "
                 "`set -o pipefail` comes first) -- or say in your answer that this is not verified. Asked once.")
    return "\n".join(lines)[:MAX_REASON]
