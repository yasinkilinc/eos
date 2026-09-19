"""Measure EOS's own tools against the plain alternatives, on THIS project.

Ground truth comes from the language plugins: they already know which symbol
is defined in which file, and that claim is objective. Where no such truth
exists -- "which files does changing X affect?" -- grep is used as a
comparison baseline and the report says so, because a baseline is not a
correct answer.

Numbers in the generated report describe the project it was run against and
only that project. A symbol lookup that is cheap on a small Python codebase
may behave completely differently on a large Java one; nothing here is a
claim about EOS in general.
"""
from __future__ import annotations

import json
import re
import random
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core import index
from core import inspector
from core.generators.json.evidence import EvidenceGenerator
from core.generators.json.graph_index import GraphIndexGenerator
from core.generators.markdown.brain import BrainGenerator
from core.knowledge.builder import KnowledgeBuilder
from core.lib.cache_store import CacheStore
from core.plugins.registry import PluginRegistry
from core.scanner import Scanner

_SEED = 0
_TOKEN_DIVISOR = 4  # chars/4: a rough estimate, not a real tokenizer.


@dataclass
class Symbol:
    """A known (name, file) location, as parsed by a language plugin.

    This is the ground truth bench.py measures `find_symbol` against: the
    plugin already decided this symbol lives in this file, independent of
    whatever `.eos/` cache does or does not currently say.
    """
    name: str
    rel_path: str


@dataclass
class ProbeMetric:
    """One tool vs. one baseline, averaged over the sampled symbols/files."""
    n: int
    tool_recall: float | None       # None when there is no ground truth (comparative-only)
    baseline_recall: float | None
    tool_avg_chars: float
    tool_avg_seconds: float
    baseline_avg_chars: float
    baseline_avg_seconds: float
    errors: list[str] = field(default_factory=list)

    @property
    def tool_avg_tokens(self) -> float:
        return self.tool_avg_chars / _TOKEN_DIVISOR

    @property
    def baseline_avg_tokens(self) -> float:
        return self.baseline_avg_chars / _TOKEN_DIVISOR


