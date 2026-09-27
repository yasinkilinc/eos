# Plan: subagent model routing'ini kuru koşudan canlıya almak

**Durum:** Taslak, 2026-09-26. Önceki: `eos-claude-integration-plan.md` 3.6 (korpus ölçüldü,
eşik geçilmedi, hook `hook_dry_run = true`). Kısıtlar değişmedi: EOS model çağırmaz, karar
deterministik ve açıklanabilir, tablo değişikliğini bir insan kabul eder (ADR-018/025),
istem metni saklanmaz (ADR-019).

## 0. Neden şu an canlıya alınamaz (ölçüldü)

| Bulgu | Kanıt | Sonuç |
|---|---|---|
| Gerçek istemlerin **%97'si Türkçe** | 135 transcript, 175 istem | İngilizce tablolar asıl trafiği görmüyor |
| Eşleştirme yalnız İngilizce çekim eki tanır | `classify._pattern`: `\bkelime(s\|es\|ed\|ing\|d)?\b` | Config'e `hata` eklemek `hatasını`, `düzeltelim`'i yakalamaz |
| Kural sözlükleri kodda sabit | `QUESTION_OPENERS`, `REPOSITORY_WIDE`, `FAILURE_WORDS`, `CHANGE_VERBS` | "neden…", "tüm servislerde" kurala girmez |
| Karmaşıklık kelimeleri kodda sabit | `ARCHITECTURAL_WORDS`, `RISK_WORDS`, `HEDGES` (`score.py`) | "ödeme", "canlı", "sözleşme", "emin değilim" seviyeyi yükseltmez |
| Korpus küçük ve ayrı test kümesi yok | nexus 32 satır (tür %46.9, seviye %46.9, model %71.9) | Ayar yapılırsa ölçüm kendi kendini doğrular |
| Gerçek kuru koşu verisi yok | `decided` "dry run" olayı: 1 | Canlı öncesi gözlem penceresi başlamadı |

## 1. Hedef ve kapı (sonuçlar görülmeden önce yazılır)

Canlıya geçiş, **ayrılmış test kümesinde** ve **gerçek oturum gözleminde** birlikte:

| Ölçüt | Eşik |
|---|---|
| Model isabeti (test kümesi, etiket kabul edilen modellerden biri) | ≥ %85 |
| Eksik yönlendirme: HIGH/CRITICAL etiketli görevin daha ucuz modele gitmesi | ≤ %3 |
| CRITICAL → haiku | 0 |
| Modelin reddedeceği effort | 0 |
| Kuru koşu gözlemi: gerçek subagent kararlarından insanın incelediği örnekte "yanlış model" | ≤ %10 (n ≥ 40) |

Kapıdan geçemezse hook kuru koşuda kalır; eşik düşürülmez, nedeni yazılır.

## 2. Fazlar

### R0 — Korpusu test edilebilir yapmak (host)

| Satır | İş | Çıktı |
|---|---|---|
| R0.1 | Transcript'lerden gerçek istem havuzu: 175 istemi yerel bir çalışma dosyasına çıkar (scratch; kimlik bilgisi/sırrı olan satırlar elenir, repo'ya ham döküm girmez) | aday liste |
| R0.2 | ~150 istem seç (tür dağılımı dengeli), **ben ön-etiketlerim, sen düzeltirsin**; etiket = dikkatli bir mühendisin seçimi, politikanın cevabı değil | `docs/eos-evals/golden/routing.tsv` ≥ 150 satır |
| R0.3 | `split` kolonu: sabit %70 dev / %30 test (istem özetine göre deterministik) | test kümesine ayar yapılmaz |
| R0.4 | Mevcut 32 satır ve EOS'un genel korpusu korunur (İngilizce regresyon) | iki korpus |

### R1 — Motor: dilden bağımsız genişletme noktaları (EOS upstream, 1.4.0)

