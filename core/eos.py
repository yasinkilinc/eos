#!/usr/bin/env python3
"""EOS CLI — local, stdlib-only project intelligence engine.

Commands:
  init      Create .eos/ in current directory.
  scan      Parse source files and regenerate brain/graph artifacts.
  update    Update the eos-core engine from the canonical runtime.
  doctor    Validate .eos/ integrity and report issues.
  info      Print summary about the local .eos instance.
  status    Print project status as JSON.
  graph     Export the generated project graph.
  context   Generate an AI-oriented project context.
  compose   Compose focused context for a task and target file.
  impact    Analyze direct import dependencies and dependents.
  mcp       Start the read-only stdio MCP server.
  bench     Measure EOS tools against plain alternatives on this project.
  ui        Start the multi-project dashboard (installs its own deps on request).
  index     Rebuild the queryable per-project database (.eos/data/eos.db).
  query     Run read-only SQL, or --search, against that database.
  clean     Remove generated artifacts (cache/brain/graph/index).
"""
import argparse
import sys
from pathlib import Path

# Bootstrap so the same eos.py runs both from the dev repo (<repo>/core/eos.py)
# and from a deployed flat runtime (.eos/runtime/eos.py) without the dev repo.
# In deployed mode (parent dir holds id.txt) we alias the `core` package to
# this directory, so `from core.x import y` resolves to runtime siblings.
_HERE = Path(__file__).resolve().parent
if (_HERE.parent / "id.txt").exists():
    sys.path.insert(0, str(_HERE))
    import types as _types

    _core = _types.ModuleType("core")
    _core.__path__ = [str(_HERE)]
    sys.modules["core"] = _core
    _CORE_ROOT = _HERE.parent
else:
    if str(_HERE.parent) not in sys.path:
        sys.path.insert(0, str(_HERE.parent))
    _CORE_ROOT = _HERE.parent

# A harness hook runs on every tool call of every session, so `eos hook` is
# dispatched before the scanner, the generators and the index are imported --
# none of which it uses. Measured: the full import is most of `eos`'s startup.
if __name__ == "__main__" and sys.argv[1:2] == ["hook"]:
    from core import hooks as _hooks

    raise SystemExit(_hooks.main(sys.argv[2:]))

from core import notes


_FILL_SESSION = frozenset(
    {("work", verb) for verb in ("add", "claim", "log", "block", "unblock", "done", "drop")}
    | {("note", verb) for verb in ("add", "amend", "skip")}
    | {("procedure", "new")}
    | {("route", None)}
)


