# RAGFlow, area A: retrieval and knowledge side (source-level investigation)

Scope: ingestion, chunking, embeddings, full-text, hybrid fusion, reranking, rank features, GraphRAG,
context assembly, citations, persistence, configuration, benchmarks, recent changes. Agents, memory,
MCP and workflow are covered by the companion report (`ragflow-agents-memory.md`) and appear here
only where they touch retrieval.

- Snapshot: shallow clone, one commit `313ca90` (2026-09-24). Release notes go up to v0.27.2
  (2026-09-10); `docker/.env:271` pins image `v0.27.2`.
- Default deployment read from code, not README: `DOC_ENGINE=elasticsearch` (`docker/.env:30`,
  `common/settings.py:169`), `DB_TYPE=mysql` (`docker/.env:34`), `API_PROXY_SCHEME=python`
  (`docker/.env:262-264`). A parallel Go implementation exists (`internal/`, `cmd/ragflow_server.go`,
  ~2,400 files) and runs only with `API_PROXY_SCHEME=go|hybrid` (`docker/entrypoint.sh:333-398`).
  **Everything below describes the Python path** unless marked "Go".
- Paths are relative to the repo root. Line numbers are from this snapshot.

---

## 0. Executive summary

1. **"Hybrid" search in the default ES path is lexical-gated dense ranking, not a union.** The kNN
   leg is filtered by the same bool query that carries the `query_string` full-text clause
   (`rag/utils/es_conn.py:245-270`), the text leg's boost is zeroed (fusion weights `"0.001,1"` from
   `rag/nlp/search.py:331` become `bool_query.boost = 1 - 1 = 0` at `es_conn.py:251`), and the final
   score is recomputed in Python: `sim = (1-w)·term_sim + w·knn_score + rank_features`
   (`rag/nlp/search.py:604-629`). Infinity does the same gating (`rag/utils/infinity_conn.py:250`).
   OpenSearch (`opensearch_conn.py:405-460`) and SereneDB (`serenedb_conn.py:458-489`) do a real
   union. Semantics depend on the backend.
2. **The term-similarity signal is "weighted query-term coverage"**, not BM25 and not symmetric:
   `Σ w(q-term present in chunk) / Σ w(q-term)` over unigrams (×0.4) and adjacent bigrams (×0.6)
   (`rag/nlp/query.py:180-209`). Chunk-side weights are ignored (`# * dtwt[k]` is commented out), so
   the `title×2 / important×5 / question×6` repetition at `search.py:618-623` has no effect.
3. **Rank features are additive and large.** Tag-feature cosine is multiplied by 10
   (`search.py:526`), and the per-chunk `pagerank_fea` (0-100 integer) is added **raw**
   (`search.py:528-531`), so a KB with pagerank 5 outranks every chunk of a pagerank-0 KB. A disabled
   feedback loop adjusts `pagerank_fea` from thumbs up/down (`api/db/services/chunk_feedback_service.py`).
4. **Embedding = 0.1·title + 0.9·content vector** (`rag/svr/task_executor_refactor/embedding_utils.py:49,160-183`).
   When auto-questions exist, the *questions* are embedded instead of the content (`:185-199`). Stored
   as `q_<dim>_vec` (`embedding_utils.py:134-158`, `search.py:110`).
5. **Chunking is document-centric.** Code files (`py/java/go/ts/...`) are plain text split on the
   default delimiters `"\n!?;。；！？"` (`rag/app/naive.py:1324-1329`, `rag/nlp/delim.py:88`,
   `rag/flow/parser/parser.py:1129-1145`). There is no AST-aware chunking anywhere.
6. **GraphRAG and RAPTOR are deprecated in the UI since v0.27.0** (`docs/release_notes.md:95`), replaced
   by "knowledge compilation" (Graph/Tree/PageIndex/Timeline/Wiki). The legacy code still runs and
   existing graph rows stay searchable. The new RAPTOR clusters by an O(N) 1-D "watershed" over
   *adjacent* chunk similarity (`rag/advanced_rag/knowlege_compile/raptor.py:475-540`), and summaries
   are built from LLM-extracted **claims whose verbatim quotes are validated against the source chunk**
   (`raptor.py:64-101,262-370`, `structure.py:931-1034`).
7. **The graph lives in the same index as the chunks**, one row per graph, subgraph, entity, relation and
   community report, told apart by `knowledge_graph_kwd` and hidden with `available_int=0`
   (`rag/graphrag/utils.py:414-499,550-755`). KG retrieval scores entities by `sim × pagerank` and boosts
   relations along precomputed 2-hop paths (`rag/graphrag/search.py:139-275`). It returns one synthetic
   chunk that holds CSV tables.
8. **Citations have two paths.** In the first, the LLM writes `[ID:n]` and bad formats are repaired
   (`api/db/services/dialog_service.py:530-583,865-886`). In the second, used only when the answer has
   no markers, citations are inserted after the fact from token+embedding similarity with a falling
   threshold, 0.63 multiplied by 0.8 each step while it stays above 0.3 (`search.py:422-499`).
   **Apparent off-by-one:** `kb_prompt` numbers blocks from 1 (`rag/prompts/generator.py:176`), while
   the answer decorator (`dialog_service.py:881-888`) and the web client
   (`web/src/components/markdown-content/index.tsx:333-352`) index `reference.chunks` from 0. The Go
   port documents this exact bug and renders from 0 (`internal/rag/prompts/generator.go:180-218`).
9. **Many stores, and eventual cross-store consistency.** Metadata sits in MySQL (peewee), chunks,
   vectors, graph and TOC in the doc engine, blobs in MinIO, and queues, caches, locks and checkpoints
   in Redis. A "temporary safety net" deletes chunks at query time when their document row is gone
   (`search.py:150-228`).
10. **Transfer to EOS.** EOS is lexical-only by decision (C-10), and the best matches for it are
    RAGFlow's lexical machinery: field-weighted BM25, term weighting, bigram and phrase boosts, a
    short-query minimum-should-match, filler-word stripping and a synonym dictionary. Also useful: the
    coverage-style token similarity (EOS already uses it), bounded feedback priors, digest-based chunk
    reuse, content-addressed chunk IDs, parent/child "small-to-big", verbatim-evidence gates and RRF.
    Its own newest code (`rag/advanced_rag/harness/tools/navigation.py:1529-1549`) uses RRF because
    RRF needs no threshold calibration. Infrastructure-bound: ES/Infinity DSLs, HNSW parameters, Redis
    Streams, MinIO, ONNX OCR/layout/TSR, XGBoost, spaCy, graspologic.

---

## 1. System map

| Concern | Where | Code |
|---|---|---|
| API server (Python) | `api/ragflow_server.py` | chat, retrieval and dataset endpoints (`api/apps/restful_apis/*`) |
| Ingestion worker | `rag/svr/task_executor.py` → `task_executor_refactor/` (default `TE_RUN_MODE=0`) | `task_executor.py:1797-1815` |
| Task queue | Redis Streams + consumer group | `rag/utils/redis_conn.py:404-500` |
| Relational metadata | MySQL / Postgres / OceanBase / GaussDB via peewee | `api/db/db_models.py:803-809` |
| Chunks, vectors, graph, TOC, compiled artifacts | ES (default), Infinity, OpenSearch, OceanBase/SeekDB, GaussDB, SereneDB | `common/settings.py:398-430` |
| Document metadata index | `ragflow_doc_meta_<tenant>` (ES object field `meta_fields`) | `conf/doc_meta_es_mapping.json`, `api/db/services/doc_metadata_service.py:72-82` |
| Blobs (files, chunk images) | MinIO default; S3, OSS, Azure, GCS, OpenDAL | `common/settings.py:224,294-300,449-479` |
| Retriever singletons | `settings.retriever = search.Dealer(docStoreConn)`, `settings.kg_retriever = KGSearch(...)` | `common/settings.py:481-485` |

Index naming: ES/OpenSearch/OceanBase use one index per **tenant** (`ragflow_<tenant_id>`,
`rag/nlp/search.py:47-48`) and filter by `kb_id`. Deleting a KB never drops the index
(`common/doc_store/es_conn_base.py:205-214`). Infinity uses one table per (tenant, KB),
`f"{indexName}_{kb_id}"` (`rag/utils/infinity_conn.py:204,284`), and fans out across tables.

---

## 2. Area 1: document ingestion pipeline

### 2.1 Task creation (API side)
- `queue_tasks` (`api/db/services/task_service.py:444-592`) splits a document into tasks:
  - PDFs by page range: 12 pages per task, 22 for `paper` (`:487-489`). A single task for
    `one`/`knowledge_graph`/`resume`, for `toc_extraction`, and for MinerU (`:503-518`).
  - Tables by 3,000 rows (`:529-536`). Everything else is one task.
- **Digest-based reuse.** Each task gets `xxh64(chunking_config ∥ doc_id ∥ from_page ∥ to_page)`
  (`:544-555`). `chunking_config` covers parser_id, parser_config minus raptor/graphrag, size,
  `content_hash`, language, embd_id and the tenant model ids (`api/db/services/document_service.py:981-1004`).
  A previous task with the same digest and page start, finished with chunk ids, has its chunks adopted
  as-is (`reuse_prev_task_chunks`, `task_service.py:595-637`). All other old chunks are deleted (`:559-571`).
- Messages are `XADD`ed as JSON to a priority/suffix stream (`task_service.py:582-589`,
  `redis_conn.py:404-413`). Cancellation is a Redis flag `<task_id>-cancel` (`task_service.py:640-653`).

### 2.2 Worker loop
- `collect()` replays this consumer's un-acked PEL entries first, then `XREADGROUP count=1 block=5`
  (`task_executor.py:247-322`, `redis_conn.py:415-478`). Delivery is at-least-once and the message is
  acked only after handling (`task_executor.py:1851`).
- Concurrency uses loop-local semaphores (`rag/svr/task_executor_limiter.py`): tasks 5, chunk builders 1,
  embedding 1, MinIO 10, KG 2.
- Heartbeats go to per-consumer sorted sets. A lock-elected cleaner evicts dead workers
  (`task_executor.py:1866-1944`). Optional recycling after N tasks releases ONNX arena memory (`:185-201`).
- `TE_RUN_MODE`: `0` (default) runs the refactored `TaskManager`, `1` runs legacy plus a shadow
  dry-run of the refactor with a diff report (`RecordingContext`, write interceptor, `comparator.py`),
  and anything else runs legacy (`task_executor.py:1797-1815`).

### 2.3 Standard chunking task (refactored path)
`TaskHandler._run_standard_chunking_impl` (`task_executor_refactor/task_handler.py:566-697`):
1. Bind the embedding model and probe it with `encode(["ok"])` to learn `vector_size`; create the
   index with `create_idx(..., vector_size)` (`task_handler.py:339-391,308-314`).
2. Fetch the file from blob storage and run `chunker.chunk(...)`. The parser comes from the
   `FACTORY` map by `parser_id` (`chunk_builder.py:39-68,71-116`).
3. Prepare the docs (`chunk_service.py:226-275`):
   - chunk `id = xxh64(content_with_weight + doc_id)`, which makes chunks content-addressed and idempotent (`:241`);
   - chunk images go to MinIO under `img_id = "<kb_id>-<chunk_id>"` (`rag/utils/base64_image.py:32-82`);
   - `pagerank_fea = KB.pagerank` is stamped when non-zero (`:231-232`).
