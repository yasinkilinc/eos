"""Provenance: where a derived fact came from, and what nothing looked for.

Facts are deliberately kept out of graph.json. Measured on a real 255-file
service with a 2,397-file linked parent: graph.json is 25,466,819 bytes for
6,040 nodes and 72,139 edges, and MCP get_graph returns it whole. At ~154 bytes
of provenance per edge that is 10.6 MB raw and ~15 MB pretty-printed, for an
artifact whose cost already carries a warning. They travel in
.eos/data/brain/evidence.jsonl and are queried from SQLite instead.

The coverage half exists because "found nothing" and "never looked" print the
same otherwise. Measured on the same service: the generic Spring detectors for
@Repository, @KafkaListener and @FeignClient all return zero, because its
persistence, messaging and remote calls go through framework base classes and
generated clients rather than those annotations.
"""
import json
import shutil
import sqlite3
from pathlib import Path

from core import index
from core.generators.json.evidence import EvidenceGenerator
from core.generators.json.graph_index import GraphIndexGenerator
from core.knowledge.builder import KnowledgeBuilder
from core.knowledge.evidence import Coverage, Fact, detector, edge_subject
from core.lib.cache_store import CacheStore
from core.scanner import Scanner

FIXTURE = Path(__file__).parent / "fixtures" / "polyglot_project"

# The exact edge shape graph.json has always had. Pinned, not described: the
# whole point of the sidecar is that this set does not grow.
EDGE_KEYS = {"source", "target", "kind", "weight", "metadata"}
NODE_KEYS = {"id", "type", "label", "path", "language", "tags", "metadata", "doc"}


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "polyglot"
    shutil.copytree(FIXTURE, root)
    return root


def _scan(root: Path):
    project = Scanner(root, CacheStore(root / ".eos" / "data" / "cache")).scan(full=True)
    return project, KnowledgeBuilder().build(project, root=root)


def _write_artifacts(root: Path, project, graph) -> dict:
    brain = root / ".eos" / "data" / "brain"
    brain.mkdir(parents=True, exist_ok=True)
    GraphIndexGenerator(graph).generate(brain / "graph.json")
    counts = EvidenceGenerator(graph, project.report, "test").generate(brain / "evidence.jsonl")
    (root / ".eos" / "data" / "last_scan.json").write_text(json.dumps({
        "languages": project.detected_languages,
        "files_parsed": len(project.files),
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        **counts,
    }), encoding="utf-8")
    return counts