from core.cli.common import VERSION  # noqa: F401
from core.cli.intel import cmd_ask, cmd_brief, cmd_cite, cmd_consolidate, cmd_cost, cmd_draft_test, cmd_impact, cmd_parent, cmd_parents, cmd_query, cmd_rules, cmd_trace, cmd_verify, cmd_why  # noqa: F401
from core.cli.notes import cmd_findings, cmd_note  # noqa: F401
from core.cli.procedures import cmd_procedure  # noqa: F401
from core.cli.project import cmd_ai, cmd_bench, cmd_clean, cmd_compose, cmd_context, cmd_doctor, cmd_graph, cmd_hook, cmd_index, cmd_info, cmd_init, cmd_mcp, cmd_scan, cmd_status, cmd_ui, cmd_update  # noqa: F401
from core.cli.route import cmd_capabilities, cmd_route  # noqa: F401
from core.cli.runs import cmd_run  # noqa: F401
from core.cli.work import cmd_work  # noqa: F401


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eos", description="EOS project intelligence engine")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_path(p):
        # Support both `eos init path` and `eos init --path path`. argparse
        # assigns positional defaults after optionals are parsed, so a plain
        # default="." on the positional clobbered an already-parsed --path
        # back to "." whenever only the flag form was given (verified:
        # `eos note add --path /tmp ...` silently wrote into cwd's .eos/,
        # not /tmp/.eos/). SUPPRESS means "no positional token means don't
        # touch the namespace," leaving --path's own default in charge.
        p.add_argument("path", nargs="?", default=argparse.SUPPRESS, help="Project root path (default: current directory)")
        p.add_argument("--path", dest="path", default=".", help=argparse.SUPPRESS)

    init_p = sub.add_parser("init", help="Initialize .eos/ in a project")
    add_path(init_p)
    init_p.add_argument("--link-parent", dest="link_parent", default=None, help="Path to a linked source project this one overlays: a vendored dependency, a fork's upstream, or a platform repo")
    init_p.add_argument(
        "--link-label",
        dest="link_label",
        default=None,
        help="Label for the link (default: parent). Use a distinct label to add a second "
        "link, e.g. --link-label common for the shared upstream-common-* libraries.",
    )
    init_p.add_argument(
        "--no-ai",
        dest="no_ai",
        action="store_true",
        help="Do not write the .claude/, .mcp.json and AGENTS.md integration files",
    )
    init_p.add_argument(
        "--surface",
        choices=("cli", "mcp", "both"),
        default="cli",
        help="Which surface this project exposes. 'cli' (default) writes no MCP "
        "registration: an MCP tool roster is charged to every request whether "
        "or not a tool is called. 'mcp' or 'both' registers the server too.",
    )
    init_p.add_argument(
        "--claude",
        choices=("files", "plugin"),
        default="files",
        help="How Claude Code gets EOS's hooks, skill and agent: copied into .claude/ "
        "(files, default) or from the EOS plugin (plugin), which writes nothing there.",
    )

    scan_p = sub.add_parser("scan", help="Scan project and regenerate knowledge artifacts")
    add_path(scan_p)
    scan_p.add_argument("--full", action="store_true", help="Force full rescan ignoring cache")
    scan_p.add_argument("--with-parents", dest="with_parents", action="store_true", help="Also index linked parent projects")

    update_p = sub.add_parser("update", help="Update eos-core runtime from canonical core/")
    add_path(update_p)
    update_p.add_argument("--dry-run", action="store_true", help="Preview changes without applying")

    doctor_p = sub.add_parser("doctor", help="Validate .eos/ integrity")
    add_path(doctor_p)
    doctor_p.add_argument(
        "--memory", action="store_true",
        help="Instead: the operational-memory matrix for this project "
             "(docs/plans/operational-memory.md); exit 1 while any row is open")
    doctor_p.add_argument("--format", choices=("text", "json"), default="text")

    info_p = sub.add_parser("info", help="Show instance summary")
    add_path(info_p)

    clean_p = sub.add_parser("clean", help="Remove generated artifacts")
    add_path(clean_p)

    index_p = sub.add_parser(
        "index",
        help="Rebuild .eos/data/eos.db from notes, brain, graph, journeys and git history",
    )
    add_path(index_p)

    query_p = sub.add_parser("query", help="Run read-only SQL against .eos/data/eos.db")
    add_path(query_p)
    query_p.add_argument("sql", nargs="?", help="One read-only SQL statement")
    query_p.add_argument("--search", help="Full-text search over notes, brain docs and journeys instead of SQL, best match first (a word matching nothing costs rank, not the answer)")
    query_p.add_argument("--limit", type=int, default=20, help="Maximum --search results")

    status_p = sub.add_parser("status", help="Show project status and latest scan metadata")
    add_path(status_p)

    graph_p = sub.add_parser("graph", help="Export the generated project graph")
    add_path(graph_p)
    graph_p.add_argument("--type", default="import", help="Edge type to include, or 'all'")
    graph_p.add_argument("--format", choices=("json",), default="json", help="Output format")
    graph_p.add_argument("--output", default=None, help="Output path, or '-' for stdout")

    context_p = sub.add_parser(
        "context",
        help="Generate an AI-oriented project context (writes .eos/data/brain/llm_context.md)")
    add_path(context_p)
    context_p.add_argument("--budget", type=int, default=12000, help="Approximate token budget")
    context_p.add_argument("--stdout", action="store_true",
                           help="Print it instead of writing the file")

    compose_p = sub.add_parser("compose", help="Compose focused context for a task")
    add_path(compose_p)
    compose_p.add_argument("task", help="Task or focus description")
    compose_p.add_argument("target", nargs="?", help="Optional project-relative target file")
    compose_p.add_argument("--budget", type=int, default=12000, help="Approximate token budget")

    impact_p = sub.add_parser("impact", help="Analyze direct import impact for a file")
    add_path(impact_p)
    impact_p.add_argument("file", help="Project-relative file path")
    impact_p.add_argument("--depth", type=int, default=1,
                          help="How many hops to follow (1-5, default 1)")
    impact_p.add_argument("--include", action="append", choices=("facts", "coverage", "history"),
                          help="Add provenance, detector coverage, or this file's commits")

    verify_p = sub.add_parser(
        "verify", help="Record what an adapter ran for a behaviour code, and what happened")
    add_path(verify_p)
    verify_p.add_argument("code", help="The behaviour code the run was about")
    verify_p.add_argument("--outcome", required=True, choices=("passed", "failed", "errored"))
    # dest is not "command": the subparser already stores the subcommand name
    # there, and a flag writing to it replaced "verify" with the shell line,
    # which surfaced as a KeyError on dispatch.
    verify_p.add_argument("--command", required=True, dest="ran", help="Exactly what was run")
    verify_p.add_argument("--exit-code", type=int, dest="exit_code")
    verify_p.add_argument("--log", help="Where the full output was kept")
    verify_p.add_argument("--output", help="File holding the output, or - for stdin; only its digest is stored")
    verify_p.add_argument("--verdict", choices=(
        "expected-behaviour", "test-defect", "environment-failure",
        "potential-defect", "confirmed-defect"),
        help="What a person concluded. Never filled in automatically.")
    verify_p.add_argument("--note", help="One line of context for the run")

    cost_p = sub.add_parser("cost", help="What EOS has cost this project, per command")
    add_path(cost_p)
    cost_p.add_argument("--format", choices=("text", "json"), default="text")
    cost_p.add_argument("--context", action="store_true",
                        help="What this project's agent sessions keep re-reading, by source, from the "
                             "harness's transcripts (tokens x calls kept), checked against their cache reads")
    cost_p.add_argument("--sessions", action="store_true",
                        help="Per session: what EOS delivered against what the model read, joined from "
                             "telemetry, routing-usage and the hooks' session lines")
    cost_p.add_argument("--since", help="With --context or --sessions: sessions seen on or after this date (YYYY-MM-DD)")
    cost_p.add_argument("--transcripts", help="With --context: the transcript folder (default: the harness's)")

    findings_p = sub.add_parser("findings", help="Recorded runs and what they were judged to be")
    add_path(findings_p)
    findings_p.add_argument("--failed-only", action="store_true", dest="failed_only")
    findings_p.add_argument("--format", choices=("text", "json"), default="text")

    draft_p = sub.add_parser(
        "draft-test", help="Draft a test for a behaviour code the suite does not assert")
    add_path(draft_p)
    draft_p.add_argument("code", help="The behaviour code, as `eos rules` lists it")
    draft_p.add_argument("--write", action="store_true",
                         help="Also save it under .eos/data/candidates/")
    draft_p.add_argument("--format", choices=("text", "json"), default="text")

    ask_p = sub.add_parser(
        "ask", help="Run a question this project's index extensions provide")
    add_path(ask_p)
    ask_p.add_argument("name", nargs="?", help="Question name; omit to list them")
    ask_p.add_argument("argument", nargs="?", help="Value for a question that takes one")
    ask_p.add_argument("--format", choices=("text", "json"), default="text")

    trace_p = sub.add_parser(
        "trace", help="What an entry point reaches, and what is wired at run time")
    add_path(trace_p)
    trace_p.add_argument("file", help="Project-relative file path")
    trace_p.add_argument("--depth", type=int, default=4, help="How many hops to follow (1-5)")
    trace_p.add_argument("--format", choices=("text", "json"), default="text")

    rules_p = sub.add_parser(
        "rules", help="Behaviour codes this project throws, and which have no test")
    add_path(rules_p)
    rules_p.add_argument("--untested", action="store_true",
                         help="Only codes no test names")
    rules_p.add_argument("--limit", type=int, default=20,
                         help="How many codes to list (0 for all); the tally always covers every one")
    rules_p.add_argument("--format", choices=("text", "json"), default="text")

    why_p = sub.add_parser(
        "why", help="Where a fact came from, and which detectors found nothing")
    add_path(why_p)
    why_p.add_argument("subject", nargs="?", help="Project-relative file path; omit for coverage only")
    why_p.add_argument("--predicate", help="Only facts with this predicate")
    why_p.add_argument("--min-confidence", type=float, dest="min_confidence",
                       help="Only facts at or above this confidence")
    why_p.add_argument("--format", choices=("text", "json"), default="text")

    mcp_p = sub.add_parser("mcp", help="Start the read-only stdio MCP server")
    add_path(mcp_p)

    bench_p = sub.add_parser("bench", help="Measure EOS tools against plain alternatives on this project")
    add_path(bench_p)
    bench_p.add_argument("--samples", type=int, default=50, help="How many symbols to sample (default: 50)")

    ui_p = sub.add_parser("ui", help="Start the multi-project dashboard")
    ui_sub = ui_p.add_subparsers(dest="ui_command")
    ui_p.set_defaults(ui_command="start", yes=False, port=None, no_install=False)
    # `eos ui` already means `eos ui start`, so the start flags have to parse
    # on the bare form too -- the README documents `eos ui --yes` and it exited
    # 2 with `unrecognized arguments`, which is a document sending a reader
    # into an argparse error.
    ui_p.add_argument("--yes", action="store_true", help="Install missing packages without asking")
    ui_p.add_argument("--no-install", dest="no_install", action="store_true",
                      help="Report missing packages and stop")
    ui_start_p = ui_sub.add_parser("start", help="Start the dashboard")
    ui_start_p.add_argument("port", nargs="?", type=int, default=None)
    ui_start_p.add_argument("--yes", action="store_true", help="Install missing packages without asking")
    ui_start_p.add_argument("--no-install", dest="no_install", action="store_true", help="Report missing packages and stop")
    ui_sub.add_parser("uninstall", help="Remove EOS's own venv")

    parents_p = sub.add_parser("parents", help="List configured parent-project links")
    add_path(parents_p)

    parent_p = sub.add_parser(
        "parent", help="Real source for a symbol from a linked parent project")
    add_path(parent_p)
    parent_p.add_argument("symbol", help="Symbol name, or part of one")
    parent_p.add_argument("--limit", type=int, default=3,
                          help="How many matches to show source for (default: 3)")
    parent_p.add_argument("--format", choices=("text", "json"), default="text")

    note_p = sub.add_parser("note", help="Manage authored knowledge notes")
    note_sub = note_p.add_subparsers(dest="note_command", required=True)

    note_add_p = note_sub.add_parser("add", help="Record a note")
    add_path(note_add_p)
    note_add_p.add_argument("--kind", choices=notes.KINDS, required=True)
    note_add_p.add_argument("--title", required=True)
    note_add_p.add_argument("--body", help="Note body (required for 'finding')")
    note_add_p.add_argument("--tags", help="Comma-separated tags")
    note_add_p.add_argument("--scope", help="Comma-separated files/classes this note is about")
    note_add_p.add_argument("--source", help="Issue or PR reference, e.g. TICKET-123")
    note_add_p.add_argument("--cause", help="Root cause (required for 'defect')")
    note_add_p.add_argument("--solution", help="Fix applied (required for 'defect')")
    note_add_p.add_argument("--metric", help="Measured before/after (required for 'defect')")
    note_add_p.add_argument("--session", default=None, help="Session id, set by the gate hook")
    note_add_p.add_argument("--execution", help="For a lesson: the run that taught it (eos run list)")
    note_add_p.add_argument("--procedure", help="For a lesson: the procedure it concerns; for a "
                            "procedure: its slug")
    note_add_p.add_argument("--supersedes", help="The note this one replaces (file name or title): it stays "
                                                 "on disk, search and briefs stop offering it")
    note_add_p.add_argument("--provenance", choices=notes.PROVENANCES,
                            help="Who wrote it; --agent alone implies 'agent'")
    note_add_p.add_argument("--agent", help="The agent that wrote it (claude, devin, ...)")
    note_add_p.add_argument("--valid-until", help="YYYY-MM-DD: after this day search and briefs stop "
                                                  "offering it; `eos consolidate` lists it")

    note_list_p = note_sub.add_parser("list", help="List notes")
    add_path(note_list_p)
    note_list_p.add_argument("--tag", help="Only notes carrying this tag")

    note_show_p = note_sub.add_parser("show", help="Print one note in full")
    add_path(note_show_p)
    note_show_p.add_argument("name", help="Note file name, or part of its title")
    note_related_p = note_sub.add_parser(
        "related", help="Notes linked to one: citations, a lesson's procedure, shared scope files and tickets")
    add_path(note_related_p)
    note_related_p.add_argument("name", help="Note file name, or part of its title")
    note_related_p.add_argument("--limit", type=int, default=10)
    note_graph_p = note_sub.add_parser("graph", help="The note graph's size; --output writes it as node-link "
                                                     "JSON (Graphify's shape)")
    add_path(note_graph_p)
    note_graph_p.add_argument("--output", help="File to write the graph to")

    note_search_p = note_sub.add_parser("search", help="Search notes by relevance")
    add_path(note_search_p)
    note_search_p.add_argument("query")
    note_search_p.add_argument("--limit", type=int, default=None)

    note_eval_p = note_sub.add_parser(
        "eval", help="Score note search against a golden file of question/answer pairs")
    add_path(note_eval_p)
    note_eval_p.add_argument("golden", help="TSV: '<question><TAB><note filename>' per line")
    # Kept in step with core.retrieval.DEFAULT_DEPTH by the test below it; the
    # module is imported in the handler, not here, so the parser stays cheap.
    note_eval_p.add_argument("--depth", type=int, default=10,
                             help="How far down to look for the expected note")
    note_eval_p.add_argument("--format", choices=("text", "json"), default="text")

    note_skip_p = note_sub.add_parser("skip", help="Record that this session needs no note")
    add_path(note_skip_p)
    note_skip_p.add_argument("--reason", required=True, help="Why nothing durable was learned")
    note_skip_p.add_argument("--session", default=None, help="Session id, set by the gate hook")

    note_amend_p = note_sub.add_parser("amend", help="Revise a note and re-hash its scope")
    note_amend_p.add_argument("note", help="Path to the note file")
    add_path(note_amend_p)
    amend_what = note_amend_p.add_mutually_exclusive_group()
    amend_what.add_argument("--body", help="New body; '-' reads stdin")
    amend_what.add_argument("--reaffirm", help="The note still holds; say why")
    note_amend_p.add_argument(
        "--scope",
        help="Comma-separated files/classes this note is about; replaces the "
        "scope list wholesale. May stand alone or combine with --body/--reaffirm.",
    )
    note_amend_p.add_argument("--session", default=None, help="Session id, set by the gate hook")

    note_audit_p = note_sub.add_parser("audit", help="Report notes whose scoped files changed")
    add_path(note_audit_p)

    cite_p = sub.add_parser(
        "cite", help="References and quotes in an answer that cannot be right: a missing file, a line "
                     "past the end, a quote not at the lines it cites")
    add_path(cite_p)
    cite_p.add_argument("--output", default="-", help="File holding the answer, or - for stdin")
    cite_p.add_argument("--format", choices=("text", "json"), default="text")
    consolidate_p = sub.add_parser(
        "consolidate", help="What needs attention in this project's memory: failing and unrun procedures, "
                            "lint, notes that read alike, stale notes and work (changes nothing)")
    add_path(consolidate_p)
    consolidate_p.add_argument("--format", choices=("text", "json"), default="text")
    brief_p = sub.add_parser(
        "brief", help="What a session needs before it starts: in flight, and known here")
    add_path(brief_p)
    brief_p.add_argument("--session", default=None, help="Session id, so 'yours' means something")
    brief_p.add_argument("--agent", default=None, help="Which agent this is, e.g. claude or devin")
    brief_p.add_argument("--task", default=None,
                         help="What this session is about to do; leads the brief with the procedure, "
                              "its last runs and lessons. Used as a query and never stored")
    brief_p.add_argument("--budget", type=int, default=None,
                         help="Token budget for a --task brief (default 1500)")
    brief_p.add_argument("--task-only", action="store_true",
                         help="Only the task sections, and nothing at all when none found anything "
                              "(for a hook that fires on every prompt)")
    brief_p.add_argument("--for-subagent", action="store_true",
                         help="With --task: the handoff a subagent should start with (parent run, "
                              "procedure rules, wrappers, notes), at most 400 tokens")
    brief_p.add_argument("--resume", action="store_true",
                         help="What this session left: its open runs, held work and failed runs "
                              "(with --session, or the harness's session variable)")

    route_p = sub.add_parser(
        "route", help="Which model and how much effort a task deserves, and why (ADR-025)")
    add_path(route_p)
    route_p.add_argument("task", nargs="*",
                         help="What the task is; quoting is optional. Used as a query and never stored")
    route_p.add_argument("--file", action="append", default=None,
                         help="A project-relative file the task touches; repeatable. Feeds the "
                              "file-count and dependency factors")
    route_p.add_argument("--model", default=None, help="Fix the model (a registry id or alias), or 'auto'")
    route_p.add_argument("--effort", default=None, help="Fix the effort (low|medium|high|xhigh|max), or 'auto'")
    route_p.add_argument("--json", action="store_true", help="Print the decision as JSON")
    route_p.add_argument("--fresh", action="store_true",
                         help="Decide again even if this run already has a decision")
    route_p.add_argument("--no-record", action="store_true",
                         help="Decide without writing the trace line or the run event")
    route_p.add_argument("--session", default=None, help="Session id; filled from the harness when omitted")
    route_p.add_argument("--stats", action="store_true",
                         help="Recorded decisions joined to their runs' outcomes; ignores the task")
    route_p.add_argument("--usage-from", default=None, metavar="TRANSCRIPT",
                         help="Fold a harness transcript into this session's model and token totals "
                              "(for a Stop hook); ignores the task")
    route_p.add_argument("--eval", default=None, metavar="CORPUS",
                         help="Score the policy against a labelled TSV corpus (prompt, type, level, "
                              "model, effort, split); no model is called. Exit 0 when the gate "
                              "passes, 1 when it fails")
    route_p.add_argument("--split", choices=("dev", "test"), default=None,
                         help="--eval: only the rows of this split; test prints metrics and the "
                              "gate but no rows or suggestions, and is recorded")

    proc_p = sub.add_parser("procedure", help="How a recurring task is done here, and how it has gone (ADR-023)")
    proc_sub = proc_p.add_subparsers(dest="procedure_command", required=True)

    proc_list_p = proc_sub.add_parser("list", help="Procedures with their run counts")
    add_path(proc_list_p)
    proc_list_p.add_argument("--tool", help="Only procedures whose steps name this tool")
    proc_list_p.add_argument("--target", help="Only procedures that have run against this target")
    proc_list_p.add_argument("--format", choices=("text", "json"), default="text")

    proc_show_p = proc_sub.add_parser("show", help="One procedure: steps, counts, recent runs")
    add_path(proc_show_p)
    proc_show_p.add_argument("procedure", help="Slug, slug prefix, or part of the title")
    proc_show_p.add_argument("--format", choices=("text", "json"), default="text")

    proc_new_p = proc_sub.add_parser("new", help="Write a procedure note")
    add_path(proc_new_p)
    proc_new_p.add_argument("--title", required=True)
    proc_new_p.add_argument("--step", action="append", help="One step; repeat in order. `(tool: X)` names its tool")
    proc_new_p.add_argument("--steps", choices=("-",), help="Read steps from stdin, one per line")
    proc_new_p.add_argument("--prerequisite", action="append", help="Repeat for each")
    proc_new_p.add_argument("--success", action="append", help="What proves it worked; repeat for each")
    proc_new_p.add_argument("--body", help="Prose before the steps")
    proc_new_p.add_argument("--slug", help="Slug executions will name; defaults to the title's")
    proc_new_p.add_argument("--tags")
    proc_new_p.add_argument("--scope", help="Comma-separated files this procedure depends on")
    proc_new_p.add_argument("--source")
    proc_new_p.add_argument("--session", default=None)

    proc_lint_p = proc_sub.add_parser("lint", help="Steps naming a tool nobody can find; procedures without a success")
    add_path(proc_lint_p)
    proc_lint_p.add_argument("--format", choices=("text", "json"), default="text")
    proc_audit_p = proc_sub.add_parser("audit", help="Counters against the ledger, failing and unverified procedures")
    add_path(proc_audit_p)
    proc_audit_p.add_argument("--format", choices=("text", "json"), default="text")

    run_p = sub.add_parser("run", help="What a session did: executions and their events (ADR-022)")
    run_sub = run_p.add_subparsers(dest="run_command", required=True)

    def run_actor(p):
        p.add_argument("--session", default=None,
                       help="Session id; defaults to the harness's own (EOS_SESSION, CLAUDE_CODE_SESSION_ID)")

    run_start_p = run_sub.add_parser("start", help="Open an execution; prints its id")
    add_path(run_start_p)
    run_start_p.add_argument("--title", required=True, help="What this run is, as the session declares it")
    run_start_p.add_argument("--procedure", help="Slug of the procedure this run follows")
    run_start_p.add_argument("--work", help="Work item id this run belongs to")
    run_start_p.add_argument("--target", help="Environment or system this run acts on")
    run_start_p.add_argument("--agent", help="Which agent this is, e.g. claude or devin")
    where = run_start_p.add_mutually_exclusive_group()
    where.add_argument("--project", help="Record it in this workspace project's ledger (.eos/projects.toml); "
                       "without it, the one project the title names by alias")
    where.add_argument("--here", action="store_true",
                       help="Record it here even when the title names a workspace project")
    run_actor(run_start_p)

    run_event_p = run_sub.add_parser("event", help="Append one thing the session did")
    add_path(run_event_p)
    run_event_p.add_argument("execution", nargs="?", default=None,
                             help="Execution id; defaults to this session's open one")
    run_event_p.add_argument("--kind", required=True,
                             choices=("ran", "read", "changed", "called", "verified", "noted", "decided"))
    run_event_p.add_argument("--tool", help="The wrapper or program that did it")
    run_event_p.add_argument("--target", help="Environment or system it acted on")
    run_event_p.add_argument("--ref", help="Log path, cache path, build id or file -- a reference, never a payload")
    run_event_p.add_argument("--exit", type=int, default=None, help="Exit code")
    run_event_p.add_argument("--ms", type=int, default=None, help="Duration in milliseconds")
    run_event_p.add_argument("--body", help="One sentence, when the ref does not say it")
    run_actor(run_event_p)

    run_finish_p = run_sub.add_parser("finish", help="Close an execution with its outcome")
    add_path(run_finish_p)
    run_finish_p.add_argument("execution", nargs="?", default=None,
                              help="Execution id; defaults to this session's open one")
    run_finish_p.add_argument("--outcome", required=True, choices=("ok", "failed", "abandoned"))
    run_finish_p.add_argument("--lesson", help="What was learned; required for --outcome failed "
                              "unless a lesson note already names this run. Written as a lesson note")
    run_finish_p.add_argument("--next-time", help="What the next run of this should do differently")
    run_actor(run_finish_p)

    run_list_p = run_sub.add_parser("list", help="Executions, most recent first")
    add_path(run_list_p)
    run_list_p.add_argument("--session", default=None)
    run_list_p.add_argument("--procedure")
    run_list_p.add_argument("--target")
    run_list_p.add_argument("--outcome", choices=("ok", "failed", "abandoned", "open"))
    run_list_p.add_argument("--since", help="ISO date; runs started on or after it")
    run_list_p.add_argument("--limit", type=int, default=20)
    run_list_p.add_argument("--format", choices=("text", "json"), default="text")
    run_list_p.add_argument("--stats", action="store_true",
                            help="verified / claimed counts of ok runs (ADR-028)")

    run_tools_p = run_sub.add_parser("tools", help="Which tools ran, how often, and how the last run went")
    add_path(run_tools_p)
    run_tools_p.add_argument("--procedure")
    run_tools_p.add_argument("--target")
    run_tools_p.add_argument("--format", choices=("text", "json"), default="text")

    run_diff_p = run_sub.add_parser("diff", help="What one execution changed: recorded paths and its commit range")
    add_path(run_diff_p)
    run_diff_p.add_argument("execution", help="Execution id, id prefix, or part of its title")
    run_diff_p.add_argument("--format", choices=("text", "json"), default="text")

    run_show_p = run_sub.add_parser("show", help="One execution and its timeline")
    add_path(run_show_p)
    run_show_p.add_argument("execution", help="Execution id, id prefix, or part of its title")
    run_show_p.add_argument("--format", choices=("text", "json"), default="text")

    work_p = sub.add_parser("work", help="What is in flight, across sessions")
    work_sub = work_p.add_subparsers(dest="work_command", required=True)

    def add_actor(p):
        # Both are optional and both are only ever markers: who wrote this
        # line, not a permission or an identity. The ledger tolerates their
        # absence -- an item held by "nobody" still reads as held.
        p.add_argument("--session", default=None, help="Session id, so a claim has a holder")
        p.add_argument("--agent", default=None, help="Which agent this is, e.g. claude or devin")

    work_add_p = work_sub.add_parser("add", help="Record a piece of work")
    add_path(work_add_p)
    work_add_p.add_argument("--title", required=True)
    work_add_p.add_argument("--body", help="What this is, in a sentence the next session can act on")
    work_add_p.add_argument("--ticket", help="Issue key, e.g. TICKET-123")
    work_add_p.add_argument("--scope", help="Comma-separated files this work touches")
    work_add_p.add_argument("--claim", action="store_true",
                            help="Claim it in the same command; the usual case")
    add_actor(work_add_p)

    work_claim_p = work_sub.add_parser("claim", help="Take an item; a second claim is reported")
    add_path(work_claim_p)
    work_claim_p.add_argument("item", help="Item id, or part of its title")
    work_claim_p.add_argument("--note", help="What you are about to do")
    add_actor(work_claim_p)

    work_log_p = work_sub.add_parser("log", help="Record progress without changing status")
    add_path(work_log_p)
    work_log_p.add_argument("item", help="Item id, or part of its title")
    work_log_p.add_argument("--body", required=True, help="Where this got to")
    add_actor(work_log_p)

    work_block_p = work_sub.add_parser("block", help="Record that this is stuck, and on what")
    add_path(work_block_p)
    work_block_p.add_argument("item", help="Item id, or part of its title")
    work_block_p.add_argument("--reason", required=True, help="What it is waiting on")
    add_actor(work_block_p)

    work_unblock_p = work_sub.add_parser("unblock", help="Record that the blocker cleared")
    add_path(work_unblock_p)
    work_unblock_p.add_argument("item", help="Item id, or part of its title")
    work_unblock_p.add_argument("--note", help="What cleared it")
    add_actor(work_unblock_p)

    work_done_p = work_sub.add_parser("done", help="Record that a session finished it")
    add_path(work_done_p)
    work_done_p.add_argument("item", help="Item id, or part of its title")
    work_done_p.add_argument("--note", help="What was done")
    add_actor(work_done_p)

    work_drop_p = work_sub.add_parser("drop", help="Record that this will not be done, and why")
    add_path(work_drop_p)
    work_drop_p.add_argument("item", help="Item id, or part of its title")
    work_drop_p.add_argument("--reason", required=True, help="Why it was dropped")
    add_actor(work_drop_p)

    work_list_p = work_sub.add_parser("list", help="What is in flight here")
    add_path(work_list_p)
    work_list_p.add_argument("--status", default="live",
                             choices=("live", "open", "active", "blocked", "done", "dropped", "all"),
                             help="Default 'live': active, blocked and untaken work")
    work_list_p.add_argument("--across", action="store_true",
                             help="Every sibling ledger under the same knowledge root")
    work_list_p.add_argument("--session", default=None, help="Only items this session holds")
    work_list_p.add_argument("--format", choices=("text", "json"), default="text")

    work_show_p = work_sub.add_parser("show", help="One item, its events, and its commits")
    add_path(work_show_p)
    work_show_p.add_argument("item", help="Item id, or part of its title")
    work_show_p.add_argument("--format", choices=("text", "json"), default="text")

    work_stats_p = work_sub.add_parser(
        "stats", help="What happened here: collisions, claims gone quiet, time to close")
    add_path(work_stats_p)
    work_stats_p.add_argument("--since", default=None,
                              help="Only events at or after this date, e.g. 2026-09-01")
    work_stats_p.add_argument("--format", choices=("text", "json"), default="text")

    ai_p = sub.add_parser("ai", help="Manage the AI integration files")
    ai_sub = ai_p.add_subparsers(dest="ai_command", required=True)
    ai_update_p = ai_sub.add_parser("update", help="Refresh the integration files for this EOS version")
    add_path(ai_update_p)
    ai_update_p.add_argument("--no-agents-md", dest="no_agents_md", action="store_true",
                             help="Leave AGENTS.md alone (for a repository that tracks it)")
    ai_update_p.add_argument("--surface", choices=("cli", "mcp", "both"), default=None,
                             help="Change which surface this project exposes; without it, "
                                  "the choice recorded at init is kept")
    ai_update_p.add_argument("--claude", choices=("files", "plugin"), default=None,
                             help="How Claude Code gets the hooks, skill and agent: copied into "
                                  ".claude/ (files) or from the EOS plugin (plugin), which also "
                                  "removes the copies; the choice is recorded")

    caps_p = sub.add_parser(
        "capabilities",
        help="The wrappers this project offers instead of raw commands (capabilities.toml, ADR-026)")
    add_path(caps_p)
    caps_p.add_argument("--for", dest="task", default=None,
                        help="Only the capabilities a task's words point at")
    caps_p.add_argument("--command", dest="raw", default=None,
                        help="Which capability, if any, covers this command line")
    caps_p.add_argument("--format", choices=("text", "json"), default="text")

    hook_p = sub.add_parser(
        "hook", help="Handle one harness hook event; the event JSON arrives on stdin (ADR-026)")
    hook_p.add_argument("event", help="session-start, user-prompt, post-tool, post-tool-failure, "
                                      "subagent-start, subagent-stop, instructions-loaded, "
                                      "stop-failure, session-end, pre-agent")
    hook_p.add_argument("--agent", default=None, help="Which agent the harness is, e.g. claude")
    hook_p.add_argument("--project", default=None, help="Project root (default: from the event's cwd)")

    args = parser.parse_args(argv)

    commands = {
        "init": cmd_init,
        "scan": cmd_scan,
        "update": cmd_update,
        "doctor": cmd_doctor,
        "info": cmd_info,
        "clean": cmd_clean,
        "index": cmd_index,
        "query": cmd_query,
        "status": cmd_status,
        "graph": cmd_graph,
        "context": cmd_context,
        "compose": cmd_compose,
        "impact": cmd_impact,
        "why": cmd_why,
        "rules": cmd_rules,
        "trace": cmd_trace,
        "ask": cmd_ask,
        "draft-test": cmd_draft_test,
        "verify": cmd_verify,
        "findings": cmd_findings,
        "cost": cmd_cost,
        "mcp": cmd_mcp,
        "bench": cmd_bench,
        "ui": cmd_ui,
        "parent": cmd_parent,
        "parents": cmd_parents,
        "note": cmd_note,
        "work": cmd_work,
        "run": cmd_run,
        "procedure": cmd_procedure,
        "brief": cmd_brief,
        "cite": cmd_cite,
        "consolidate": cmd_consolidate,
        "route": cmd_route,
        "ai": cmd_ai,
        "capabilities": cmd_capabilities,
        "hook": cmd_hook,
    }
    handler = commands[args.command]
    path = getattr(args, "path", None)
    # One session id across every store (ADR-022). A write that names no
    # session gets the harness's own, the way telemetry reads it, so "what did
    # this session do" joins executions, work and notes without anyone having
    # remembered to pass --session. Writes only: on `work list` and `run list`
    # --session is a filter, and filling it would silently narrow the answer.
    sub_command = getattr(args, f"{args.command}_command", None)
    if (args.command, sub_command) in _FILL_SESSION and not getattr(args, "session", None) and path:
        from core import telemetry
        args.session, _ = telemetry.detect_session(path)
    if path is None or args.command in ("init", "ui", "mcp", "cost"):
        # init has no project yet, ui and mcp are long-running rather than one
        # answer, and cost reading itself would be a call that changes what it
        # reports.
        return handler(args)

    from core import telemetry

    # Flag *names*, never their values: a search query, a task or a note body
    # is whatever somebody typed, and a log that captured them would be a
    # liability in every project EOS touches.
    flags = [f"--{name.replace('_', '-')}" for name, value in vars(args).items()
             if name not in ("command", "path", "func") and value not in (None, False)]
    # The environment is what makes this measurable at all: only a handful of
    # commands take --session, and the harness already knows which session it
    # is running. Claude Code exports CLAUDE_CODE_SESSION_ID into every
    # command it runs, so sessions are counted with nothing installed and
    # nothing configured; EOS_SESSION is for a harness that exports no id of
    # its own.
    session = getattr(args, "session", None)
    session_from = "--session" if session else None
    if not session:
        session, session_from = telemetry.detect_session(path)
    with telemetry.Timer(path, args.command, flags, session=session,
                         session_from=session_from) as timer:
        # A pass-through counter, not a buffer. Buffering stdout and replaying
        # it broke the escaping a non-UTF-8 terminal needs -- measured by the
        # test that exists for it -- and a statistic may not change what a
        # command prints.
        counter = _CountingStream(sys.stdout)
        sys.stdout = counter
        try:
            code = handler(args)
            timer.ok = code in (0, None)
        finally:
            sys.stdout = counter.wrapped
            timer.chars = counter.chars
    return code


class _CountingStream:
    """Forwards everything to the real stream and counts the characters."""

    def __init__(self, wrapped):
        self.wrapped = wrapped
        self.chars = 0

    def write(self, text):
        self.chars += len(text)
        return self.wrapped.write(text)

    def __getattr__(self, name):
        return getattr(self.wrapped, name)


if __name__ == "__main__":
    sys.exit(main())