4. Optional LLM enrichment: keywords, questions, metadata, tags (§3.4).
5. Embed (§4).
6. Insert "mother" (parent) chunks, then the chunks, in `DOC_BULK_SIZE` batches:
   - `DOC_BULK_SIZE` is 32 in code (`common/settings.py:219`) and 4 in `docker/.env:322`;
   - chunk ids are checkpointed to the `Task` row about every 256 chunks (`chunk_service.py:408-454`);
   - the index is refreshed at the end.
7. If `toc_extraction` is set, build a TOC chunk (§3.5). Table-parser metadata is pushed to document
   metadata (`post_processor`).

### 2.4 Parsers per type (`rag/app/*`, `FACTORY` at `task_executor.py:112-129`)

| parser_id | Algorithm (from code) |
|---|---|
| `naive` (general) | Per-extension dispatch (`rag/app/naive.py:1120-1526`):<br>• PDF goes through `PARSERS` (`:553-564`): DeepDOC, MinerU, MonkeyOCRv2, Docling, OpenDataLoader, TCADP, PaddleOCR, SoMark, Mistral OCR, or plain text/VLM.<br>• DOCX: `naive_merge_docx`.<br>• Excel/CSV: row groups with real positions, or `html4excel`.<br>• Markdown: section merge with short-header glue and optional VLM figure captions.<br>• HTML, EPUB, JSON: dedicated parsers.<br>• txt and code: `TxtParser`.<br>• Also recursive: embedded files and hyperlinks (`:1152-1187,1500-1510`). |
| `book` | `bullets_category` → `hierarchical_merge(bull, sections, 5)`, else `naive_merge` (`rag/app/book.py:178-201`) |
| `laws` | `tree_merge(bull, sections, 2)`, which prepends title paths (`rag/app/laws.py:248-257`) |
| `paper` | Layout sections, then `title_frequency` for heading level. The abstract chunk gets `important_kwd=["abstract","summary",...]` (`rag/app/paper.py:223-253`) |
| `manual` | Heading levels via `title_frequency`, DOCX heading styles (`rag/app/manual.py:237-287`) |
| `qa` | One chunk per Q/A pair. The **question** is indexed as content (`rag/app/qa.py:254-297`) |
| `table` | Per-row chunks, column-type inference (int/float/bool/datetime/text), typed ES fields plus `field_map` saved on the KB for text-to-SQL (`rag/app/table.py:380-445,535-668`) |
| `tag` | (content, tags) pairs → `tag_kwd`. This builds a "tag KB" used for rank features (`rag/app/tag.py:27-120`) |
| `presentation`, `one`, `email`, `picture`, `resume`, `audio` | Per-slide, whole-doc, mail parts, OCR/VLM, a large rule/LLM hybrid (`resume.py` 2,771 lines), ASR |
| `knowledge_graph` | Aliased to `naive` for chunking (`task_executor.py:127`) |

### 2.5 DeepDoc: what is model-based and what is rule-based
- **Model (ONNX)**:
  - OCR text detection plus CTC recognition, `det.onnx`/`rec.onnx` with dictionary `ocr.res`
    (`deepdoc/vision/ocr.py:74-125,133-139,417`). CUDA provider if available, otherwise CPU.
  - Layout detector with 11 classes (Text, Title, Figure, Figure caption, Table, Table caption, Header,
    Footer, Reference, Equation) (`deepdoc/vision/layout_recognizer.py:33-66`). Also a YOLOv10 and an
    Ascend variant (`:189,266`). `DEEPDOC_URL` offloads to a remote service.
  - Table structure recognizer with 6 classes: table, column, row, column header, projected row
    header, spanning cell (`table_structure_recognizer.py:30-66`).
  - **XGBoost** "up/down concatenation" classifier over hand-made features decides whether two
    vertically adjacent boxes join (`deepdoc/parser/pdf_parser.py:92-101,1117-1184`).
- **Rules / classic ML**:
  - pdfplumber characters are poured into OCR-detected boxes. Boxes whose text is garbled
    (≥50% PUA/CID characters, or font-encoding garbage) are re-OCRed (`pdf_parser.py:775-881`).
  - Column detection: KMeans on x0 with k from 1 to 4 chosen by silhouette, then the page-level
    majority (`:891-973`).
  - Horizontal merge by y-distance < mean height / 3 (`:975-1011`).
  - Header, footer and reference removal. Coordinate tags `@@page\tx0\tx1\ttop\tbottom##` are carried
    inline through chunking and extracted at the end (`:1541,1935-1945`).
- Pipeline order: `__images__ → _layouts_rec → _table_transformer_job → _text_merge → _concat_downward
  → _filter_forpages → _extract_table_figure` (`pdf_parser.py:1750-1772`). PDFs over 50 pages are
  processed in windows (`:1774-1812`).

### 2.6 Dataflow pipeline (`rag/flow/*`)
- `Pipeline` subclasses the agent `Canvas` `Graph` (`rag/flow/pipeline.py:13`). `run()` walks a
  linear downstream path, passing each component's `output()` as the next component's kwargs
  (`:123-178`). Progress and logs go to Redis with a 30-minute TTL.
- Components: `File → Parser → TokenChunker | TitleChunker(hierarchy|group) → Extractor (LLM field or
  TOC) → Tokenizer (full-text tokenization + embedding) → Compiler (knowledge compilation)`.
  - Parser setups: `rag/flow/parser/parser.py:134-262`; the code path is `_code` at `:1129-1145`.
  - Token chunker: `rag/flow/chunker/token_chunker.py`.
  - Title chunker: `rag/flow/chunker/title_chunker/title_chunker.py`.
  - Extractor: `rag/flow/extractor/extractor.py:41-124`.
  - Tokenizer: `rag/flow/tokenizer/tokenizer.py:55-210`.
- The Tokenizer sets `chunk_order_int` (`tokenizer.py:146`). Embedding uses the same 0.1/0.9
  title mix (`:118-119`). Output is indexed by `run_dataflow` (`task_executor.py:810-974`), which
  embeds `questions → summary → text` if the pipeline did not.

**Strengths.** Content-addressed chunks with digest-based reuse make re-parsing cheap and idempotent.
Page-range fan-out parallelises big PDFs. A pluggable parser registry. The dry-run comparator is a
careful way to ship a rewrite. Positions travel inline and cannot drift from the text.

**Weaknesses.**
- The `DOC_BULK_SIZE` default differs between code and env.
- Chinese is the fallback language (`task_executor.py:1456-1458`, `naive.py:1130`).
- Heavy dependencies: ONNX models, XGBoost, KMeans and optional GPU.
- Two task executors kept in sync (legacy plus refactor).
- The per-page-range split cuts cross-page context: each task chunks independently.
- LLM enrichment runs per chunk with no batching, relying on the Redis LLM cache.

**EOS relevance.**
- **Transfers:** a per-file task digest `xxh64(content_hash ∥ chunker_version ∥ config)` with
  chunk-id reuse, which beats rebuilding the whole `eos.db` when any input digest changes. Also
  content-addressed chunk ids, and position markers carried inline then extracted (for code:
  `@@path:start-end##`).
- **Infra-bound:** Redis Streams, heartbeats and locks, MinIO, DeepDoc vision, XGBoost.

---

## 3. Area 2: chunking, and where chunk metadata is built

### 3.1 `naive_merge` and the chunking contract (#17799)
- Every section is split on the delimiter. Delimiter text never enters a chunk.
- Paragraphs are then grouped (`rag/nlp/__init__.py:1450-1510`) by `_merge_paragraph_groups` (`:1286-1361`):
  - **OVER_CAP** (default): accumulate until the running sum exceeds `chunk_token_num·(100-overlap)/100`,
    allowing one boundary overflow. An oversized paragraph stands alone, with no atom split.
  - **UNDER_CAP**: strict cap.
- Overlap: the tail of the previous chunk (`overlapped_percent` of its visible characters) is prepended
  unconditionally (`:1256-1268,1428-1447`).
- Delimiter grammar: every bare character is its own delimiter, and backticked strings are
  multi-character delimiters, sorted longest-first (`rag/nlp/delim.py`, `DEFAULT_DELIMITER="\n!?;。；！？"` at `:88`).
- The KB default for `naive` is `chunk_token_num=512, delimiter="\n"` (`api/utils/api_utils.py:366-377`).
  The code fallback is 128.
- Token counts use tiktoken `cl100k_base` (`common/token_utils.py:23-60`).

### 3.2 Hierarchical merges
- `hierarchical_merge(bull, sections, depth)` (`rag/nlp/__init__.py:1167-1253`):
  - lines are classified by bullet patterns (`BULLET_PATTERN`) or the layout "title";
  - each deep line is linked up to its nearest ancestor per level by binary search;
  - leaf groups are packed below about 218 tokens.
- `tree_merge` plus `Node` (`:1120-1164,1927-2006`) builds a heading tree and **emits each body block
  prefixed with its full title path**. That is breadcrumb chunking.

### 3.3 Parent/child ("mom") chunking
- When `children_delimiter` is set, the whole chunk is kept as `mom_with_weight` and the text is split
  into children (`rag/nlp/__init__.py:458-483`).
- At insert time the parent is stored once with `id = xxh64(mom)` and `available_int=0`. Children
  carry `mom_id` (`chunk_service.py:340-374`).
- Retrieval swaps the children for the parent, scored by the mean of the child similarities
  (`search.py:1085-1139`).

### 3.4 LLM enrichment at ingestion
Code: `chunk_post_processor.py:88-355`, legacy `task_executor.py:477-674`.

| Feature | Trigger | Stored fields | Notes |
|---|---|---|---|
| Auto-keywords | `parser_config.auto_keywords = topn` | `important_kwd` (list), `important_tks` | Split on `,，;；、\n`. Oversized terms are cut to the ES keyword limit (`chunk_post_processor.py:52-85`) |
| Auto-questions | `auto_questions = topn` | `question_kwd`, `question_tks` | Questions replace the content in the embedding (§4) |
| Metadata | `enable_metadata` + schema | Document-level metadata (doc-meta index) | JSON-schema prompt. Per-chunk values are merged into the document (`:171-262`) |
| Tags | KB `tag_kb_ids` | `tag_feas` = {tag: score} | First tries retrieval-based tagging (`Dealer.tag_content`, `search.py:996-1006`), otherwise a few-shot LLM (`content_tagging`, `rag/prompts/generator.py:336-384`) |

- All four are cached in Redis under `xxh64(model ∥ text ∥ kind ∥ params)` with a 24h TTL
  (`rag/graphrag/utils.py:170-187`).
- Tag scoring (`search.py:990-1020`) is a **lift ratio**: `round(0.1·(c+1)/(cnt+S) / P(tag))`, where
  `P(tag) = (c+1)/(total+S)` over the whole tag KB, S=1000, keeping the top-3.

### 3.5 TOC (table of contents) chunk
- `run_toc_from_text` (`rag/prompts/generator.py:850-936`):
  - chunks are batched at ≤1024 tokens, and an LLM proposes section titles with chunk indices;
  - numeric-only and overlong titles are filtered out;
  - a second LLM call assigns levels.
- The result is stored as a hidden chunk with `toc_kwd="toc"`, `available_int=0`,
  `page_num_int=[100000000]`, `content_with_weight = JSON [{level,title,ids:[chunk_ids]}]`
  (`task_handler.py:886-940`).
- At query time, `retrieval_by_toc` (`search.py:1022-1083`):
  - takes the document with the highest summed similarity;
  - lets the LLM score each TOC entry 0-5 (`relevant_chunks_with_toc`, `generator.py:943-968`,
    keeping scores/5 ≥ 0.3);
  - adds that score to the similarity of existing chunks, or pulls new chunks in.

