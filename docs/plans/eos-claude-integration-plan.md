# Plan: EOS'u Claude Code'a bağlamak — v2 (doğrulanmış, uygulanan)

**Durum:** v2, 2026-09-26. v1 (`ef1ffce`) taslaktı; bu sürüm her büyük varsayımı depoya ve
kurulu Claude Code'a (2.1.281) karşı ölçerek düzeltir ve uygulama sırasında satır satır
güncellenir (§9 durum tablosu). Dallar: EOS `feature/claude-code-integration`, nexus
`feature/eos-claude-integration` (push yok).

**Dayanak.** v1'in docs okuması (hooks, sub-agents, skills, memory, settings, model-config,
mcp, plugins, cli-reference, costs) + bu oturumda **ölçülenler**: iki headless probe
(`claude -p`, haiku ve sonnet, toplam $0.26) tüm hook event'lerinin gerçek girdi alanlarını
kaydetti; 135 nexus transcript'i (739 MB) tarandı; `claude-api` skill'inin model tablosu
(2026-06-24 önbelleği) fiyat ve effort desteği için kullanıldı.

**Değişmeyen kısıtlar (ADR-001/010/018/019/021/025).** EOS model çağırmaz, iş yürütmez,
hüküm vermez; dosya önce, SQLite türev; teslimat hook ile; prompt metni saklanmaz.

---

## 0. v1 → v2: ne değişti, neden

### 0.1 Mimari karar: wrapper/CLI varsayılan, MCP istisna (yeni ADR-026)

v1 §6 "tool search ≤ 300 token ise MCP varsayılan olabilir" diyordu. v2 kararı tersine
kurar: **dış sistemlere erişimin varsayılanı EOS/host shell wrapper'larıdır; MCP yalnız
wrapper maddeten pratik değilse** kullanılır. Gerekçe yalnız roster maliyeti değil:

