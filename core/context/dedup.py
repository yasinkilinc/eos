"""Dedup against the harness's own always-loaded files (2.x roadmap C4).

Day 3b measured this gap against the report: ADR-027 suppresses EOS
repeating *itself* (a note or a file outline delivered twice in one
session), and a passive `.eos/data/loaded.jsonl` record of what the harness
reports loading has no reader. Neither compares EOS's own delivered content
against the *content* of files the harness puts in context on every turn --
`CLAUDE.md`, a project's `.claude/rules/*.md`, a preloaded skill's own
`SKILL.md`. That is this file's one job.

`[ai] loaded` in `.eos/config.toml` names those files as project-relative
globs. Unset -- the default -- `always_loaded_text` returns "" and
`already_loaded` never matches anything: byte-identical to today's
behaviour, the same guarantee every other C-item config knob makes. Set, a
note whose whole body is already contained, normalised, in the concatenated
text of every glob's matches is left out of the note/context injection
(`core/inject.py`'s PostToolBatch body tier, `notes.render_context_section`'s
"## Accumulated Knowledge"), and the caller says how many it left out --
never silently, and never a keyword-level heuristic that could hide a note
over a coincidental phrase: containment is checked on the whole normalised
body, not a fragment of it, so a short or generic body can never match by
accident (`_MIN_BODY_CHARS`).
"""
from __future__ import annotations

import re
from pathlib import Path

CONFIG_KEY = "loaded"
# Mirrors inject.py's own per-file ceiling (a packaged jar-sized file is not
# worth reading here either); an always-loaded file is normally small prose.
MAX_FILE_BYTES = 4 * 1024 * 1024
# A body shorter than this, normalised, could plausibly appear inside any
# prose by coincidence (a title, a one-line note) -- never counted as "the
# harness already has this", which would be a guess dressed as a fact.
MIN_BODY_CHARS = 40

_WHITESPACE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().casefold()


def always_loaded_text(project_root: str | Path) -> str:
    """Normalised text of every file `[ai] loaded` names, concatenated.

    "" when the key is unset, empty, not a list, or nothing resolves -- every
    one of those is "dedup is off", not an error, so a malformed table costs
    nothing beyond the dedup it would have done."""
    from core.lib.config_io import ConfigIO

    root = Path(project_root).expanduser().resolve()
    config_path = root / ".eos" / "config.toml"
    if not config_path.is_file():
        return ""
    try:
        patterns = ConfigIO.read_toml(config_path).get("ai", {}).get(CONFIG_KEY)
    except Exception:  # noqa: BLE001 - a malformed config leaves dedup off
        return ""
    if not isinstance(patterns, list) or not patterns:
        return ""
    chunks: list[str] = []
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern.strip():
            continue
        try:
            matches = sorted(root.glob(pattern))
        except (OSError, ValueError):
            continue
        for path in matches:
            try:
                if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                    continue
                chunks.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    return _normalize("\n".join(chunks))


def already_loaded(body: str, loaded_text: str) -> bool:
    """Whether `body`, normalised, sits whole inside `loaded_text`.

    `loaded_text` == "" (dedup off, or nothing resolved) never matches. A
    body shorter than `MIN_BODY_CHARS` never counts either -- long enough
    that containment is evidence, not coincidence."""
    if not loaded_text:
        return False
    normalized = _normalize(body)
    if len(normalized) < MIN_BODY_CHARS:
        return False
    return normalized in loaded_text