### 3.6 RAPTOR (the code the "Tree" template uses)
- Default config: max_cluster 64, max_token 512, threshold 0.3, ratio 0.5, `scope: file`
  (`task_handler.py:411-423`). The task-level `clustering_method = "watershed"` is at
  `task_executor.py:1093`.
- **Clustering** (`raptor.py:475-540`):
  - L2-normalise the vectors and compute **adjacent** cosine similarities (O(N));
  - the split threshold is the `clustering_threshold` percentile of those similarities;
  - if the cluster count exceeds `ratio·N`, the threshold is lowered to the cap;
  - layers of ≤8 nodes collapse into one parent (`small_layer_collapse`);
  - a "no reduction" guard forces one cluster (`:753-845`).
  - No GMM code remains in this snapshot. Release notes v0.25.6 describe the "legacy GMM mode" and
    the "AHC (Ψ-RAG)" mode that superseded it.
- **Claims**:
  - Before clustering, the LLM extracts atomic claims per chunk in batches of 4, each with a verbatim
    `evidence.quote` and `chunk_id` (`raptor.py:64-101,139-241`).
  - `_struct_apply_evidence_gate` keeps a quote only if its whitespace-normalised form is a substring
    of the cited chunk's text, and records character offsets (`structure.py:931-1034`).
  - Leaf clusters summarise claims, not raw text (`raptor.py:619-646`).
- **Provenance**: every summary carries the de-duplicated union of its leaves' `source_chunk_ids`
  (`raptor.py:724-751`).
- Persisted rows use `raptor_kwd="raptor"`, `raptor_layer_int`, `extra.raptor_method`
  (`task_executor.py:1156-1189`), plus a tree materialiser (`raptor.py:850-893`).

### 3.7 The chunk record
Built by the parsers, `tokenize()` and `add_positions()`, `rag/nlp/__init__.py:422-429,1022-1034`.

| Field | Meaning |
|---|---|
| `id` | xxh64(content + doc_id) |
| `doc_id`, `kb_id`, `docnm_kwd` | Provenance |
| `content_with_weight` | Display text, which may contain HTML tables |
| `content_ltks`, `content_sm_ltks` | Coarse and fine token strings (space-separated), after table-tag stripping |
| `title_tks`, `title_sm_tks` | File name without extension, tokenized |
| `important_kwd`, `important_tks` | Keywords |
| `question_kwd`, `question_tks` | Generated questions |
| `page_num_int[]`, `top_int[]`, `position_int[(page,x0,x1,top,bottom)]` | Layout provenance |
| `img_id` | MinIO key for a cropped image of the chunk region |
| `doc_type_kwd` | text / table / image |
| `mom_id` | Parent chunk |
| `chunk_order_int` | Order within the document (dataflow) |
| `create_time`, `create_timestamp_flt` | Timestamps |
| `available_int` | 0 hides the row from retrieval |
| `pagerank_fea` | Rank prior |
| `tag_feas`, `tag_kwd` | Tag features |
| `q_<dim>_vec` | Embedding |
| `knowledge_graph_kwd`, `raptor_kwd`, `toc_kwd`, `compile_kwd`, `source_chunk_ids`, ... | Row-kind discriminators for derived rows (`conf/infinity_mapping.json`) |

**Strengths.**
- One explicit chunking contract, OVER_CAP versus UNDER_CAP, and a canonical delimiter parser.
- Title-path prefixing.
- Parent/child small-to-big.
- Rich, typed metadata.
- Hidden derived rows (TOC, parents) live beside the chunks.
- RAPTOR claims carry verified quotes and chunk-level provenance.

**Weaknesses.**
- Chunk size is set in tokens but split on characters. Code gets no structural chunking.
- Every enrichment is an LLM call per chunk.
- The TOC is LLM-reconstructed even for formats that have explicit structure (PDF outlines are
  extracted and stored separately, `naive.py:1521-1524`).
- The `hierarchical_merge` packing constant 218 is unexplained.

**EOS relevance.** For code the tree is known exactly (the AST), so use the Python AST (`ast` is
stdlib) and the Java lexer that EOS already has:
- index each **method** as a child with `mom_id` = the enclosing class or file section;
- prefix every chunk with its **path breadcrumb** (`pkg/File.java › Class › method`), as `tree_merge` does;
- keep a hidden **outline row** per file (the TOC analogue) with no LLM involved.
- "Questions" can be harvested for free from Javadoc/docstrings and from note titles.
- RAPTOR's adjacent-similarity watershed does not need embeddings: the same O(N) segmentation works
  on lexical Jaccard between adjacent notes or sections.

---

## 4. Area 3: embeddings

- **Provider abstraction** (`rag/llm/embedding_model.py`):
  - Roughly 50 classes are registered by their `_FACTORY_NAME` attribute through module
    introspection (`rag/llm/__init__.py`).
  - Shared template `Base._batched_encode` (`:163-203`): per-text truncation to the provider limit,
    batches (usually 16), token summing, one `EmbeddingError`, results re-ordered by `index` (`:60-64`).
  - `encode_queries` is separate so asymmetric models can use query modes (Jina `task="retrieval.query"`,
    `:627-629`; NVIDIA `input_type="query"`, `:906-908`).
  - Jina v4 multivector outputs are **mean-pooled** into one vector (`:616-619`). Late interaction is
    lost, and `MatchTensorExpr` exists but is never used.
- **Wrapper** `LLMBundle.encode` (`api/db/services/llm_service.py:158-210`): empty or whitespace text
  is replaced by `"None"`, text over 95% of the model's `max_length` is truncated, and Langfuse traces
  the call.
- **Title/content mixing** (refactor `embedding_service.py:58-117`, legacy `task_executor.py:744-793`):
  - the title vector is encoded **once**, from the first chunk's `docnm_kwd`, and tiled across all
    chunks;
  - content text = `"\n".join(question_kwd)` if questions exist, otherwise `content_with_weight` with
    table tags stripped (`embedding_utils.py:185-208`);
  - `v = w·title + (1-w)·content` with `w = parser_config.filename_embd_weight` (default **0.1**,
    `embedding_utils.py:49,178-183`);
  - there is no re-normalisation after mixing, so cosine in the engine handles it.
- **Storage**:
  - `d["q_%d_vec" % dim] = vector` (`embedding_utils.py:134-158`).
  - The ES mapping declares `dense_vector` HNSW cosine templates only for `*_512/768/1024/1536_vec`
    (`conf/mapping.json:160-203`). OpenSearch goes up to 10240 (`conf/os_mapping.json:161-249`).
  - The query-time field name is derived from the query vector's length (`search.py:104-111`), so one
    index can hold several dimensions, but a KB must use one model
    (`validate_dataset_embedding_models`).
- **Model-switch check** (`api/apps/services/dataset_api_service.py:1330-1423`):
  - samples N stored chunks and re-encodes them (content, and 0.1 title + 0.9 content);
  - allows a switch without re-embedding only if the average cosine to the stored vectors is ≥ 0.9.
- **Caches**: the Redis embed cache (24h) is used by GraphRAG and RAPTOR (`rag/graphrag/utils.py:190-238`).
  Chunk embeddings at ingestion are **not** cached.

**Strengths.**
- Clean provider interface with a query/passage split.
- The title prior is cheap: one extra call per document.
- Questions-as-embedding is a HyDE-style inversion, and it is cheap at query time.
- The drift check is a practical guard.

**Weaknesses.**
- The title vector comes only from the first chunk's `docnm_kwd`, which is fine because all chunks share a document.
- The title weight is global, not learned.
- Mean-pooling multivector outputs throws information away.
- ES mapping templates cover only four dimensions, so other dimensions get default dynamic mapping.
- No normalisation after mixing.

**EOS relevance.**
- Embeddings are excluded by C-10. If that ever changes, RAGFlow's own default ES path shows vectors
  can be a **re-scorer over ≤64 lexical candidates** (no ANN index). Brute-force cosine over `array('f')`
  BLOBs is stdlib-feasible at that size.
- The title-prior mix and the drift check (sample, re-encode, average cosine ≥ 0.9) transfer as-is.

---

## 5. Area 4: full-text (tokenizer, query builder, term weights, synonyms)

### 5.1 Tokenizer
- `rag/nlp/rag_tokenizer.py:17-60` is a thin wrapper over **`infinity.rag_tokenizer.RagTokenizer`**
  from `infinity-sdk==0.7.3` (`pyproject.toml:60`). The implementation is not in this repo.
- The C++ analyzer vendored for Go shows its parts (`internal/binding/cpp/`):
  - a darts double-array trie dictionary;
  - OpenCC traditional-to-simplified conversion;
  - a stemmer (Snowball, 16 languages per release note v0.26.4);
  - a WordNet lemmatizer;
  - thinc NER and POS.
- The API offers `tokenize` (coarse), `fine_grained_tokenize`, `tag` (POS), `freq`, `set_language`,
  `_tradi2simp`, `_strQ2B`.
- **With Infinity, `tokenize` returns the raw line** and Infinity's own `rag-coarse`/`rag-fine`
  analyzers do the work (`rag_tokenizer.py:21-35`, `conf/infinity_mapping.json`). The "tokenized
  fields" are therefore engine-dependent.

### 5.2 `FulltextQueryer.question()` has two very different paths (`rag/nlp/query.py:42-168`)
Preprocessing: full-width to half-width, traditional to simplified, lower-case, strip Lucene/Infinity
special characters (`:44-55`), then `rmWWW`, which removes question words, stop words and English
fillers ("what/how/is/the/please/…") (`common/query_base.py:38-56`).

- **"Chinese" path**. `is_chinese` (`common/query_base.py:21-30`) returns **True for any query of ≤3
  whitespace tokens**, so short English queries also take this path (`query.py:96-167`):
  - `term_weight.split` merges runs of English words into one term (`term_weight.py:183-190`);
  - every term is expanded with fine-grained sub-tokens (`OR "sub1 sub2"` plus proximity
    `("sub1 sub2"~2)^0.5`) and synonyms (`OR (syns)^0.2`);
  - it is weighted `(…)^w`, gets a multi-token proximity `("t"~2)^1.5`, and term-level synonyms
    `(tms)^5 OR (syns)^0.7`;
  - **`minimum_should_match = min_match`**, which is 0.3 by default, 0.1 on retry, and 0 when the
    vector weight is ≥0.8 (`search.py:773`);
  - keywords are capped at 32.
- **English path** (more than 3 tokens and mostly alphabetic) (`query.py:61-94`):
  - tokens are weighted by `tw.weights(preprocess=False)`;
  - each token becomes `(tk^w "syn1"^(w/4) …)`;
  - every **adjacent pair** becomes a phrase `"t1 t2"^(2·max(w1,w2))`;
  - **there is no minimum_should_match**, so any single term matches (ES gets `"0%"`).
- Output: `MatchTextExpr(fields=query_fields, matching_text, topn=100, extra_options)` plus the keyword
  list, which feeds highlighting and the term-similarity re-score.

### 5.3 Field boosts and ES similarity
- `query_fields = title_tks^10, title_sm_tks^5, important_kwd^30, important_tks^20, question_tks^20,
  content_ltks^2, content_sm_ltks` (`query.py:32-40`).
- ES runs this as `query_string` with `type=best_fields`, so the maximum across fields wins
  (`es_conn.py:250`).
- In `conf/mapping.json`, `*_tks` fields use a **scripted similarity**:
  `idf·min(tf,1)` with `idf = ln(1+(N−df+0.5)/(df+0.5)) / ln(1+(N−0.5)/1.5)`, which is binary TF and
  normalised IDF (`:8-15,72-81`). `*_ltks` (content) use default BM25 (`:82-91`). Keywords use boolean
  similarity (`:92-102`).
