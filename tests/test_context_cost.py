"""`eos cost --context` (2.x roadmap C7): what a project's sessions keep re-reading, by source.

Each item that enters a main session is charged tokens x the model calls it stays
for (until a compaction or the end); the total plus the first call's context is
checked against the cache reads the transcript recorded.
"""
import json

from core import context_cost


def _session(path, events):
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def _assistant(mid, ctx, content=()):
    return {"type": "assistant", "timestamp": "2026-09-20T10:00:00Z",
            "message": {"id": mid, "usage": {"input_tokens": 0, "cache_creation_input_tokens": 0,
                                             "cache_read_input_tokens": ctx}, "content": list(content)}}


def test_resident_cost_is_charged_by_source_until_a_compaction(tmp_path):
    folder = tmp_path / "transcripts"
    folder.mkdir()
    big = "x" * 2220                                   # 1,000 tokens at 2.22 chars/token
    _session(folder / "s1.jsonl", [
        _assistant("m1", 1000, [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a"}}]),
        {"type": "user", "timestamp": "2026-09-20T10:00:01Z",
         "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": big}]}},
        _assistant("m2", 2000), _assistant("m3", 2000),
        {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-09-20T10:00:02Z"},
        _assistant("m4", 1000),
    ])
    report = context_cost.report(tmp_path, transcripts=folder, since="2026-09-01")
    assert report["sessions"] == 1 and report["calls"] == 4
    [row] = [r for r in report["sources"] if r["source"] == "tool: Read"]
    assert row["resident_tokens"] == 2000                # entered after call 1, read by calls 2 and 3
    assert report["cache_reads"] == 6000


def test_the_transcript_folder_follows_the_harness_naming(tmp_path):
    assert context_cost.transcripts_dir("/Volumes/Data/work/my.proj").name == "-Volumes-Data-work-my-proj"


def test_an_unreadable_line_or_file_does_not_stop_the_report(tmp_path):
    folder = tmp_path / "t"
    folder.mkdir()
    (folder / "bad.jsonl").write_text('[1, 2]\n"a string"\nnot json\n', encoding="utf-8")
    _session(folder / "good.jsonl", [_assistant("m1", 10), _assistant("m2", 10)])
    assert context_cost.report(tmp_path, transcripts=folder)["sessions"] == 1


def test_an_image_is_charged_what_the_model_pays_not_its_base64_length(tmp_path):
    """Measured on the host: one screenshot's 592k base64 characters made a session
    'explain' 182% of its cache reads."""
    folder = tmp_path / "t"
    folder.mkdir()
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "A" * 500_000}}
    _session(folder / "s.jsonl", [
        _assistant("m1", 1000, [{"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a.png"}}]),
        {"type": "user", "timestamp": "2026-09-20T10:00:01Z",
         "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
                                  "content": [image, {"type": "text", "text": "x" * 222}]}]}},
        _assistant("m2", 2700),
    ])
    [row] = [r for r in context_cost.report(tmp_path, transcripts=folder)["sources"] if r["source"] == "tool: Read"]
    assert row["entered_tokens"] == context_cost.IMAGE_TOKENS + 100


def test_a_table_that_explains_more_than_the_reads_says_it_over_counts():
    data = {"sessions": 1, "calls": 2, "since": None, "cache_reads": 1000, "base_tokens": 600,
            "explained": 1.2, "transcripts": "t", "sources": [
                {"source": "tool: Read", "resident_tokens": 600, "entered_tokens": 300, "share": 0.5}]}
    assert "over-counts" in context_cost.render(data)
    assert "over-counts" not in context_cost.render({**data, "explained": 0.97})