Motora Türkçe girmez (ADR-013: dil proje config'indedir); motor, config'in Türkçeyi
anlatabilmesi için gereken genel mekanizmayı kazanır.

| Satır | İş | Test |
|---|---|---|
| R1.1 | Kök eşleşmesi: config'te `*` ile biten kelime önek olarak eşleşir (`hata*` → hatası, hataları); yalnız config kelimelerinde, yerleşik tablolar aynen | birim + mevcut korpus değişmez |
| R1.2 | `[model_routing.rules]`: `question_openers`, `repository_wide`, `failure_words`, `change_verbs`, `implementation_verbs` — sabitleri **genişletir**, değiştirmez | kural sırası testleri |
| R1.3 | `[model_routing.factors]`: `architectural`, `risk`, `hedges` kelimeleri | seviye testleri |
| R1.4 | Normalleştirme: `İ`/`I` doğru küçülür (Python `lower()` `İ`→`i̇` üretir) | birim |
| R1.5 | `eos route --eval`: `--split dev\|test`, karışıklık matrisi, eksik/aşırı yönlendirme oranı, CRITICAL→haiku sayısı; kapı ölçütlerini tek satırda PASS/FAIL basar | CLI testi |
| R1.6 | Hook'a güven tabanı: `hook_min_confidence` (varsayılan 0.0); canlıda altındaki kararlar uygulanmaz, kaydedilir | hook testi |

### R2 — nexus sözlüğü (host config, senin kabulünle)

| Satır | İş |
|---|---|
| R2.1 | **Yalnız dev kümesinden** Türkçe kelime listesi önerisi: tür başına kökler (`düzelt*`, `hata*`, `tasarla*`, `mimari*`, `planla*`, `incele*`, `test*`, `yeniden düzenle*`…), kurallar (`neden`, `nasıl`, `nerede`, `tüm servislerde`, `bütün repolarda`) ve faktör kelimeleri (`ödeme*`, `canlı`, `prod*`, `sözleşme*`, `emin değilim`) |
| R2.2 | Liste sana tablo olarak gelir: kelime → tür → dev'de düzelttiği/bozduğu satır sayısı. Kabul edilenler `.eos/config.toml`'a yazılır; config izlenmediği için blok `ensure-eos-mcp.sh`'ın nexus bloğuna taşınır |
| R2.3 | Test kümesi **bir kez** ölçülür; sonuç kapı tablosuna yazılır. Kapıdan kalırsa dev'e dönülür, test kümesi yeniden etiketlenmez |

### R3 — Gerçek oturumda kuru koşu gözlemi (host)

| Satır | İş |
|---|---|
| R3.1 | Kuru koşu açık kalır; `decided` olayları birikir (tool_use_id ile) |
| R3.2 | İnceleme raporu `context-budget.sh --route-review`: kuru koşu kararını, harness'ın **kendi transcript'indeki** subagent istemiyle `tool_use_id` üzerinden eşler (EOS istemi saklamaz); her satır: istemin ilk 120 karakteri, önerilen model, gerçekte koşan model, subagent'ın atıf sayısı |
| R3.3 | En az 40 karar birikince sen örneklemi işaretlersin (doğru/yanlış model); oran kapıya girer |
| R3.4 | Maliyet etkisi tahmini: kuru koşu kararlarının dağılımı × model fiyatları, bugünkü (hep sonnet) ile karşılaştırma — ucuzlama mı pahalanma mı, sayı olarak |

### R4 — Canlıya geçiş ve geri dönüş

| Satır | İş |
|---|---|
| R4.1 | Kapı geçilince `hook_dry_run = false`, `hook_min_confidence = 0.6` |
| R4.2 | Bir hafta izleme: `eos route --stats` (subagent mesajlarının önerilen modelde payı), run sonuçları, SubagentStop atıf sayısı, subagent başı maliyet |
| R4.3 | Geri dönüş tek satır: `hook_dry_run = true`. Tetikleyiciler: başarısız run oranında artış, haiku'ya giden görevlerde atıfsız cevap artışı, maliyet artışı |
| R4.4 | Effort subagent başına verilemiyor (harness kısıtı); ölçülür ama canlıya alınmaz |

## 3. Sıra ve tahmini iş

R0 (etiketleme senin ~1 saatin) → R1 (EOS 1.4.0, testlerle) → R2 → R3 (en az 40 karar: birkaç
günlük normal kullanım) → R4. R1, R0 ile paralel yürür. Model çağrısı gerektiren tek adım yok;
korpus ölçümü ücretsiz.

## 4. Senin kararın gerekenler

1. **Etiketleme:** ben ön-etiketleyip sen düzeltir misin, yoksa ilk 50'yi birlikte mi yapalım?
2. **Kapı eşikleri:** §1'deki değerler uygun mu (özellikle %85 ve eksik yönlendirme ≤ %3)?
3. **Gerçek istemlerden korpus:** repo'ya yalnız etiketli, sırlardan arındırılmış istemler girer — kabul mü?
4. **Güven tabanı:** canlıda 0.6 altı kararlar uygulanmasın mı?

## 4a. Kararlar (2026-09-26) ve as-built

**Kararlar:** iki geçişli etiketleme (ben ön-etiket, sen düzeltme); kapı eşikleri §1'deki gibi,
sonuç görüldükten sonra değişmez; korpusa yalnız temizlenmiş asgari metin, ham transcript asla;
`hook_min_confidence = 0.6` (altı uygulanmaz, yalnız kaydedilir, yerine başka karar seçilmez).

| Satır | Durum | Kanıt |
|---|---|---|
| R1.1–R1.6 | DONE — EOS 1.4.0 | kök `*`, `[model_routing.rules]`, `[model_routing.factors]`, `İ` normalleştirme, `--split` + kapı (exit 0/1), `routing.withheld` (kuru koşu / güven tabanı / seviye gereksinimi); 20 yeni test, paket 1,014 |
| R0 | DONE (pass 1) | `automation/lib/routing_corpus.py` (çıkar, temizle, 70/30 hash bölme, `check`); `routing-real.tsv` 63 satır, `review=pending`; `test-routing-corpus.sh` |
| R2 | KABUL — config'de | 15 kelime birebir (`mı`, `sorun*`, kanıtsızlar yok); `ensure-eos-mcp.sh` nexus bloğu `[model_routing]` + `[model_routing.keywords]` sahibi; dev yeniden: tür 61.0 / seviye 58.5 / model 100 / eksik %0 PASS; İngilizce 62.7 / 71.2 / 74.6 değişmedi |
| R3 araç | DONE | `automation/lib/route_review.py`: karar ↔ transcript `tool_use_id` eşleşmesi, maskelenmiş önizleme, gerçek model, atıf sayısı, `--summary` kapı |
| Pass-2 (A) | DONE 2026-09-26 | 63/63 `review=ok`; beş seviye düzeltmesi (satır 3, 6, 11, 17, 25); dev yeniden: seviye 58.5 → 56.1, diğerleri aynı, PASS |
| Test ölçümü (A) | DONE — bir kez, 2026-09-26 | 22 satır: model 22/22 (0 hata), eksik yönlendirme 0/5, CRITICAL→haiku 0, effort 0 → **GATE PASS**; tür 10/22 (%45.5, 12 hata), seviye 12/22 (%54.5, 10 hata); 1 hata ≈ 4.55 puan; kayıt `.eos/data/routing-eval.jsonl` (corpus 68a45bd597c329e6, config 4f9d94ff0edc74f4) |
| R3 korpus (B) | DONE | `routing-subagent.tsv` 277 satır, 53 yönlendirilebilir; `routing_opportunity.py`; 2000 karakter sınırı testi EOS `test_hooks.py` |

**Ölçülen engel:** 175 gerçek istemden 107 benzersiz aday çıktı (600 karakterden uzunlar
yapıştırılmış talimat sayıldı); görev içermeyenler atıldıktan sonra 63 satır kaldı — §2'deki
≥150 hedefinin altında. Test kümesi 22 satır: tek bir hata %4.5 oynatır.

**Planı değiştiren bulgu — hook'un girdisi kullanıcı istemi değil.** Subagent hook'u modelin
Agent çağrısına yazdığı istemi sınıflar: 237 gerçek subagent isteminin **%91'i İngilizce**,
medyan 3,019 karakter; yalnız **19'u** yönlendirilebilir (tipsiz/general-purpose ve model
belirtilmemiş). Mevcut politika general-purpose istemlerin **133'ünden 132'sini sonnet'e**
gönderiyor — bugünkü `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` ile aynı. Karar ilk ~2,000 karaktere
bağlı (1,500'de kesilince %83 aynı), yani subagent korpusu kısaltılamaz. Sonuçlar:

- Türkçe sözlük (R2) **ROUTE satırını** (oturum tavsiyesi) iyileştirir; hook kapısı için
  doğru korpus, subagent istemleridir.
- Hook'u bugünkü politikayla canlıya almak pratikte hiçbir çağrıyı değiştirmez; değer ancak
  politika subagent işlerinde haiku/opus ayrımı yaptığında doğar.
- Kuru koşu inceleme kapısı (n ≥ 40) bu hızla (135 oturumda 19 yönlendirilebilir çağrı) uzun
  sürer.

**Karar bekleyen (yeni):** (a) subagent istem korpusu — model yazısı, kısaltılamaz; repo'ya
temizlenmiş olarak mı girsin, yoksa yerel mi kalsın (`logs/`, klondan yeniden üretilemez)?
(b) hook kapısı bu korpusla mı ölçülsün? (c) önerilen Türkçe blok kabul mü?

## 4b. Revize karar (2026-09-26): iki ayrı değerlendirme hedefi

İki hedef ayrı ölçülür, doğruluk sayıları **asla birleştirilmez**:

| | A — ROUTE korpusu | B — subagent korpusu |
|---|---|---|
| Dosya | `docs/eos-evals/golden/routing-real.tsv` | `docs/eos-evals/golden/routing-subagent.tsv` |
| Girdi | kullanıcının yazdığı istem (oturum tavsiyesi) | modelin Agent çağrısına yazdığı istem (hook'un gördüğü) |
| Boyut | 63 (dev 41 / test 22), etiketli | 277, 53 yönlendirilebilir (35'i pin'den sonra), etiketsiz |
| Ölçtüğü | sınıflandırma doğruluğu ve güvenlik kapıları | canlı hook'un bugünkü davranışı ne kadar değiştireceği |
| Araç | `eos route . --eval … --split dev\|test` | `python3 automation/lib/routing_opportunity.py [--since '']` |

**B korpusu nasıl kuruldu:** ana oturumlar ve iç içe subagent transcript'leri; istem hook gibi ilk
2000 karakterde kesilip maskelenir (sırlar, host, ≥5 haneli id, hex, workspace kökü, commit
yazarının adı; çağrı kimliği yok). Maskelenmiş satırla ham metin 53/53 aynı kararı verir (güven
dahil) — yol kökü maskelenirken `…/test/…` segmenti bu yüzden korunur. Satırda gerçek model
(`resolvedModel`) ve gün var. İki elle yazılmış hook probu çıkarıldı. Önceki "19" yalnız ana
oturumlardı (bugün 20); iç içe subagent'lar 33 ekler.

**Taban:** `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` 2026-09-17'de (35cee02) geldi. Kayıtlı model bunu
doğruluyor: pin öncesi bilinen 11 yönlendirilebilir çağrının 11'i Opus, sonrası 7'nin 7'si Sonnet.
Fırsat ölçümü bu yüzden varsayılan olarak pin'den sonrasını sayar.

**R3 sonucu (B, pin sonrası, 35 çağrı, taban 0.6):**

| Ölçü | Değer |
|---|---|
| Önerilen model | sonnet 31, opus 4, haiku 0 |
| Seviye | MEDIUM 26, HIGH 7, CRITICAL 2 |
| Güven | <0.4: 14, 0.4–0.6: 12, 0.6–0.8: 9, ≥0.8: 0 |
| Taban altında tutulan (uygulanmaz, sonnet kalır) | 26 / 35 |
| **Canlı hook'un değiştireceği** | **2 / 35 (%5.7), ikisi de sonnet → opus** |
| HIGH/CRITICAL → sonnet altı | 0 |
| CRITICAL → haiku | 0 |
| Kayıtlı model ↔ öneri farkı | 0 / 7 bilinen |
| Göreli maliyet (tüm sonnet = 1) | 1.057 (tasarruf yok, +%5.7) |
| Türkçe sözlüğün B'ye etkisi | 0 (sözlükle ve sözlüksüz aynı) |
| Tüm dönem (53 çağrı) | 4 / 53 değişir, hepsi → opus; maliyet 1.076 |

Değişen iki çağrı aynı Ruflo kaynak denetimi istemi (salt okuma denetimi → `architecture CRITICAL`,
güven 0.71–0.73); salt okuma denetim için opus'un gerekli olduğu tartışmalı. Politika hiçbir
subagent işini haiku'ya indirmiyor.

**Kapılar (ayrı ayrı):**

| Kapı | A (ROUTE, dev) | B (subagent) |
|---|---|---|
| model doğruluğu ≥ %85 | 100% PASS | ölçülemez (etiket yok) |
| HIGH/CRITICAL → daha ucuz ≤ %3 | %0 (0/6) PASS | 0 PASS |
| CRITICAL → haiku = 0 | 0 PASS | 0 PASS |
| desteklenmeyen effort = 0 | 0 PASS | — (hook effort yazmaz) |
| elle incelenen gerçek kuru koşu kararı yanlış ≤ %10, n ≥ 40 | — | **ölçülemez: 1 kayıtlı karar** |
| test kümesi (bir kez) | model 100% (22/22), eksik 0/5, CRITICAL→haiku 0, effort 0 — PASS | — |

**R4 kararı: canlıya geçiş gerekçesi yok.** Fırsat pratikte sıfır (35'te 2, yalnız pahalı yöne,
tasarruf yok). Kural gereği `hook_dry_run = true` kalır; `hook_min_confidence = 0.6` değişmez;
gizli yedek model seçimi yok. Hook kuru koşuda karar kaydetmeye devam eder. Yeniden açma koşulu:
politika subagent işlerinde haiku ayrımı yapabildiğinde ya da B korpusunda anlamlı değişiklik
oranı ölçüldüğünde.

**Ek bulgu:** pin sonrası 10 `test_generation` kararının 6'sı yalnız workspace yolundaki `test`
kelimesinden (`<ws>/test/nexus`). Model değişmiyor (sonnet) ama sınıflandırıcı yol parçalarını
kelime sayıyor. Dile bağlı olmayan bir çözüm (yol biçimli belirteçleri anahtar kelime eşleşmesinden
dışlamak) EOS için aday; test kümesine göre ayar sayılmaması için bu turda yapılmadı.

## 4c. Ruflo ve RAGFlow neden "tasarruf ediyor", EOS neden etmiyor (2026-09-26)

Kaynak: `docs/eos-plans/eos-2x-research/ruflo-runtime-routing.md` §4, `ragflow-agents-memory.md`
§ modlar, `ragflow-retrieval.md` §9; yeni ölçümler `docs/eos-evals/savings/` (Ruflo 88955d9).

**1. Ruflo'nun router'ı bizim gerçek subagent istemlerimizde tasarruf etmiyor, harcamayı ikiye
katlıyor.** İki router da değiştirilmeden B korpusunda koşuldu (soğuk başlangıç, bandit 400 çekiliş):

| B, pin sonrası 35 çağrı | haiku | sonnet | opus | maliyet / hep-sonnet | maliyet / hep-opus |
|---|---:|---:|---:|---:|---:|
| Ruflo `EnhancedModelRouter` | 0 | 2 | 33 | 1.94 | 0.97 |
| Ruflo `ModelRouter` (bandit, ort.) | 0 | 1.8 | 33.2 | 1.95 | 0.97 |
| EOS (canlı, taban 0.6) | 0 | 33 | 2 | 1.06 | 0.53 |

Sebep: Ruflo'nun karmaşıklık puanı kısa görev cümleleri için ("fix typo in README" 0.12); `words/50`
ve `words/100` terimleri uzun istemde doyar, alt dizge eşleşmesi her kelimeyi sayar — gerçek
istemlerde medyan 0.67 (0.31–0.81) → 0.6 eşiğinin üstü → opus.

**2. Ruflo'nun "~%75" iddiası hep-opus tabanına göre ve ölçülmemiş.** Maliyet çarpanları opus'a
göre (haiku ×0.04, sonnet ×0.2); kendi baseline belgesi token tasarrufu rakamlarını "claimed
upstream, not yet verified" diye işaretler; seviye doğruluğunu ya da maliyet/kaliteyi ölçen test
yok. Aynı tabanla EOS bugün zaten 0.53 (%47 "tasarruf") — ama bunu router değil,
2026-09-17'deki `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` pini sağladı: pin öncesi yönlendirilebilir
çağrıların 11/11'i opus, sonrası 7/7 sonnet. Router'ın alabileceği büyük pay pinle alınmış.

**3. Ruflo'nun gerçek yapısal tasarrufu model seçimi değil, modeli hiç çağırmamak:** Tier-1 codemod
(AST dönüşümü, $0). Bandit'in "öğrendiği" haiku tercihi ise kalite değil maliyet ödülü (haiku
başarısı 1.0, opus 0.4; başarı = API hata vermedi). Claude Code içinde karar yalnız metin
(`[TASK_MODEL_RECOMMENDATION]`), `updatedInput` yok; mekanik uygulama yalnız kendi doğrudan-API
çalışma zamanında.

**4. RAGFlow modeli hiç yönlendirmez** (`Dialog.llm_id` sabit). Tasarrufu **iş miktarından**:
modlar (`low` ajan/araç yok; `medium` en çok 3 SCA turu; `high` fan-out), sabit bütçeler (tur,
adım, süre, `recursion_limit=60`), bağlam bütçesi (`kb_prompt` ≤ %97 `max_tokens`,
`message_fit_in` geçmişi atar), tekrarlı araç çağrısı bastırma (Jaccard 0.8 + önbellek), 2 boş
sonuçtan sonra aracı kapatma. Orkestratör kendisi olduğu için çağrı sayısını ve bağlamı yönetir.

**5. EOS'un kolu harcamanın %2'sine dokunuyor.** `spend_share.py`, pin sonrası, göreli birim:

| Nereye gidiyor | Pay |
|---|---:|
| Ana oturum (insanın seçtiği opus) | %78.7 |
| Tipli / modeli açık subagent (atlas %8.2, açık opus %6.8, Explore %1.6 — opus'ta) | %19.2 |
| **Yönlendirilebilir subagent (hook'un tek alanı)** | **%2.1** |

Kusursuz bir router bu %2.1'in tamamını haiku'ya indirse toplam en çok ~%1 düşer. Ana oturum
harcamasının %79.4'ü **cache okuması**; çağrı başına ortalama bağlam 289k token (13,964 çağrı).
Maliyeti belirleyen model fiyatı değil, her çağrıda yeniden okunan bağlamın büyüklüğü × çağrı sayısı.

**Sonuç — tasarruf açısından aktarılabilecek olanlar (öneri, uygulanmadı):**
- Model routing'i genişletmek değil, **bağlamı ve çağrı sayısını küçültmek** (RAGFlow'un kolu):
  büyük araç çıktıları (wrapper'lar zaten), oturum içi tekrarlı enjeksiyon, "changed on disk"
  yeniden gönderimi (1.41M token ölçülmüştü), erken/planlı compact.
- **Modeli hiç çağırmamak** (Ruflo Tier-1'in EOS karşılığı): deterministik iş için wrapper/betik.
- Ana oturum modeli: ROUTE satırı zaten öneriyor (sonnet), uygulanmıyor; karar insanın.
- Tipli ajanların modeli config meselesi (ajan tanımı), routing değil: Explore pine rağmen opus'ta
  koştu (gözlem; sebebi doğrulanmadı).

## 5. Yapılmayacaklar

| Özellik | Neden değil |
|---|---|
| Öğrenen/istatistiksel sınıflandırıcı | ADR-025: deterministik ve açıklanabilir; düzeltilebilir olmalı |
| Motora Türkçe tablo gömmek | ADR-013: dil proje config'inde |
| Test kümesine göre kelime ayarı | ölçüm kendi kendini doğrular |
| Belirsizlikte üst modele yükseltme | Ruflo #2250 dersi; ADR-025 |
| Subagent effort'unu çağrı başına ayarlamak | harness desteklemiyor |