- OceanBase approximates the boosts by normalising column weights and summing
  `MATCH…AGAINST` scores (`ob_conn.py:165-181`, `ob_conn_base.py:510-545`).

### 5.4 Term weights (`rag/nlp/term_weight.py:192-274`)
```
w(t) = (0.3·idf(freq(t), 1e7) + 0.7·idf(df(t), 1e9)) · ner(t) · pos(t),  normalised Σw = 1
idf(s, N) = log10(10 + (N − s + 0.5)/(s + 0.5))
ner:  numeric 2, 1-2-letter [a-z] 0.01, dictionary types (corp/loca/sch/stock 3, toxic 2, func/firstnm 1)
pos:  r/c/d (pronoun/conj/adverb) 0.3, ns/nt (place/org) 3, n 2, numeric 2, else 1
OOV latin: prior freq = max(10, 300 / 2^((letters−3)/2))   (longer unknown words ⇒ rarer)
```
- `freq` comes from the tokenizer dictionary.
- `df` comes from `rag/res/term.freq`, **which is not shipped or downloaded in this snapshot**:
  `rag/res/` holds only `ner.json` and `synonym.json`, and the loader swallows `FileNotFoundError`
  (`term_weight.py:121-127`). The "document-frequency" part therefore falls back to constants and the
  OOV prior.
- The weights are **not corpus statistics of the user's KB**. They come from general dictionaries.

### 5.5 Synonyms (`rag/nlp/synonym.py:34-101`)
- Lookup order: the `rag/res/synonym.json` dictionary; then a Redis override (`kevin_synonyms`),
  reloaded at most hourly after 100 lookups; then **WordNet** synsets, only for pure `[a-z]+` tokens.
- At most 8 synonyms per token. Synonyms get a quarter of the term weight in the English path and
  0.2 or 0.7 boosts in the other path.

**Strengths.**
- Field-aware boosts: keywords ≫ questions ≫ title ≫ content.
- Binary-TF scoring on short fields, so title stuffing is not rewarded.
- Adjacent-bigram phrase boosts give cheap proximity.
- Question-word stripping.
- Dictionary-plus-hot-reload synonyms.

**Weaknesses.**
- Heavily CJK-oriented heuristics. The "≤3 tokens ⇒ Chinese" rule makes behaviour differ by query
  length. Long English queries have no minimum-should-match.
- IDF priors are not corpus-specific, and `term.freq` is missing.
- WordNet synonyms are noisy for technical text.
- Two tokenization regimes: Python for ES/OB, engine analyzers for Infinity.

**EOS relevance (high; this is the lexical core EOS already bets on).** EOS today (see
`eos-core-audit.md` §2.7) has FTS5 with **equal column weights** and no stemming or synonyms.
Borrow in this order:
1. **Column weights**: `ORDER BY bm25(search, w_title, w_body, w_terms)`, mirroring RAGFlow's ratios
   (keywords 30, questions 20, title 10, content 2).
2. **Stemming**: `tokenize='porter unicode61'`, which is built into FTS5 and costs nothing.
3. **A filler and question-word strip** like `rmWWW`. EOS's "ok, continue" false positive is exactly
   what that regex guards against.
4. **Short-query minimum-should-match**: require ⌈0.3·n⌉ terms, or all terms when n≤2.
5. **Adjacent-bigram phrase boost**: FTS5 phrase `"a b"` or `NEAR(a b, 2)`.
6. **A domain synonym dictionary** in SQLite (service aliases, Turkish/English pairs), with no WordNet.
7. Replace dictionary IDF with **corpus IDF** from `fts5vocab`, and keep RAGFlow's idea of type
   multipliers (identifier kinds instead of POS/NER): class names ×3, method names ×2, very short
   tokens ×0.01.

---

## 6. Area 5: hybrid search and the doc-store abstraction

### 6.1 The expression layer (`common/doc_store/doc_store_base.py`)
- Expression types:
  - `MatchTextExpr(fields, matching_text, topn, extra_options)`, lines 58-69;
  - `MatchDenseExpr(vector_column_name, embedding_data, dtype, distance, topn, extra_options{similarity, num_candidates})`, lines 72-87;
  - `MatchSparseExpr`, 90-103, and `MatchTensorExpr`, 106-119. **Both are unused anywhere** (a repo grep finds no users);
  - `FusionExpr(method, topn, fusion_params{"weights":"t,v"})`, 122-126;
  - `OrderByExpr`, 132-145.
- `DocStoreConnection` has 16 abstract methods: db_type, health, create/delete/exists index,
  search, get, insert, update, delete, five result accessors, and `sql` (`:148-277`).
- **Conditions are "conjunctive equivalent" dictionaries**, e.g. `{"kb_id": [...], "available_int": 1,
  "must_not": {"exists": "compile_kwd"}}`. Each backend translates them.

### 6.2 `Dealer.search` (`rag/nlp/search.py:243-416`)
1. Filters come from the request keys: kb_ids, doc_ids, id, knowledge_graph_kwd, available_int,
   entity/from/to_entity_kwd, removed_kwd, must_not (`:230-241`).
2. With no question: a filter-only scan, optionally ordered by `chunk_order_int, page_num_int,
   top_int, -create_timestamp_flt` (`:289-297`).
3. Otherwise build `matchText` (§5). Without an embedding model it runs text-only (`:301-305`).
4. The dense expression is `q_<dim>_vec`, cosine, `topn = knn_top_k` (1024),
   `num_candidates` (2048), `similarity = similarity_threshold` (`:307`).
5. Fusion weights (`:319-331`):
   - Infinity and GaussDB: `(1-w, w)`;
   - **every other backend: `"0.001,1"`**, pinned by a unit test
     (`test/unit_test/rag/test_search_fusion_weight.py:146-172`).
6. Empty-result fallbacks (`:340-399`):
   - dense-only with similarity 0.17;
   - or doc-scoped filter-only;
   - or min_match 0.1 with similarity 0.17, then dense-only.
7. Keywords are expanded with their fine-grained tokens for highlighting (`:402-409`). Highlighting
   is a Python regex that wraps `<em>` around keyword matches in `content_with_weight`, not engine
   highlighting (`es_conn_base.py:324-331`).

### 6.3 Fusion by backend (the part that differs)

| Backend | What happens | Code |
|---|---|---|
| **Elasticsearch** (default) | `bool{must: query_string, filter: kb/doc/available, should: rank_feature}` with boost `1-1=0`. `knn{k=knn_top_k, num_candidates, similarity=threshold, filter=<the same bool incl. the text must>}`, so kNN runs only among lexical matches. Take the top `rerank_candidates_count` (64). A second kNN-only call returns scores for those ids (ES reports cosine kNN `_score` as (1+cos)/2). Final: `(1-w)·token_coverage + w·knn_score + rank_fea`. | `es_conn.py:201-276`, `search.py:533-564,604-629,827-840` |
| **Infinity** | `match_text(filter)` plus `match_dense(filter = filter_fulltext AND cond)` (also lexical-gated), then `fusion weighted_sum` with **atan normalisation**. `_score = SCORE + pagerank_fea`, then sort. No local re-score (`tsim = vsim = sim`). | `infinity_conn.py:219-268,326-329`, `search.py:803-808` |
| **OpenSearch** | kNN filter keeps **only structural filters**. `{"hybrid": {queries: [bool, knn]}}` through a search pipeline (`min_max` normalisation, `arithmetic_mean` with **static** weights `[0.5,0.5]` from config, *not* `vector_similarity_weight`). Without the pipeline (<2.10 or no permission) it is vector-only. Then the ES-style local re-score. | `opensearch_conn.py:107-150,405-460` |
| **OceanBase/SeekDB** | With the ES hybrid client: ES-like. Otherwise SQL. The default `USE_FULLTEXT_FIRST_FUSION_SEARCH=true` takes the full-text top (vec+ft) candidates, filters by `1-cosine_distance ≥ threshold`, and scores `relevance·(1-w) + (1-cos_dist)·w + pagerank/100`. The alternative is a FULL OUTER JOIN of the two top-n lists. It skips a leg when w≤0 or w≥1. Local `rerank()` with raw cosine. | `ob_conn.py:582-596,761-880`, `ob_conn_base.py:135-139` |
| **SereneDB** | One SQL statement: BM25 leg normalised by `s / MAX(s) OVER ()`, FULL OUTER JOIN with the ANN leg (inner product on an L2-normalised shadow column), `(1-w)·bm25_norm + w·cos + pagerank/100`. Minimum-should-match and tag boosts are skipped. | `serenedb_conn.py:16-54,458-489` |
| **GaussDB** | Fusion and pagerank in SQL. Tag features applied locally. | `search.py:819-826` |

### 6.4 `Dealer.retrieval` (`rag/nlp/search.py:708-928`)
- Requires `page·page_size ≤ rerank_candidates_count`, and **page 1 when a reranker is used**
  (`:744-748`). The docstring explains why: re-ranked windows are not stable across pages (`:730-739`).
- Prunes chunks of deleted documents (`:777`, backed by a 120 s TTL "doc exists" LRU at `:80-148`).
- Scoring branch per backend (`:793-840`). Stable sort (`:848`). **Post-threshold**
  `sim ≥ similarity_threshold`, skipped when w=0 (`:851-853`).
- Page slice (`:861-863`).
- `doc_aggs` counts **all** chunks above the threshold per document (`:904-924`).
- `similarity_threshold` is used **twice**: as the raw-cosine floor inside kNN, and as the floor on
  the fused score.

**Strengths.**
- One retrieval façade over seven engines.
- Engine-side vector math (ES) avoids shipping vectors to Python.
- Explicit, tested fallbacks for empty results.
- A deterministic stable sort.
- The pagination limits under reranking are documented honestly.

**Weaknesses.**
- **Semantics depend on the backend**: gated versus union, and vector scales of (1+cos)/2 versus raw
  cosine versus atan-normalised scores. So `similarity_threshold`/`vector_similarity_weight` mean
  different things per engine.
- In ES the **candidate set is chosen by vector similarity alone, inside the lexical gate**. A strong
  lexical match with a middling vector outside the top-64 is dropped, and a semantic match below the
  30% should-match never enters (only the zero-hit fallback helps).
- Magic numbers: 0.17, 0.3, 0.8, 0.001.
- Commented-out helpers show that ungating at high vector weight was tried and reverted
  (`es_conn.py:34,75-97,268`, `infinity_conn.py:46-64,218,253-255`).

**EOS relevance (the fusion formula transfers directly).** In SQLite:
```sql
-- lexical leg, normalised within the window (SereneDB shape)
WITH lex AS (SELECT rowid AS id, -bm25(search, 10.0, 1.0, 5.0) AS s
             FROM search WHERE search MATCH :q ORDER BY s DESC LIMIT 200),
lexn AS (SELECT id, s / (SELECT MAX(s) FROM lex) AS sn FROM lex)
SELECT c.*, 0.7*lexn.sn + 0.3*:cov(c) + c.prior/100.0 AS score  -- cov = coverage similarity in Python
FROM lexn JOIN chunks c ON c.id = lexn.id ORDER BY score DESC LIMIT :k;
```
Recommended EOS shape:
1. FTS5 BM25 leg with column weights.
2. Python **coverage** re-score over the top 64. EOS already computes IDF coverage; add RAGFlow's
   bigram term at weight 0.6.
