"""A project's own labelled routing corpus, read by `eos consolidate` (L1, ADR-025 addendum).

`[model_routing] corpus` names the TSV files `eos route --eval` already reads.
Only their development rows are read here -- `split = dev`, or no split at all,
the way `route --eval` tunes a corpus without splits -- never a test row: the
test split is measured once, by hand, and never tuned against (L5).

Two things come out of them. The policy's accuracy on those rows, from the
same `evaluate.run` `route --eval` uses. And keyword proposals: a word of a
row the classifier mistypes that, added to its labelled type's
`[model_routing.keywords]`, moves at least `MIN_FIXED` development rows to
their labelled type and moves no correctly typed row away from its own. A
word some keyword table already holds is not a gap but a contest, and a word
most of whose rows carry another label is not that type's word; neither is
proposed. Words come from the corpus file, never from `routing.jsonl`, which
keeps no task text (ADR-019).

Proposals are printed, never written: a person accepts one into the config
(`eos consolidate --accept <id>` counts that it happened).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

MIN_FIXED = 2
TEST = "test"


def _options(label: str) -> set[str]:
    return {part.strip() for part in label.split("|") if part.strip()}


def dev_rows(project_root: str | Path, cfg) -> list[dict]:
    """Per configured corpus: its path, its development rows, or why it could not be read.

    A development row whose prompt another named corpus holds for test is left
    out too: tuning on it would be tuning on that corpus's test split."""
    from core.routing import evaluate
    from core.routing.types import normalise

    root = Path(project_root).expanduser().resolve()
    loaded = []
    for name in cfg.corpus:
        try:
            loaded.append((name, evaluate.load(root / name), None))
        except (OSError, ValueError) as exc:
            loaded.append((name, [], str(exc).replace(f"{root}/", "")))
    held_for_test = {normalise(row["prompt"]) for _, rows, _ in loaded for row in rows
                     if row.get("split", "") == TEST}
    return [{"path": name, "error": error,
             "rows": [row for row in rows if row.get("split", "") != TEST
                      and normalise(row["prompt"]) not in held_for_test]}
            for name, rows, error in loaded]


def proposal_id(task_type: str, word: str) -> str:
    """Stable across runs: the same word for the same type is the same proposal."""
    return "kw-" + hashlib.sha256(f"keyword\t{task_type}\t{word}".encode("utf-8")).hexdigest()[:8]


def keyword_proposals(cfg, rows: list[dict]) -> list[dict]:
    """Words that would move mistyped development rows to their labelled type, breaking none."""
    from core import notes
    from core.routing import classify
    from core.routing.types import normalise

    labelled = [(normalise(row["prompt"]), _options(row["type"])) for row in rows
                if row.get("type", "") not in ("", "*")]

    def typed(text: str, keywords: dict) -> str:
        return classify.classify(text, keywords, cfg.rules, no_signal_type=cfg.default_task_type).type

    right = [typed(text, cfg.keywords) in wanted for text, wanted in labelled]
    held = {token for table in classify.TABLES.values() for token, _ in table}
    held |= {token for words in cfg.keywords.values() for token in words}
    candidates = sorted({(task_type, word) for (text, wanted), ok in zip(labelled, right) if not ok
                         for word in notes._words(text) for task_type in wanted})
    proposals = []
    for task_type, word in candidates:
        if any(token.rstrip("*") in word and classify.matches(word, token) for token in held):
            continue
        hits = [i for i, (text, _) in enumerate(labelled) if word in text and classify.matches(text, word)]
        if sum(1 for i in hits if task_type in labelled[i][1]) * 2 < len(hits):
            continue
        if sum(1 for i in hits if not right[i] and task_type in labelled[i][1]) < MIN_FIXED:
            continue
        keywords = {**cfg.keywords, task_type: tuple(cfg.keywords.get(task_type, ())) + (word,)}
        fixed = broken = 0
        for i in hits:
            now = typed(labelled[i][0], keywords) in labelled[i][1]
            fixed += int(now and not right[i])
            broken += int(right[i] and not now)
        if fixed >= MIN_FIXED and not broken:
            proposals.append({"id": proposal_id(task_type, word), "kind": "keyword", "type": task_type,
                              "word": word, "fixes": fixed, "rows": len(hits)})
    proposals.sort(key=lambda p: (-p["fixes"], p["type"], p["word"]))
    return proposals


def describe(proposal: dict) -> str:
    return f'{proposal["type"]} += "{proposal["word"]}"'


def _pooled(entries: list[dict]) -> list[dict]:
    """Every corpus's development rows, a prompt two corpora share counted once."""
    from core.routing.types import normalise

    seen, pooled = set(), []
    for entry in entries:
        for row in entry["rows"]:
            key = normalise(row["prompt"])
            if key not in seen:
                seen.add(key)
                pooled.append(row)
    return pooled


def proposals(project_root: str | Path) -> list[dict]:
    """The keyword proposals alone -- what `consolidate --accept` checks an id against."""
    from core.routing import config

    cfg = config.load(project_root)
    return keyword_proposals(cfg, _pooled(dev_rows(project_root, cfg))) if cfg.corpus else []


def report(project_root: str | Path, records=None) -> dict | None:
    """None when no corpus is configured: `consolidate` then prints nothing of this."""
    from core.routing import config, evaluate, trace

    cfg = config.load(project_root)
    if not cfg.corpus:
        return None
    entries = dev_rows(project_root, cfg)
    corpora = [{"path": entry["path"], "prompts": len(entry["rows"]), "error": entry["error"],
                "accuracy": evaluate.run(project_root, entry["rows"])["accuracy"] if entry["rows"] else None}
               for entry in entries]
    groups = trace.advised_vs_used_outcome_stats(project_root, records=records, sources=False)
    return {"corpora": corpora,
            "advised_vs_used": {key: len(members) for key, members in groups.items()},
            "proposals": keyword_proposals(cfg, _pooled(entries))}
