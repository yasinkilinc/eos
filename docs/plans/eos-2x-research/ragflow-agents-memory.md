# RAGFlow, area B: agents, memory, MCP, orchestration, context engineering

Source-level investigation of `infiniflow/ragflow`, shallow clone at commit
`313ca90f6abd7682fe8523e16fd67b3653a3fa84` (2026-09-24). Paths are relative to the repo
root and `file:line` refers to that commit. Claims come from the code; READMEs were only
used to find code. Ingestion, chunking, embeddings, hybrid search, rerank, GraphRAG and
persistence internals are out of scope; they appear only where they touch this area.

Each area ends with **S** (architectural strength), **W** (architectural weakness) and
**EOS** (what transfers to EOS). EOS is local-first, runs on SQLite with stdlib-only
Python, calls no LLM, and advises the Claude Code and Devin harness. Its stores are notes,
procedures, executions/events, lessons, decisions, the work ledger and briefs, and ADR-001
and ADR-010 rule out vector retrieval.

<!-- SECTION:LIVE -->

---

## 0. Executive summary

- **The Python agent engine is a data-driven batch scheduler over a JSON DSL**, not a
  topological or event-bus engine. `Canvas._run_impl` keeps a growing `path` worklist.
  Each pass takes the window `path[idx:]`, runs the nodes whose `{cpn@var}` data
  dependencies have already finished (`_schedulable`, `agent/canvas.py:472-529`) at up to 5
  at a time, then appends successors from `downstream`, branch outputs `_next` or loop
  back-edges (`agent/canvas.py:869-897`). Cycles fail deterministically. A node whose
  input was never scheduled is dropped. Human-in-the-loop pauses by persisting `path` and
  returning a `user_inputs` event (`agent/canvas.py:904-920`).
- **The "ReAct agent" is native function-calling inside the LLM driver.** `Agent` binds
  tools, sub-agents and MCP tools to `LLMBundle` (`agent/component/agent_with_tools.py:78-117`).
  The loop itself is `Base.async_chat(_streamly)_with_tools` in `rag/llm/chat_model.py:616-927`:
  up to `max_rounds` (default 5) rounds, tool calls run in parallel with `asyncio.gather`,
  and results are appended verbatim. The older explicit planner (`analyze_task_async`,
  `next_step_async`, `reflect_async`, `rank_memories_async`, `tool_call_summary`,
  `rag/prompts/generator.py:439-524`) and `Canvas.add_memory` have **no callers**, so that
  code is dead.
- **Multi-agent means agents used as tools.** A lead `Agent` lists child `Agent` components
  in `params.tools`. Each child exposes the schema `{user_prompt, reasoning, context}`
  (`agent_with_tools.py:46-64`) and gets the id `parent-->child`. The shipped
  `deep_research.json` template is a lead with 3 sub-agents (search, reader, synthesizer).
- **"Memory" is two unrelated things.**
  1. `Canvas.memory` is a dead list of tool-call summaries.
  2. The new `memory/` subsystem is an LLM-extracted, embedded, per-tenant store in the doc
     engine (ES, Infinity, OceanBase or GaussDB). A raw turn plus 0..n extracted items are
     typed semantic, episodic or procedural, carry `valid_at`/`invalid_at`/`forget_at`/
     `status`, and are subject to a byte budget with FIFO eviction. The `Message` component
     writes asynchronously through a `memory` task on the ingestion queue, and the
     `Retrieval` tool reads through a weighted text+vector fusion. There is no
     dedup/reconciliation or update-in-place in Python. `invalid_at` is stored but never
     filtered on.
- **Context engineering is simple and partly lossy.** `message_fit_in`
  (`rag/prompts/generator.py:69-137`) either keeps everything or collapses to
  `[system, last user]`. If that is still over budget, it trims the system prompt when it
  holds more than 80% of the tokens and the last message otherwise. Chat sends the
  whole conversation history with no window (`conversation_service.py:274-280`). Agents
  window the history to `2 × message_history_window_size`. Tool results are **never**
  truncated or summarized between tool-loop rounds, and fitting happens once, before the
  loop.
- **Provenance has real defects.**
  - Chat numbers knowledge blocks from 1 (`generator.py:176`) but resolves `[ID:n]` from 0
    (`dialog_service.py:881-888`, `web/.../reference-utils.ts:79-80`). The Go port
    documents this at `internal/service/kb_prompt.go:92-95`.
  - Agent flows cite by `sha1(chunk_id) % 500` (`generator.py:157`, `canvas.py:1096`).
    The chance of a birthday collision is 32% at 20 chunks and 59% at 30.
- **MCP.**
  - RAGFlow as a server (`mcp/server/server.py`) is a thin REST proxy with 3 tools, SSE plus
    stateless streamable-HTTP transports, and 2 auth modes. The tool descriptions embed the
    caller's live dataset list.
  - As a client, tool schemas are snapshotted at registration
    (`MCPServer.variables["tools"]`) and copied into the agent DSL. Only the first
    `TextContent` of a result is used, and SSRF DNS pinning applies at registration but not
    at run time.
- **Observability.**
  - Per-node SSE events (`node_started` with a "thoughts" teaser; `node_finished` with
    inputs, outputs, error and elapsed time).
  - Tool traces in Redis with a 10-minute TTL.
  - A per-run token sink held in a ContextVar.
  - Optional Langfuse per tenant.
  - `TenantLLM.used_tokens`, `API4Conversation.tokens`, `duration` and `thumb_up` exist in
    the schema but **nothing in Python writes them**.
- **Failure handling.**
  - Components turn exceptions into `_ERROR`, a default value, or an exception `goto`
    branch.
  - The `@timeout` decorator is a no-op unless `ENABLE_TIMEOUT_ASSERTION` is set
    (`common/connection_utils.py:50-55`).
  - LLM retry backoff is `base_delay * U(10,150)`, 20 to 300 s per retry
    (`chat_model.py:346-347`).
  - A retryable error inside the tool loop restarts the **entire** loop and re-executes
    tools that already ran (`chat_model.py:645-647`).

---

## 1. Agent architecture

### 1.1 DSL (the canvas is data)

The shape is `agent/canvas.py:50-88`, which `load()` and `__str__` enforce:

```
{ "components": { "<cpn_id>": { "obj": {"component_name": "...", "params": {...}},
                                "downstream": [...], "upstream": [...], "parent_id": "<loop/iteration id>" } },
  "history":  [(role, content|output-dict), ...],     # conversation, unbounded
  "path":     ["begin", ...],                         # execution worklist = resumable cursor
  "retrieval":[{"chunks": {hashid: chunk}, "doc_aggs": {doc_name: agg}}, ...],  # per-turn citation pool
  "globals":  {"sys.query","sys.user_id","sys.conversation_turns","sys.files","sys.history","sys.date", "env.*"},
  "variables":{name: {type, value}},                  # typed env.* defaults (canvas.py:409-429)
  "memory":   [],                                     # dead (no writer)
  "graph":    {"nodes":[...], "edges":[...]}          # UI layout; also used for display names (canvas.py:186-191)
}
```

- **Registry by reflection.** `component_class(name)` searches `agent.component`,
  `agent.tools` and `rag.flow` for a class of that name (`agent/component/__init__.py:53-60`).
  Params are loaded from `<Name>Param`, merged with `update()` (recursion capped at
  `PARAM_MAXDEPTH=5`, `agent/settings.py`) and checked with `check()`
  (`canvas.py:113-124`). The same resolver is how the ingestion pipeline (`rag.flow`)
  reuses the canvas machinery.
- **Legacy migration.** `normalize_chunker_dsl` (`agent/dsl_migration.py:22-60`) is a pure
  rename step (`Splitter` becomes `TokenChunker`, for example) that runs on every load
  (`canvas.py:95`).
- **Serialization writes the whole state back, outputs included.** `Graph.__str__`
  (`canvas.py:126-158`) and `Canvas.__str__` (`376-380`) dump every component's params
  (with `outputs[k].value`) plus history, path and retrieval. That string becomes
  `API4Conversation.dsl` after every turn (`canvas_service.py:424`,
  `agent_api.py:342`), so a session row is a full state snapshot.

### 1.2 Component contract (`agent/component/base.py`)

- **Parameters.** Common fields on `ComponentParamBase` (`base.py:57-67`):
  `message_history_window_size=13`, `max_retries=0`, `delay_after_error=2.0`,
  `exception_method` (`"comment"` means use a default value; otherwise `goto`),
  `exception_default_value`, `exception_goto`, `inputs`, `outputs`, `debug_inputs`.
  `validate()` (`215-238`) reads a `param_validation/` directory that does not exist and
  has no callers, so it is dead.
- **Outputs.** Stored as `outputs[key] = {"value": v, "type": str(type(v))}`
  (`base.py:501-509`). Reserved keys are `_ERROR`, `_created_time`, `_elapsed_time`,
  `_next` (branch targets), `_references` and `_ARTIFACTS`.
- **Variable references.** `variable_ref_patt` accepts `cpn_id@var[.path]`, `sys.*` and
  `env.*`, with an optional balanced double brace (`base.py:43-53`, `376-379`).
  `get_variable_value` walks dotted paths through dicts, lists and JSON strings
  (`canvas.py:232-282`). `get_input_elements_from_text` records `_cpn_id` and `_retrieval`
  for each reference (`base.py:583-613`). **Scheduling dependencies are derived from these
  references** (`get_dependency_ids`, `base.py:623-628`, plus `param_refs()` overrides such
  as `VariableAggregator`, `agent/component/variable_aggregator.py:57-58`), not from the
  `upstream` edges.
- **Invocation.** `invoke`/`invoke_async` (`base.py:451-495`) stamp times, consume the
  scheduler's "unrunnable" verdict (`404-435`) and catch every exception into `_ERROR` or
  the default value. Tools (`agent/tools/base.py:163-204`) instead **return `str(e)` as the
  tool result**, so the LLM sees the error text and can react.
- **Streaming by deferred partial.** LLM and Agent set `outputs.content` to a
  `functools.partial` generator only when *every* downstream node is a `Message`
  (`llm.py:472-488`, `agent_with_tools.py:251-255`). The `Message` node consumes the stream
  (`message.py:197-265`) and the canvas relays deltas as `message` events.

### 1.3 Component catalogue (Python)

| Component | Mechanism | Evidence |
|---|---|---|
| Begin / UserFillUp | form inputs → outputs; file inputs parsed via `FileService`; UserFillUp clears stale answers so loops re-prompt | `begin.py:36-67`, `fillup.py:60-113` |
| LLM | prompt template with refs, history window, `sys.files` text/image auto-injection, vision model switch, structured-output (JSON schema prompt + `json_repair` + retries) | `llm.py:300-367`, `426-514` |
| Agent | LLM + tools/MCP/sub-agents; see §1.4 | `agent_with_tools.py` |
| Categorize | LLM classification; winner = category name with most substring occurrences in the answer; fallback = last category | `categorize.py:137-150` |
| Switch | deterministic condition table (`contains`, `=`, `≥`…) with and/or; `_next` targets; ELSE required | `switch.py:61-133` |
| Iteration / IterationItem | sequential for-each over a list ref; item/index outputs; collation of child outputs into parent arrays; `max_concurrency` param is **unused** | `iteration.py:28-65`, `iterationitem.py:37-95` |
| Loop / LoopItem / ExitLoop | loop variables; termination conditions evaluated after each pass; `maximum_loop_count` cap | `loop.py:74-97`, `loopitem.py:36-152` |
| Message | template or Jinja2 sandbox render; streams upstream partials; optional pandoc export (md/html/pdf/docx/xlsx) to storage; **writes to Memory** | `message.py:283-315`, `415-586`, `588-601` |
| VariableAggregator / Assigner, DataOperations, ListOperations, StringTransform, ExcelProcessor, DocsGenerator, Browser | data plumbing / document generation | `agent/component/*.py` |
| Retrieval (tool) | dataset hybrid retrieval (+TOC, children, KG, metadata filter, cross-language) **or** memory retrieval | `agent/tools/retrieval.py:127-323` |
| CodeExec, ExeSQL, web/search/finance tools | tool components; see §11 for sandbox. The full list: akshare, arxiv, bgpt, code_exec, crawler, deepl, duckduckgo, email, exesql, github, google, googlescholar, jin10, keenable, pubmed, querit, qweather, retrieval, searxng, sofya, tavily, tushare, wencai, wikipedia, yahoofinance, youcom. Web tools funnel results through `ToolBase._retrieve_chunks`, which adds them to the canvas citation pool (`tools/base.py:206-224`). **There is no `agent/tools/mcp_tool.py`**: MCP tools are bound directly inside `Agent.__init__` (§1.4). | `agent/tools/*.py` |
| LLM tool plugins | `pluginlib` plugins loaded at server start (`api/ragflow_server.py:140`) and listed by `plugin_api.py`; only a sample `bad_calculator` ships. `get_llm_tools_by_names` has no caller, so plugins are **not** wired into Agent tool loading | `agent/plugin/plugin_manager.py:16-43` |