3. An additive **prior** (feedback, source priority).
4. When several independent legs exist (notes, index, symbols, runs), fuse with **RRF k=60**, which is
   what RAGFlow's newest navigation code does to avoid calibrating thresholds
   (`rag/advanced_rag/harness/tools/navigation.py:1529-1549`).

---

## 7. Area 6: reranking and rank features

### 7.1 Model reranking (`search.py:664-703`)
- The reranker gets the **natural** `content_with_weight`, not tokens; `remove_redundant_spaces` would
  damage multilingual text (`:684-693`).
- `sim = tkweight·token_coverage + vtweight·rerank_score + rank_fea`. **The embedding similarity is
  not used at all** when a reranker is set.
- Weights: `tkweight = 1 - vector_similarity_weight`, `vtweight = vector_similarity_weight`
  (`:793-801`), so the default is 0.7 lexical and 0.3 reranker.
- Providers (`rag/llm/rerank_model.py`, 25 `_FACTORY_NAME`s: Jina, Cohere/VLLM, Voyage,
  Tongyi, SiliconFlow, NVIDIA, Xinference, LocalAI, HuggingFace/TEI, Bedrock, …):
  - `Base.similarity` enforces `RERANK_TOKEN_LIMIT_MODE` (`truncate|passthrough|raise_error`,
    `:35-38,80-88`);
  - **min-max normalisation only when a provider leaves [0,1]** (e.g. NVIDIA logits), and clamping
    when the spread is below 1e-3 (`:105-130`).
