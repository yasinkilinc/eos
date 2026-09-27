# Plan: bağlama yalnız değerli bilgi, gereksizi dışarı (EOS, 2026-09-26)

Dayanak: `docs/eos-evals/savings/` (spend_share, compact_sim, resident_context,
context-tools-review). Ölçüm: 2026-09-17 sonrası 87 ana oturum. Maliyet = bağlamdaki her öğe ×
kaldığı çağrı sayısı ("yerleşik maliyet"). Kontrol: öğeler 3.28B + ilk çağrı tabanı 0.71B ≈
kayıtlı cache okuması 4.00B.

## Bugün cache okumasını ne sürüklüyor

| Kaynak | Pay | Kimin elinde |
|---|---:|---|
| Sistem istemi + araç şemaları + ilk bağlam (taban) | %18 | harness, ayarlar |
| Modelin yazdığı araç girdileri — Bash komutu %6, Write %5, heredoc/inline betik %4.5, Edit %3.5 | %20.5 | model davranışı |
| Read çıktısı — %75'i dosyanın tamamı | %13 | model + hook |
| Ham Bash çıktısı — sed/cat/head ile dosya okuma %3.6, grep %2, python %1.6, diğer %5 | %13 | wrapper + hook |
| Wrapper çıktısı | %10.5 | EOS/nexus wrapper'ları |
| CLAUDE.md ve kurallar (`instructions`) | %4.8 | nexus |
| Skill listesi | %3.2 | kurulu plugin'ler |
| "Dosya diskte değişti" bildirimi (`edited_text_file`) | %2.9 | model davranışı |
| Harness hatırlatmaları (token, kuyruk) | %4.6 | harness |
| **EOS enjeksiyonu (brief + not)** | **%1.0** | EOS |

Compact: 92 oturumda 12. Pencere (`autoCompactWindow`) tanımlı değil.
EOS run'ları 11 oturumda, 57 bitiş: yalnız bu bitişlerde temizlenseydi cache okuması ×0.91.

## 1. Yalnız değerli bilgiyi almak

| # | Öneri | Hedef pay | Mekanizma | Risk | Ölçüt |
|---|---|---:|---|---|---|
| A1 | **Büyük dosyayı tamamen okumadan önce harita** | Read-tamamı %10 | PreToolUse(Read), `limit` yok ve dosya > N satır: EOS'un bildiği yapı (Java sınıf/metot, Markdown başlıkları, satır aralıkları) `additionalContext` olarak verilir, okuma serbest kalır; ikinci aşama: yalnız uyarı yerine `deny` + harita, ölçümle karar | bir ek tur | Read-tamamı payı, oturum başına Read sayısı |
| A2 | **Kabukla dosya okumayı Read'e/aralığa çevirmek** | sed/cat/head %3.6 | `capabilities.toml`'a `sed -n`, `cat`, `head` → "Read offset/limit" kaydı; bypass ipucu zaten oturumda bir kez | yok | ham Bash payı |
| A3 | **Tekrarlanan inline betiği araca dönüştürmek** | heredoc %4.5 | `eos` betik gövdesinin hash'ini tutar (içeriği değil); aynı/benzer betik 2+ oturumda görülünce "bunu `automation/`'a al" önerisi (`eos capabilities --suggest`) | yok | heredoc payı |
| A4 | **Wrapper çıktı sözleşmesi** | wrapper %10.5 | her wrapper: özet ≤ K satır + ayrıntı diskte (çoğu zaten); en büyük 5 wrapper'ı resident_context ile bulup kısalt | eksik bilgi → ek çağrı | wrapper payı |
| A5 | **Her zaman yüklenen ön ekler** | taban %18, skill %3.2, CLAUDE.md %4.8 | kullanılmayan user-level plugin'leri kapat (skill listesinin ~%60'ı önceki ölçüm); CLAUDE.md'den nasıl-yapılır'ı EOS notuna taşımaya devam; `context-budget.sh` eşik | bir skill'in eksikliği | taban, skill, instructions payı |
| A6 | **EOS enjeksiyonunun isabetini ölçmek** | %1.0 | not/brief teslim edildikten sonra konusu (dosya, wrapper, run) oturumda kullanıldı mı → isabetsiz türleri kes | yok | isabet oranı |

## 2. Gereksizi dışarı atmak

Claude Code'da bağlamdan tek tek öğe silinemez; araçlar: compact, /clear, alt ajan.