### 1.4 The Agent component and tool loop

- **Tool assembly** (`agent_with_tools.py:78-117`):
  - Component tools are loaded as `<name>_<idx>`.
  - MCP tools are `MCPToolBinding(session, original_name)`, built from the **DSL snapshot**
    `mcp[i]["tools"]`.
  - The OpenAI schema comes from `ToolParamBase.get_meta()` (`tools/base.py:130-147`) or
    `mcp_tool_metadata_to_openai_tool` (`common/mcp_tool_call_conn.py:582-600`).
  - The session is `LLMToolPluginCallSession(tools, callback, default_timeout=tool_timeout=10)`
    (`tools/base.py:50-106`). The timeout is **only forwarded to MCP calls**; component
    tools run unbounded. `test_llm_tool_plugin_session.py:50-72` asserts only the MCP case.
- **Run path** (`agent_with_tools.py:196-298`):
  - With no tools, the Agent falls back to `LLM._invoke_async`.
  - When the Agent is itself used as a tool, the prompt is composed as
    `REASONING / CONTEXT / QUERY` (`211-221`).
  - When streaming to a `Message`, `stream_output_with_tools_async` does the following
    (`300-366`):
    1. Rewrites the query with `full_question` once history exceeds 3 messages (`301-305`).
    2. Fits the messages.
    3. Injects the citation prompt when the canvas holds references and fewer than 7
       messages exist (`322-326`); otherwise it buffers the whole answer and runs a second
       LLM pass, `citation_plus` over `kb_prompt` of the pool, to add citations (`354-373`).
  - Structured output is JSON-schema-prompted, re-forced up to `max_retries` times
    (`277-291`).
  - Tool artifacts (`_ARTIFACTS`, such as images and files from code execution) are
    appended as markdown (`375-394`).
- **Loop** (`rag/llm/chat_model.py:616-729`; streaming at `731-927`):
  1. Run `for _ in range(max_rounds+1)`.
  2. Call completions with `tools, tool_choice="auto"`.
  3. With no tool call, return the content (an empty answer is an error, `657-662`).
  4. Otherwise run `asyncio.gather(_exec_tool…)`. Arguments are parsed with `json_repair`
     and must be a dict (`674-690`).
  5. A "terminal tool" (`rag` by default) short-circuits (`691-707`).
  6. Else append one assistant message holding all tool calls plus one `tool` message per
     result (`_append_history_batch`, `546-575`).
  7. After `max_rounds`, append the user message `"Exceed max rounds: N"` and make one
     final completion call (`712-723`).
  8. The streaming variant emits `<think>Running the X tool...</think>` markers
     (`863-870`).
- **Traces.** Every tool call goes through `Canvas.tool_use_callback`
  (`canvas.py:1072-1088`). It appends
  `{path:"Lead-->Sub", tool_name, arguments, result, elapsed_time}` to Redis
  `{task_id}-{message_id}-logs` with a TTL of **600 s**.

**S.**
- The DSL is plain data with a reflective registry, and one resolver serves agents and the
  ingestion pipeline.
- Readiness is computed from actual data references, which prevents the classic "join
  runs before the slow branch" bug. `test_canvas_batch_readiness.py:314-434` pins this
  down, cycles included.
- Branching, loops and human-in-the-loop are all expressed as edits to one path list, so
  pause/resume is simply "persist the path".
- Tool errors come back to the model as text instead of crashing the run.
- Sub-agents-as-tools composes without any special orchestration.

**W.**
- **State and definition are mixed.** A session's DSL carries params, outputs and history,
  so every turn rewrites a growing JSON blob.
- The scheduler window is fixed at 5 (`ThreadPoolExecutor(max_workers=5)`,
  `canvas.py:99, 615-616`), and components block the event loop through `asyncio.run`
  inside `_invoke` wrappers (`agent_with_tools.py:192-193`, `llm.py:516-518`).
- Branch selection by substring counting (`categorize.py:138-147`) is fragile.
- `Iteration.max_concurrency` is dead.
- The tool loop has no per-round context fitting, no result truncation and no dedup of
  repeated calls. A 5-round loop with 200k-token retrieval outputs
  (`retrieval.py:261`, `tools/base.py:224` pass a 200 000-token budget) can overflow the
  model context.
- The timeout on component tools is not enforced.

**EOS.**
- *Transfers:*
  - **DSL-as-data procedures with typed step I/O and `{step@field}` references.** EOS
    procedures are ordered steps today. Adding declared inputs and outputs per step lets
    EOS *lint* a procedure (unknown reference, cycle, never-scheduled step) exactly the way
    `_schedulable` does, with no LLM and no execution.
  - **The event vocabulary.** `workflow_started`, `node_started`, `node_finished`
    (inputs, outputs, error, elapsed), `user_inputs`, `workflow_finished` (usage) map onto
    EOS `run event` kinds.
  - **"Pause = persist cursor + pending inputs"** maps onto blocked work items.
  - **"Tool error returns as result text"** matches EOS returning NOOP/UNKNOWN rather than
    raising.
- *Does not transfer:* the LLM-in-the-loop tool loop, and streaming partials.

---

## 2. Task decomposition and planning

- **Explicit planner: dead code.**
  - `rag/prompts/generator.py:407-524` still defines `tool_schema(..., complete_task)`,
    `analyze_task_async` (prompt `analyze_task_system.md`), `next_step_async`
    (`next_step.md`, "call `complete_task` when ready"), `reflect_async` (`reflect.md`,
    "Observation / Reflection"), `tool_call_summary` (`summary4memory.md`) and
    `rank_memories_async` (`rank_memory.md`).
  - A repo-wide search shows no callers (only definitions and `LLM.add_memory`, which is
    itself uncalled, `llm.py:538-541`).
  - `ReActMode {FUNCTION_CALL, REACT}` (`chat_model.py:63-65`) is unused.
  - What survives is `_extract_prompts` (`llm.py:369-377`). It strips
    `<TASK_ANALYSIS>`, `<PLAN_GENERATION>`, `<REFLECTION>`, `<CONTEXT_SUMMARY>`,
    `<CONTEXT_RANKING>` and `<CITATION_GUIDELINES>` blocks from a system prompt, but only
    `citation_guidelines` is consumed (`generator.py:226-233`).
- **Planning today is prompt-level plus hierarchy.** The `deep_research.json` template
  (`agent/templates/deep_research.json`) is a "Strategy Research Director" lead Agent
  (`max_rounds=3`, `message_history_window_size=12`) whose system prompt describes stages.
  Its tools are three child Agents: Web Search Specialist (TavilySearch, `max_rounds=1`),
  Content Deep Reader (TavilyExtract, `max_rounds=3`) and Research Synthesizer
  (`max_rounds=3`). The supervisor's "plan" lives only in its own LLM turn; there is no plan
  object, no checklist state and no re-planning hook. Citation is disabled for sub-agents
  (`agent_with_tools.py:322`, `self._id.find("-->") < 0`).

### 2.1 The real planner: agentic RAG for chat ("thinking modes", `rag/advanced_rag/`)

This is where RAGFlow's current decomposition, reflection and deep-research logic lives. It
is reached from chat through `dialog_service.rag_agent` (`dialog_service.py:2018-2175`),
which binds `rag_tools.tools` and marks `rag` as terminal (`2175-2181`). It is not reached
from the agent canvas.

- **A LangGraph state machine.** It imports
  `from langgraph.graph import END, START, StateGraph` (`agentic_rag_graph.py:60`). The
  state `AgenticState` (TypedDict, `188-229`) holds `question`, `keywords`, `plan`,
  `current_queries`, `slot_table`, `slot_draft`, `unresolved_slots`, `research_feedback`
  (the `add_messages` reducer), `kbinfos`, `draft`, `verdict`, `sca`, `deadline`,
  `search_rounds`, `attempted`, `fills_found`, `no_progress` and a few more.
  - `build_low_graph` (`1256-1293`) is linear: formalize → direct search → answer.
  - `build_agentic_graph` (`1296-1781`) runs `formalize_question → [planner/fan-out] →
    prefetch → rag_agent (slot research) → draft → sca → query_rewrite ↺ →
    formalize_answer`.
  - Routing is `_route_sca` (`1712-1737`) and `_route_rewrite` (`1739-1747`). They exit on
    `no_progress`, on pool saturation (60 chunks), on rounds reaching `sca_max_rounds`, or
    when less than 50 s of the budget remains.
- **Budgets as constants** (`75-81`): total 180 s, 50 s headroom per round, pass 120 s,
  prefetch 90 s, draft 60 s, SCA 60 s, rewrite 45 s. They are enforced by `_bounded()`,
  which returns `None` on expiry (`178-186`), with `recursion_limit=60` as a backstop
  (`2470`).
- **Modes** (`harness/config.py:44-120`, `ModeSpec{agentic, enable_sca, sca_max_rounds,
  use_fanout, action_max_turns, tools}`):
  - `low`: no agent, no tools.
  - `medium`: SCA up to 3 rounds, no fan-out.
  - `high`: adds the planner and fan-out.
  - `ultra`: 5 SCA rounds, 6 action turns, adds `graph_explore`.
  - `NAIVE`: fallback, one retrieve plus one compose.
- **Planner and decomposition.**
  - `_expand_fanouts` (`461-527`) is one strict-JSON LLM call.
  - Sub-questions form a **slot table**: unresolved gaps become new slot `Variable`s
    (`1658-1683`), capped at `_MAX_SLOT_DEPTH=3` and `_MAX_SLOTS_TOTAL=8`.
  - Reflection is the SCA ("sufficient context") judge, `sca_select.md`. It returns a
    structured `sub_queries[{sub_query, satisfied, missing_fact, search_hint}]` consumed by
    `_sca_gaps_to_rewrite` (`309-348`), not free text.
- **Action session** (`harness/action_session.py`): one bounded LangGraph sub-graph per slot
  (`2027-2044`).
  - Native tool calling over 9 tools (`_TOOL_MAP`, `1405-1445`: retrieve, search_chunks,
    list_chunks, navigate_tree, navigate_structure, calculate, graph_explore, web_search,
    metadata_search).
  - Up to 4 turns, or 6 in ultra (`1986-1990`).
  - Near-duplicate calls are suppressed at Jaccard 0.8 (`69-87`), with a same-call cache.
  - A **400 000-character context budget** truncates payloads (`42`, `1823-1825`).
  - `REDUNDANT` hits collapse to one line (`1806-1815`). A tool is disabled after 2 empty
    results (`48`, `1797-1805`).
  - Each result carries `ToolOutcome.status ∈ {OK, EMPTY, MISS, POOR, REDUNDANT, ERROR}`
    (`168-173`) and `evidence_ids` (`176-194`).
  - On budget exhaustion: one forced tools-off call, then a deterministic "loose clue
    harvest" so the round never returns empty (`1894-1960`).
- **Evidence and citations.**
  - The final answer comes from `_compose_answer_from_evidence`
    (`agentic_rag_graph.py:1071-1250`).
  - Its citation pool is slot-cited chunks, plus up to 4 table chunks, plus the
    top-similarity chunks, deduplicated and capped at `_CITE_CHUNK_CAP=12`.
  - Evidence gets a fixed `_EVIDENCE_BUDGET_TOKENS=8000` whatever the model's context size
    (`agentic_rag.py:772-787`).
  - `[ID:Slot N]` and range markers are rewritten to real ids (`agentic_rag.py:224-272`).
- **Deterministic narrowing** (`harness/grep_sed_narrow.py`). `narrow_by_terms`
  (`245-382`) behaves like `grep -n -C N` over chunk text:
  - Word-boundary regexes, CJK-aware.
  - Whole-line spans.
  - 600-character context, 1 200 characters per chunk, 16 000 in total.
  - A fallback chain.
  - **Tables are never narrowed**, because row position decides table answers.