- Built-in rerankers were removed in v0.18.0 ("minimal impact on retrieval rates but significantly
  increase retrieval time", `docs/release_notes.md:950`).

### 7.2 Rank features
- **KB pagerank** (`Knowledgebase.pagerank`, an integer 0..100, `api/db/db_models.py:1287`):
  - stamped on every chunk as `pagerank_fea` at ingestion (`chunk_service.py:231-232`);
  - rewritten by `update_by_query` when changed, and removed at 0
    (`api/apps/services/dataset_api_service.py:393-400`);
  - ES maps `*_fea` to `rank_feature` (`conf/mapping.json:144-151`).
  - Locally **the raw value is added** to a similarity that otherwise sits roughly in [0,1]
    (`search.py:528-531`). In practice it is a KB **priority tier**, as the v0.15.0 note describes:
    "search across multiple datasets".
- **Per-chunk feedback** (`api/db/services/chunk_feedback_service.py:16-316`, off by default,
  `CHUNK_FEEDBACK_ENABLED`):
  - a thumbs up or down moves `pagerank_fea` by an integer budget of 1, clamped to [0,100];
  - `relevance` mode splits the unit across the cited chunks in proportion to their best retrieval
    score, using largest-remainder integer allocation (`:84-138`);
  - `uniform` mode gives ±1 to each chunk;
  - the update is atomic (painless script in ES, `es_conn.py:38-60,503-556`; SQL
    `GREATEST/LEAST` in GaussDB).
- **Tag features**:
  - chunks carry `tag_feas = {tag: lift}` (§3.4);
  - the query gets `rank_feature = label_question(q, kbs)` (`rag/app/tag.py:123-143`), which runs a
    full-text query over the tag KB and takes the top-3 lifts;
  - the local score is `10 · cos(query_tags, chunk_tags)` (`search.py:501-526`);
  - in ES it is also a `rank_feature` should-clause on `tag_feas.<tag>` (`es_conn.py:272-276`), but
    **that is zeroed by the bool boost of 0** whenever a text clause exists.
- `Dealer.retrieval` defaults to `rank_feature={pagerank_fea: 10}`, but chat and the API pass
  `label_question(...)`, which is **None** when no tag KB is configured (`dialog_service.py:770`).

**Strengths.** Cheap, explainable priors. The feedback loop is bounded, integer and atomic, with a
relevance-weighted credit split. Tag lift is computed without an LLM from the tag KB's own statistics.

**Weaknesses.**
- Scale mixing: `+pagerank` in the range 0..100 and `+10·tag_cos` swamp a [0,1] similarity and bypass
  `similarity_threshold`.
- The ES-side rank features are neutralised.
- Feedback is global per chunk, not per query.
- Cross-encoders get about 30% weight by default, which undersells them.

**EOS relevance.** EOS's "learning" is limited today (counters and lessons, no adaptive ranking).
- **Adopt** the bounded feedback prior: `note.prior ∈ [0,100]`, updated from `run finish --outcome`
  and `note eval` results with a relevance-split integer budget. Apply it as `score +
  prior/100·α`, a *scaled* prior rather than RAGFlow's raw add.
- **Adopt** tag lift: EOS notes have tags, and `P(tag|query-neighbourhood)/P(tag)` is a pure-SQL
  statistic.
- **Infra-bound:** ES `rank_feature` fields and painless scripts.

---

## 8. Area 7: knowledge graph (GraphRAG)

### 8.1 Build (`rag/graphrag/general/index.py:256-644`, KB-level task `graphrag`)
- **Defaults** when the KB has none (`task_handler.py:496-521`):
  - entity_types organization/person/geo/event/category;
  - **method `light`** (LightRAG prompts);
  - batch_chunk_token_size 4096, retries 2, and generous timeouts.
- **Per document**:
  - chunks are loaded in position order and **concatenated into ≤4096-token batches** (`:323-350`);
  - up to 4 documents run in parallel, with retry and exponential backoff (`:149-187,354-437`);
  - a per-document **subgraph row** is the checkpoint: if it exists, extraction is skipped (`:209-253,369-373`).
- **Extractors** (`_select_extractor`, `:122-138`):
  - `general` (MS GraphRAG prompt, **2 "gleaning" passes** with a continue/loop Y/N check,
    `general/graph_extractor.py:94-147`). A tiktoken logit-bias dict is built but never passed:
    `_loop_args` at `:84` is unused.
  - `light` (LightRAG prompt with examples, the same gleaning, `light/graph_extractor.py:74-126`).
  - `ner`: spaCy NER plus "stacking" keyword extraction. **Relations are sentence co-occurrence edges
    with no LLM**, optionally TF-weighted (`ner/graph_extractor.py:16-35,328-520`).
- **Record format**: `("entity"<|>NAME<|>TYPE<|>DESC)` and `("relationship"<|>SRC<|>TGT<|>DESC<|>KEYWORDS<|>STRENGTH)`.
  Names are upper-cased, edges are undirected with sorted endpoints, and entities of types not
  requested are dropped (`rag/graphrag/utils.py:344-387`, `general/extractor.py:114-129`).
- **Merge inside a document** (`extractor.py:259-290`):
  - entity type = majority vote;
  - descriptions joined with `<SEP>`, **truncated to 512 tokens**, and LLM-summarised only above
    12 parts (`:338-361`);
  - edge weights summed, keywords and source ids unioned.
- **Relation sanity filter**: drops relations whose description is a negative judgment ("no direct
  relationship…") or whose sentence subject matches neither endpoint (`index.py:676-728`).
- **Global merge**:
  - under a Redis lock `graphrag_task_<kb>` (`index.py:471-533`), `graph_merge`
    (`utils.py:306-336`) appends descriptions and sums weights;
  - node `rank = degree`, then **`nx.pagerank`** on every node (`index.py:840-842`);
  - `set_graph` rewrites the rows.
- **Entity resolution** (`rag/graphrag/entity_resolution.py:73-197`):
  - block by entity type, and consider only pairs where one side is newly added;
  - cheap candidate test `is_similarity` (`:265-289`): reject if the differing character bigrams
    contain a digit; for English, Levenshtein ≤ ⌊min(len)/2⌋; for CJK, character-set overlap ≥ 0.8;
  - LLM yes/no in batches of 100 pairs, 5 concurrent;
  - merge connected components (`extractor.py:292-336`) and recompute pagerank.
  - Checkpoints live in Redis (`rag/graphrag/checkpoints.py`). Phase markers in Redis let a resumed
    run skip finished phases (`phase_markers.py`, `index.py:463-632`).
- **Communities** (`general/leiden.py:72-137`, `community_reports_extractor.py:58-190`):
  - graspologic `hierarchical_leiden(max_cluster_size=12, seed=0xDEADBEEF)` on the **largest connected
    component only**, so nodes outside the LCC never get a community;
  - community weight = Σ(rank·weight) over its nodes, normalised by the maximum per level;
  - communities with fewer than 2 entities are skipped;
  - the LLM report has the JSON schema `{title, summary, findings[{summary, explanation}], rating, rating_explanation}`;
  - report ids are deterministic from (kb, title), inserted first, with stale ids pruned afterwards (`index.py:937-1012`).

### 8.2 Persistence: rows in the chunk index, all with `available_int=0`

| `knowledge_graph_kwd` | Content | Other fields | Vector |
|---|---|---|---|
| `graph` | `node_link_data` JSON of the whole KB graph | `source_id` = doc ids, `removed_kwd` | none |
| `subgraph` | Per-document subgraph JSON (checkpoint, rebuild source) | `source_id=[doc]` | none |
| `entity` | JSON meta (description, type, source_id, pagerank, …) | `entity_kwd`, `entity_type_kwd`, `content_ltks` = description tokens, `rank_flt` = pagerank, **`n_hop_with_weight`** = precomputed 2-hop paths with edge weights (`utils.py:803-835`) | embedding of the **name only** |
| `relation` | JSON meta | `from/to_entity_kwd`, `weight_int`, `important_kwd` = keywords | embedding of `"A->B: description"` |
| `community_report` | `{report, evidences}` | `docnm_kwd` = title, `weight_flt`, `entities_kwd` | none |

- Code: `rag/graphrag/utils.py:414-499,550-755`, `index.py:937-967`.
- `set_graph` builds every new row first, then deletes the old ones, then inserts (`utils.py:560-756`),
  so a crash never leaves a half-deleted graph.
- `rebuild_graph` recomposes the KB graph from subgraph rows (`:869-903`).
- **Provenance granularity:** legacy entity `source_id` holds **document** ids. The extractor's
  `chunk_key` is the doc_id (`extractor.py:160`), so graph facts cannot be traced to a chunk.

### 8.3 KG retrieval (`rag/graphrag/search.py:139-275`)
1. **Query rewrite** (MiniRAG prompt): the LLM returns `answer_type_keywords` (at most 3, from an
   "answer type pool") and `entities_from_query` (at most 5) (`:46-66`). **The pool comes from
   `ty2ents` rows that no code in this snapshot writes** (a repo grep finds only readers:
   `utils.py:838-854`, Go `internal/service/graph/retrieval.go:57-64`), so the pool is always empty.
2. Entities: dense kNN of `", ".join(entities)` against entity-name vectors, similarity ≥ 0.3, N=56
   (`:110-117`). Entities by type: top by `rank_flt` (`:128-137`). Relations: dense kNN of the whole
   question against relation vectors (`:119-126`).
3. Scoring (`:171-221`):
   - **P(E|Q) ∝ pagerank · sim**, and entities also found by type get sim ×2;
   - every edge on a matched entity's 2-hop path gets `sim/(2+i)` at hop i;
   - relation score `sim·(1 + nhop_sim + #endpoints in type-entities)`, then
     `score = sim·pagerank`, where pagerank for a relation means its weight.
   - The top 6 entities and top 6 relations are kept, within an 8196-token budget.
4. **Community reports**: the top `comm_topn=1` by `weight_flt` whose `entities_kwd` intersects the
   matched entities (`:277-295`).
5. Output: **one synthetic chunk** made of CSV tables (`---- Entities ----`, `---- Relations ----`,
   `---- Community Report ----`), with `similarity=1.0`, `doc_id=""`, `docnm_kwd="Related content in
   Knowledge Graph"` (`:261-275`). Chat inserts it at **position 0** of the context
   (`dialog_service.py:783-789`), and the default tenant chat model does the rewrite.

### 8.4 Successor: knowledge-compilation "structure" rows (v0.27.0+)
- `rag/advanced_rag/knowlege_compile/structure.py:1381-1493` writes entity and relation rows with
  **`source_chunk_ids`** (chunk-level provenance) and verified `evidence` quotes (`:971-1034`).
- Other fields: `compile_kwd` (list/set/hypergraph/tree/timeline/…), `scope_kwd` (doc|dataset),
  `mention_count_int`, `name_kwd`, and a stable `row_id` hashed from the payload and template.
- Dedup: exact name, then kNN candidates, then batched LLM judgment, at document or dataset scope
  under a Redis lock (`structure.py:59-72,2120-2360`).
- Newer agentic navigation searches **claim rows** with two legs, BM25 and kNN, fused by **RRF k=60**
  with no thresholds (`navigation.py:1620-1725`). An internal note says flat scoring beats top-down
  tree descent, with routing accuracy "20.2% vs 39.3%" (`navigation.py:1099-1104,1124-1129`).

**Strengths.**
- The graph reuses the chunk index, so it needs no extra store.
- Checkpoints at every expensive phase.
- Precomputed n-hop paths keep query-time expansion to one lookup.
- The pagerank prior. Cheap string blocking before LLM entity resolution.
- The NER method needs no LLM.
- The successor adds chunk-level provenance and verified quotes.

**Weaknesses.**
- Legacy provenance stops at the document.
- Entity vectors cover names only. The type pool is dead (`ty2ents`).
- Leiden runs on the LCC only.
- Results are serialised as CSV in one pseudo-chunk that cannot be cited (`doc_id=""`).
- Many LLM calls: gleaning plus summaries plus resolution plus reports.
- Magic scoring constants: ×2, 1/(2+i), ×10.

**EOS relevance.** The code graph comes from static analysis (graphify, EOS's index), not from an LLM,
which removes the expensive half. Transferable pieces:
- Store graph nodes and edges as rows beside the chunks, with a `kind` discriminator.
- Precompute each symbol's 2-hop neighbourhood with edge weights for cheap expansion.
- Rank nodes by `pagerank × lexical match` (pagerank is ~30 lines of stdlib power iteration).
- Expand relations with hop-decayed scores.
- Detect communities with a pure-Python label propagation or Louvain as a stand-in for graspologic,
  labelled deterministically, with summaries written only on demand.
- Borrow RAGFlow's **blocking and digit-bigram guard** for alias resolution (`OrderCapture` vs
  `order-capture`; ticket-key variants).
- Keep chunk- and line-level provenance, which the legacy RAGFlow graph lost.

---

## 9. Area 8: context assembly for chat

### 9.1 `async_chat` (`api/db/services/dialog_service.py:594-994`)
1. No KB and no web search → plain LLM chat (`:600-603`).
2. Models (`get_models`, `:355-395`). `max_tokens` = the model config's value, or 8192 (`:630`).
3. `questions` = the last 3 user turns (`:657`). Document scope comes from kwargs or the message.
   **Metadata filter** (`common/metadata_utils.py:153-277`):
   - `auto` (the LLM writes conditions over the whole metadata inventory, `generator.py:527-568`);
   - `semi_auto` (only the selected keys, optional operator constraints);
   - `manual`.
   - It is pushed down to the doc-meta index when possible.
   - **auto/semi-auto fail open**: no hits → `None` → search everything. **Manual fails closed**:
     `["-999"]` (`metadata_utils.py:239-275`).
4. If any KB has a `field_map` (table parser), **text-to-SQL** runs first over the doc engine's SQL
   (`use_sql`, `:682-717,997-…`, `es_conn_base.py:344-379`). Vector search is the fallback.
5. `refine_multiturn` (on by default for new chats, `api/apps/restful_apis/chat_api.py:95-120`):
   `full_question` rewrites the last question from the conversation, and the prompt receives
   today/yesterday/tomorrow dates (`generator.py:271-304`). Otherwise only the last question is used
   (`:736-739`).
6. `cross_languages`: the LLM translates into several languages, joined by newlines
   (`generator.py:307-333`). `keyword`: LLM keywords are appended to the question (`:741-745`).
7. `retriever.retrieval(" ".join(questions), …, page 1, top_n, threshold, w, knn_top_k=top_k,
   rerank, rank_feature=label_question, rerank_candidates_count)` (`:757-772`).
8. `toc_enhance` → `retrieval_by_toc` (`:773-776`). Then `retrieval_by_children` swaps in parents
   (`:777`). Web search chunks are appended (`:778-782`). The KG chunk is inserted at 0 (`:783-789`).
   Optional document-metadata enrichment follows (`:791-797`).
9. **`kb_prompt(kbinfos, max_tokens)`** (`generator.py:140-185`):
   - one block per chunk: `ID: n`, `├── Title:`, `├── URL:`, `├── <metadata k>: v`, `└── Content:`;
   - **the token budget covers the whole rendered block**, and the loop **stops at the first block**
     that would push past 97% of `max_tokens`, even if later, smaller blocks would fit (`:172-184`).
10. Empty knowledge plus `empty_response` → the canned answer, unless attachments exist (`:806-817`).
11. The system prompt gets `{knowledge}` (auto-appended when missing). The citation guidelines are
    appended when `quote` is set (`citation_prompt`, `generator.py:226-233`, and
    `rag/prompts/citation_prompt.md`), and so is the chat history.
12. **`message_fit_in(msg, 0.95·max_tokens)`** (`generator.py:69-137`):
    - when over budget, drop every turn except system and last;
    - if the system message is over 80% of the total, keep the last message and trim the system
      (the knowledge) to fit, token-accurately with tiktoken, which can cut a block mid-way;
    - otherwise trim the last user message.
    - `gen_conf.max_tokens` is clamped to what remains (`:852-853`).
13. Stream the answer, then `decorate_answer` (§10). Timing and token statistics are appended to the
    "prompt" shown in the UI (`:901-926`).

### 9.2 Search app (`async_ask`, `:1705-1830`)
- `page_size=12`, similarity 0.1, vector weight 0.3.
- System prompt `ask_summary.md`, which does **not** ask for citations.
- Citations are always post-hoc through `insert_citations` with **tkweight 0.7 / vtweight 0.3** (`:1798`).

**Strengths.**
- One linear, readable pipeline.
- The budget is measured on rendered blocks.
- Multi-turn rewrite with date anchoring.
- The metadata filter can be pushed down.
- SQL for tabular KBs.
- Retrieval, rerank and generation timings are exposed to the user.

**Weaknesses.**
- Prefix-stop packing drops smaller later chunks.
- `message_fit_in` cuts knowledge by token count, not by block.
- Several LLM pre-calls (rewrite, translate, keywords, metadata filter, KG rewrite, TOC) add latency.
- The KG/TOC helpers use the **tenant default** chat model rather than the dialog's.
- auto/semi-auto metadata filters fail open.

**EOS relevance.**
- EOS's `build_context` has the same prefix-stop behaviour (`eos-core-audit.md` §2.8), and neither
  system packs past the first oversized item. A better policy: skip the item and keep packing smaller
  ones, then list what was omitted, which EOS already does.
- Date-anchored rewrite and "fail open vs fail closed" per filter type are good design notes.
- Multi-turn rewrite and translation are LLM-bound and optional for EOS.

---

## 10. Area 9: provenance and citations

### 10.1 LLM-written citations plus repair (primary in chat)
- The model is told to write `[ID:i]`, at most 4 per sentence, never ranges (`citation_prompt.md`,
  plus the suffix at `generator.py:228-233`).
- `decorate_answer` (`dialog_service.py:855-942`):
  - if `[ID:n]` markers are present, collect them;
  - `repair_bad_citation_formats` normalises `(ID: 12)`, `[ID: 12]`, `【ID: 12】` and `ref12`, and
    normalises Arabic-Indic digits (`:530-583`);
  - cited chunk indices map to their documents, and `doc_aggs` shrinks to the cited documents;
  - vectors are stripped.
- The agentic path adds `[ID:Slot N]` rewriting and range expansion (`rag/advanced_rag/agentic_rag.py:220-276`).

### 10.2 Post-hoc insertion (fallback, and the search app) (`Dealer.insert_citations`, `search.py:422-499`)
- Split the answer into sentences: CJK, Latin and Arabic punctuation, with code fences kept whole.
  Pieces under 5 characters are dropped.
- Embed each piece (one batch). Chunk vectors are **hydrated on demand**, because main ES retrieval
  no longer fetches vectors (`dialog_service.py:65-109`, `search.py:566-602`).
- For each piece: `sim = tkweight·token_coverage + vtweight·cosine` (`query.py:170-178`).
  `mx = 0.99·max(sim)`. Cite every chunk with `sim > mx`, i.e. within 1% of the best, capped at 4.
- The threshold starts at **0.63** and is multiplied by **0.8** until something is cited or it
  reaches 0.3 (`:472-481`).
- **Each chunk is cited at most once in the whole answer** (`seted`, `:483-497`).

### 10.3 Index base mismatch (apparent bug in the Python chat path)
- `_kb_block(ck, len(out) + 1, …)` renders `ID: 1..n` (`generator.py:176`).
- The decorator maps `[ID:i]` to `kbinfos["chunks"][i]` (`dialog_service.py:881-888`), and the web
  client uses `reference.chunks[chunkIndex]` and displays `chunkIndex+1`
  (`web/src/components/markdown-content/index.tsx:333-352`, `web/src/utils/citation-utils.ts:33-45`).
  Both are 0-based.
- Post-hoc `insert_citations` emits 0-based ids (`search.py:480`).
- An empty-content chunk is skipped by `kb_prompt` and shifts every later ID
  (`test/unit_test/rag/prompts/test_kb_prompt_metadata.py:125-150` asserts the skip, not the numbering).
- The Go port states it plainly: "A 1-based render made every marker point one chunk off — or past
  the end" and switched to `KBPromptZeroBasedWithSourceIndices` (`internal/rag/prompts/generator.go:180-218`).

### 10.4 Reference payload and positions
- `retrieval()` chunk dict (`search.py:881-899`): `chunk_id, content_ltks, content_with_weight, doc_id,
  docnm_kwd, kb_id, important_kwd, tag_kwd, image_id, similarity, vector_similarity, term_similarity,
  vector, positions(position_int), doc_type_kwd, mom_id, row_id`.
- `chunks_format` renames the fields for the API/UI as `{id, content, document_id, document_name,
  dataset_id, image_id, positions, url, similarity, vector_similarity, term_similarity, row_id,
  doc_type, document_metadata}` (`generator.py:41-66`).
- The reference is `{total, chunks[], doc_aggs[{doc_name, doc_id, count}]}`. It is stored per assistant
  turn in `Conversation.reference` (a JSON list aligned with the messages,
  `api/db/services/conversation_service.py:188-229`, `db_models.py:1499-1508`).
- Positions `(page, x0, x1, top, bottom)` drive PDF highlighting. `image_id` points at the MinIO crop.

### 10.5 Evidence gates (knowledge compilation)
`_struct_locate_evidence` collapses whitespace runs, keeps an index map back to the original offsets,
and finds the quote as an exact substring of the cited chunk, returning `(start, end)`. `soft` mode keeps
a claim without evidence, and `hard` mode drops it (`structure.py:931-1034`).

**Strengths.** Two-tier citations (model first, similarity fallback). Tolerant repair of malformed
markers. Citations are tied to chunk ids and the retrieval scores are carried along. Layout positions
come for free. Deterministic quote validation with offsets.

**Weaknesses.**
- Positional IDs: the off-by-one above.
- The post-hoc threshold loop is heuristic, and it forbids citing the same chunk twice.
- The KG pseudo-chunk has no document.
- References are stored per turn as full chunk copies (bulky JSON in MySQL).

**EOS relevance (high).**
1. Use **stable, non-positional citation keys**, e.g. note slug or `path:Lstart-Lend`, or a short
   hash, never array indices.
2. Adopt the **evidence gate** for anything EOS stores as a claim or lesson: the quote must be found
   (after whitespace normalisation) in the cited file at the cited lines, and the offsets are recorded.
   This is deterministic, stdlib-only, and blocks hallucinated provenance.
3. A cheap post-hoc attribution fallback (coverage similarity with a falling threshold) is
   implementable without embeddings.

---

## 11. Area 10: persistence model

**Relational (peewee; MySQL default, Postgres, OceanBase and GaussDB pooled with retry, `db_models.py:417-809`):**

| Model | Retrieval-relevant fields |
|---|---|
| `Knowledgebase` (`:1265-1321`) | `embd_id`/`tenant_embd_id`; `similarity_threshold=0.2`; `vector_similarity_weight=0.3`; `parser_id`, `parser_config` (JSON: chunk config, raptor, graphrag, tag_kb_ids, field_map…); `pagerank`; task ids for graphrag, raptor, wiki, skill and structure merges; `language` |
| `Document` (`:1324-1351`) | `kb_id`, `parser_id`/`parser_config`, `type`, `name`, `location`, `size`, `token_num`, `chunk_num`, `progress`, **`content_hash`** (xxhash128), `run`, `status` |
| `Task` (`:1434-1457`) | `doc_id`, `from_page`/`to_page`, `task_type`, `priority`, `progress`, **`digest`**, **`chunk_ids`** (space-joined), `retry_count` |
| `Dialog` (`:1460-1496`) | `llm_id`, `llm_setting`, `prompt_config` (system/quote/keyword/refine_multiturn/toc_enhance/use_kg/cross_languages/reasoning/…), `meta_data_filter`, `similarity_threshold=0.2`, `vector_similarity_weight=0.3`, **`top_n=6`**, **`rerank_candidates_count=64`**, **`top_k=1024`**, `rerank_id`, `kb_ids` |
| `Conversation` (`:1499-1508`) | `message` (JSON), **`reference` (JSON list per turn)** |
| `Search` (`:1637-1682`) | `search_config` defaults. The key `"chat_settingcross_languages"` looks like a typo and is never read |
| Others | `File`/`File2Document`, `Tenant`/`TenantLLM`/`TenantModel*`, `UserCanvas`, `PipelineOperationLog`, `CompilationTemplate(Group)`, `Connector`, `Memory` |

**Doc store** (schemas `conf/mapping.json`, `conf/infinity_mapping.json`, `conf/os_mapping.json`):
- ES uses dynamic templates by suffix: `*_int|_long|_flt` numeric, `*_tks` scripted-similarity text,
  `*_ltks` BM25 text, `*_kwd|_id` keyword, `*_with_weight` stored keyword, `*_fea` rank_feature,
  `*_feas` rank_features, `*_<dim>_vec` dense_vector (`mapping.json:25-212`).
- Infinity uses an explicit column list with analyzers and JSON columns for provenance lists.
- Metadata goes to a separate `ragflow_doc_meta_<tenant>` index.

**Blob store**: original files by (bucket = kb, name = location), and chunk image crops as JPEG at
`<kb_id>/<chunk_id>` (`rag/utils/base64_image.py:32-82`).

**Redis**:
- task streams and consumer groups;
- cancel flags;
- heartbeats (ZSET) and the executor set;
- distributed locks (`RedisDistributedLock`, token-matched delete, `redis_conn.py:537-565`);
- LLM cache (24h), embedding cache (24h), tag cache (10 min) (`graphrag/utils.py:170-259`);
- synonym override;
- pipeline logs (30 min);
- GraphRAG checkpoints and phase markers;
- document chunking counters.

**Strengths.** Each store does what it is good at. The doc engine carries every derived artifact under
one schema. Everything is content-addressed or digest-guarded.

**Weaknesses.**
- No cross-store transactions, hence `_prune_deleted_chunks` at query time (`search.py:150-228`) and
  best-effort rollbacks (`chunk_service.py:456-520`).
- Seven doc-engine back-ends drift apart (§6.3).
- References are duplicated per conversation turn.

**EOS relevance.** Put everything in one SQLite file:
- `docs(id, path, content_hash, …)`;
- `chunks(id TEXT PRIMARY KEY /*xxh-like*/, doc_id, kind, parent_id, title, symbols, questions,
  body, start_line, end_line, available, prior, tags_json)`;
- `chunks_fts` (FTS5, external content, porter unicode61);
- `edges(src, dst, kind, weight)`;
- `tasks(doc_id, digest, chunk_ids)`;
- `llm_cache(key, value, expires_at)`;
- `feedback(chunk_id, delta, ts)`.

Foreign keys and one transaction per file remove RAGFlow's consistency safety nets. The per-kind
discriminator plus `available` flag mirrors RAGFlow's hidden rows, with a partial index or a separate
FTS table so hidden rows never reach FTS.

---

## 12. Area 11: retrieval configuration surface

| Parameter | Default (where) | Effect |
|---|---|---|
| `similarity_threshold` | 0.2 (KB, Dialog, Search, `/retrieval` API, `chunk_api.py:464`); 0.0 in dataset search (`dataset_api_service.py:1061,1451`); 0.1 in `async_ask` | kNN raw-cosine floor **and** floor on the fused score (skipped when w=0) |
| `vector_similarity_weight` (w) | 0.3 (KB, Dialog, Search, API); agent tool `1 - keywords_similarity_weight(0.5)` = 0.5 (`agent/tools/retrieval.py:74,197-206`) | ES: final `(1-w)·coverage + w·knn`; Infinity: engine fusion weights; w≥0.8 turns off min_match |
| `top_n` (page_size) | Dialog 6; API 30 (`api/utils/pagination_utils.py:18`); ask/mindmap 12; agent 8 | Chunks returned |
| `top_k` / `knn_top_k` | 1024 (max 2048 in search API) | kNN `k` |
| `knn_num_candidates` | 2048, or ≥ knn_top_k | HNSW candidates |
| `rerank_candidates_count` | 64 (Dialog, API); 100 (Search app) | Candidate window re-scored; must be ≥ page·size |
| `rerank_id` | "" | Replaces vector sim with the reranker score in the blend |
| `use_kg` | False | Prepends the KG synthetic chunk |
| `toc_enhance` | False | LLM TOC scoring and chunk injection |
| `keyword` | False | Appends LLM keywords to the query |
| `cross_languages` | [] | LLM translation of the query |
| `refine_multiturn` | True for new chats | LLM rewrite of the last question |
| `quote` | True for KB chats | Citation prompt and decoration |
| `meta_data_filter` / `metadata_condition` | {} | auto/semi_auto/manual document scoping |
| `highlight` | False | Regex `<em>` on keywords |
| `include_knowledge_compilation` | True (API) | `must_not exists compile_kwd` when False |
| `reasoning` | 0 | 1..4 → agentic RAG thinking modes |
| Ingestion: `chunk_token_num` | 512 (naive default), 128 code fallback | |
| `delimiter` | `"\n"` (KB default); `"\n!?;。；！？"` code default | |
| `overlapped_percent`, `children_delimiter` (`parent_child`), `auto_keywords`, `auto_questions`, `topn_tags=3`, `tag_kb_ids`, `filename_embd_weight=0.1`, `layout_recognize="DeepDOC"`, `task_page_size` 12/22, `toc_extraction`, `raptor{…}`, `graphrag{…}` | `api/utils/api_utils.py:356-420` | |
| Env | `DOC_ENGINE`, `DOC_BULK_SIZE` (32 code / 4 env), `EMBEDDING_BATCH_SIZE=16`, `MAX_CONCURRENT_TASKS=5`, `MAX_CONCURRENT_CHATS=10`, `RERANK_TOKEN_LIMIT_MODE`, `CHUNK_FEEDBACK_ENABLED/WEIGHTING`, `OS hybrid_search_weights`, `USE_FULLTEXT_FIRST_FUSION_SEARCH` | |

---

## 13. Area 12: benchmarks and tests

- **`rag/benchmark.py`, relevance**:
  - indexes MS MARCO v1.1, TriviaQA or MIRACL passages directly (bypassing parsers), embeds them,
    and inserts into a fresh index;
  - queries through `settings.retriever.retrieval(q, …, page 1, size 30, threshold 0.0, KB weight)`
    (`:52-62`);
  - scores with `ranx`: **nDCG@10, MAP@5, MRR@10** (`:225-260`), plus a per-query nDCG@10 report
    sorted worst-first (`:206-223`).
  - **Queries with zero results are deleted from the qrels** (`:61`), which inflates the scores.
- **`test/benchmark/`, performance, not quality**: an HTTP CLI for `/retrieval` and chat completions.
  - Metrics: latency avg/min/p50/p90/p95, qps, success_qps, failure_rate, time to first token
    (`metrics.py:51-78`, `retrieval.py:32-54`).
  - Three sample PDFs. No relevance judgments.
- **Unit tests pinning retrieval behaviour** (`test/unit_test/rag/`):
  - `test_search_fusion_weight.py` (ES keeps `0.001,1`; Infinity uses `(1-w,w)`);
  - `test_rank_feature_scores.py` (one matching tag scores 10.0);
  - `test_prune_deleted_chunks.py`, `test_doc_exists_cache.py`, `test_search_pagination.py`,
    `test_rerank_by_model_input.py`;
  - chunking: `test_naive_merge.py`, `test_merge_paragraphs.py`, `test_delim*`;
  - `graphrag/` (checkpoint resume, entity resolution, merge nodes).
- **Eval claims found only in code comments**:
  - SereneDB "content-only scoring reached ES-parity MRR on the gold eval" (`serenedb_conn.py:48-51`);
  - flat versus tree routing "20.2% vs 39.3%" (`navigation.py:1099-1104`);
  - a measured 9-17 → 37 LLM calls per question when TOC-LLM selection is on (`navigation.py:1105-1112`).
  - Release notes claim Ψ-RAG beats GMM on Recall@5/F1 (v0.25.6).

**EOS relevance.** EOS measures recall@1 on a 20-question golden set (`core/retrieval.py:60-146`). Add
MRR@10 and nDCG@10, which are about 20 lines of stdlib code each, and a **worst-first per-query report**.
Keep zero-result queries in the denominator, which is the opposite of RAGFlow's `del qrels[query]`.
Add p50/p90 latency for `note search` and `brief`.

---

## 14. Area 13: recent changes relevant to retrieval
The git history is shallow, so this comes from `docs/release_notes.md` and is cross-checked in code.

| Version / date | Change | Code evidence |
|---|---|---|
| v0.27.2 (2026-09-10) | Agentic RAG framework refactor; `RERANK_TOKEN_LIMIT_MODE`; retrieval highlight; threshold shown as % | `rerank_model.py:35-38`; `rag/advanced_rag/*` |
| v0.27.1 (2026-08-28) | Retrieval API exposes `rerank_candidates_count`, `knn_top_k`, `num_candidates`; **metadata filters pushed down to the metadata index** | `chunk_api.py:471-491`; `metadata_utils.py:280-320` |
| v0.27.0 (2026-08-19) | **Knowledge compilation** (Wiki, Graph, Tree, PageIndex, MindMap, Timeline, Skills); **GraphRAG and RAPTOR deprecated in the UI**, old content stays searchable; agentic RAG thinking modes; GaussDB and SereneDB engines; Infinity 0.7.3 | `rag/advanced_rag/knowlege_compile/*`, `serenedb_conn.py`, `gaussdb_conn.py` |
| v0.26.4 | Snowball stemmer for 16 languages; dataset `language` wired through tokenization | `rag_tokenizer.tokenizer.set_language(...)` calls throughout |
| v0.26.0 | Checkpoint/resume for community extraction and entity resolution; entity-ranking fix | `rag/graphrag/checkpoints.py`, `phase_markers.py` |
| v0.25.6 | RAPTOR "AHC/Ψ-RAG" mode; fix: vector weight not applied | now 1-D watershed only (`raptor.py:475-540`) |
| v0.25.5 | **ES stops fetching vectors in the main search** (50-100% faster); Infinity metadata push-down | `search.py:310-317,533-602`, `dialog_service.py:65-109` |
| v0.24.0 / v0.23.0 | "TOC" renamed to "PageIndex"; **parent-child chunking** | `rag/nlp/__init__.py:476-479`, `search.py:1085-1139` |
| v0.21.0 | Long-context TOC; GraphRAG/RAPTOR moved from automatic incremental to manual batch build | `task_executor.py:689-734` |
| v0.18.0 | Built-in rerankers removed | model registry |
| v0.15.0 / v0.14.0 | Page rank; Infinity back-end; English synonyms; term-weight speedup | `search.py:528-531`; `synonym.py` |

Changes visible only in code:
- the refactored task executor is the default, with a dry-run comparator;
- the chunking contract (#17799) and the canonical delimiter parser (#17383);
- reranker input switched to natural text, with per-provider normalisation;
- the chunk-feedback pagerank loop;
- the OpenSearch hybrid pipeline;
- the doc-exists TTL cache for pruning;
- RRF claim search in agentic navigation;
- a Go port behind `API_PROXY_SCHEME`, whose `KBPrompt` comments document the Python citation-index bug.

---

## 15. Defects and quirks found while reading (with evidence)
1. **Citation index base mismatch.** 1-based `kb_prompt` against 0-based decoding and UI
   (`generator.py:176`, `dialog_service.py:881-888`, `markdown-content/index.tsx:333-352`; confirmed in
   Go comments `internal/rag/prompts/generator.go:180-218`).
2. **Lexical gating of the kNN leg** in ES and Infinity: semantic-only matches are unreachable except
   through the zero-hit fallback (`es_conn.py:268`, `infinity_conn.py:250`).
3. **Scale mixing**: raw `pagerank_fea` (0..100) and `10·tag_cos` added to a [0,1] similarity; the ES
   kNN score (1+cos)/2 versus raw cosine (OB/SereneDB `rerank`) versus atan-fused (Infinity). The
   threshold and weight semantics are therefore backend-specific.
4. **Field repetition is a no-op** in `token_similarity` (presence-based, `query.py:197-209`, versus
   `search.py:618-623`).
5. **"≤3 tokens ⇒ Chinese path"** changes query semantics by length (`query_base.py:21-30`). Long
   English queries have no minimum-should-match (`query.py:94`).
6. **`term.freq` is missing** (`term_weight.py:121-127`), so the df-IDF half is priors only.
7. **Dead features**: `ty2ents` has readers but no writers; `MatchSparseExpr` and `MatchTensorExpr`
   are unused; `_loop_args` is unused (`graph_extractor.py:84`); `_build_knn_filter_query` and
   `_build_dense_filter` are unused; the `Search` default key `chat_settingcross_languages` is never read.
8. **ES rank-feature clauses are neutralised** by `bool.boost = 0` whenever a text clause exists (`es_conn.py:251,272-276`).
9. **Benchmark drops zero-hit queries** (`rag/benchmark.py:61`).
10. **KG `__main__`** passes a dict as `question` (`rag/graphrag/search.py:327`), a stale harness.
11. auto/semi_auto metadata filters **fail open** (`metadata_utils.py:239-266`).

---

## 16. Consolidated transfer guide for EOS (local-first, SQLite, stdlib, code + notes, lexical by decision)

**Adopt (deterministic, cheap, stdlib):**

| # | RAGFlow idea | Where | EOS form |
|---|---|---|---|
| 1 | Field boosts (kwd 30 / questions 20 / title 10 / content 2) and binary-TF short fields | `query.py:32-40`, `mapping.json:8-15` | `bm25(search, …weights)`; put symbols and title in short columns |
| 2 | Query cleanup: filler and question-word strip, stop words, full/half-width | `query_base.py:38-56`, `term_weight.py:58-92` | fixes the "ok, continue" false positive |
| 3 | Short-query minimum-should-match (30%), with relaxation retries (0.1) on zero hits | `search.py:300,340-399` | two-pass FTS query |
| 4 | Adjacent-bigram phrase boost `"a b"^2max(w)` and proximity `~2` | `query.py:79-90,148,155` | FTS5 phrase / `NEAR()` leg |
| 5 | Coverage similarity with unigram 0.4 / bigram 0.6 | `query.py:180-209` | extend EOS `cov()` with bigrams |
| 6 | Term-type multipliers (NER/POS → identifier kinds) and length priors for OOV | `term_weight.py:30-53,197-217` | weights by symbol kind |
| 7 | Synonym dictionary first, hot-reloadable (skip WordNet) | `synonym.py:79-101` | `synonyms` table with service aliases and TR/EN pairs |
| 8 | Porter stemming | release v0.26.4 / analyzer | `tokenize='porter unicode61'` |
| 9 | Bounded feedback prior with relevance-split integer budget | `chunk_feedback_service.py:84-138` | `note.prior` from run outcomes and eval; add a *scaled* prior |
| 10 | Tag lift `P(tag|hits)/P(tag)` as a rank feature | `search.py:990-1020` | SQL over note tags |
| 11 | RRF (k=60) to fuse independent legs with no thresholds | `navigation.py:1529-1549` | fuse notes, index, symbols, runs |
| 12 | Content-addressed chunk ids plus task digests for chunk reuse | `chunk_service.py:241`, `task_service.py:544-637` | per-file incremental rebuild instead of a full rebuild |
| 13 | Parent/child small-to-big and title-path breadcrumbs | `rag/nlp/__init__.py:458-483,1120-1164`, `search.py:1085-1139` | method → class/file; `path › Class › method` prefix |
| 14 | Hidden outline/TOC row per document | `task_handler.py:886-940` | AST outline per file, no LLM |
| 15 | Stable citation keys and a verbatim evidence gate with offsets | `structure.py:931-1034` (avoid `generator.py:176`'s positional IDs) | quote must be found in `path:Lx-Ly` |
| 16 | Pre-computed 2-hop neighbourhoods, pagerank prior, hop-decayed expansion | `utils.py:803-835`, `search.py:171-221` | on EOS's static code graph |
| 17 | Entity/alias blocking with a digit-bigram guard and Levenshtein half-length | `entity_resolution.py:265-289` | symbol and service alias merge |
| 18 | O(N) adjacent-similarity segmentation (watershed) | `raptor.py:475-540` | segment long notes by lexical Jaccard |
| 19 | LLM cache keyed by hash(model, prompt, history, conf) with TTL | `graphrag/utils.py:170-187` | `llm_cache` table for optional AI paths |
| 20 | Eval: nDCG@10 / MAP@5 / MRR@10 plus worst-first report; latency percentiles | `rag/benchmark.py:206-260`, `test/benchmark/metrics.py` | extend `core/retrieval.py`; keep zero-hit queries |
| 21 | Shadow/dry-run comparator for indexer rewrites | `task_executor_refactor/comparator.py`, `recording_context.py` | diff old versus new `eos.db` builds |

**Adapt only if C-10 (no embeddings) is ever revisited.** Treat vectors as a **re-scorer over the
≤64-candidate lexical window**, exactly the default ES shape of `(1-w)·coverage + w·cos + prior`.
Brute-force cosine in pure Python is feasible at that size. Keep the 0.1 title / 0.9 content mix and the
drift check (average cosine ≥ 0.9 on sampled chunks).

**Reject (infra-bound or not applicable):**
- ES/OpenSearch/Infinity/OB/GaussDB query DSLs, `rank_feature` fields, painless scripts, HNSW
  `k`/`num_candidates`;
- Redis Streams, consumer groups, heartbeats and distributed locks;
- MinIO image crops;
- the DeepDoc stack (ONNX OCR, layout and TSR, XGBoost concatenation, KMeans columns);
- spaCy NER;
- graspologic Leiden (use pure-Python community detection if needed);
- LLM-heavy enrichment on every chunk (keywords, questions, tags, TOC, GraphRAG gleaning,
  community reports);
- the multi-tenant index layout;
- the Go port.

**Anti-patterns to write down:**
- positional citation ids;
- adding un-normalised priors to normalised similarities;
- backend-dependent semantics behind one parameter name;
- deleting zero-hit queries from evaluation;
- "safety-net" query-time pruning as a substitute for transactional deletes;
- prefix-stop context packing.

---

## Appendix: key files

- Retrieval core: `rag/nlp/search.py`, `rag/nlp/query.py`, `rag/nlp/term_weight.py`,
  `rag/nlp/synonym.py`, `rag/nlp/rag_tokenizer.py`, `common/query_base.py`.
- Doc-store layer: `common/doc_store/doc_store_base.py`, `rag/utils/es_conn.py`,
  `common/doc_store/es_conn_base.py`, `rag/utils/infinity_conn.py`, `rag/utils/opensearch_conn.py`,
  `rag/utils/ob_conn.py`, `common/doc_store/ob_conn_base.py`, `rag/utils/serenedb_conn.py`,
  `rag/utils/gaussdb_conn.py`, `conf/mapping.json`, `conf/infinity_mapping.json`.
- Ingestion: `api/db/services/task_service.py`, `rag/svr/task_executor.py`,
  `rag/svr/task_executor_refactor/{task_handler,chunk_service,chunk_builder,chunk_post_processor,embedding_service,embedding_utils}.py`,
  `rag/utils/redis_conn.py`, `rag/app/*.py`, `rag/nlp/__init__.py`, `rag/nlp/delim.py`,
  `deepdoc/parser/pdf_parser.py`, `deepdoc/vision/*.py`, `rag/flow/**`.
- Models: `rag/llm/embedding_model.py`, `rag/llm/rerank_model.py`, `api/db/services/llm_service.py`.
- Graph and compilation: `rag/graphrag/**`, `rag/advanced_rag/knowlege_compile/{raptor,structure}.py`,
  `rag/advanced_rag/harness/tools/navigation.py`.
- Chat and citations: `api/db/services/dialog_service.py`, `rag/prompts/generator.py`,
  `rag/prompts/citation_prompt.md`, `api/db/services/conversation_service.py`,
  `web/src/components/markdown-content/index.tsx`, `internal/rag/prompts/generator.go`.
- Persistence and config: `api/db/db_models.py`, `common/settings.py`, `api/utils/api_utils.py`,
  `api/apps/restful_apis/{chunk_api,chat_api}.py`, `api/apps/services/dataset_api_service.py`,
  `api/db/services/chunk_feedback_service.py`, `common/metadata_utils.py`, `docker/.env`.
- Evaluation: `rag/benchmark.py`, `test/benchmark/*`, `test/unit_test/rag/*`.
