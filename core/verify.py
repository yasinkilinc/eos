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
_SEGMENTS = re.compile(r"&&|\|\||;|\n")
_LEAD = r"\s*(?:\w+=\S*\s+)*(?:(?:bash|sh|env)\s+)?"


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
            scopes.append(Scope(name, paths, passes, run))
        return scopes
    except Exception:  # noqa: BLE001 - a broken file declares nothing
        return []


def _fill(pattern: str, values: dict, escape: bool = True) -> str:
    return _PLACEHOLDER.sub(lambda m: re.escape(values.get(m.group(1), "")) if escape
                            else values.get(m.group(1), m.group(0)), pattern)


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
            out.append(f"(?P<{found.group(1)}>[^/]+)")
            index = found.end()
        else:
            out.append(re.escape(full[index]))
            index += 1
    return re.compile("".join(out))


def _names(scope: Scope) -> list[str]:
    return sorted({name for pattern in scope.paths for name in _PLACEHOLDER.findall(pattern)})


def instance(scopes: list[Scope], root: str | Path, path: str) -> str | None:
    """`<scope>` or `<scope>:<value>…` for the first scope whose paths match."""
    if not path:
        return None
    full = _real(Path(root), path)
    for scope in scopes:
        for pattern in scope.paths:
            found = _glob(Path(root), pattern).fullmatch(full)
            if found is not None:
                values = found.groupdict()
                return scope.name + "".join(f":{values[name]}" for name in sorted(values))
    return None


def _values(scope: Scope, key: str) -> dict:
    return dict(zip(_names(scope), key.split(":")[1:]))


def _runs(command: str, regex: str) -> bool:
    for segment in _SEGMENTS.split(command):
        head, piped = segment.split("|", 1)[0], "|" in segment
        if re.match(_LEAD + regex, head) and (not piped or "pipefail" in command):
            return True
    return False


def cleared(scopes: list[Scope], command: str, keys: list[str]) -> list[str]:
    """The instances among `keys` that this command (which exited 0) checks."""
    by_name = {scope.name: scope for scope in scopes}
    done = []
    for key in keys:
        scope = by_name.get(key.split(":", 1)[0])
        if scope is not None and any(_runs(command, _fill(p, _values(scope, key))) for p in scope.passes):
            done.append(key)
    return done


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
    lines.append("Run the check, or say in your answer that this is not verified. Asked once.")
    return "\n".join(lines)[:MAX_REASON]
