"""How well the routing policy labels a corpus of tasks, without calling a model.

`eos route --eval <corpus.tsv>` runs every prompt of a labelled corpus through
the same `route()` every surface uses (`record=False`: nothing is written) and
reports, per column, how often the decision matched the label. It is the gate
the subagent hook's live mode waits for (claude plan 3.6): a policy is applied
to other agents' work only after it has been measured on work somebody labelled.

The corpus is a TSV with a header: `prompt`, then any of `type`, `level`,
`model`, `effort`. A cell may list alternatives (`MEDIUM|HIGH`), `*` accepts
anything, and a blank cell is not scored. Labels say what a careful person
would pick for the task, not what the policy currently does -- a corpus
written from the policy's own output measures nothing.

Misses come back with the words that might have fixed them: prompt words no
keyword table holds, counted over the prompts whose type was wrong. They are
suggestions for a person to accept into `[model_routing.keywords]`; nothing
here changes a table (ADR-018: the engine draws no conclusion on its own).
"""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

COLUMNS = ("type", "level", "model", "effort")
SUGGESTIONS = 8


def load(path: str | Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader((line for line in handle if not line.startswith("#")), delimiter="\t")
        if not reader.fieldnames or "prompt" not in reader.fieldnames:
            raise ValueError(f"{path}: the header must name a `prompt` column")
        for row in reader:
            prompt = (row.get("prompt") or "").strip()
            if prompt:
                rows.append({key: (value or "").strip() for key, value in row.items() if key})
    return rows


def _accepts(label: str, value: str) -> bool | None:
    if not label:
        return None
    if label == "*":
        return True
    return value in {part.strip() for part in label.split("|")}


def run(project_root: str | Path | None, rows: list[dict]) -> dict:
    import core.routing as routing
    from core.routing import classify, config, registry

    cfg = config.load(project_root)
    models = registry.load(project_root, cfg)
    known = {word for table in classify.TABLES.values() for token, _ in table for word in token.lower().split()}
    known |= {word for words in cfg.keywords.values() for token in words for word in token.split()}
    scored = {column: [0, 0] for column in COLUMNS}
    misses, unknown_words, invalid = [], Counter(), 0
    for row in rows:
        # fresh: each line is its own task, never an open run's decision
        decision = routing.route(project_root, row["prompt"], record=False, fresh=True)
        values = {"type": decision.task_type, "level": decision.level,
                  "model": decision.model, "effort": decision.effort or "none"}
        spec = models.get(decision.model)
        if spec is None or (decision.effort and decision.effort not in spec.efforts) \
                or (not decision.effort and spec.efforts):
            invalid += 1
        wrong = []
        for column in COLUMNS:
            verdict = _accepts(row.get(column, ""), values[column])
            if verdict is None:
                continue
            scored[column][1] += 1
            if verdict:
                scored[column][0] += 1
            else:
                wrong.append(f"{column} {values[column]} (want {row[column]})")
        if wrong:
            misses.append({"prompt": row["prompt"], "wrong": wrong})
            if any(item.startswith("type ") for item in wrong):
                from core import notes

                unknown_words.update(w for w in notes._words(row["prompt"]) if w not in known and len(w) > 3)
    return {
        "prompts": len(rows),
        "accuracy": {column: (round(right / total, 3) if total else None)
                     for column, (right, total) in scored.items()},
        "scored": {column: total for column, (_, total) in scored.items()},
        "invalid_effort": invalid,
        "misses": misses,
        "suggested_words": [word for word, _ in unknown_words.most_common(SUGGESTIONS)],
    }


def render(result: dict) -> str:
    lines = [f"Routing corpus: {result['prompts']} prompt(s), no model called"]
    for column in COLUMNS:
        accuracy, total = result["accuracy"][column], result["scored"][column]
        if total:
            lines.append(f"  {column:<7} {accuracy:.1%}  ({total} labelled)")
    lines.append(f"  effort a model would refuse: {result['invalid_effort']}")
    if result["misses"]:
        lines.append("")
        lines.append(f"Misses ({len(result['misses'])}):")
        for miss in result["misses"][:15]:
            lines.append(f"  - {miss['prompt'][:70]}: " + "; ".join(miss["wrong"]))
    if result["suggested_words"]:
        lines.append("")
        lines.append("Words in mistyped prompts no keyword table holds (for a person to judge): "
                     + ", ".join(result["suggested_words"]))
    return "\n".join(lines)
