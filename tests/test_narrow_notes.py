"""Note bodies narrowed to a task's terms in the context section (2.x roadmap C3b)."""
from core import notes


def _long_finding(topic_line: str, filler: int = 120) -> str:
    rows = [f"Observation {i}: the service handled the ordinary request path as expected." for i in range(filler)]
    rows[filler // 2] = topic_line
    return "\n".join(rows)


def test_a_long_finding_is_narrowed_to_the_query(tmp_path):
    body = _long_finding("Observation X: the refund queue drops messages after a broker restart.")
    notes.add_note(tmp_path, kind="finding", title="Service behaviour survey", body=body)
    section = notes.render_context_section(tmp_path, "refund queue", max_chars=16000)
    assert "the refund queue drops messages" in section
    assert "Observation 3:" not in section
    assert "narrowed" in section.lower() and "eos note show" in section
    assert len(section) < 2500


def test_a_short_finding_stays_whole(tmp_path):
    body = "The refund queue drops messages after a broker restart.\nRestart order matters."
    notes.add_note(tmp_path, kind="finding", title="Refund queue restart", body=body)
    section = notes.render_context_section(tmp_path, "refund queue", max_chars=16000)
    assert body in section
    assert "narrowed" not in section.lower()


def test_a_lesson_is_never_narrowed(tmp_path):
    went_wrong = _long_finding("Observation X: the refund worker retried without a lock.", filler=40)
    body = (f"## What went wrong\n\n{went_wrong}\n\n## What was learned\n\nRetries raced.\n\n"
            "## Next time\n\nTake the queue lock first.\n")
    notes.add_note(tmp_path, kind="lesson", title="Refund retries need a lock", body=body)
    section = notes.render_context_section(tmp_path, "refund lock", max_chars=16000)
    assert "Take the queue lock first." in section
    assert "Observation 3:" in section
    assert "narrowed" not in section.lower()


def test_without_a_query_bodies_are_whole(tmp_path):
    body = _long_finding("Observation X: the refund queue drops messages after a broker restart.")
    notes.add_note(tmp_path, kind="finding", title="Service behaviour survey", body=body)
    section = notes.render_context_section(tmp_path, None, max_chars=100000)
    assert "Observation 3:" in section
    assert "narrowed" not in section.lower()


# --- review RVa ---------------------------------------------------------------------------


def test_a_finding_matched_only_through_a_synonym_is_still_narrowed(tmp_path):
    (tmp_path / ".eos").mkdir()
    (tmp_path / ".eos" / "config.toml").write_text(
        '[notes]\nsynonyms = [["wiki", "confluence"]]\n', encoding="utf-8")
    body = _long_finding("Observation X: the confluence export job drops attachments over 10MB.")
    notes.add_note(tmp_path, kind="finding", title="Confluence export behaviour", body=body)
    section = notes.render_context_section(tmp_path, "wiki", max_chars=16000)
    assert "confluence export job drops attachments" in section
    assert "Observation 3:" not in section
    assert "narrowed" in section.lower()