@dataclass
class BenchReport:
    project: str
    root: str
    requested_samples: int
    used_samples: int
    symbol_universe: int
    generated_at: str
    degraded_reason: str | None = None
    scan_note: str | None = None
    find_symbol: ProbeMetric | None = None
    get_context: ProbeMetric | None = None
    dependents: ProbeMetric | None = None
    dependents_rule: str | None = None
    """How the dependents oracle was constructed, printed with its result.

    ADR-011 requires a measurement to name either real ground truth or a
    labelled baseline. This one has ground truth, and the rule that builds it
    is stated so a reader can re-derive it independently."""

    def to_markdown(self) -> str:
        lines = [
            f"# EOS Bench: {self.project}",
            "",
            f"Measured on **this project only** (`{self.root}`) at {self.generated_at}. "
            "These numbers describe this project and nothing else -- a different "
            "codebase, a different language, a different scale, would measure "
            "differently. Re-run `eos bench` on the project you actually care "
            "about rather than reusing this file for another one.",
            "",
        ]

        if self.degraded_reason:
            lines += [
                f"**No benchmark was run: {self.degraded_reason}**",
                "",
                "Neither the objective metric (find_symbol recall) nor the "
                "comparative metrics (get_context/Read sizing) below could be "
                "computed for this project.",
            ]
            return "\n".join(lines) + "\n"

        lines += [
            f"Sampled {self.used_samples} of {self.symbol_universe} known symbol(s), "
            f"chosen deterministically (seed {_SEED}): re-running `eos bench` on an "
            "unchanged project samples the same symbols again. Timings still vary "
            "run to run -- only the sample and the recall outcomes are reproducible.",
            "",
        ]
        if self.scan_note:
            lines += [self.scan_note, ""]
        lines += [
            "Token counts below are an ESTIMATE (characters / 4), not an exact "
            "tokenizer count.",
            "",
            "## Objective: find_symbol recall against known symbol locations",
            "",
            "Ground truth here comes straight from EOS's language-plugin parser, "
            "which already knows the true file for every sampled symbol. Recall is "
            "therefore a real correctness score, not agreement with a guess.",
            "",
        ]
        lines += _metric_table(self.find_symbol, "find_symbol", "grep -rn")
        lines += [
            "",
            "## Comparative: get_context vs. reading the file directly",
            "",
            "There is no ground truth for \"how much context is enough\" -- Read "
            "here is a comparison BASELINE, not a correct answer. The numbers "
            "below say only how the two compare on this project, never which one "
            "is right.",
            "",
        ]
        lines += _metric_table(self.get_context, "get_context", "Read")

        errors = (self.find_symbol.errors if self.find_symbol else []) + (
            self.get_context.errors if self.get_context else []
        )
        if errors:
            lines += ["", "## Errors encountered while probing", ""]
            lines += [f"- {e}" for e in errors]

        return "\n".join(lines) + "\n"

    def to_text(self) -> str:
        if self.degraded_reason:
            return f"eos bench: {self.project} -- no benchmark run: {self.degraded_reason}"

        fs = self.find_symbol
        gc = self.get_context
        lines = [
            f"eos bench: {self.project} (this project only, {self.used_samples}/"
            f"{self.symbol_universe} symbols, seed {_SEED})",
            "",
        ]
        if self.scan_note:
            lines += [self.scan_note, ""]
        lines += [
            "Objective -- find_symbol recall vs grep -rn (against known symbol locations):",
            f"  find_symbol: {fs.tool_recall:.0%} recall, "
            f"~{fs.tool_avg_tokens:.0f} tok (est.), {fs.tool_avg_seconds * 1000:.1f} ms avg",
            f"  grep -rn:    {fs.baseline_recall:.0%} recall, "
            f"~{fs.baseline_avg_tokens:.0f} tok (est.), {fs.baseline_avg_seconds * 1000:.1f} ms avg",
            "",
            "Comparative -- get_context vs Read (no ground truth, size/time only):",
            f"  get_context: ~{gc.tool_avg_tokens:.0f} tok (est.), {gc.tool_avg_seconds * 1000:.1f} ms avg",
            f"  Read:        ~{gc.baseline_avg_tokens:.0f} tok (est.), {gc.baseline_avg_seconds * 1000:.1f} ms avg",
        ]
        dep = self.dependents
        if dep is None:
            lines += ["", "Objective -- dependent recall: not run (no Java test/subject pairs "
                          "in this project). Absent, not zero."]
        else:
            lines += [
                "",
                f"Objective -- dependent recall over {dep.n} known pair(s):",
                f"  impact_analysis: {dep.tool_recall:.0%}",
                f"  oracle: {self.dependents_rule}",
            ]
        return "\n".join(lines)


def _metric_table(metric: ProbeMetric, tool_name: str, baseline_name: str) -> list[str]:
    rows = [f"| | {tool_name} | {baseline_name} |", "|---|---|---|"]
    if metric.tool_recall is not None:
        rows.append(
            f"| Recall (n={metric.n}) | {metric.tool_recall:.0%} | {metric.baseline_recall:.0%} |"
        )
    rows.append(
        f"| Avg output (n={metric.n}) | ~{metric.tool_avg_tokens:.0f} tokens (est.) | "
        f"~{metric.baseline_avg_tokens:.0f} tokens (est.) |"
    )
    rows.append(
        f"| Avg time | {metric.tool_avg_seconds * 1000:.1f} ms | "
        f"{metric.baseline_avg_seconds * 1000:.1f} ms |"
    )
    return rows


def _project_scanner(root: Path) -> Scanner:
    """A `Scanner` bound to `root`, so ground truth and the real scan
    pipeline agree on which files exist.

    Reusing `Scanner` here (instead of a second, narrower ignore set) is
    deliberate: a project can exclude paths via `.eos/config.toml`
    (`[scan] ignore`/`unignore`) or `.gitignore`, and `Scanner._load_config`
    already knows how to apply both. A bench-specific reimplementation of
    "which files count" would drift from the real one the moment either
    gains a new rule, and would silently sample ground-truth symbols from
    files `find_symbol` structurally cannot see -- deflating its recall for
    a reason that has nothing to do with the tool.
    """
    return Scanner(root, CacheStore(root / ".eos" / "data" / "cache"))