| # | Öneri | Etki (üst sınır) | Mekanizma | Risk |
|---|---|---:|---|---|
| R1 | **Compact penceresi** | 400k: ana girdi ×0.68 | `autoCompactWindow` (ayar, senin kararın) | compact sonrası kayıp |
| R2 | **EOS'un compact talimatı** | R1'i güvenli kılar | PreCompact hook stdout = `newCustomInstructions` (2.1.283'te doğrulandı): açık run/prosedür, değişen dosyalar, alınan kararlar, açık iş; "araç çıktılarını özetleme, dosyada" | yok |
| R3 | **Compact'tan sonra doğrulama** | kalite | PostCompact `compact_summary` alır: run id / değişen dosya özetde yoksa SessionStart(compact) brief'i onları ekler (brief zaten gelir) | yok |
| R4 | **Görev sınırında temizleme önerisi** | run bitişlerinde ×0.91 (bugünkü az kullanımla) | `eos run finish` / Stop: bağlam > X ise "bu görev kapandı; durum EOS'ta (run, not, dosyalar) — /clear güvenli" tek satır | kullanıcı yeni görevde eski bağlamı isteyebilir |
| R5 | **Keşfi alt ajanda yapmak** | Read + ham grep/sed/cat ana oturumdan çıkar | brief: kapsamı geniş investigation için "keşfi Explore/eos-researcher'a ver"; alt ajan bağlamı atılır, ana oturuma yalnız özet döner (Agent sonuçları bugün %0.3) | alt ajan maliyeti (sonnet) |
| R6 | **"Dosya değişti" bildirimini kaynağında kesmek** | %2.9 | okunmuş bir dosyaya kabukla yazma (python/sed -i) PostToolUse'da tespit → Edit aracı ipucu (kural brief'te var, zorlanmıyor) | yok |

## Sıra

1. Ölçüm çizgisi: `resident_context.py` bugünkü tabloyu verir; her adımdan sonra aynı tablo.
2. Ucuz ve risksiz: R2, R3, A2, R6, A6 (EOS hook'ları, motor dilden bağımsız; Claude'a özgü kısım plugin'de).
3. Karar isteyen: R1 (pencere), A5 (plugin kapatma).
4. Ölçümle doğrulanacak: A1 (uyarı → deny), A4, R4, R5.

Yapılmayacak: canlı isteği yeniden yazan proxy, MCP optimizer, transcript dosyasını düzenlemek,
tekrar okuma blokajı (bizde %4), ölçülmemiş "tasarruf" panosu.

## As-built (2026-09-26, EOS 1.5.1)

| # | Durum | Nerede / kanıt |
|---|---|---|
| R1 | DONE | `.claude/settings.json` `autoCompactWindow: 400000` (tamsayı; 2.1.283 şeması `int` 100k–1M) |
| R2 | DONE | EOS `pre-compact`: açık run + prosedür, tutulan iş, değişen dosyalar, "ham çıktıyı at"; canlı: headless `/compact` → telemetri `pre-compact` 212 karakter |
| R3 | DONE | EOS `post-compact` özetin neyi tuttuğunu sayar; `session-start` (compact) listeyi geri verir; kanıt `sessions.jsonl` `compactions`. `async` PostCompact headless'ta hiç koşmadı → senkron (1.5.1) |
| R4 | DONE | EOS `post-tool`: `eos run finish` + son çağrı ≥ 150k bağlam → tek /clear ipucu |
| R5 | DONE | ROUTE bloğuna `Context:` satırı (investigation / code_review / repository_wide_change, LOW hariç) |
| R6 | DONE | EOS `post-tool` okunan dosyaları mtime ile izler; kabuk yeniden yazınca oturumda bir kez dosya adıyla ipucu; Edit aracı tabanı günceller |
| A1 | DONE — sapma | Plan "önce uyarı" diyordu; uyarı okumayı geçirip haritayı da bağlama eklediği için maliyeti artırır. Uygulanan: bir kez `deny` + harita, aynı Read tekrar geçer (`outline_lines = 600`). Canlı: 2102 satırlık dosya → harita → model 23 satır okudu (649 karakter) |
| A2 | DONE | `capabilities.toml` `read`: `sed -n`, `cat/head/tail <dosya>` → Read ipucu |
| A3 | ERTELENDİ | Tekrarlanan inline betik tespiti için betik hash'i gerekiyor; önce R/A etkisini ölç |
| A4 | DONE (search) | En büyük wrapper `search.sh` (%15): glob başlığı sayıyla, tüm satırlar görünürken FILES listesi yok. `db.sh pg` (%9) satır verisi — dokunulmadı |
| A5 | DONE | nexus'ta 7 kullanılmayan plugin kapalı (5'i claude.ai synced); skill listesinin ~2/3'ü, oturum başına ~7k token |
| A6 | ÖLÇÜLDÜ, değişiklik yok | `injection_hits.py`: EOS enjeksiyonu cache okumasının ~%1'i; not başlıklarının ~%31'i kullanılıyor ve liste sırasından bağımsız — kısaltmak isabeti aynı oranda keser, kazanç ≤ %0.4 |

**Bir hafta sonra (2026-10-03) ölçülecek:** `resident_context.py` (kaynak tablosu), `spend_share.py`
(ana oturum payı), `compact_sim.py` yerine gerçek compact sayısı, `.eos/data/sessions.jsonl`
(`outlined`, `hinted`, `compactions[].missing`). Kalite: compact sonrası kayıp (missing), harita
reddinden sonra modelin tam okuma oranı.