def _rows(db: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_graph_json_shape_is_unchanged_by_provenance(tmp_path):
    root = _copy(tmp_path)
    project, graph = _scan(root)
    _write_artifacts(root, project, graph)

    written = json.loads((root / ".eos" / "data" / "brain" / "graph.json").read_text(encoding="utf-8"))

    assert "facts" not in written, f"provenance leaked into graph.json: {sorted(written)}"
    assert set(written["edges"][0]) == EDGE_KEYS, (
        f"edge shape changed: {sorted(written['edges'][0])}"
    )
    assert set(written["nodes"][0]) == NODE_KEYS, (
        f"node shape changed: {sorted(written['nodes'][0])}"
    )


def test_every_import_edge_has_a_fact(tmp_path):
    root = _copy(tmp_path)
    project, graph = _scan(root)

    subjects = {fact.subject for fact in graph.facts if fact.predicate == "import-edge"}
    imports = [edge for edge in graph.edges if edge.kind == "import"]

    assert imports, "fixture produced no import edges; the test proves nothing"
    for edge in imports:
        wanted = edge_subject(edge.source_id, edge.target_id, "import",
                              edge.metadata.get("imported") or "")
        assert wanted in subjects, f"import edge without provenance: {wanted}"


def test_fact_records_detector_origin_and_source_ref(tmp_path):
    root = _copy(tmp_path)
    _, graph = _scan(root)

    fact = next(f for f in graph.facts if f.predicate == "import-edge")

    assert fact.origin == "extracted", fact
    assert fact.confidence == 1.0, fact
    assert fact.detector == detector("imports"), fact
    path, _, line = (fact.source_ref or "").rpartition(":")
    assert (root / path).is_file(), f"source_ref does not resolve to a file: {fact.source_ref}"
    assert line.isdigit(), f"source_ref carries no line: {fact.source_ref}"


def test_coverage_distinguishes_not_looked_for_from_found_nothing(tmp_path):
    """The distinction falls out of row presence, which is why it is a table."""
    root = _copy(tmp_path)
    project, graph = _scan(root)
    # A detector that was asked about every Java file and found nothing -- the
    # real shape of a generic @KafkaListener probe on a codebase that publishes
    # through a framework base class instead.
    for file in project.files:
        if file.language == "java":
            project.report.note_coverage(detector("java.kafka"), "kafka-topic", 0)
    _write_artifacts(root, project, graph)
    db = index.build(root).path

    rows = {(d, p): (e, w, h) for d, p, e, w, h
            in _rows(db, "SELECT detector, predicate, files_eligible, files_with_hits, hits FROM coverage")}

    looked_and_found_nothing = rows[(detector("java.kafka"), "kafka-topic")]
    assert looked_and_found_nothing[0] > 0 and looked_and_found_nothing[2] == 0, (
        f"a detector that examined files and found nothing must still report them: {looked_and_found_nothing}"
    )
    assert (detector("java.mongo"), "collection") not in rows, (
        "a detector that never ran must have no row at all, or 'found nothing' and "
        f"'not looked for' become the same answer: {sorted(rows)}"
    )


def test_observed_at_survives_a_cache_reuse_scan(tmp_path):
    """A file that did not change was last actually read on an earlier scan.

    Stamping its facts with the current scan time would be a confident wrong
    answer of exactly the kind provenance exists to end.
    """
    root = _copy(tmp_path)
    _, first = _scan(root)
    first_observed = {f.subject: f.observed_at for f in first.facts}

    cache = CacheStore(root / ".eos" / "data" / "cache")
    project = Scanner(root, cache).scan(full=False)   # incremental: nothing changed
    second = KnowledgeBuilder().build(project, root=root)

    assert project.files, "incremental scan produced no files"
    for fact in second.facts:
        assert fact.observed_at == first_observed[fact.subject], (
            f"{fact.subject} was restamped: {first_observed[fact.subject]} -> {fact.observed_at}"
        )


def test_a_half_written_sidecar_is_refused_not_indexed(tmp_path):
    """`eos scan` writes evidence.jsonl before last_scan.json; a scan that died
    in between leaves counts that disagree. Indexing it anyway would leave
    `eos why` citing provenance for edges of a previous graph."""
    root = _copy(tmp_path)
    project, graph = _scan(root)
    _write_artifacts(root, project, graph)
    sidecar = root / ".eos" / "data" / "brain" / "evidence.jsonl"
    lines = sidecar.read_text(encoding="utf-8").splitlines()
    sidecar.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")   # lose the last row

    result = index.build(root)

    assert _rows(result.path, "SELECT COUNT(*) FROM fact") == [(0,)], (
        "a truncated sidecar must index nothing rather than a subset"
    )
    problems = [problem for source, _, problem in result.issues if source == "evidence"]
    assert problems and "interrupted" in problems[0], f"the refusal was not reported: {result.issues}"


def test_facts_reach_sqlite_and_join_to_their_edge(tmp_path):
    root = _copy(tmp_path)
    project, graph = _scan(root)
    _write_artifacts(root, project, graph)

    db = index.build(root).path

    # The fact keys on node ids, never on node.nid: _load_brain assigns nid in
    # graph.json iteration order, so every rebuild renumbers every node and a
    # stored number would come to mean a different file.
    orphans = _rows(db, """
        SELECT COUNT(*) FROM edge e
        JOIN node s ON s.nid = e.src
        JOIN node d ON d.nid = e.dst
        LEFT JOIN fact f ON f.subject = s.id || '|' || d.id || '|' || e.kind || '|' || e.imported
        WHERE f.fid IS NULL""")
    assert orphans == [(0,)], f"{orphans[0][0]} indexed edge(s) have no provenance"


def test_scan_exclusions_are_queryable(tmp_path):
    root = _copy(tmp_path)
    project, graph = _scan(root)
    _write_artifacts(root, project, graph)

    db = index.build(root).path

    rules = {rule for kind, rule in _rows(db, "SELECT kind, rule FROM scan_exclusion") if kind == "ignore"}
    assert "build" in rules, f"the ignored build/ directory was not recorded: {rules}"