def _detected_symbols(root: Path, scanner: Scanner | None = None) -> list[Symbol]:
    """Parse every source file EOS's plugins recognize -- using the same
    file-inclusion decision `Scanner` uses for a real scan -- and return
    every symbol they find. This is the ground-truth oracle: it never reads
    parsed data back out of `.eos/` cache, so a stale or absent cache cannot
    make the oracle agree with the tool under test by accident.

    The walk is sorted so the resulting list -- and therefore any sample
    drawn from it -- does not depend on filesystem or OS iteration order.
    """
    detected = PluginRegistry.detect_languages(root)
    if not detected:
        return []

    scanner = scanner or _project_scanner(root)
    symbols: list[Symbol] = []
    for rel_path in sorted(scanner._walk(root)):
        plugin = PluginRegistry.plugin_for_file(rel_path, detected)
        if plugin is None:
            continue
        try:
            text = (root / rel_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        file_sem = plugin.parse_file(rel_path, text)
        symbols.extend(Symbol(name=sym.name, rel_path=rel_path) for sym in file_sem.symbols)
    return symbols


def _sample(universe: list[Symbol], n: int) -> list[Symbol]:
    n = max(n, 0)
    if len(universe) <= n:
        return list(universe)
    return random.Random(_SEED).sample(universe, n)


def sample_symbols(root: Path, n: int) -> list[Symbol]:
    """Deterministically sample up to `n` known symbols from `root`.

    Same project, same `n` -> same sample, every time: the sampling seed is
    fixed, and the population it samples from is produced in a stable order.
    """
    return _sample(_detected_symbols(Path(root)), n)


def _ensure_scanned(root: Path, scanner: Scanner) -> str:
    """Bring `.eos/data/cache` and `.eos/data/brain` up to date so
    find_symbol and get_context are measured against real, current output
    instead of an absent or stale index. Returns a one-line disclosure of
    what was written, for the report and the terminal -- this is a
    deliberate side effect (`eos bench` writes into `.eos/` the same way
    `eos scan` does, even on a project that was never `eos init`-ed) and it
    must not be a silent one.

    This mirrors `eos scan`'s incremental behaviour -- unchanged files are
    not reparsed, so a repeat bench run on an unchanged project is fast.
    """
    project = scanner.scan(full=False)
    if project.detected_languages:
        graph = KnowledgeBuilder().build(project, root=root)
        brain = root / ".eos" / "data" / "brain"
        BrainGenerator(graph).generate(brain)
        # The graph and the index too, not just the brain. impact_analysis is
        # answered from the index, so a probe of it measured whatever an older
        # scan happened to leave behind -- a dependent-recall probe read 0% on
        # a project where the same pairs resolve at 100% when freshly built.
        GraphIndexGenerator(graph).generate(brain / "graph.json")
        counts = EvidenceGenerator(graph, project.report, _engine_version()).generate(
            brain / "evidence.jsonl")
        (root / ".eos" / "data" / "last_scan.json").write_text(json.dumps({
            "languages": project.detected_languages,
            "files_parsed": len(project.files),
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            **counts,
        }, indent=2), encoding="utf-8")
        try:
            index.build(root)
        except Exception as exc:  # noqa: BLE001 - disclosed below, not fatal
            return (f"Scanned this project before probing, but the index could not be "
                    f"rebuilt ({type(exc).__name__}: {exc}); impact-based probes below "
                    "may be measured against stale data.")
    return (
        f"Scanned this project before probing: wrote/updated `.eos/data/cache`, "
        f"`.eos/data/brain` and `.eos/data/eos.db` under `{root}` (the same "
        "artifacts `eos scan` produces)."
    )


def _engine_version() -> str:
    version = Path(__file__).resolve().parent / "VERSION"
    try:
        return version.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _grep_hit(output: str, root: Path, rel_path: str) -> bool:
    target = (root / rel_path).resolve()
    for line in output.splitlines():
        path_part = line.split(":", 1)[0]
        try:
            if Path(path_part).resolve() == target:
                return True
        except OSError:
            continue
    return False


def _bench_find_symbol(root: Path, sampled: list[Symbol]) -> ProbeMetric:
    """`find_symbol` vs `grep -rn`, both queried with the same plain
    identifier (a symbol's last dotted segment, e.g. "submit" for
    "OrderService.submit") -- the term an agent that does not already know
    the qualifying class would actually type. Recall is objective: it is
    checked against the plugin-parsed ground truth, not against the other
    tool.
    """
    tool_hits = 0
    baseline_hits = 0
    tool_chars: list[float] = []
    tool_seconds: list[float] = []
    baseline_chars: list[float] = []
    baseline_seconds: list[float] = []
    errors: list[str] = []

    for symbol in sampled:
        query = symbol.name.rsplit(".", 1)[-1]

        start = time.perf_counter()
        try:
            matches = inspector.find_symbols(root, query, max_results=100)
            text = json.dumps(matches, ensure_ascii=False)
            hit = any(m.get("path") == symbol.rel_path for m in matches)
        except Exception as exc:
            text, hit = "", False
            errors.append(f"find_symbol({query!r}): {type(exc).__name__}: {exc}")
        tool_seconds.append(time.perf_counter() - start)
        tool_chars.append(len(text))
        tool_hits += hit

        start = time.perf_counter()
        try:
            result = subprocess.run(
                [
                    "grep", "-rn", "-F",
                    "--exclude-dir=.eos", "--exclude-dir=.git",
                    "--", query, str(root),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = result.stdout
            hit_b = _grep_hit(output, root, symbol.rel_path)
        except (OSError, subprocess.SubprocessError) as exc:
            output, hit_b = "", False
            errors.append(f"grep({query!r}): {type(exc).__name__}: {exc}")
        baseline_seconds.append(time.perf_counter() - start)
        baseline_chars.append(len(output))
        baseline_hits += hit_b

    n = len(sampled)
    return ProbeMetric(
        n=n,
        tool_recall=tool_hits / n,
        baseline_recall=baseline_hits / n,
        tool_avg_chars=_mean(tool_chars),
        tool_avg_seconds=_mean(tool_seconds),
        baseline_avg_chars=_mean(baseline_chars),
        baseline_avg_seconds=_mean(baseline_seconds),
        errors=errors,
    )


_DEPENDENTS_RULE = (
    "a src/test/java/<pkg>/<Name>Test.java and a src/main/java/<pkg>/<Name>.java "
    "in the same package, where the test's source with comments and string "
    "literals removed names <Name> as a standalone token"
)


def _java_dependent_pairs(root: Path) -> list[tuple[str, str]]:
    """(test path, subject path) for every same-package test/subject pair.

    Ground truth, and derived by a different rule than the one under test: the
    edge builder works from declared field types, imports and the package
    table, while this reads the token out of the masked source. Java requires
    no import for a same-package reference, so an import-only graph cannot see
    any of these -- measured at 0 of 142 on one service before declaration-site
    edges existed.

    Masking is what keeps the oracle honest: without it a subject named only
    inside a comment or a string would count as a reference.
    """
    from core.plugins.java.lexer import lex

    main_root, test_root = root / "src" / "main" / "java", root / "src" / "test" / "java"
    if not (main_root.is_dir() and test_root.is_dir()):
        return []
    subjects: dict[tuple[str, str], str] = {}
    for path in main_root.rglob("*.java"):
        rel = path.relative_to(main_root)
        subjects[(str(rel.parent), path.stem)] = f"src/main/java/{rel.as_posix()}"

    pairs = []
    for path in test_root.rglob("*Test.java"):
        rel = path.relative_to(test_root)
        subject = subjects.get((str(rel.parent), path.stem[: -len("Test")]))
        if subject is None:
            continue
        try:
            masked = lex(path.read_text(encoding="utf-8", errors="replace")).masked
        except OSError:
            continue
        if re.search(rf"\b{re.escape(path.stem[: -len('Test')])}\b", masked):
            pairs.append((f"src/test/java/{rel.as_posix()}", subject))
    return pairs


def _bench_dependents(root: Path) -> ProbeMetric | None:
    """Does impact_analysis report the test that exercises a class?

    None -- not zero -- when the project has no such pairs. A 0% line on a
    project with no Java would be a false claim about EOS, which is the same
    principle the coverage table applies to detectors.
    """
    pairs = _java_dependent_pairs(root)
    if not pairs:
        return None
    hits, chars, seconds, errors = 0, 0, 0.0, []
    for test_path, subject in pairs:
        started = time.perf_counter()
        try:
            answer = inspector.impact(root, subject)
        except Exception as exc:  # noqa: BLE001 - a failure is a miss, not a crash
            errors.append(f"{subject}: {type(exc).__name__}: {exc}")
            continue
        finally:
            seconds += time.perf_counter() - started
        if any(entry.get("path") == test_path for entry in answer.get("dependents", [])):
            hits += 1
        chars += len(json.dumps(answer))
    return ProbeMetric(
        n=len(pairs), tool_recall=hits / len(pairs), baseline_recall=None,
        tool_avg_chars=chars / len(pairs), tool_avg_seconds=seconds / len(pairs),
        baseline_avg_chars=0.0, baseline_avg_seconds=0.0, errors=errors,
    )


def _bench_get_context(root: Path, sampled: list[Symbol]) -> ProbeMetric:
    """`get_context` (focused on each sampled file) vs. reading that file
    directly. There is no ground truth for how much context is "enough", so
    this is size/time only -- comparative, not a correctness score.
    """
    targets = sorted({s.rel_path for s in sampled})
    tool_chars: list[float] = []
    tool_seconds: list[float] = []
    baseline_chars: list[float] = []
    baseline_seconds: list[float] = []
    errors: list[str] = []

    for rel_path in targets:
        start = time.perf_counter()
        try:
            context = inspector.build_context(root, budget=12000, target=rel_path)
        except Exception as exc:
            context = ""
            errors.append(f"get_context({rel_path!r}): {type(exc).__name__}: {exc}")
        tool_seconds.append(time.perf_counter() - start)
        tool_chars.append(len(context))

        start = time.perf_counter()
        try:
            raw = (root / rel_path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raw = ""
            errors.append(f"Read({rel_path!r}): {type(exc).__name__}: {exc}")
        baseline_seconds.append(time.perf_counter() - start)
        baseline_chars.append(len(raw))

    return ProbeMetric(
        n=len(targets),
        tool_recall=None,
        baseline_recall=None,
        tool_avg_chars=_mean(tool_chars),
        tool_avg_seconds=_mean(tool_seconds),
        baseline_avg_chars=_mean(baseline_chars),
        baseline_avg_seconds=_mean(baseline_seconds),
        errors=errors,
    )


def run(root: Path, samples: int) -> BenchReport:
    root = Path(root).resolve()
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    scanner = _project_scanner(root)
    universe = _detected_symbols(root, scanner)

    if not universe:
        return BenchReport(
            project=root.name,
            root=str(root),
            requested_samples=samples,
            used_samples=0,
            symbol_universe=0,
            generated_at=generated_at,
            degraded_reason=(
                "no symbols were found in this project (no source files in a "
                "language EOS's plugins parse -- Python, JavaScript, TypeScript, "
                "Java -- or the project has no such files yet). There is nothing "
                "to sample."
            ),
        )

    sampled = sorted(_sample(universe, samples), key=lambda s: (s.rel_path, s.name))

    if not sampled:
        return BenchReport(
            project=root.name,
            root=str(root),
            requested_samples=samples,
            used_samples=0,
            symbol_universe=len(universe),
            generated_at=generated_at,
            degraded_reason=(
                f"{samples} sample(s) requested, so nothing was drawn from the "
                f"{len(universe)} known symbol(s) in this project. Pass "
                "--samples 1 or higher."
            ),
        )

    try:
        scan_note = _ensure_scanned(root, scanner)
    except Exception as exc:
        # Scanning is groundwork, not the thing under test: if it fails, the
        # individual probes below still run and report their own failure
        # (e.g. get_context needs the brain _ensure_scanned would have
        # written) rather than the whole report crashing. The failure is
        # still disclosed, not swallowed.
        scan_note = (
            f"Could not scan this project before probing ({type(exc).__name__}: "
            f"{exc}); find_symbol/get_context below may be measured against "
            "missing or stale data."
        )

    return BenchReport(
        project=root.name,
        root=str(root),
        requested_samples=samples,
        used_samples=len(sampled),
        symbol_universe=len(universe),
        generated_at=generated_at,
        scan_note=scan_note,
        find_symbol=_bench_find_symbol(root, sampled),
        get_context=_bench_get_context(root, sampled),
        dependents=_bench_dependents(root),
        dependents_rule=_DEPENDENTS_RULE,
    )
