"""How well the routing policy labels a corpus of tasks, without calling a model.

`eos route --eval <corpus.tsv> [--split dev|test]` runs every prompt through the
same `route()` every surface uses (`record=False`, `fresh=True`: nothing is
written, no open run's decision is reused) and reports how often the decision
matched the label, plus the gate the subagent hook's live mode waits for.

The corpus is a TSV with a header: `prompt`, then any of `type`, `level`,
`model`, `effort`, `split`. A cell may list alternatives (`MEDIUM|HIGH`), `*`
accepts anything, and a blank cell is not scored. Labels say what a careful
person would pick, not what the policy answers -- a corpus written from the
policy's own output measures nothing.

**Development and test stay apart, by construction.** `split` is `dev` or
`test`; the same prompt in both is refused. Configuration is tuned on `dev`
only: misses and the keyword suggestions are printed for `dev` (and for a
corpus without splits), never for `test`, which reports metrics and the gate
alone. Every `test` measurement is appended to `.eos/data/routing-eval.jsonl`
with a hash of the corpus and of the routing configuration, and the report
says how many earlier test measurements used a different configuration -- a
test set measured again after tuning is visible, not silent.

**The gate** (claude subagent-routing plan §1; fixed here so a run cannot
loosen it): model accuracy >= 0.85; under-routing <= 0.03, a task labelled
HIGH or CRITICAL sent to a model cheaper than every model its label accepts;
0 CRITICAL tasks on the cheapest registered model; 0 efforts a model would
refuse. Suggestions are for a person to accept into `[model_routing.*]`;
nothing here changes a table (ADR-018).
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

COLUMNS = ("type", "level", "model", "effort")
SPLITS = ("dev", "test")
SUGGESTIONS = 8
GATE = {"model_accuracy_min": 0.85, "under_routing_max": 0.03,
        "critical_to_cheapest_max": 0, "invalid_effort_max": 0}
HIGH_LEVELS = {"HIGH", "CRITICAL"}
RECORD = "routing-eval.jsonl"


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
    _refuse_leaks(path, rows)
    return rows


def _refuse_leaks(path, rows: list[dict]) -> None:
    from core.routing.types import normalise

    seen: dict[str, str] = {}
    for row in rows:
        split = row.get("split", "")
        if split and split not in SPLITS:
            raise ValueError(f"{path}: split must be one of {', '.join(SPLITS)}; got {split!r}")
        key = normalise(row["prompt"])
        if split and seen.get(key, split) != split:
            raise ValueError(f"{path}: the same prompt is in both dev and test: {row['prompt'][:60]!r}")
        if split:
            seen[key] = split


def _options(label: str) -> set[str]:
    return {part.strip() for part in label.split("|") if part.strip()}


def _accepts(label: str, value: str) -> bool | None:
    if not label:
        return None
    if label == "*":
        return True
    return value in _options(label)


def run(project_root: str | Path | None, rows: list[dict], split: str | None = None) -> dict:
    import core.routing as routing
    from core.routing import classify, config, registry

    cfg = config.load(project_root)
    models = registry.load(project_root, cfg)
    cheapest = models.available()[0].id if models.available() else None
    chosen = [row for row in rows if not split or row.get("split") == split]
    known = {word for table in classify.TABLES.values() for token, _ in table for word in token.lower().split()}
    known |= {word for words in cfg.keywords.values() for token in words for word in token.split()}
    scored = {column: [0, 0] for column in COLUMNS}
    misses, unknown_words, invalid = [], Counter(), 0
    confusion: dict[str, Counter] = {}
    high_rows, under, critical_cheap = 0, 0, 0
    for row in chosen:
        decision = routing.route(project_root, row["prompt"], record=False, fresh=True)
        values = {"type": decision.task_type, "level": decision.level,
                  "model": decision.model, "effort": decision.effort or "none"}
        spec = models.get(decision.model)
        if spec is None or (decision.effort and decision.effort not in spec.efforts) \
                or (not decision.effort and spec.efforts):
            invalid += 1
        label_model, label_level = row.get("model", ""), row.get("level", "")
        if label_model:
            confusion.setdefault(label_model, Counter())[decision.model] += 1
        levels = _options(label_level) if label_level not in ("", "*") else set()
        if levels and levels <= HIGH_LEVELS and label_model not in ("", "*"):
            high_rows += 1
            costs = [models.get(m).cost for m in _options(label_model) if models.get(m)]
            if spec is not None and costs and spec.cost < min(costs):
                under += 1
        if decision.model == cheapest and ("CRITICAL" in levels or decision.level == "CRITICAL"):
            critical_cheap += 1
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
    accuracy = {column: (round(right / total, 3) if total else None) for column, (right, total) in scored.items()}
    under_rate = round(under / high_rows, 3) if high_rows else 0.0
    checks = {
        "model_accuracy": accuracy["model"] is not None and accuracy["model"] >= GATE["model_accuracy_min"],
        "under_routing": under_rate <= GATE["under_routing_max"],
        "critical_to_cheapest": critical_cheap <= GATE["critical_to_cheapest_max"],
        "invalid_effort": invalid <= GATE["invalid_effort_max"],
    }
    blind = split == "test"
    result = {
        "prompts": len(chosen), "split": split or "all",
        "accuracy": accuracy,
        "scored": {column: total for column, (_, total) in scored.items()},
        "confusion": {label: dict(counts) for label, counts in sorted(confusion.items())},
        "under_routing": {"rate": under_rate, "count": under, "high_or_critical": high_rows},
        "critical_to_cheapest": {"count": critical_cheap, "cheapest": cheapest},
        "invalid_effort": invalid,
        "gate": {"pass": all(checks.values()), "checks": checks, "thresholds": dict(GATE)},
        # Test is measured, never tuned against: no rows, no suggestions.
        "misses": [] if blind else misses,
        "suggested_words": [] if blind else [word for word, _ in unknown_words.most_common(SUGGESTIONS)],
    }
    return result


def fingerprint(project_root, corpus: str | Path) -> dict:
    """Hashes that identify one measurement: the corpus bytes and the routing config."""
    from core.routing import config

    cfg = config.load(project_root)
    config_text = json.dumps({"keywords": cfg.keywords, "rules": cfg.rules, "factors": cfg.factors,
                              "models": cfg.models}, sort_keys=True, ensure_ascii=False, default=list)
    return {"corpus": hashlib.sha256(Path(corpus).read_bytes()).hexdigest()[:16],
            "config": hashlib.sha256(config_text.encode("utf-8")).hexdigest()[:16]}


def record_test(project_root, corpus: str | Path, result: dict) -> int:
    """Append a test measurement; return how many earlier test measurements of
    this corpus used a different configuration."""
    from core.knowledge.evidence import utc_now

    prints = fingerprint(project_root, corpus)
    path = Path(project_root) / ".eos" / "data" / RECORD
    earlier = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                earlier.append(json.loads(line))
            except ValueError:
                continue
    different = sum(1 for e in earlier if e.get("corpus") == prints["corpus"] and e.get("config") != prints["config"])
    entry = {"at": utc_now(), **prints, "accuracy": result["accuracy"], "under_routing": result["under_routing"],
             "critical_to_cheapest": result["critical_to_cheapest"]["count"],
             "invalid_effort": result["invalid_effort"], "gate": result["gate"]["pass"]}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return different


def render(result: dict) -> str:
    lines = [f"Routing corpus ({result['split']}): {result['prompts']} prompt(s), no model called"]
    for column in COLUMNS:
        accuracy, total = result["accuracy"][column], result["scored"][column]
        if total:
            lines.append(f"  {column:<7} {accuracy:.1%}  ({total} labelled)")
    ur = result["under_routing"]
    lines.append(f"  under-routing (HIGH/CRITICAL to a cheaper model): {ur['rate']:.1%} "
                 f"({ur['count']} of {ur['high_or_critical']})")
    cc = result["critical_to_cheapest"]
    lines.append(f"  CRITICAL -> cheapest model ({cc['cheapest']}): {cc['count']}")
    lines.append(f"  effort a model would refuse: {result['invalid_effort']}")
    if result["confusion"]:
        lines.append("")
        lines.append("Confusion (label -> model chosen):")
        for label, counts in result["confusion"].items():
            lines.append(f"  {label:<14} " + "  ".join(f"{model} {n}" for model, n in sorted(counts.items())))
    if result["misses"]:
        lines.append("")
        lines.append(f"Misses ({len(result['misses'])}):")
        for miss in result["misses"][:15]:
            lines.append(f"  - {miss['prompt'][:70]}: " + "; ".join(miss["wrong"]))
    if result["suggested_words"]:
        lines.append("")
        lines.append("Words in mistyped prompts no keyword table holds (for a person to judge): "
                     + ", ".join(result["suggested_words"]))
    gate = result["gate"]
    failed = [name for name, ok in gate["checks"].items() if not ok]
    lines.append("")
    lines.append(f"GATE: {'PASS' if gate['pass'] else 'FAIL'}" + (f"  (failed: {', '.join(failed)})" if failed else ""))
    return "\n".join(lines)