| Boyut | Wrapper (CLI/sh) | MCP |
|---|---|---|
| Tanım/bağlam yükü | 0 (çalıştırılana kadar); keşif CLAUDE.md tablosu + hedefli brief satırı | roster (tool search açıkken ad listesi, kapalıyken tam şema) her istekte |
| Dönen çıktı | wrapper filtreler: `jira.sh search` ~40k–233k → ~605 token; mvn ~13k → ~5 satır; `gitx span` 957k → ~160 | ham JSON; `MAX_MCP_OUTPUT_TOKENS` 25k'ya kadar, aşan dosyaya |
| Kontrol / izin | `--confirm` yazma kapısı, env2 reddi, guard | tool başına izin (değerli olduğu yer: izolasyon) |
| Gözlemlenebilirlik | `eos-event` ile her çağrı ledger'a (20 wrapper) | ayrı yakalama gerekir |
| Determinizm / bakım | sürümlü script, test edilir | sunucu + protokol + oturum |
| Kimlik | env/secret dosyası | OAuth akışı (MCP'nin gerçek avantajı) |

**MCP'nin haklı olduğu durumlar:** pratik bir CLI yok; OAuth/oturum entegrasyonu MCP ile
maddeten kolay (claude.ai konnektörleri); kalıcı protokol/oturum semantiği gerekiyor
(ör. 33 MB graph'ı bellekte tutan `graphify-project`); tool düzeyinde izin izolasyonu
maddeten değerli; yeniden üretmesi orantısız pahalı bir işlev. Mevcut wrapper'lar MCP'ye
**çevrilmez**.

### 0.2 v1 iddiaları ölçüme karşı

| v1 iddiası | Ölçülen gerçek (2.1.281 / bu depo) | v2 sonucu |
|---|---|---|
| B2: her event'te `effort:{level}`, `agent_id`, `agent_type` | `effort` yalnız tool event'leri ve Stop'ta, **yalnız effort destekleyen modelde** (haiku'da yok; SessionStart/UserPromptSubmit/SessionEnd/InstructionsLoaded'da yok). `agent_id/agent_type` yalnız subagent içindeki event'lerde; ana oturumda alan hiç yok | effort kaynağı: hook (varsa) + transcript `effort` alanı; ana oturum = `agent` yok |
| PostToolUse `tool_response`'tan exit/status | Bash `tool_response` = `stdout, stderr, interrupted, isImage, noOutputExpected` — exit kodu yok. Sıfır olmayan çıkış **PostToolUseFailure**'a düşer: `error: "Exit code 1"`, `is_interrupt` | status: PostToolUse=ok, PostToolUseFailure=error (+exit kodu `error`'dan) |
| 1.10: guard'a `if` filtreleri → "264 gereksiz Python başlatma" | 264 (bugün 294) **override sayısı**, başlatma sayısı değil. Guard p50 27 ms / p95 29 ms. `if` bileşik komutta eşleşiyor (doğrulandı: `cd . && echo` ↔ `Bash(echo *)`), ama guard'ın "komutun herhangi yerinde" kuralları (cache yolu, script okuma, python ile cache) `if` önekleriyle ifade edilemez | guard'a `if` **eklenmez** (kazanç ~27 ms/Bash, risk: boşluk). `if`/`async` yalnız EOS'un kendi hook'larında |
| 1.6: TaskCreated/TaskCompleted ↔ work ledger | Headless probe'da hiç ateşlenmedi; model TodoWrite kullandı. Ayrıca her todo'yu commit'li `work.jsonl`'a yazmak paralel oturumların defterini kirletir | düşürüldü |
| 1.8: PostModelSwitch ile gerçek model | Transcript'te her assistant mesajında `model` **ve `effort`** var; Stop hook'u zaten fold ediyor | hook yok; fold'a effort eklenir (3.4) |
| 4.5: 13 serviste "hiç ateşlenmeyen hook üçlüsü" | Doğru: 135 transcript'te servis `.claude/hooks/eos-*` çağrısı 0; `~/.claude/projects`'te hiçbir servis dizini yok. **Ama** servislerin `.claude/skills/eos` ve `.claude/agents/eos-researcher.md` dosyaları `--add-dir` ile **yükleniyor** (bu oturumun skill ve agent listesinde görünüyorlar) | hook üçlüsü kanıtla kaldırılır; skill+agent plugin'e taşınır, 13 kopya kalkar |
| B10: registry | `opus` alias'ı `claude-opus-5[1m]` → `claude-opus-5-5[1m]`'e kaymış (transcript `resolvedModel`: 40 → 6); Sonnet 5 $2 (registry 3.0); Haiku 4.5 effort'u reddeder (registry beş seviye diyor) | registry düzeltilir, "effort yok" temsil edilir |
| 2.1: CLAUDE.md < 200 satır hedefi | Zaten 146 satır / ~2,722 token. Devin de CLAUDE.md'yi yükler: kuralı `.claude/rules/`'a taşımak Devin'den siler | yalnız başka yerde zorlanan dosya-bağlı kural taşınır (tools/eos) |
| 2.2: skill açıklamaları tavanı aşıyor | Hepsi ≤ 353 karakter (tavan 1,536). Asıl maliyet: `eos` skill'i ve `eos-researcher` agent'ı 13 servisten | açıklama işi yok; çoğaltma kalkar |
| 6.2: varış testi elle | `context-budget.sh --arrival` zaten otomatik (54/54) | headless uçtan uca kontrol eklenir |
| Tek estimator | Üç ayrı sabit: nexus 3.5 chars/token, EOS brief 3.0, bench 4.0 | transcript'ten kalibre edilir (0.2) |

---

## 1. Mimari kararlar

- **D1 — Wrapper varsayılan, MCP istisna** (§0.1, ADR-026). EOS MCP roster'ı 12 → 10:
  `get_graph` (33.8 MB; CLI `eos graph --output` aynı işi yapıyor) ve `compose`
  ("bir sürüm için" deprecated alias, 8+ sürümdür duruyor) kalkar. MCP'ye sıfır-roster
  kanallar eklenir: resources (`eos://brief`, `eos://capabilities`), prompt (`brief`).
- **D2 — Motor sağlayıcıdan bağımsız, Claude sınırda.** Motor yeni kavramlar alır —
  event atfı (`agent`, `source`, `status`, `tool_use_id`), yetenek kaydı, normalize edilmiş
  hook girdisiyle `eos hook <event>` dağıtıcısı, routing registry/kuru koşu/kullanım fold'u.
  Claude'a özgü alan adları tek bir adaptör fonksiyonunda (`core/hooks.py:normalize`);
  paketleme (`plugin/`: hooks.json, skill/agent frontmatter) EOS reposunda ama motorun
  dışında; host bağlantısı nexus `.claude/`'da.
- **D3 — Tek hook giriş noktası.** Proje başına kopyalanan şablonlar yerine plugin'in
  `hooks.json`'u `eos hook <event>` çağırır; `eos init/ai update --claude plugin` artık
  `.claude/` içine hook/skill/agent yazmaz ve eskisini (yalnız kendi yazdıklarını) siler.
  `--claude files` eski davranış (varsayılan, geriye uyum).
- **D4 — Yetenek kaydı tek kaynak.** `capabilities.toml` bilgi dizininde (nexus:
  `.devin/knowledge/nexus/`, sürümlü; `.eos/config.toml` nexus'ta izlenmediği için orada
  değil). Her yetenek: ad, çalıştırılacak wrapper, ne yaptığı, anahtar kelimeler,
  `block` (guard reddeder) ve `hint` (izin verilir, PostToolUse bir kez ipucu verir)
  desenleri. Okuyanlar: `eos capabilities`, task brief'in WRAPPERS bloğu, PostToolUse bypass
  tespiti, nexus `wrapper_guard.py`, CLAUDE.md tablo tutarlılık testi.
- **D5 — Durum dizini tek.** Run pointer'ları wrapper'ların da okuduğu `EOS_STATE_DIR`'da
  kalır (wrapper'lar plugin ortamında çalışmaz, `${CLAUDE_PLUGIN_DATA}`'yı göremez).
  `${CLAUDE_PLUGIN_DATA}` yalnız plugin'e özel durum için: ipucu tekilleştirme,
  compaction işaretleri (`EOS_HOOK_STATE_DIR` olarak aktarılır; motorda `CLAUDE_` adı yok).
- **D6 — Effort oturum başında.** ROUTE satırı effort'u oturum başında seçtirir
  (`claude --effort`), oturum içi değişimin cache'i sıfırladığını söyler (Opus 5.5 /
  Fable 5.1 hariç); effort almayan modelde effort yarısını basmaz;
  `CLAUDE_CODE_EFFORT_LEVEL` set ise "uygulanamaz" der.
- **D7 — Her hook ölçülebilir amaçla.** Eklenen: SessionStart(compact|clear) dedup
  sıfırlama, PostToolUse(Bash|Edit|Write|NotebookEdit) yakalama+bypass, PostToolUseFailure,
  SubagentStart/Stop atıf, InstructionsLoaded varış günlüğü, StopFailure, SessionEnd özet,
  PreToolUse(Agent|Task) routing (kuru koşu varsayılan). Eklenmeyen (gerekçe §8):
  TaskCreated/Completed, CwdChanged/DirectoryAdded, PostModelSwitch, Setup, Pre/PostCompact.

Katmanlar:

```text
Agent (Claude Code / Devin)
  ├── host wrapper'ları (automation/*.sh) ──> tercih edilen yol ── eos-event ──┐
  ├── EOS CLI (eos brief|run|note|capabilities|route) ──────────────────────────┤──> ledger
  ├── hook'lar: plugin hooks.json ──> eos hook <event> (normalize → yakala) ─────┘
  └── MCP ──> istisna (graphify-project; OAuth konnektörleri)
```

---

## 2. Faz 0 — Ölçüm (önce ölç)

| Satır | İş | Nerede | Kabul |
|---|---|---|---|
| 0.1 | Transcript taban çizgisi: `context-budget.sh --sessions` (`automation/lib/claude_sessions.py`) — model × effort (ana/subagent, atlas), cache isabeti, Stop hook süreleri ve iptaller, hook bağlam boyutları, yüklü talimatlar, changed-on-disk, tool çıktı maliyeti sınıfa göre (wrapper / ham / MCP / Read / EOS), guard blokları ve override'lar, MCP ertelenmiş tool listesi, skill listesi boyutu | host | rapor sayılarla |
| 0.2 | chars/token kalibrasyonu: büyük tool sonuçlarında `usage` farkı / karakter medyanı | host | tek değer; 3.5'ten sapma yazılı |
| 0.3 | İleriye dönük yakalama: InstructionsLoaded → `.eos/data/loaded.jsonl`; her `eos hook` çağrısı süresiyle telemetry'ye | motor | canlı oturumda satırlar |
| 0.4 | MCP roster: EOS `tools/list` boyutu (önce/sonra), graphify-project ertelenmiş ad maliyeti, `claude plugin details` | motor+host | ADR-021 zeyilnamesi |
| 0.5 | Hook gecikmesi: guard p50 27 ms; `eos brief --task` p50 104 / p95 205 ms (telemetry, n=2,080); `eos hook` yeni yol p95 hedefi < 150 ms | host | tablo |
| 0.6 | Effort: transcript'te atlas oturumlarının gerçek effort'u vs `atlas.md effort: low` | host | karar yazılı |
| 0.7 | Headless eval tabanı: Faz 6 koşucusuyla küçük set | host | koşu kaydı + maliyet |

Çıktı: `docs/eos-evals/claude-baseline.md`.

## 3. Faz 1 — Hook yüzeyi

| Satır | Event / matcher | Ne yapar | Doğrulanmış girdi |
|---|---|---|---|
| 1.1 | — | `eos hook <event>`: stdin JSON → `normalize()` (Claude + Devin alan varyantları) → işleyici; her yolda exit 0, stderr sessiz, süre telemetry'ye | — |
| 1.2 | PostToolUse `Bash\|Edit\|Write\|NotebookEdit` | açık run'a `source: hook` event; `tool_use_id` ile tekilleştirme (ledger kuyruğu); wrapper komutları atlanır (kendileri yazar); önemsiz salt-okur programlar atlanır; Edit/Write → `changed` + proje-göreli yol; kayıttaki `hint` deseni → oturumda bir kez `additionalContext` ipucu + `status: bypass` | `tool_name, tool_input, tool_response, tool_use_id, duration_ms, agent_id?` |
| 1.3 | PostToolUseFailure | `status: error` event, exit kodu `error`'dan | `error, is_interrupt, duration_ms` |
| 1.4 | SubagentStart / SubagentStop | `called` event: tool `subagent`, target `agent_type`, ref `agent:<id>`; Stop'ta son mesajdaki atıf sayısı (`path:Lx`, `note:`, `run:`) → `verified` event (gözlem; tier yükseltme yok — ADR-018) | `agent_id, agent_type, last_assistant_message, agent_transcript_path` |
| 1.5 | SessionStart `compact\|clear` | oturumun prompt dedup durumunu sıfırlar → task brief compaction sonrası yeniden gelir (nexus kendi dosyası için aynısını yapar) | `source` |
| 1.6 | SessionEnd | `.eos/data/sessions.jsonl`'a tek satır (bitiş nedeni, run/event/bypass sayıları); 1.5 s bütçe → ölçülür | `reason` |
| 1.7 | StopFailure | açık run'a `status: error, tool: harness` | `error_type` |
| 1.8 | InstructionsLoaded | `.eos/data/loaded.jsonl` (göreli yol, `load_reason`, `memory_type`, tetikleyen dosya) | `file_path, load_reason, memory_type, globs, trigger_file_path` |
| 1.9 | PreToolUse `Agent\|Task` | routing (Faz 3), kuru koşu varsayılan | `tool_input.model/subagent_type/prompt` |
| 1.10 | ledger | `Event`'e `agent, source, status, tool_use_id`; eski okurlar alanları yok sayar; `eos-event` `source: wrapper` yazar; `eos run show/tools` gösterir. `v:2` ve rotasyon yok (ledger git'te) | — |

## 4. Faz 2 — Bağlam kanalları

| Satır | İş | Kabul |
|---|---|---|
| 2.1 | `.claude/rules/eos-subtree.md` (`paths: tools/eos/**`); CLAUDE.md'de tek satır kalır; Devin tarafında `check-structure.sh` zorlar | InstructionsLoaded `path_glob_match` kanıtı |
| 2.2 | 13 servis kopyası yerine tek plugin skill'i/agent'ı; plugin skill açıklaması kısa | skill/agent listesinde tek `eos` |
| 2.3 | `eos:brief` skill'i: `disable-model-invocation: true` (liste maliyeti 0), gövdesi `!`eos brief … --task``; elle yeniden sorgu kanalı | skill çalışıyor |
| 2.4 | Enjeksiyon bütçesi: SessionStart ≤ 800 / task ≤ 1,500 / oturum p95 ≤ 4,000 token — tabana karşı ölçülür, aşan kanal daraltılır | tablo |
| 2.5 | Yüklü dosyaları yeniden yazmama: `eos ai update` yalnız içerik değişince yazar; `--claude plugin` servis `.claude/`'una hiç yazmaz; changed-on-disk kaynağa ayrılır (kendi düzenlemesi / dış) | ölçüm |
| 2.6 | CLAUDE.md `# Compact instructions` (3 satır: açık run, claim, ROUTE, dal/anahtar) | var |
| 2.7 | Tek estimator: 0.2 kalibrasyonu; sapma ≤ %10 ise 3.5 kalır, EOS brief 3.0'ı gerekçesiyle belgelenir | yazılı |

## 5. Faz 3 — Model / effort routing

| Satır | İş | Kabul |
|---|---|---|
| 3.1 | Registry: haiku `efforts = ()` (effort yok; clamp "yok" döner), sonnet 2.0, opus 4.0 (alias → Opus 5.5; config'te düzeltilir), fable 10.0; tam model kimlikleri ve `[1m]` varyantları alias | registry testleri |
| 3.2 | `eos hook pre-agent`: `[model_routing] hook = true` + `hook_dry_run = true` (varsayılan) → "yazılacaktı" `decided` event'i, çıktı yok; canlıda `updatedInput.model` | testler |
| 3.3 | ROUTE metni: `Agent({model})`, effort oturum başında, env override uyarısı, effort'suz modelde effort yok | brief testi |
| 3.4 | Kullanım fold'u effort'u da sayar (model × effort mesaj sayısı) | fold testi |
| 3.5 | `eos route --stats`: tavsiye (routing.jsonl) ⋈ kullanım (routing-usage) oturum bazında | çıktı |
| 3.6 | Korpus + `eos route --eval <tsv>`: tür/seviye/model isabeti, effort geçerliliği, yanlış sınıflarda kelime önerisi (insan kabul eder). EOS'ta genel korpus, nexus'ta Türkçe/İngilizce iş korpusu | rapor; canlıya geçiş eşiği yazılı |

## 6. Faz 4 — Plugin

| Satır | İş | Kabul |
|---|---|---|
| 4.1 | `plugin/.claude-plugin/plugin.json` (sürüm = `core/VERSION`, testle), `hooks/hooks.json` (exec, `async` yalnız saf yakalamada), `skills/eos`, `skills/brief`, `agents/eos-researcher.md` (read-only zarf: `model`, `effort: low`, `maxTurns`, `tools`, `disallowedTools`); `.mcp.json` yok (D1) | `claude plugin validate` temiz |
| 4.2 | `.claude-plugin/marketplace.json` (repo kökü); nexus `enabledPlugins` + `extraKnownMarketplaces` (subtree: `tools/eos`) | nexus'ta plugin yüklü |
| 4.3 | `eos init/ai update --claude plugin\|files`; nexus 13 serviste `--claude plugin` → üçlü+skill+agent+settings girdileri silinir | servislerde EOS `.claude/` kalıntısı yok |
| 4.4 | `claude plugin details` maliyeti ≤ 600 token | ölçüm |
| 4.5 | Devin paritesi aynen (AGENTS.md bloğu, INTAKE komutu) | — |

## 7. Faz 5 — MCP / entegrasyon sınırı ve wrapper keşfi

| Satır | İş | Kabul |
|---|---|---|
| 5.1 | Ölçüm: EOS roster önce/sonra, graphify-project, oturum başı MCP talimat/ertelenmiş ad maliyeti; wrapper vs MCP çıktı | belge |
| 5.2 | ADR-026 + ADR-021 zeyilnamesi; nexus entegrasyon tablosu (her biri: wrapper / MCP-istisna + gerekçe) | belge |
| 5.3 | `get_graph`, `compose` kaldırılır; roster testi 10'a | test |
| 5.4 | MCP resources/prompts: `eos://brief`, `eos://capabilities`, prompt `brief` | test |
| 5.5 | Wrapper keşfi: `capabilities.toml`, `eos capabilities [--for]`, task brief WRAPPERS bloğu, PostToolUse bypass (1.2), guard kaydı okur, eksik `Bash(...)` izinleri, CLAUDE.md tablo tutarlılık testi | testler + canlı kanıt |

## 8. Faz 6 — Değerlendirme ve öğrenme

| Satır | İş | Kabul |
|---|---|---|
| 6.1 | `automation/eval-run.sh`: `claude -p --agent atlas --output-format stream-json --verbose --include-hook-events --max-budget-usd --max-turns --json-schema`; stream'den: doğruluk (deterministik çapa), varış (hook_response çıktısı), wrapper kullanımı, bypass, maliyet, `usage`, `modelUsage`, tur → `docs/eos-evals/runs/<tarih>-<set>.jsonl` + özet | tek komut |
| 6.2 | Setler: wrapper-keşfi, uçtan uca varış, routing (model çağrısız `eos route --eval`) | koşu kaydı |
| 6.3 | `claude plugin eval` vakaları (`plugin/evals/`) | koşu |
| 6.4 | Metrik: değer / token — skor ÷ (girdi+çıktı token) ve ÷ $; gerçek oturumlarda wrapper çıktı token'ı vs ham/MCP (0.1) | raporda |
| 6.5 | Çevrimdışı öğrenme: `--eval` kelime önerileri, insan kabulü | çıktı |
| 6.6 | `count_tokens`, Batches: varsayılan kapalı; 0.2 kalibrasyonu yeterliyse `count_tokens` gereksiz; Batches için ADR yazılmadıkça kod yok | karar yazılı |

## 8a. Yapılmayacaklar

| Özellik | Neden değil |
|---|---|
| Wrapper'ları MCP'ye çevirmek | D1; çıktı filtresi, `--confirm`, ledger yakalaması kaybolur |
| Guard'a `if` önekleri | 27 ms kazanç; "komutun her yerinde" kuralları öneklerle ifade edilemez (§0.2) |
| TaskCreated/Completed ↔ work ledger | ateşlenmedi; commit'li defteri todo'larla kirletir |
| PostModelSwitch hook'u | transcript fold'u model+effort'u zaten taşıyor |
| CwdChanged/DirectoryAdded | workspace resolver yok (EOS 2.x C5) |
| Setup(maintenance) | watcher ve `ensure-eos-mcp.sh` zaten yeniden indeksliyor |
| `prompt`/`agent` tipi hook, `UserPromptSubmit.updatedInput` | model çağırır / kullanıcı metnine dokunur |
| `updatedToolOutput` ile Read daraltma | tool çıktısını değiştirir; wrapper'lar zaten filtreliyor; ölçülebilir kazanç kanıtı yok |
| `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`, agent teams, effort'u oturum içinde otomatik değiştirmek | routing'i öldürür / ~7× token / cache sıfırlar |
| `${CLAUDE_PLUGIN_DATA}`'da run pointer'ları | wrapper'lar plugin ortamı dışında çalışır (D5) |
| Ledger rotasyonu, `v:2` | ledger git'te; okurlar ek alanları zaten yok sayıyor |
| Tier yükseltmesi (derinlik-1 doğrulama sonucu) | ADR-018/025: motor sonuçtan karar çıkarmaz; yalnız gözlem kaydı |

## 9. Durum tablosu

Motor = upstream `yasinkilinc/eos` (dal `feature/claude-code-integration`), host = nexus.

| Satır | Kapsam | Motor/Host | Durum |
|---|---|---|---|
| 0.1–0.2 | transcript taban çizgisi, kalibrasyon | host | DONE — `context-budget.sh --sessions`; `docs/eos-evals/claude-baseline.md` |
| 0.3 | loaded.jsonl, hook telemetry | motor | DONE — canlı oturumda satırlar var |
| 0.4–0.7 | MCP ölçümü, gecikme, effort kararı, headless taban | host+motor | DONE — baseline §4, §7, §2, §9 |
| 1.1–1.10 | `eos hook` dağıtıcısı ve event'ler, ledger alanları | motor | DONE — EOS 1.3.0/1.3.1; canlı doğrulandı |
| 2.1 | `.claude/rules/eos-subtree.md` | host | DONE — `path_glob_match` kanıtı |
| 2.2–2.3 | tek plugin skill/agent; `eos:brief` | host+motor | DONE — 13 servis temizlendi; `/eos:brief` canlı |
| 2.4 | enjeksiyon bütçesi | host | DONE — `note_inject` p95 11,597 → 4,433 (yeniden oynatma) |
| 2.5–2.7 | yeniden yazmama, compact, estimator | host+motor | DONE — write-if-changed, `edit` hint; 2.22 chars/token |
| 3.1–3.5 | registry, kuru koşu hook'u, ROUTE metni, effort fold, stats | motor | DONE |
| 3.6 | korpus + eşik | motor+host | DONE (ölçüm) — model %74.6 / %71.9 < %85 → hook kuru koşuda |
| 4.1–4.5 | plugin, marketplace, `--claude plugin`, servis temizliği | motor+host | DONE — validate temiz, ~203 token |
| 5.1–5.5 | MCP ölçümü, ADR-026, roster 10, resources/prompts, wrapper keşfi | motor+host | DONE — resources/prompts yalnız birim testle (nexus MCP yüzeyi kapalı) |
| 6.1–6.6 | eval koşucusu, setler, plugin eval, metrik, öğrenme, T3 kararları | host+motor | DONE — 6/6 headless, plugin eval Δ=0 (küçük fixture) |

## 11. As built — plandan sapmalar ve açık kalanlar

**Sapmalar (gerekçeli):**

- Guard'a `if` önekleri eklenmedi (§0.2); bunun yerine blok desenleri kayda taşındı ve
  guard kayıttan okuyor (tek kaynak). Kayıt okuma +7 ms.
- `effort: low` (`atlas.md`) korundu: subagent'ta uygulanıyor, kullanıcı kararı (`dc9b933`).
- Plugin nexus'ta yalnız eksik yarıyı yapar (`[hooks] brief/close/usage = false`, bloğu
  `ensure-eos-mcp.sh` yazar); nexus'un workspace brief'i, note-gate'i ve usage fold'u yerinde.
- Subagent routing'i her subagent istemini **kendi başına** sınıflar (`fresh`); run kararı
  run'ın görevine aittir. Bir run'ın kararı artık yalnız o projenin ledger'ında yeniden
  kullanılır (korpus değerlendirmesi başka projenin run'ını her satıra döndürüyordu).
- Canlı oturumda iki yakalama hatası bulundu ve 1.3.1'de düzeltildi (`sed -i ''` boş
  argümanı; `eos` komutunun kendi run'ına olay yazması).
- Kullanıcının bildirdiği hata düzeltildi: runtime güncelleyici `.md` şablonlarını
  kopyalamıyordu (`eos ai update` runtime'dan çöküyordu) — EOS 1.3.0, regresyon testli.

**Açık kalanlar (gerçekten tamamlanmayan):**

1. 20-oturumluk kabul pencereleri (≥%95 eylem yakalama, iş defteri eşleşmesi, changed-on-disk
   −%50, advised/used): enstrümantasyon canlı, veri birikmesi gerekiyor.
2. Türkçe routing anahtar kelimeleri: `--eval` önerileri insan kabulü bekliyor (ADR-018);
   canlı subagent hook'u eşik geçilene kadar kuru koşuda.
3. `context-budget.sh --arrival` 53/54: "ok, continue" bugün eklenen bir notun başlığındaki
   "Continue" ile RELATED NOTES'a düşüyor — bu işten bağımsız, brief'in konuşma kelimesi filtresi.
4. Guard yanlış pozitifleri: python/heredoc gövdesindeki `mongo `/`psql ` kelimeleri, tırnak
   soyulunca birleşen komutlar (override günlüğünün çoğu). Davranışı değiştirmeden bırakıldı.
5. Kullanıcı düzeyi plugin'ler her oturuma ~6.6k token skill listesi ekliyor (engineering,
   data, product-management, design, anthropic-skills, cowork) — kullanıcı kararı.
6. Plugin eval'leri küçük fixture'da Δ skor 0 veriyor (yalnız tur/maliyet farkı); gerçekçi
   fixture ve Bash'li vakalar `~/.docker` sembolik bağı yüzünden bu makinede koşulamıyor.
7. EOS MCP resources/prompts canlı bir istemciyle denenmedi (nexus'ta MCP yüzeyi kapalı).
8. `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` denetimi (`eos doctor`) yapılmadı — değer düşük.

## 10. Kaynaklar

- Probe kayıtları: `claude -p` 2.1.281, haiku (tüm event'ler, `if`, `args`, `async`, subagent,
  path-scoped rule) ve sonnet (`effort` alanı); scratchpad'de, özet §0.2.
- `code.claude.com/docs/en/{hooks,sub-agents,skills,memory,settings,model-config,mcp,plugins,cli-reference,costs,headless}`
- `claude-api` skill model tablosu (2026-06-24): Haiku 4.5 $1/$5 (effort yok), Sonnet 5 $2/$10,
  Opus 5.5 $4/$20 (varsayılan effort `medium`), Opus 5 $5/$25, Fable 5.1 $10/$50.
- İç: `docs/eos-plans/eos-2x-architecture-research.md` (§17), `context-budget.md` (W-06),
  `docs/eos-evals/*`, EOS ADR-009/021/022/025.