- **The "memory" ledger is dead.** `harness/memory.py` `add()` deduplicates and appends
  every retrieved chunk, but `grep()` and `search()` (a term-overlap ratio of at least 0.12
  over at most 18 significant terms plus CJK 3-grams, `360-393`) have **no callers**. A
  repo-wide search finds only `add` and `_STOPWORDS` imported, from `harness/tools/search.py`
  (`181`, `243`, `281`) and `agentic_rag_graph.py:716`. The prompt instead receives
  `_dump_pool_evidence`, which is the whole pool (`action_session.py:2400-2423`).
- **Trace plumbing.** `think_log.py:46-83` forwards `"["`-prefixed INFO log lines from
  `rag.advanced_rag` and the chat model to a per-request ContextVar sink that becomes the
  SSE `<think>` stream. `stats.py` counts calls, tokens and wall-clock per phase and round
  (`30-41`, `CountingChatModel` `391-449`), but only logs them.

**S.**
- An inspectable graph with a budget on every edge and 4 independent stop conditions.
- A structured sufficiency verdict aims the rewriter precisely.
- A status taxonomy per tool result.
- A mechanical grep/sed narrowing step with a measured table carve-out.

**W.**
- 2 500-line modules with shared mutable `tools.kbinfos`.
- Comments that contradict the code (prefetch "DISABLED" while `use_prefetch = use_fanout`,
  `1760-1767`).
- A dead recall path.
- A character count used as the token budget.
- The whole pool is dumped into every slot prompt.
- The UI trace is coupled to the *wording* of log messages.
- Tests (`test/unit_test/rag/advanced_rag/`, 9 files, 883 lines) cover tool-schema contracts
  but not graph routing or termination, narrowing, or memory.

**EOS (agentic RAG).**
- *Transfers directly, all of it deterministic:*
  1. **`narrow_by_terms`** as EOS's evidence narrowing for large files. It is stdlib `re`,
     already takes terms as input, and has the table carve-out.
  2. **The `ToolOutcome.status` vocabulary** (`ok`, `empty`, `miss`, `poor`, `redundant`,
     `error`) for wrapper results and run events.
  3. **Near-duplicate suppression by Jaccard** for repeated queries in a session.
  4. **A budgeted, phase-structured control flow** (brief → gather → coverage check →
     targeted re-query) with an explicit deadline per phase.
  5. **A deterministic coverage check**: `memory.search()`'s term-overlap ratio applied to
     each declared sub-question, as a stand-in for the LLM SCA judge.
  6. **The "no ghost args" schema test** (`test_action_session_schema.py`): every
     documented flag is actually consumed.
- *Does not transfer:* SCA judgment, fan-out generation, weighted keyword extraction. All
  are LLM calls.

**S (canvas-level planning).**
- Deleting the bespoke plan/reflect loop in favour of native tool calling removed three
  extra LLM calls per step.
- The sub-agent tool schema (`reasoning`, `context`, `user_prompt`) forces the supervisor
  to hand over a self-contained brief.

**W (canvas-level planning).**
- There is no durable plan state and no step-level success signal, so a run's "plan"
  cannot be inspected, resumed or evaluated.
- The dead planner code and prompts still ship, which misleads readers (the README-level
  "reflection" claims describe code that no longer runs).

**EOS (canvas-level planning).**
- *Transfers:*
  - **The sub-agent handoff schema** `{user_prompt, reasoning, context}` is a good shape
    for EOS briefs handed to sub-agents: *why* the step exists, *what is known*, and *what
    to do*.
  - The negative lesson: keep plan state as data (EOS work items and procedure steps), not
    in a model's hidden turn.
- *Does not transfer:* LLM planning and reflection.

---

## 3. Memory

### 3.1 The "Memory" feature (new `memory/` package + `api/db/joint_services/memory_message_service.py`)

- **Config row.** `Memory` (`api/db/db_models.py:1816-1838`) has these fields:
  - `memory_type`: bit flags, 1=raw, 2=semantic, 4=episodic, 8=procedural
    (`common/constants.py:241-245`).
  - `storage_type`: `table|graph`; graph is a UI facet only
    (`memory_api_service.py:313`).
  - `memory_size`: default 5 242 880 bytes.
  - `forgetting_policy`: default FIFO. The enum has **only FIFO** (`constants.py:253-254`)
    even though the help text says `LRU|FIFO`.
  - `embd_id`/`llm_id`, `temperature`=0.5, editable `system_prompt`/`user_prompt`,
    `permissions` me|team.
- **Message schema.** Stored in the doc engine index `memory_{uid}` with doc id
  `{memory_id}_{message_id}` (`memory/services/messages.py:29-54`). Fields:
  - `message_id`, an int from Redis `INCR id_generator:memory`
    (`memory_message_service.py:301-314`).
  - `message_type`: `raw|semantic|episodic|procedural`.
  - `source_id`: 0 for raw; the raw id for extracted items.
  - `memory_id`, `user_id`, `agent_id`, `session_id`, `content`, `content_embed`
    (`q_<dim>_vec`), `tokenized_content_ltks`.
  - `valid_at`, `invalid_at`, `forget_at`, `status` (0/1).
  - The Infinity schema (`conf/message_infinity_mapping.json`) adds float shadows
    `valid_at_flt`, `invalid_at_flt` and `forget_at_flt`, plus `zone_id`, so the store can
    range-filter on validity. No query does.
- **Write path** (asynchronous, two phase):
  1. When a `Message` component finishes, it calls `_save_to_memory`
     (`agent/component/message.py:588-601`) with
     `{user_id, agent_id=canvas id, session_id=task_id, user_input=sys.query,
     agent_response=final text}`.
  2. `queue_save_to_memory_task` (`memory_message_service.py:376-442`) **synchronously
     embeds and stores the raw turn**. It then inserts a `Task(task_type="memory",
     digest=str(raw_id))` and pushes it to the ingestion queue at priority 0.
  3. The task executor calls `handle_save_to_memory_task` (`445-478`;
     `rag/svr/task_executor.py:1442-1444`), then `extract_by_llm` (`162-207`).
  4. `extract_by_llm` uses a single LLM call with `PromptAssembler`
     (`memory/utils/prompt_util.py:24-190`). The output is JSON
     `{"semantic":[{content, valid_at, invalid_at}], "episodic":[…], "procedural":[…]}`,
     at most 5 items per type (`prompt_util.py:132`). It is parsed by stripping code fences
     and `json.loads`; a failure yields `{}` (`memory/utils/msg_util.py:19-37`).
  5. `embed_and_save` (`210-258`) embeds all items and creates the index lazily with the
     vector width. When `current + new > memory_size`, it evicts through
     `pick_messages_to_delete_by_fifo` (forgotten messages first, then oldest by
     `valid_at`, `messages.py:218-261`) and inserts. Size accounting is `sys.getsizeof`
     over the content plus the vector, cached in Redis `memory_{id}` with INCRBY/DECRBY
     (`messages.py:179-183`, `memory_message_service.py:317-340`).
  6. Progress is written to the Task row at 0.15, 0.35, 0.5, 0.65, 0.85, 0.95 and 1.0.
- **Read path.** `Retrieval` with `memory_ids` (`agent/tools/retrieval.py:269-303`) calls
  `query_message` (`memory_message_service.py:261-298`):
  - `MsgTextQuery` (`memory/services/query.py:43-169`) does term weighting, synonym
    expansion at weight/4, and adjacent-bigram phrase boosts at 2×max weight.
  - A dense `MatchDenseExpr` is added, and the two are combined with
    `FusionExpr("weighted_sum", top_n, weights="kw,1-kw")`.
  - Filters: `status=1` (`messages.py:153-155`), messages with `forget_at` hidden
    (`memory/utils/es_conn.py:140-142`), optional `user_id`, ordered by `valid_at` desc.
  - Results are packed with `memory_prompt(..., 200000)` (`generator.py:188-198`): raw
    content only, no type, timestamps or ids.
- **Lifecycle APIs.** `forget_message` sets `forget_at` as a soft delete that is later
  evicted first (`memory_api_service.py:365-372`). `update_message_status` toggles
  `status` (`375-381`). `get_messages` returns the most recent entries by agent and session
  (`407-423`). Listing groups extracted items under their raw source
  (`messages.py:69-122`).
- **Scoping.** One index per tenant uid; filters by `memory_id`, `agent_id`, `session_id`
  and `user_id`; access by `permissions` (`memory_service.py:97-98`).

**S.**
- The schema is clean and typed: a raw turn is kept alongside derived facts, with
  `source_id` provenance.
- Bi-temporal intent (`valid_at`/`invalid_at`) plus soft-forget (`forget_at`) plus an
  enable flag (`status`).
- A hard byte budget with deterministic eviction that prefers already-forgotten items.
- Extraction runs off the request path as a queued, progress-reporting task.
- Prompts are user-editable per memory, with detection of "is this still the default
  prompt?" (`prompt_util.py:120-123`).

**W.**
- There is no reconciliation. Each turn adds new facts, and no ADD, UPDATE, DELETE or NOOP
  pass exists against existing memories, so contradictory facts accumulate.
- `invalid_at` is never used as a filter, so expired facts are still retrieved.
- FIFO is the only policy, and there is no recency or importance weighting in retrieval.
- The raw turn is embedded synchronously on the request path (`queue_save_to_memory_task`
  calls `embed_and_save` before queueing).
- Size is Python `sys.getsizeof`, not storage bytes.
- Ids come from Redis INCR, so a Redis flush leads to re-seeding from `max(message_id)`
  (`301-314`).
- The retrieved memories reach the prompt without dates or types, which throws away the
  temporal schema at the last step.

### 3.2 Conversation memory (chat and agent)

- **Chat.** `Conversation.message` (`db_models.py:1499-1508`) is one JSON list. Every turn
  passes the **entire** list to `async_chat` (`conversation_service.py:274-280`); only
  `system` messages and a leading assistant prologue are skipped. `async_chat` uses the
  last **3** user questions for query refinement (`dialog_service.py:657`,
  `full_question` if `refine_multiturn`, `736-739`) and sends all history.
  `message_fit_in(msg, 0.95*max_tokens)` (`840`) then drops **all** middle history if the
  total is over budget. There is no summarization, no sliding window and no memory
  retrieval in chat.
- **Agent.** `Canvas.history` holds `(role, content)`, where assistant turns are the last
  component's entire output dict (`canvas.py:934`). `get_history(n)` returns the last `2n`
  entries (`canvas.py:991-1000`). The default is n=13, so 26 messages; Categorize uses 1
  (`categorize.py:39`). `sys.history` is a parallel list of rendered strings `"role: …"`
  (`canvas.py:935, 1005`) that can be referenced in prompts. Both lists are unbounded and
  persisted inside the session DSL.
- **Run state.** `API4Conversation` (`db_models.py:1523-1541`) holds `message`,
  `reference` (a list per turn), `dsl` (the full canvas snapshot), `round` (incremented,
  `api_service.py:125-128`), `errors`, `version_title`. A resumed session rebuilds
  `Canvas(conv.dsl, task_id=session_id)` (`canvas_service.py:366-374`). A new session
  loads the agent DSL (or the latest *released* version, `canvas_service.py:338-354`) and
  calls `reset()` or `start_new_session()` (`canvas.py:382-387`).
- **Runtime replicas.** `CanvasReplicaService` keeps per-(canvas, tenant, runtime-user)
  working copies that are committed after each run (`agent_api.py:288-303`,
  `1607-1625`).
- The replica store is **Redis-only**, with key
  `canvas:replica:{canvas_id}:{tenant_id}:{runtime_user_id}` and a 3 h TTL.
  `commit_after_run` does an **unlocked read-modify-write**
  (`api/apps/services/canvas_replica_service.py:211-247`), while the explicit set path
  takes a `RedisDistributedLock` (`158-194`). Two concurrent runs by the same user can
  therefore overwrite each other's final replica.

**S.**
- Sessions are self-contained, since one row holds everything needed to resume, including
  a paused `UserFillUp` path.
- Released versus draft DSL versions (`UserCanvasVersion`) are separated cleanly.

**W.**
- Chat history is all-or-nothing under budget pressure: the model silently loses every
  prior turn at once.
- History and DSL blobs grow without bound and are rewritten whole on every turn.
- Two parallel history representations (`history` and `sys.history`) can drift.

<!-- SECTION:GO_MEMORY -->

