"""Authored knowledge notes.

The package is split by concern (2.x roadmap F4); every name the single module
`core/notes.py` offered is re-exported here, so `from core import notes` and
`notes.<name>` keep working unchanged.

  store       where notes live, parse, load, sections
  guards      credentials, duplicates, paraphrases
  procedures  steps, rules, success checks, run counters
  validate    required sections, placeholders, scope
  render      ranking and the context section
  write       add, amend, deliberate skips
"""
from __future__ import annotations

from core.notes.store import (
    DEFAULT_NOTES_DIR, notes_dir, note_synonyms, PROVENANCES, expired, _slug, _front_matter, _SAFE_SCALAR, _scalar, KINDS,
    GENERATOR_SOURCES, BULK_INDEX_SOURCES, is_generated, is_bulk_index, _is_sensitive,
    _scope_anchor, _resolve_scope_entry, _hash_file, _one_note, superseded, Note,
    _unscalar, parse_note, _count, load_notes, load_dir, _WORD, stale_notes,
    is_notes_dir_gitignored, _gitignore_pattern_matches, _HEADING, _LIST_ITEM,
    section_in, _items, steps_in, _set_front, append_bullet, append_to_note_section,
)  # noqa: F401
from core.notes.guards import (
    _PLACEHOLDER_VALUES, _SECRET_ASSIGNMENT, _CREDENTIAL_VALUE, _AUTH_HEADER,
    _CREDENTIAL_PATTERNS, _looks_opaque, _credential_value, _find_credential,
    DuplicateNoteError, _duplicate_escape, body_digest, normalized_title_key,
    PARAPHRASE_JACCARD, PARAPHRASE_MIN_TRIGRAMS, _FENCED, _prose, _trigrams,
    trigram_jaccard, paraphrase_of,
)  # noqa: F401
from core.notes.procedures import (
    _STEP_TOOL, KNOWN_FAILURES, RULES_SECTION, RULES_MAX_CHARS, procedure_steps,
    procedure_tools, procedure_rules, procedure_prerequisites, procedure_success,
    procedure_known_failures, _procedure_front, procedures, replacing_procedure,
    find_procedure, _append_known_failure, record_procedure_run, FRESH_DAYS,
    AGING_DAYS, CONFIDENCE_WORDS, procedure_confidence, lessons_for,
)  # noqa: F401
from core.notes.validate import (
    _REQUIRED_SECTIONS, _compose_body, _PLACEHOLDER_RE, _is_placeholder, _refuse_scope,
    _refuse_placeholder, _DEFECT_SECTIONS, _DEFECT_HEADING, _canonical_scope,
)  # noqa: F401
from core.notes.render import (
    _RELEVANCE_THRESHOLD, SCORE_FLOOR, _KEY, _words, word_weights, relevance, NARROWED_KINDS,
    search_notes, rank, _OMITTED_TITLE_LIMIT, _render_note, render_context_section,
    synonym_groups, canonical_words,
)  # noqa: F401
from core.notes.write import (
    SKIPS_FILE, skips_path, record_skip, was_skipped, add_note, amend_note,
)  # noqa: F401