**EOS for §3.**
- *Transfers directly to SQLite:*
  1. **The typed memory row**: `kind ∈ {raw, semantic, episodic, procedural}` plus
     `source_id` linking derived facts to the raw event, which EOS already does as
     lesson→execution, plus
     `valid_at / invalid_at / forget_at / status`. EOS should adopt `invalid_at` (a
     superseded note) and `forget_at` (a soft delete) as columns, and **filter on them** in
     brief and search, which RAGFlow forgets to do.
  2. **A byte or token budget per store with deterministic eviction** ("forgotten first,
     then oldest").
  3. **Showing type and dates when injecting memories**, the opposite of RAGFlow's
     `memory_prompt`.
  4. **Bigram phrase boosts and synonym expansion at reduced weight** in the text query
     (`query.py:70-94`). This is deterministic and could lift EOS note recall@1, which is
     0.67 in the local `note-eval.txt`.
  5. **Default-prompt detection by permutation** (`prompt_util.py:120-123`) is a neat
     trick for "is this config still the shipped default?"
- *Does not transfer:* LLM extraction and embeddings (EOS ADR-001/010). EOS's equivalent
  of "extraction" is the harness writing `eos note add` or `eos run finish --lesson`; the
  RAGFlow prompt is still a good *checklist* for what a lesson should contain (a time-bound
  episode versus a timeless fact versus a procedure with a validity window).

---

## 4. Context engineering

### 4.1 Budget primitives (`rag/prompts/generator.py`, `common/token_utils.py`)

- **One tokenizer for every model**: `tiktoken cl100k_base` (`token_utils.py:55-61`,
  `144-153`). Provider-specific tokenizers are never used.
- **`message_fit_in(msg, max_length)`** (`generator.py:69-137`):
  1. If the total is under `max_length`, keep everything.
  2. Otherwise keep only the system messages and the last message.
  3. If still over, and the system prompt holds more than 80% of the tokens, keep the last
     message whole (up to the budget) and trim the system prompt to the remainder.
  4. Otherwise keep the system prompt and trim the last message.

  Trimming is a head-truncation of tokens, so the *tail* of a long system prompt, often the
  knowledge block, is cut.
- **`kb_prompt(kbinfos, max_tokens, hash_id)`** (`140-185`) renders each chunk as a tree
  block: `ID`, `Title`, `URL`, `document_metadata` k/v, then `Content`. It budgets the
  **complete block** including the decoration, stops at 97% of `max_tokens`, and skips
  empty chunks without shifting numbering (`test_kb_prompt_metadata.py:94-151`).
- **`memory_prompt`** (`188-198`) budgets content only, at 97%.
- **Agent budget.** `LLM.context_fit_budget = 0.97 × (model max_tokens or 8192)`
  (`llm.py:141-147`). `validate_fitted_messages` rejects results with fewer than 2 messages
  or an empty final user message (`149-156`).
- **Chat budget.** Knowledge is packed with `kb_prompt(kbinfos, max_tokens)`, which is the
  **entire** model context (`dialog_service.py:799`). The whole message list is then fitted
  to 95% (`840`). Output `max_tokens` is reduced by the used count (`852-853`), but
  OpenAI-compatible backends then delete `max_tokens` from `gen_conf` anyway
  (`chat_model.py:370-375`).

### 4.2 Prompt assembly

- **Chat** (`dialog_service.py:594-994`), in order:
  1. Metadata filter to `doc_ids`, where an LLM may generate the filter
     (`generator.py:527-568`).
  2. Optional text-to-SQL shortcut (`685-717`).
  3. Parameter checks.
  4. `full_question` multi-turn rewrite (`generator.py:271-304`, which resolves relative
     dates to absolute dates).
  5. `cross_languages` (`307-333`, which joins translations with `===`).
  6. `keyword_extraction` appended to the query (`744-745`).
  7. Hybrid retrieval, with optional web search, KG chunk and TOC enhancement (`752-789`).
  8. Optional metadata enrichment.
  9. `system.format(knowledge=…)`, with auto-append when `{knowledge}` is missing
     (`828-832`).
  10. Citation prompt appended to the system prompt (`834-836`, `965`).
  11. The full history.
  12. `message_fit_in`.

  `decorate_answer` then appends a per-stage timing and token breakdown to the returned
  `prompt` (`903-926`).
- **Agent LLM node** (`llm.py:300-367`): the system prompt, with `{refs}` substituted, plus
  the history window minus the current turn, plus the configured prompts (merged into the
  last message when roles match, `_sys_prompt_and_msg`, `128-139`). `sys.files` text is
  auto-merged into the last user message unless the template references `{sys.files}`.
  Images are auto-routed to a vision model. The citation prompt is added when the canvas
  already holds references.
- **Ingestion-time helpers** (`keyword_extraction`, `question_proposal`, `content_tagging`,
  `gen_metadata`, TOC extraction; `generator.py:241-268`, `336-384`, `594-990`) produce
  chunk-side context: keywords, questions, tags, metadata and TOC. The other researcher
  covers them; they matter here only because `toc_enhance` and metadata filters reuse
  their outputs at query time.
- **Tool results.** They are appended verbatim to the tool history
  (`chat_model.py:567-574`). The only compression is `ToolBase._retrieve_chunks`, which cuts
  each web result at 10 000 characters and strips base64 images (`tools/base.py:206-224`).
  Retrieval tools emit `kb_prompt` blocks with a 200 000-token budget, so effectively no
  budget. `tool_call_summary` (compression by LLM) exists but is dead (§2).

**S.**
- Block-level budgeting that counts every byte the model sees.
- Stable rendering that skips empty chunks.
- Relative-date normalization in the query rewrite.
- A per-stage latency breakdown in every chat answer.

**W.**
- A single global tokenizer.
- All-or-nothing history.
- Head-truncation of an over-long system prompt drops the knowledge block's tail without
  any signal.
- Knowledge and history compete for the same window, with no reserved shares (knowledge can
  take 100% of the context before history is even considered).
- No compression of tool results inside the loop.

**Agentic RAG context tactics (§2.1).**
- A fixed 8 000-token evidence budget with 1 024 tokens reserved for the template.
- A 48 000-character cap for SCA claims (`harness/orchestrator/sufficient_context.py:57`).
- A multi-turn rewrite done inline in `RAGTools.formalize`, applied only when the
  conversation has more than one turn (`agentic_rag.py:466-553`).
- Entity and qualifier keywords repeated 3× to weight BM25 (`harness/keywords.py:121-169`).
- A deduplicated, capped citation pool.
- These are more deliberate than the classic chat path, but still character- or
  constant-based rather than model-aware.

**EOS.**
- *Transfers:*
  1. **"Budget the whole rendered block, not just the payload"**, with a regression test.
     EOS `brief --task` targets 1500 tokens, so the same test shape applies: header and
     decoration counted, an empty entry does not shift numbering, and an overflow entry is
     excluded whole.
  2. **Reserved shares** per section: a lesson from RAGFlow's *absence* of them.
  3. **Deterministic relative-date normalization** of task text: "yesterday" becomes an
     ISO date, done with a regex table, no LLM.
  4. A **per-stage cost line** in brief output, which EOS already logs per command in
     `cost.txt`.
- *Does not transfer:* LLM rewrite, translation and keyword extraction.

---

## 5. Provenance in agent flows

- **Pool.** Each turn appends a fresh `{"chunks": {}, "doc_aggs": {}}` to
  `Canvas.retrieval` (`canvas.py:592, 605`). Every retrieval-type tool calls
  `add_reference` (`canvas.py:1090-1103`), which keys chunks by
  **`hash_str2int(chunk_id, 500)`**, that is `sha1 % 500` (`common/misc_utils.py`,
  `hash_str2int`). The render uses the same hash as the block `ID`
  (`generator.py:157`, `hash_id=True` from `retrieval.py:261` and `tools/base.py:224`).
  - Collisions are therefore silent overwrites. The birthday odds are 32% at 20 chunks,
    59% at 30 and 92% at 50.
  - The "already present" check tests `cid not in r` against the outer dict rather than
    `r["chunks"]`, so it is always true and the last writer wins (`canvas.py:1098-1103`).
- **Emission.** `message_end` carries `reference = retrieval[-1]` (`canvas.py:1116-1131`).
  The API layer merges references across events and appends one per turn to
  `API4Conversation.reference` (`agent_api.py:341`, `canvas_service.py:415-422`).
- **Citation generation.**
  - The prompt instructs the model to "cite `[ID:i]` individually, never ranges"
    (`citation_prompt.md`; suffix at `generator.py:228-233`).
  - With a long history (7 or more messages), the Agent buffers the answer and runs a
    second LLM pass (`citation_plus`) over the whole pool (`agent_with_tools.py:354-373`).
  - Sub-agents never cite.
- **Chat citation (post-hoc fallback).** If the model emitted no markers,
  `retriever.insert_citations` assigns them after the fact (`dialog_service.py:865-879`;
  `rag/nlp/search.py:423-499`):
  - It splits the answer into sentences, keeping fenced code blocks whole and skipping
    pieces under 5 characters.
  - It scores each sentence against every chunk with a hybrid of token overlap and vector
    similarity.
  - It cites up to 4 chunks whose similarity exceeds 0.99 × the sentence's best score,
    starting at a 0.63 threshold and decaying by ×0.8 down to 0.3 until something is cited.
  - It emits **0-based** ` [ID:c]` markers.
  `repair_bad_citation_formats` normalizes `(ID: 12)`, `【ID:12】`, `ref12` and similar
  forms (`530-583`). `doc_aggs` are narrowed to the documents actually cited
  (`888-892`).
- **Off-by-one defect.** `kb_prompt` numbers blocks from 1 (`len(out) + 1`,
  `generator.py:176`). The resolvers index from 0: `kbinfos["chunks"][int(i)]`
  (`dialog_service.py:881-888`) and `reference.chunks[chunkIndex]`
  (`web/src/components/markdown-content/reference-utils.ts:79-80`). The Go port's comment
  records this: "Python's kb_prompt numbers the blocks 1-based while its own resolver reads
  0-based; Go keeps both on 0" (`internal/service/kb_prompt.go:92-95`). Markers from
  `insert_citations` are 0-based and resolve correctly. Markers *the model writes*, which
  follow the 1-based `ID:` labels in the prompt, point at the next chunk. When every chunk
  was rendered, `[ID:n]` for the last block equals `len(chunks)` and is dropped by the
  `i < len(...)` guard. The EOS matrix below adds a lexical-only variant of
  `insert_citations`.

- **The Go port's citation pipeline is the corrected design** (`internal/service/citation.go`):
  - **Numbering.** Blocks are numbered from 0 (`kb_prompt.go:92-95`).
  - **Resolve or drop.** `ResolveCitationMarkers` (`516-562`) maps each rendered position
    to a pool index through `citeIdx`, where -1 means the chunk could not be located. It
    **deletes canonical `[ID:n]` markers that resolve to nothing**, but leaves a bare `[5]`
    alone because it might be a year or a footnote (`564-568`).
  - **Observability.** `rawCitationMarkers` logs what the model actually wrote *before*
    repair (`570-593`).
  - **No-answer replies.** `reportsNoAnswer` and `decorateQuote` suppress citations and the
    document list on a "not found in the knowledge base" reply (`595-634`).
  - **Streaming.** `citationStreamFilter` holds an unfinished marker back until its closing
    bracket arrives when it strips citations from a stream (`480-514`).
  - **Repair.** `RepairBadCitationFormats`, `RepairSlotCitations` and
    `ExpandRangeCitations` (`350-478`).
  - **Fallback.** `InsertCitations` mirrors the Python fallback (`55-104`).

**S.**
- Citations resolve to a structured reference object (chunk id, document id and name,
  dataset, positions, similarity: `chunks_format`, `generator.py:41-66`).
- A deterministic post-hoc citation fallback, plus format repair.
- Cited-documents-only `doc_aggs`.

**W.**
- Two numbering schemes (hash for agents, position for chat), each with its own defect.
- An LLM second pass to add citations, which costs another call over the whole pool and
  can alter the text (the prompt says "DO NOT modify … original text", but nothing
  enforces it).

**EOS.**
- *Transfers:*
  1. **Citation by stable id, verified afterwards.** Every brief item should carry its real
     id (`note:<slug>`, `run:<id>`, `file:<path>:<line>`), never a position or a truncated
     hash.
  2. A **deterministic "which cited ids exist"** check, which EOS `verify` could run over a
     harness answer.
  3. RAGFlow's `repair_bad_citation_formats` regex table is a good template for tolerant
     parsing of citations written by an agent.
  4. Keep one numbering authority and test that the renderer and the resolver agree.
     RAGFlow shows what happens otherwise.
  5. Adopt the Go rules as they are, since all of them are deterministic:
     - **Resolve or drop** canonical markers.
     - **Never delete a non-canonical bracket**, which may be a year or a footnote.
     - **Log the raw markers before repair.**
     - **No citations on a "nothing found" reply.**

---

## 6. MCP integration

### 6.1 RAGFlow as an MCP server (`mcp/server/server.py`)

- **Deployment shape.** A separate Starlette/uvicorn process (default `127.0.0.1:9382`)
  that proxies the REST API through `httpx` (`58-104`, `830-925`). It holds no DB access of
  its own.
- **Modes** (`LaunchMode`, `38-40`):
  - `self-host`: a single tenant with `--api-key` (`878-879`).
  - `host`: multi-tenant. `AuthMiddleware` requires a token on `/sse`, `/messages/` and
    `/mcp` (`745-767`). The token is taken from `Authorization: Bearer` or
    `api_key`/`x-api-key` (`502-525`) and stored in request state
    (`AUTH_TOKEN_STATE_KEY`).
- **Transports.** Legacy SSE (`/sse` plus `/messages/`) and streamable HTTP
  (`/mcp`, `StreamableHTTPSessionManager(stateless=True, json_response=JSON_RESPONSE)`)
  (`769-827`). No stdio.
- **Tools** (`569-736`):
  - `ragflow_retrieval`: question, dataset/document ids, page, `page_size≤100`,
    similarity threshold, vector weight, `top_k≤1024`, rerank, keyword, `force_refresh`.
  - `ragflow_list_datasets`.
  - `ragflow_list_chats`.

  `list_tools` builds the **descriptions dynamically**, appending the caller's full dataset
  list and chat list as newline-delimited JSON.
- **Retrieval payload.**
  - A fixed rerank candidate window of 512 keeps pagination stable
    (`_RERANK_CANDIDATES_COUNT`, `71`, `303-326`).
  - Per-chunk document metadata comes from a TTL cache: 300 s ±30 jitter, LRU bound 32 for
    each of the dataset and document caches (`59-142`).
  - The response is JSON
    `{chunks, pagination{page,page_size,total_chunks,total_pages}, query_info}`
    (`351-368`).

### 6.2 RAGFlow as an MCP client

- **Registration** (`api/apps/restful_apis/mcp_api.py:124-178`):
  1. The URL passes `assert_url_is_safe`.
  2. `pin_dns_global(hostname, ip)` is active while tools are listed.
  3. Tools are listed through `get_mcp_tools` (`api/utils/api_utils.py`), which keeps a
     per-tool `enabled` flag.
  4. The list is **snapshotted into `MCPServer.variables["tools"]`**. `MCPServer`
     (`db_models.py:1595-1606`) holds `url`, `server_type`, `variables` and templated
     `headers`.
  5. Export writes an `mcpServers` JSON, the Claude-Desktop-style format
     (`mcp_api.py:40-59`). Import (`mcp_api.py:255-343`) reads the same shape; the only
     sample is `example/mcp/parallel_search.json`, a streamable-HTTP search server. It is not
     an agent flow.
- **Runtime session** (`common/mcp_tool_call_conn.py`):
  - Each `MCPToolCallSession` owns a daemon thread with its own event loop
    (`58-117`).
  - Headers are `string.Template`-substituted from server variables and caller
    `custom_header` (`182-196`).
  - Transports are SSE or streamable HTTP only (`198-244`), with `initialize()` bounded at
    5 s.
  - Requests go through an internal queue carrying `(task, args, result_queue, deadline,
    abandoned)`. Expired or abandoned tasks are skipped (`246-289`).
  - `tool_call` returns **strings** on timeout or error instead of raising (`360-380`).
  - Only `result.content[0]` as `TextContent` is returned (`317-329`); images, resources and
    `structuredContent` are dropped.
  - Sessions are closed per canvas (`canvas.py:170-184`) or globally
    (`shutdown_all_mcp_sessions`, `561-579`).
- **Schema conversion** (`mcp_tool_metadata_to_openai_tool`, `582-600`) passes
  `inputSchema` through verbatim, with the function name re-indexed `<name>_<n>`.

<!-- SECTION:GO_MCP -->

**S.**
- The server is stateless, runs in its own process and is multi-tenant by header.
- Stable pagination by design.
- A bounded cache with TTL jitter.
- Client calls carry deadlines and handle abandonment.
- SSRF validation with DNS pinning at registration.
- Exporting and importing the standard `mcpServers` format.

**W.**
- Tool schemas are frozen at registration, so an upstream schema change is invisible until
  a manual refresh, and the agent DSL keeps its own copy.
- There is no DNS pinning or SSRF re-check when agents connect at run time (TOCTOU).
- Only the first text block is returned.
- Dynamic tool descriptions grow with the number of datasets, and every connect costs the
  client context.
- `nv.strip().strip("Bearer")` strips *characters*, not the prefix
  (`mcp_tool_call_conn.py:190`).

**EOS.**
- *Transfers:*
  1. **The EOS MCP surface should stay small and fixed.** RAGFlow's three tools cover
     discover, retrieve and list, and paginated retrieval with a *fixed candidate window*
     keeps pages consistent.
  2. **Do not put live inventories into tool descriptions.** Offer a `list_*` tool
     instead, because descriptions are paid on every session start. EOS already measures
     arrival cost (`context-budget.sh --arrival`).
  3. Carry a deadline and an abandoned flag per request.
  4. Return errors as tool text.
- *Does not transfer:* SSE and streamable-HTTP servers. EOS is invoked locally, and stdio
  or a CLI is the right transport.

---

## 7. Workflow and orchestration

### 7.1 Canvas execution model (agent runs are in-process)

- **In-process.** Agent runs are not queued. The API process drives the async generator
  `canvas.run()` and relays each event as SSE (`agent_api.py:347-410`,
  `canvas_service.py:401-410`). The session row is written **after** the run
  (`persist_workflow_session`, `agent_api.py:330-345`). It is called only after the
  `async for` completes (`400`). *Inference:* a client disconnect that closes the generator
  mid-run skips persistence, so that turn is lost.
- **Loop.** `canvas.py:684-902`:
  1. `to = _schedulable(idx, len(path))`.
  2. Emit `node_started` for `path[idx:to]`, including `thoughts()`, a per-component
     human-readable teaser.
  3. `_run_batch`: an asyncio task per node under `Semaphore(5)`. Synchronous components
     run in a thread pool with `contextvars.copy_context()` so the token sink and Langfuse
     attributes propagate (`607-652`).
  4. Post-process each node in path order: stream its Message; run its error policy; emit
     `node_finished` (deferred for streaming partials until their `Message` drains,
     `852-867`); extend the path by component type.
  5. Break on `self.error`.
  6. If any `UserFillUp` is pending, reorder the remaining path so the UserFillUps come
     first, emit `user_inputs` and return (`904-920`).
- **Resume.** `path[0]` being a UserFillUp means `is_resume` (`577`).
- **Loops.** Iteration and loop bodies are re-entered by appending
  `parent.get_start()` when a child has no downstream (`894-895`). `_append_path` suppresses
  duplicates already scheduled ahead (`869-875`).

### 7.2 Ingestion dataflow (`rag/flow/pipeline.py`)

- **Same base, simpler walk.** `class Pipeline(Graph)` (`pipeline.py:28`) reuses `Graph`
  (DSL, registry, parameter checks, variable references) but not `Canvas`. `run()`
  (`123-178`) is a **sequential single-path walk**; its gather over one task is vestigial.
- **Trace and progress.**
  - Per-component trace goes to Redis `{flow_id}-{task_id}-logs` as
    `{progress, message, datetime, timestamp, elapsed_time}`, grouped by component, with a
    **TTL of 1 800 s** (`pipeline.py:105`). It is reset at run start (`126`).
  - Aggregate progress is `1/len(components)` per component, written into the `Task` row
    (`102`).
  - `fetch_logs()` (`113-121`) is called only by `rag/flow/tests/client.py`, so no
    production endpoint exposes pipeline step traces.
- **Not a compiler.** `rag/flow/compiler/compiler.py` is a pipeline *component* for RAPTOR
  and structured extraction (`Compiler`, `72`), not a DSL compiler. The "compile" step is
  simply `Graph.load()` swapping `obj` dicts for live instances (`canvas.py:102-111`).

### 7.3 Task executor (`rag/svr/task_executor.py`, `rag/svr/task_executor_refactor/`)

- **The refactor is the live path.** `TE_RUN_MODE` defaults to `"0"`
  (`task_executor.py:1797`), which routes to `TaskManager.run_refactored_task` and
  `TaskHandler.handle_task()` (`task_manager.py:57-106`, `task_handler.py:168-306`). Mode
  `"1"` runs legacy and refactored side by side and diffs their `RecordingContext`s, a
  migration-verification harness (`task_manager.py:108-180`). Only the refactored handler
  supports `skill`, `wiki`, `evaluation`, `reembedding` and `clone` tasks (legacy prints
  "Skill generation requires the refactored task executor", `task_executor.py:1615-1617`).
- **Concurrency.** Pure asyncio: one loop per process, `asyncio.run(main())`
  (`task_executor.py:2047`), and no trio anywhere. Limiters are loop-local semaphores
  (`task_executor_limiter.py:20-28`, `common/asyncio_utils.py:21-50`):

  | Limiter | Env var | Default |
  |---|---|---|
  | tasks | `MAX_CONCURRENT_TASKS` | 5 |
  | chunk builders | `MAX_CONCURRENT_CHUNK_BUILDERS` | 1 |
  | embedding | same `MAX_CONCURRENT_CHUNK_BUILDERS` knob | 1 |
  | MinIO | `MAX_CONCURRENT_MINIO` | 10 |
  | KG | hard-coded | 2 |

- **Queue.**
  - Redis (Valkey) Streams, one stream per `(priority, suffix)`
    (`settings.get_svr_queue_name`).
  - Messages are `XADD {"message": json}` (`redis_conn.py:404-413`).
  - Reads are `XREADGROUP count=1 block=5` (milliseconds) with lazy group creation
    (`415-454`).
  - The ack happens **after** the handler finishes, whatever the outcome
    (`task_executor.py:1851`). Redelivery therefore only covers crashes; application
    failures go through the DB `retry_count`.
- **Workers and heartbeats.**
  - Consumer identity is `task_executor_{TASK_TYPE}_{TE_IDX}` (`2042`), supplied by
    `docker/entrypoint.sh` from `HOST_ID`.
  - A heartbeat `ZADD` goes into a per-worker ZSET every 30 s (`1866-1944`).
  - One reaper, holding `RedisDistributedLock("clean_task_executor")`, removes peers
    silent for more than `WORKER_HEARTBEAT_TIMEOUT=120 s`.
- **Reclaim.**
  - There is **no XCLAIM**. A crashed worker is respawned under the same name by the
    entrypoint loop and replays *its own* pending entries from id `"0"`
    (`redis_conn.py:456-478`).
  - `get_pending_msg` and `requeue_msg` exist but are uncalled, so an orphaned consumer
    name leaves its pending messages stuck forever.
- **Retry.** `retry_count` increments on every fetch, and at 3 or more the document is
  marked FAIL and the message is acked (`task_service.py:228-241`).
- **Idempotency.**
  - An `xxh64` digest of the chunking config and page range (`task_service.py:544-555`)
    lets `reuse_prev_task_chunks` skip unchanged work before it is ever queued
    (`595-637`).
  - SETNX "credit" keys stop a redelivered task from double-counting document completion
    (`46-129`).
- **Cancel.**
  - Redis `{task_id}-cancel` is set with the **default 3 600 s TTL** of `RedisDB.set`
    (`redis_conn.py:210`, `task_service.py:640-656`), while `do_handle_task` carries a 3 h
    timeout decorator. A cancel issued early in a long task can expire unseen.
  - Agent canvases use the same flag (`canvas.py:322-328`).
- **Progress.** A forward-only float plus an appended `progress_msg`, capped at
  `TASK_MAX_LOG_LENGTH=3000` (`task_service.py:380-423`). The UI polls the DB rows.
- **Memory extraction** runs here as `task_type="memory"` (`task_executor.py:1442-1444`,
  `task_handler.py:208-214`), with `digest` holding the raw message id (§3.1).

**S.**
- Crash recovery with no ownership transfer, as long as worker names are stable.
- DB-visible, auditable retry limits.
- Digest-gated skipping means unchanged work never reaches the queue.
- A shadow-run diff harness for a risky executor rewrite (`TE_RUN_MODE=1`).

**W.**
- Reclaim is silently tied to stable host ids.
- One knob controls both chunking and embedding concurrency.
- A 5 ms block amounts to polling.
- The cancel TTL is shorter than the task timeout.
- Two unrelated trace channels (pipeline 30-minute TTL, canvas 10-minute TTL), and only the
  canvas one is fetchable.

**EOS (§7).**
- *Transfers:*
  1. **Readiness from data references.** Useful for EOS procedure linting and for ordering
     independent read-only probes.
  2. **The event vocabulary**, described in §1.
  3. **"Emit started with a human teaser, finished with inputs, outputs, error and
     elapsed"**, which is exactly the shape an EOS `run event` should take.
  4. The negative lesson: persist the run record *incrementally* (append events), not once
     at the end.
- *Does not transfer:* in-process SSE fan-out, and Redis queues.

---

## 8. Persistence model (Peewee, `api/db/db_models.py`)

| Model | Key fields | Role |
|---|---|---|
| `UserCanvas` (1544-1566) | `id, user_id, title, permission (me/team), release, canvas_type, canvas_category (agent_canvas/dataflow_canvas), tags, dsl` | agent / pipeline definition |
| `UserCanvasVersion` (1582-1593) | `user_canvas_id, title, description, release, dsl` | version history; `get_latest_released` for release mode |
| `CanvasTemplate` (1568-1580) | `title/description` (i18n JSON), `canvas_types`, `canvas_category, dsl` | template gallery (`agent/templates/*.json`) |
| `API4Conversation` (1523-1541) | `id, dialog_id(=agent id), user_id, exp_user_id, message, reference, tokens, source (none/agent/dialog/workflow), dsl, duration, round, thumb_up, errors, version_title` | agent session = full state snapshot |
| `Conversation` (1499-1508) | `dialog_id, name, message, reference, user_id` | chat session |
| `Dialog` (1460-1496) | `llm_id, llm_setting, prompt_config{system, prologue, parameters, empty_response, quote, refine_multiturn, keyword, cross_languages, toc_enhance, use_kg, tts…}, kb_ids, top_n=6, top_k=1024, similarity_threshold=0.2, vector_similarity_weight=0.3, rerank_id, rerank_candidates_count=64, meta_data_filter` | chat assistant |
| `MCPServer` (1595-1606) | `name, tenant_id, url, server_type, description, variables{..., tools}, headers` | registered MCP servers |
| `Memory` (1816-1838) | see §3.1 | memory config (messages live in the doc engine) |
| `Task` (1434-1457) | `id, doc_id, task_type, priority, begin_at, process_duration, progress, progress_msg, retry_count, digest, chunk_ids` | queue ledger (also memory extraction; `digest`=source id) |
| `PipelineOperationLog` (1685-1708) | `document_id, pipeline_id, progress, progress_msg, dsl, task_type, operation_status` | ingestion run log |
| `TenantLLM` (1233-1249) | `max_tokens=8192` (drives context budget), `used_tokens` (never written in Python) | model registry per tenant |
| `TenantLangfuse` (1252-1262) | `public_key, secret_key, host` | per-tenant tracing |
| `Search` (1637-1682) | `search_config{kb_ids, similarity, rerank, llm_setting, summary, keyword, web_search, related_search, query_mindmap…}` | search app |

**S.**
- A small number of tables.
- Definitions are versioned with a released/draft split.
- A shared Go/Python schema reconciled by *column set* (`AGENTS.md`, "Shared database
  schema").

**W.**
- JSON blobs everywhere: `message`, `reference` and `dsl` are rewritten whole.
- Analytics columns (`tokens`, `duration`, `thumb_up`, `used_tokens`) exist but are never
  written.
- Memory messages live in a *different* store (the doc engine) from their config (SQL).

**EOS.**
- *Transfers:*
  1. The **definition, released version, session snapshot** triad maps onto
     procedure, verified procedure revision, run.
  2. The warning: do not keep columns nobody writes. EOS's "confidence derived, never
     stored" rule is the better discipline.

---

## 9. Observability and execution tracking

- **Per-node events.** `node_started{component_id, name, type, thoughts}` and
  `node_finished{inputs, outputs, component_id, name, type, error, elapsed_time,
  created_at}` (`canvas.py:654-697`). `workflow_finished{outputs, elapsed_time, usage}` is
  emitted once (`921-946`).
- **Returning traces.** The API collects `node_finished` payloads into
  `data.trace = [{component_id, trace:[…]}]` when `return_trace=true`
  (`agent_api.py:363-387`). Structured outputs are keyed by component
  (`367-368`).
- **Tool-level trace.** Redis `{task_id}-{message_id}-logs`, TTL 600 s
  (`canvas.py:1072-1088`). It is read by `GET /agents/<agent_id>/logs/<message_id>`, which
  looks up **`{agent_id}-{message_id}-logs`** (`agent_api.py:1184-1200`). That only matches
  entry points that construct the canvas with `task_id=agent_id` (for example
  `agent_api.py:528`, `2100`). Session runs use `task_id=session_id`
  (`canvas_service.py:374,379`, `agent_api.py:1570,1684`). *Inference:* the logs endpoint
  misses those runs.
- **Token accounting.**
  - A `token_usage_sink` ContextVar installed per run (`canvas.py:436-445`,
    `common/token_utils.py:64-101`) accumulates prompt, completion, total and calls from
    every `LLMBundle` call (`llm_service.py:107-125`), with a thread lock for parallel
    tools.
  - Totals appear only in the `workflow_finished` event and are **not persisted**.
  - Chat computes `used_token_count` and a "token speed" estimate into the returned
    `prompt` text (`dialog_service.py:911-926`).
- **Langfuse.**
  - Keys are stored per tenant through `langfuse_api.py` and
    `api/db/services/langfuse_service.py`, a 79-line CRUD over `TenantLangfuse`.
  - Each `LLM4Tenant` looks up tenant keys in the DB and calls `langfuse.auth_check()` on
    construction (`tenant_llm_service.py:108-120`), which is a network round-trip per
    bundle.
  - Generations carry `session_id`/`user_id` from the run context
    (`llm_service.py:83-99`).
  - `close()` deliberately never flushes, because a flush deadlocked the executor
    (`tenant_llm_service.py:122-152`).
- **Logging.** Debug logs at every scheduler decision (dropping, holding, cycle) and every
  input resolution (`base.py:534-557`), truncated to 200 to 500 characters.

**S.**
- A uniform, typed event stream that a UI and an API can both replay.
- Accurate per-run token totals through a single chokepoint.
- A human-readable "thoughts" teaser per node.

**W.**
- Tool traces are ephemeral (10 minutes), and their key is inconsistent across entry
  points.
- No durable per-run usage or latency, even though schema columns exist.
- Langfuse setup costs one DB query plus one network check per bundle.

**EOS.**
- *Transfers:*
  1. The **single-chokepoint accumulator** pattern: every EOS command already logs cost;
     EOS could also aggregate per `run` id.
  2. **Durable, append-only** traces, where RAGFlow's are TTL-bound.
  3. **Per-node teaser + elapsed + error** as the minimal event row.

---

## 10. Failure and recovery

- **Component level.**
  - `invoke` catches everything and stores `_ERROR`, or the default value when
    `exception_method=="comment"` (`base.py:451-495`, `722-728`).
  - The canvas then uses `exception_handler()`: `goto` appends alternate targets to the
    path and marks `other_branch` to suppress normal successors, `default_value` emits the
    fallback as a message, and anything else sets `self.error` and stops the run
    (`canvas.py:831-844`, `899-901`).
- **Timeouts.**
  - `@timeout(COMPONENT_EXEC_TIMEOUT)` is 600 s for LLM, 20 min for Agent, 12 s for
    Retrieval and 3 s for Switch and Aggregator.
  - The decorator waits with a timeout **only if `ENABLE_TIMEOUT_ASSERTION`** is set.
    Otherwise it blocks indefinitely (sync: `result_queue.get()`; async: plain `await`,
    `common/connection_utils.py:50-73`).
  - The sync variant spawns a daemon thread for each call, and its `attempts` loop re-waits
    rather than re-running.
  - LLM HTTP calls have the OpenAI client timeout `LLM_TIMEOUT_SECONDS=600`
    (`chat_model.py:330-333`).
  - MCP calls are deadline-bound (§6.2).
- **LLM retries** (`chat_model.py:346-368`, `476-516`):
  - Errors are classified by keyword into rate-limit, server, auth, and others; only
    rate-limit and server errors are retried.
  - The delay is `base_delay × U(10,150)`, 20 to 300 s at the default 2.0.
  - Chat bundles default to `LLM_MAX_RETRIES=5`. Agent components pass
    `max_retries=param.max_retries`, default **0** (`llm.py:104`).
  - Inside the tool loop, a retryable failure restarts the loop from the original history
    (`645-647`, `764-765`) and **re-executes every earlier tool call**, so non-idempotent
    tools such as email, SQL or HTTP POST may run twice. In streaming mode, the earlier
    deltas have already reached the client.
- **Cancellation.**
  - `cancel_task()` sets Redis `{task_id}-cancel` (`canvas.py:322-328`), and
    `has_canceled` reads it.
  - The check is **cooperative**: at run start, before each batch (`607-611`), and inside
    components (`check_if_canceled`, `base.py:440-449`) and streaming loops.
  - A canceled run yields `workflow_finished{outputs:"Task has been canceled"}`
    (`936-946`).
  - The API calls `cancel_task()` on exceptions (`agent_api.py:403, 439`).
- **Human-in-the-loop resume** is the only recovery point inside a run. There is no
  checkpoint per node, so a crashed run restarts the turn from scratch.

- **Background tasks.**
  - The same no-op `@timeout` applies to `build_chunks` (80 min), `do_handle_task`
    (3 h), RAPTOR (1 h), the MinIO upload and embedding (60 s), and `ProcessBase._invoke`.
  - Pipeline components also carry a *real* `asyncio.wait_for`, but
    `ProcessParamBase.timeout` defaults to 100 000 000 s (`rag/flow/base.py:29, 46`).
  - The data-source sync loop is the one subsystem with an unconditional timeout
    (`rag/svr/sync_data_source.py:213`).
  - A crash leads to respawn, pending-entry replay, `retry_count++` and a rerun from
    scratch. There is no mid-task checkpoint; only digest reuse and credit keys prevent
    duplicate effects.
  - `TaskCanceledException.__init__` never calls `super().__init__`
    (`common/exceptions.py:17-19`). It is harmless only because callers read `e.msg`.

**EOS.**
- *Transfers:*
  1. **Deadlines that actually fire.** RAGFlow's opt-in timeout is the anti-pattern. EOS
     wrappers already treat a timed-out mutation as UNKNOWN and verify it instead of
     retrying, which is exactly the protection RAGFlow's whole-loop retry lacks.
  2. **Error-branch policy as data** (`exception_method`, `goto`, `default`) is a clean
     way to encode a procedure's "on failure do X" in a step definition.

---

## 11. Sandbox (`agent/sandbox/*`, `agent/tools/code_exec.py`)

- **Providers.** `SandboxProvider` is an ABC (`providers/base.py:62-211`) with
  `initialize`, `create_instance`, `execute_code`, `destroy_instance`, `health_check`,
  `get_supported_languages` and `get_config_schema`. Seven providers are registered
  (`client.py:84-92`): `self_managed`, `aliyun_codeinterpreter`, `e2b`, `local`, `ssh`,
  `tenki` and `ucloud_agent_sandbox`.
  - Selection is one **global** system setting, `sandbox.provider_type`, defaulting to
    `self_managed` (`client.py:135-139`).
  - `code_exec.py:363` reloads the provider on every call.
  - `E2BProvider.execute_code` raises "not yet fully implemented"
    (`providers/e2b.py:120-128`).
  - `LocalProvider` runs a host subprocess under `setrlimit` (`providers/local.py:295-309`).
    Its docstring mentions a `SANDBOX_LOCAL_ENABLED` gate that is referenced nowhere.
- **Self-managed isolation** (`executor_manager/core/container.py:82-145`):
  - `docker run --runtime=runsc` (gVisor), `--read-only`, tmpfs `/workspace` and `/tmp` as
    uid 65534, `--user nobody`, `--network none` by default, `--memory 256m`.
  - Seccomp is a default-deny allowlist of 36 syscalls, but **opt-in**:
    `SANDBOX_ENABLE_SECCOMP` defaults to `false` (`agent/sandbox/docker-compose.yml:23`).
- **Pool.** One warm container queue per language (Python, Node.js) with a semaphore. Pool
  size is `SANDBOX_EXECUTOR_MANAGER_POOL_SIZE`: default 5 in compose, 1 in code.
  Allocation polls every 0.1 s for up to 10 s, then returns "Container pool is busy"
  (`container.py:173-187`, `services/execution.py:199-206`).
- **Limits.**
  - Wall clock: `docker exec … timeout <SANDBOX_TIMEOUT=10s>`, plus an asyncio guard of
    +5 s (`execution.py:179-193, 250-253`).
  - Exit code 124 maps to TIME and 137 to MEMORY (`276-293`).
  - **No output-size cap** on this path (`executor_manager/utils/common.py:20-36`).
- **Pre-screen.** An AST denylist for Python (`os`, `subprocess`, `socket`, `pickle`,
  `eval`, `exec`, `open`, `__import__`, …) and a regex denylist for JS
  (`services/security.py:24-183`). It runs before any container is used; unsafe code
  returns exit code −999 (`api/handlers.py:39-42`). `sandbox_spec.md` calls this an
  allowlist, which is false.
- **Executor API.**
  - A shared-secret token, fail-closed: HTTP 503 when unset
    (`services/auth.py:103-129`), compared with `secrets.compare_digest`.
  - A pre-auth sliding window of 30 per minute (`services/preauth.py:56-110`) and an
    authenticated quota of 120 per minute.
  - Bound to `127.0.0.1`.
- **Result protocol.**
  - A marker line, `__RAGFLOW_RESULT__:` plus a base64 JSON envelope, embedded in stdout
    (`agent/sandbox/result_protocol.py:22`). The server side **re-implements** it rather
    than importing it (`executor_manager/services/execution.py:29-176`).
  - Artifacts: an allowlist of `png`, `jpg`, `svg`, `pdf`, `csv`, `json` and `html`, at
    most 10 files of 10 MB each, sanitized names, auto-promoted from the working directory
    (`execution.py:317-380`).
  - The RAGFlow side uploads artifacts to object storage, exposes `_ARTIFACTS
    {name, url, mime_type, size}`, and also parses them to text for downstream nodes
    (`code_exec.py:476, 509-567, 605-643`).
  - A **typed result contract** (String, Number, Boolean, Object, `Array<T>`, Null) turns
    a mismatch into a `ContractError` rather than a crash (`code_exec.py:181-196`).
- **Neighbouring safety.**
  - ExeSQL blocks only `^(insert|update|delete)` (`agent/tools/exesql.py:271`). `DROP`,
    `TRUNCATE` and `ALTER` pass, and there is no driver timeout, so combined with the no-op
    `@timeout` a hung database blocks the run.
  - Invoke passes an explicit `requests` timeout (`agent/component/invoke.py:242`).

**S.**
- Layered defences: a gVisor boundary, a read-only rootfs, non-root, no network by
  default, a pre-screen, fail-closed auth and rate limits.
- The in-container coreutils `timeout` is enforced regardless of Python.
- A provider ABC with a self-describing config schema.
- A typed contract plus an artifact allowlist for heterogeneous results.

**W.**
- The pre-screen is a denylist.
- Seccomp is off by default.
- Output size is uncapped.
- The protocol is duplicated between client and server.
- Documentation drifts from code (E2B and the local gate).
- The ExeSQL guard is weak.

**EOS.**
- *Transfers:*
  1. **The marker line plus base64 JSON envelope in stdout.** A stdlib-friendly way for EOS
     wrappers to return a machine result alongside human logs.
  2. **A typed result contract that quarantines instead of crashing.**
  3. **A provider ABC with `get_config_schema()`**, which validates EOS's multi-backend
     wrapper pattern.
- *Does not transfer:* gVisor, seccomp and warm pools. EOS runs no untrusted code.

<!-- SECTION:SANDBOX_END -->

---

## 12. Evals and tests relevant to agents

- **Canvas scheduling** (`test/unit_test/agent/test_canvas_batch_readiness.py:313-434`).
  These drive the *real* `agent/canvas.py` (loaded through
  `importlib.util.spec_from_file_location` over stubbed `sys.modules`) on DSL graphs built
  in Python. They assert:
  - A join waits for the deeper branch (`trace.at("start","join") >= trace.at("end","c")`).
  - Balanced branches still run concurrently.
  - A node whose upstream was never scheduled is dropped, and the drop cascades.
  - Two joins held back in the same batch each run once.
  - Mutual references fail with "reference each other".
  - A cycle still advances the window (`_schedulable(1,3)==3`).
  - The unrunnable flag is scoped to one dispatch.
  - A resumed run keeps its stored order.
  - CodeExec dependencies come from its arguments.
- **Resets and serialization.** `test_canvas_input_reset.py`, `test_canvas_session_reset.py`
  and `test_graph_load_missing_path.py`. `test_dsl_bridge_roundtrip.py` (507 lines) ports
  the frontend `dsl-bridge.ts` to Python and round-trips DSLs to catch drift between
  frontend and backend.
- **Variable references.** `test_variable_ref_pattern_unit.py` has 12 tests covering
  underscored and colon ids, unbalanced braces staying literal, and whitespace
  preservation.
- **Components.** 19 files: Switch with empty conditions, Message kwargs substitution safe
  against regex metacharacters, message-stream errors, LLM prompt fitting and `sys.files`,
  think-marker pairing while a tool fills chunks mid-stream
  (`test_agent_think_marker_pairing.py:177-219`), and SSRF defences in the Browser
  component.
- **Tools.**
  - `test_http_timeout.py` is an **AST lint as a test**: every HTTP call site in a tool
    must pass `timeout=`.
  - `test_exesql_ssrf.py` and per-provider parsing tests.
  - `test_llm_tool_plugin_session.py:50-72` asserts the MCP-only default tool timeout.
- **MCP server.** 4 files: pagination stops at the total, bounded caches, a fixed rerank
  window.
- **Memory.** 6 files, 67 tests, mostly DDL and SQL for GaussDB and OceanBase:
  SQL-injection rejection of column names, FIFO "forgotten first, then oldest", capacity
  eviction, and prompt assembly stable across hash seeds.
- **Prompts.** `test_generator_message_fit_in.py` (5 tests) and `test_kb_prompt_metadata.py`
  (block budget; empty chunk does not shift numbering).
- **Agentic RAG.** 9 files, schema and contract only (§2.1).
- **Evals.**
  - `rag/benchmark.py` measures retrieval quality (ndcg@10, map@5, mrr@10 via `ranx` on
    MS MARCO, TriviaQA and MIRACL).
  - `test/benchmark/` measures only load and latency.
  - **No eval of agent task success or answer quality exists.**
- **What CI runs.**
  - `run_tests.py` runs `test/unit_test` (`sep-tests.yml:368`).
  - `agent/sandbox/tests/`, the executor-manager tests, `test/testcases/test_web_api/**`
    (canvas, agent, MCP and memory apps) and the benchmark are **not in any workflow**.
  - Every canvas test stubs `@timeout` to identity, so no test exercises real timeout
    behaviour.

**S.**
- Hermetic unit tests of the real scheduler through module stubbing.
- DSL-as-fixture built in code.
- Static lint expressed as tests.
- Port-and-diff tests across languages.

**W.**
- No behavioural eval of agents.
- The sandbox and web-API suites do not run in CI.
- The stub blocks are copy-pasted across 6 or more files.

**EOS.**
- *Transfers directly:*
  1. **Scheduler tests over dict-built graphs** for procedure linting.
  2. **AST-as-test** to enforce wrapper conventions: every subprocess call has a timeout,
     every write is gated on `--confirm`.
  3. **"No ghost args" contract tests** for EOS command flags.
  4. **The `importlib` stub technique** is unnecessary for EOS: stdlib-only modules import
     cleanly.
- *Beyond RAGFlow:* EOS already has a retrieval eval in `note eval` (recall@k, MRR), which
  RAGFlow lacks for its memories and skills.

---

## 13. "Skills": four separate subsystems that share one word (context engineering and procedure store)

The word covers four subsystems that do not interoperate.

1. **Corpus-to-skill tree** (`rag/svr/task_executor_refactor/dataset_skill_generator.py`,
   `task_type="skill"`).
   - Inputs and structure. It summarizes every document (budget `max(512, 0.5 × ctx)`),
     RAPTOR-clusters the summaries into a tree (at most 12 iterations, stopping at 8 or fewer
     top clusters), and LLM-labels each node (2 to 5 words, temperature 0).
   - Output rows. It writes one `compile_kwd="skill"` row per node
     (`skill_kwd, parent_kwd, depth_int, children_kwd, md_with_weight`, `:309-330`) plus one
     aggregate `skill_all` row whose `skill_with_weight` is a JSON tree (`:357-375`). Each
     node renders as frontmatter `name/description/level/num_documents` plus markdown,
     "mirrors Corpus2Skill's SKILL.md/INDEX.md" (`:257-306`).
   - Rebuild semantics. Every run deletes and rebuilds, after 5 cancellation checkpoints
     (`:618-655`). Nothing is merged, and non-deterministic relabeling changes row ids.
   - Readers. The Python reader (`api/apps/services/dataset_api_service.py:3038-3335`)
     matches the writer. **The Go reader does not**: `GetSkillTree` selects `kwd,
     title_kwd, page_type_kwd, outlinks_int, inlinks_int`
     (`internal/service/dataset_artifact_service.go:963-990`), which were copied from the
     wiki-page reader. The writer never emits those fields.
2. **Skill Space library** (Go: `internal/service/skill_{space,search,indexer}.go`,
   `conf/skill_{es,infinity}_mapping.json`).
   - Storage. Users upload `SKILL.md` folders with frontmatter
     `{name, description, version, author, tags, tools}`. The name must match
     `^[a-z0-9][a-z0-9_-]*$`, with a 50 MB limit per skill and 5 MB per file. **A version is
     a sibling folder named by semver.** Only the numerically latest version is indexed
     (`skill_indexer.go:554-589`).
   - Indexing. One index per (tenant, space). The embedding text defaults to
     name + tags + description, *without* content (`entity/skill_search.go:38-42`).
   - Ranking. Field boosts `name^10, tags^5, description^3, content^1` are **hard-coded**
     at 3 call sites (`skill_search.go:344-349` and the others), and the editable
     `FieldConfig` never reaches the query. ES uses `scripted_sim = boost × idf × min(tf,1)`,
     which is binary TF with no length normalization (`skill_es_mapping.json:8-15`).
     Infinity uses BM25, so ranking **differs by backend**.
   - Dead or crude parts. The per-document `status` is always `"1"`, `cleanupOldVersions`
     is a no-op, and results are ordered with an O(n²) bubble sort.
3. **Harness skill middleware** (`internal/harness/core/middlewares/skill/skill.go`).
   - `ModeInline` injects the full content, truncated to 4 000 characters, into the system
     prompt.
   - `ModeFork` registers one tool `skill_<name>` whose description is only the skill
     description; the content, capped at 2 000 characters, loads on call. That is
     **progressive disclosure**.
   - It has **zero callers** (a search for the import path finds nothing). Nothing in any
     agent path calls the Skill Space services either.
4. **CLI skill hub** (`internal/cli/filesystem/skill*.go`).
   - Sources: local, GitHub, `clawhub://`, `skills.sh`.
   - Trust. A regex threat scanner gated by a trust × verdict table
     (`patterns.go:272-278`, with `TrustedRepos` = openai, anthropics, microsoft, google).
     There is **no hash pinning or signature check**.
   - Install uploads into the Skill Space. Uninstall deletes the index document and the
     folder as two independent best-effort steps.

**S.**
- The SKILL.md frontmatter convention.
- Progressive disclosure in Fork mode.
- Field-weighted search where name outweighs tags, which outweigh description, which
  outweighs content.
- Binary-TF IDF, which suits short, repetitive metadata.

**W.**
- Four disjoint implementations.
- A reader and writer that disagree about field names.
- A configurable weight object that the query ignores.
- Ranking that differs by backend.
- A consumer (the middleware) that no code calls.
- Tests only for the unused middleware.

**EOS.**
- *Transfers:*
  1. **Procedure metadata as SKILL.md-style frontmatter** (name, description, version,
     tags, tools), with **Fork-style progressive disclosure**: list name and description,
     and load the steps only when a task names the procedure. That is what
     `eos brief --task` already does, which this confirms.
  2. **Field-weighted FTS**: SQLite FTS5 `bm25(t, 10, 5, 3, 1)` over
     name/tags/description/body gives RAGFlow's intent natively. A binary-TF variant can be
     approximated by indexing a de-duplicated token set per column.
  3. **Write→read round-trip tests at every serialization boundary.** They would have
     caught the Go/Python field mismatch.
- *Does not transfer:* the LLM-generated corpus tree, and hub installation.

---

## 14. What transfers to EOS: a decision matrix

EOS constraints assumed: SQLite, stdlib only, no LLM calls, advises the harness. Local
numbers are from the scratchpad EOS outputs: `note eval` recall@1 0.667 and MRR 0.816 over
236 notes; brief `--task` about 1 391 tokens against a 1 500 budget; 15 procedures;
51 executions with 1 929 events.

### 14.1 Adopt (deterministic, cheap, clear gain)

| # | Idea | RAGFlow evidence | EOS shape |
|---|---|---|---|
| 1 | **Bi-temporal + soft-forget columns, actually filtered** | `valid_at/invalid_at/forget_at/status` stored, but only `status`/`forget_at` filtered (`messages.py:153-155`, `es_conn.py:140-142`); `invalid_at` never used | `notes.invalid_at`, `notes.forget_at`, `status`; brief/search exclude invalid and forgotten by default; `note supersede <old> <new>` sets `invalid_at` |
| 2 | **Typed memory with provenance** | `message_type ∈ raw/semantic/episodic/procedural`, `source_id` → raw turn (`memory_message_service.py:71-103`) | `kind` on notes (fact / episode / procedure), `source_run_id` on lessons and notes (EOS already links lessons to executions) |
| 3 | **Budgeted store with deterministic eviction** | "forgotten first, then oldest" (`messages.py:218-261`) | brief packing and archival order: `forget_at IS NOT NULL` first, then by `valid_at` |
| 4 | **Budget the whole rendered block** | `kb_prompt` counts decorations; overflow block excluded whole; empty block does not shift numbering (`generator.py:168-185`, `test_kb_prompt_metadata.py`) | same three regression tests for `brief --task` (1 500-token budget) |
| 5 | **Cite by stable id, verify after** | positional 1-based vs 0-based mismatch (`generator.py:176` vs `dialog_service.py:881-888`); `sha1%500` collisions (`generator.py:157`, `canvas.py:1096`) | brief entries carry `note:<slug>` / `run:<id>` / `file:line`; `eos verify` checks that cited ids exist; tolerant parser modelled on `repair_bad_citation_formats` (`dialog_service.py:530-583`) |
| 6 | **Event vocabulary and result-status taxonomy** | `workflow_started/node_started(thoughts)/node_finished(inputs,outputs,error,elapsed)/user_inputs/workflow_finished(usage)` (`canvas.py:579-946`); `ToolOutcome.status` OK/EMPTY/MISS/POOR/REDUNDANT/ERROR (`action_session.py:168-173`) | standardize `eos run event --kind` and wrapper exit semantics on these; keep usage totals on `run finish` |
| 7 | **Procedure DSL with step I/O and a deterministic linter** | readiness from `{cpn@var}` refs, dropped or cycle detection (`canvas.py:472-529`); error policy as data (`exception_method/goto/default`, `base.py:57-67`, `717-728`) | procedure steps declare `in`/`out`; `eos procedure lint` flags unknown refs, cycles, unreachable steps; `on_fail` set to `goto <step>`, `default <text>` or `stop` |
| 8 | **Digest-gated skip** | xxh64 over sorted config + range lets unchanged work skip the queue (`task_service.py:544-637`) | hash of inputs per scan unit (file mtime+size+rules version) to skip unchanged work (scan median 2.1 s) |
| 9 | **Lease rows, retry cap, cancel that outlives the run** | heartbeat ZSET + reaper (`task_executor.py:1866-1944`); `retry_count≥3` abandon (`task_service.py:228-241`); cancel TTL 1 h shorter than task timeout 3 h (`redis_conn.py:210`) | `runs(owner, started_at, last_seen_at)`; stale-lease reclaim by any process; explicit revoke instead of TTL |
| 10 | **grep/sed narrowing** | `narrow_by_terms` (`grep_sed_narrow.py:245-382`): word-boundary regex, whole-line spans, char budgets, table exemption, fallback chain | evidence snippets in briefs and `eos ask`; exempt tables, SQL and YAML blocks from narrowing |
| 11 | **Better lexical ranking without vectors** | bigram phrase boost ×2, synonyms at w/4 (`memory/services/query.py:70-94`); field boosts name^10/tags^5/desc^3/content^1; binary TF (`skill_es_mapping.json:8-15`) | FTS5 `bm25(t,10,5,3,1)` over title/tags/summary/body; add adjacent-bigram phrase queries; a synonym table; measure with the existing `note eval` (target: recall@1 above 0.667) |
| 12 | **Tests that catch drift** | AST lint as test (`test_http_timeout.py`); "no ghost args" schema test; dict-built graph tests; the missing write→read test that would have caught the skill-field mismatch | AST test: every subprocess call has `timeout=`, every write path checks `--confirm`; flag-consumption test; round-trip tests at every file↔DB boundary |
| 13 | **Near-duplicate suppression** | Jaccard 0.8 on repeated tool queries (`action_session.py:69-87`) | dedupe repeated `brief`/`ask` calls within a session (2 175 brief calls logged) |
| 14 | **Machine envelope in stdout** | `__RAGFLOW_RESULT__:` + base64 JSON (`result_protocol.py:22`) | optional `--machine` marker line on wrappers so the harness can parse results without scraping |
| 15 | **Post-hoc attribution without an LLM** | `insert_citations`: sentence split, hybrid token/vector similarity, threshold 0.63 decaying ×0.8 to 0.3, at most 4 citations per sentence, code blocks kept whole (`rag/nlp/search.py:423-499`) | a lexical-only version (token overlap against brief items) for `eos verify`: flag answer sentences with no supporting note, run or file evidence |

### 14.2 Adapt (useful, but only in a narrower form)

- **Deterministic sufficiency or coverage check.** Replace the LLM SCA judge with
  `memory.search()`'s ratio test (overlap / significant terms ≥ 0.12, `harness/memory.py:360-393`)
  applied per declared sub-question of a task, and report "no evidence for: X" in the brief.
- **A sub-agent handoff schema** `{reasoning, context, user_prompt}`
  (`agent_with_tools.py:46-64`) for briefs EOS prepares for Task/sub-agents.
- **A per-run cost accumulator at one chokepoint** (`token_utils.py:64-101`). EOS logs
  per-command cost already; aggregate it per `run_id`.
- **Budgeted, phase-structured flows** (`agentic_rag_graph.py:75-81`, `_bounded`). A
  procedure's phases can carry deadlines in data, with a harness-visible "budget exhausted,
  synthesize now" rule.
- **Relative-date normalization** of task text with a regex table (the LLM version is
  `full_question_prompt.md`).

### 14.3 Reject (LLM-in-the-loop or infra-bound)

- LLM memory extraction, SCA judge, fan-out planner, query rewrite, cross-language
  translation, keyword extraction, a second citation pass, the corpus-to-skill tree.
- Embeddings and text+vector fusion (EOS ADR-001/010).
- Redis Streams and consumer groups, SSE fan-out, canvas replicas, LangGraph, gVisor
  sandboxes, MCP over SSE/HTTP (EOS stays CLI/stdio).
- **A whole-state snapshot per turn** (`API4Conversation.dsl`). EOS's append-only events
  with derived state are already better.

### 14.4 Anti-patterns with evidence (worth a line in EOS docs)

- **Opt-in timeouts**: `@timeout` is a no-op unless `ENABLE_TIMEOUT_ASSERTION`
  (`connection_utils.py:50-73`).
- **Whole-loop retry re-executes side-effecting tools**: `chat_model.py:645-647`.
- **Ephemeral traces for things you will be asked about later**: Redis TTLs of 600 and
  1 800 s (`canvas.py:1086`, `pipeline.py:105`).
- **Columns nobody writes**: `TenantLLM.used_tokens`, `API4Conversation.tokens`,
  `duration`, `thumb_up`.
- **Config the query path ignores**: skill `FieldConfig` versus hard-coded boosts.
- **Docstrings describing dead mechanisms**: the harness memory recall, the planner and
  reflect prompts, the skill middleware, `Canvas.memory`. EOS's reachability and
  `rules --untested` tooling is the antidote.
- **Live inventories in MCP tool descriptions**: `list_tools` appends the dataset and chat
  lists to each description (`mcp/server/server.py:569-684`).
- **Two parallel numbering schemes for citations**, each wrong in its own way (§5).

<!-- SECTION:GO_MATRIX -->

---

## Appendix A: defects and risks found in code (evidence index)

| # | Finding | Evidence |
|---|---|---|
| 1 | Chat citations off by one: prompt blocks numbered from 1, resolvers read from 0 | `rag/prompts/generator.py:176`; `api/db/services/dialog_service.py:881-888`; `web/src/components/markdown-content/reference-utils.ts:79-80`; acknowledged in `internal/service/kb_prompt.go:92-95` |
| 2 | Agent citation ids are `sha1(id) % 500`, so ≈32% collide at 20 chunks; the containment check tests the wrong dict | `generator.py:157`; `agent/canvas.py:1096-1103` |
| 3 | `@timeout` is a no-op unless `ENABLE_TIMEOUT_ASSERTION` is set | `common/connection_utils.py:50-73` |
| 4 | A retryable LLM error restarts the whole tool loop and re-runs tools that already ran | `rag/llm/chat_model.py:645-647`, `764-765` |
| 5 | Retry delay is `base_delay × U(10,150)`, 20 to 300 s, with 5 retries by default for chat | `chat_model.py:336-347` |
| 6 | The Agent `tool_timeout` applies to MCP tools only | `agent/tools/base.py:70-86`; `test_llm_tool_plugin_session.py:50-72` |
| 7 | Tool-trace endpoint key (`{agent_id}-{message_id}`) differs from the writer key (`{task_id}-{message_id}`) for session runs | `agent_api.py:1192`; `canvas.py:1077-1086`; `canvas_service.py:374,379` |
| 8 | Cancel flag TTL is 3 600 s, shorter than the 3 h task timeout | `rag/utils/redis_conn.py:210`; `api/db/services/task_service.py:640-656` |
| 9 | Canvas replica commit is an unlocked read-modify-write | `api/apps/services/canvas_replica_service.py:211-247` |
| 10 | Memory `invalid_at` is never filtered; FIFO is the only forgetting policy despite "LRU" in the help text | `memory/services/messages.py:151-177`; `common/constants.py:253-254`; `db_models.py:1832` |
| 11 | Go skill-tree reader selects fields the Python writer never emits | `internal/service/dataset_artifact_service.go:963-990` vs `rag/svr/task_executor_refactor/dataset_skill_generator.py:309-375` |
| 12 | Skill search boosts are hard-coded, and the persisted `FieldConfig` is ignored at query time | `internal/service/skill_search.go:344-349` (and 2 more copies) |
| 13 | Harness "memory" recall (`grep`, `search`) is unreachable | `rag/advanced_rag/harness/memory.py:127-202`, `360-393` (only `add`/`_STOPWORDS` imported) |
| 14 | Planner, reflect and memory-summary prompt functions ship with no callers; `Canvas.memory` is never written | `rag/prompts/generator.py:439-524`; `agent/component/llm.py:538-541`; `canvas.py:1142-1146` |
| 15 | Analytics and usage columns are never written by Python | `TenantLLM.used_tokens` (`db_models.py:1241`); `API4Conversation.tokens/duration/thumb_up` (`1531-1536`) |
| 16 | ExeSQL blocks only `insert`, `update` and `delete`, and passes no driver timeout | `agent/tools/exesql.py:271` |
| 17 | Sandbox seccomp is off by default; no output-size cap on the self-managed path | `agent/sandbox/docker-compose.yml:23`; `executor_manager/utils/common.py:20-36` |
| 18 | MCP header check strips *characters* `B,e,a,r` rather than the `Bearer` prefix | `common/mcp_tool_call_conn.py:190` |
| 19 | With `use_kg`, the Retrieval tool runs KG retrieval twice and inserts two KG chunks | `agent/tools/retrieval.py:226-233` and `237-245` |
| 20 | `Iteration.max_concurrency` is declared but unused; iteration is sequential | `agent/component/iteration.py:37`; `canvas.py:884-895` |
| 21 | MCP client SSRF guard and DNS pinning apply at registration only, not at agent run time | `api/apps/restful_apis/mcp_api.py:143-164` vs `agent/component/agent_with_tools.py:105-113` |
| 22 | The E2B sandbox provider is registered but raises on execute | `agent/sandbox/providers/e2b.py:120-128` |
