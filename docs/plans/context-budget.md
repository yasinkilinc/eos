# Plan: bağlam bütçesi — oturumun okuduğu her md ve hafıza kaydı EOS üzerinden

Hedef: bir oturumun **ilk araç çağrısından önce** ödediği token'ı ve **iş sırasında**
okuduğu md/hafıza maliyetini düşürmek; hiçbir kuralı kaybetmeden, hiçbir bilgiyi
silmeden. Yöntem H-05'te (`dev-nexus` 81,689 → 2,912 token) ölçülüp doğrulanan yöntemin
geri kalan dosyalara uygulanması: kural yüklü kalır, olgu/prosedür/referans EOS'a taşınır
ve EOS'un kancaları onu işin anında getirir.

Durum yalnız §3'teki tabloda tutulur. Oturum bu dosyayı ve
`automation/context-budget.sh` (B-00) çıktısını okuyarak devam eder.

## 0. Neden — ölçüm (2026-09-24, `automation/context-budget.sh`, karakter ÷ 3.5)

Sayılar B-00 betiğinin çıktısıdır; plan ile kapı (G-01) aynı kaynaktan okur. Karakter
Unicode karakterdir (ilk taslaktaki `wc -c` bayt sayıyordu; Türkçe dosyalarda fark ~%4).

**Koşulsuz yük — Claude, atlas ana oturum** (atlas alt ajanı aynı seti yükler — B-00):

| Dosya | Karakter | Token | Hedef |
|---|---:|---:|---:|
| `~/.claude/CLAUDE.md` (kullanıcı, global) | 1,408 | 402 | 405 — dokunulmaz |
| `CLAUDE.md` (nexus) | 22,015 | 6,290 | ≤ 3,500 |
| `.claude/agents/atlas.md` | 9,223 | 2,635 | ≤ 1,300 |
| `project-intelligence/SKILL.md` | 17,941 | 5,126 | ≤ 2,300 |
| `dev-atlas/SKILL.md` | 61,126 | 17,465 | ≤ 4,500 |
| `dev-nexus/SKILL.md` | 9,685 | 2,767 | ≤ 3,000 — H-05'te yapıldı |
| `MEMORY.md` (kişisel hafıza) | 12,003 | 3,429 | ≤ 1,000 |
| **Toplam** | **133,401** | **38,114** | **≤ 16,000 (−58%)** |

Bağlam için: araç çağırmayan bir haiku atlas alt ajanı 64,307 token tüketti; harness'ın
kendi yükü (sistem prompt'u, araç şemaları) bunun ~26k'sı, bu dosyalar ~38k'sı. Yani ilk
çağrıdan önceki bağlamın ~%60'ı bu planın kapsamında.

**Devin, atlas** (`devin rules list`, B-00): koşulsuz yük `CLAUDE.md` 6,290 +
`~/.claude/CLAUDE.md` 402 + `.devin/rules/global_rules.md` 2,769 + `.devin/agents/atlas.md`
2,664 = **12,125**. `graphify.md` `manual` — yüklenmiyor. Windsurf'ün eski
`~/.codeium/windsurf/memories/global_rules.md`'si hâlâ always-on ama boş (0). Devin
atlas'ında `skills:` önyüklemesi yok: skill'ler görev başına `/project-intelligence` (her
görevde) ve `/devin:dev-atlas`, `/devin:dev-nexus` ile uygulanıyor; üçü uygulanınca
**37,483** → hedef **≤ 16,000**.

**İş sırasında — ölçülen:**

| Kaynak | Maliyet |
|---|---|
| `eos-prompt.py` (UserPromptSubmit) | prompt başına ≤ 6,000 karakter tavan; bir görev için 1,938 ölçüldü; oturum içi tekrar yok |
| `note_inject.py` (PostToolBatch) | dokunuş başına ≤ 3,000 token; `CLAUDE.md` okumak 8,286 karakter getirdi (zaten yüklü dosya), `pom.xml` 8,348, bir Java dosyası 8,267 |
| dev-atlas yan sayfaları (10 dosya) | 256,638 karakter; en büyükleri version-upgrade 51,744, dev-rules 38,157, hexagonal-template 22,432, test-template 18,842 |
| Diğer talep üzerine açılanlar | project-intelligence ekleri 30,839; graphify skill 62,287; `docs/open-items.md` 20,400; `docs/journeys/*` 78,838; `automation/scenarios/README.md` 14,678; Devin workflow'ları 40,457 |
| Hafıza konu dosyaları | 32 dosya, 57,803 karakter; `MEMORY.md`'den işaretle açılır |
| **Değişen-dosya bildirimleri** (B-01) | harness, bağlamdaki dosya dışarıdan değişince farkını gönderir: 40 oturumda **medyan 11,454 token/oturum**, toplam 716,507 — gözlenen en büyük kalem; başı `CLAUDE.md` 27.4k, `MEMORY.md` 26.4k, dev-atlas `SKILL.md` 17.2k |
| Servis `AGENTS.md` | 18 dosya, FM repolarında takipli; yalnız servis dizininde başlayan Devin oturumu yükler (always-on, B-00); oturum başına medyan 1,028, en büyük 8,486 token (`fm-next-gen-admin-toolbox-config-manager`) |

**Prompt hook'unun bugünkü davranışı** (`eos brief . --task "<p>" --task-only`, 2026-09-24,
planın incelenmesi sırasında ölçüldü — taşıma bunların üstüne yapılamaz):

| Prompt | Çıktı | Ne oldu |
|---|---:|---|
| `planı incele` | 2,140 kr | `run-a-scenario` prosedürü basıldı: prosedür seçici gövdeye de bakıyor, "planı" → `plan` tokeni bir known-failure satırındaki "rate-plan-change" ile eşleşti, "incele" ağırlığı 0 olduğu için tek gövde kelimesi sorguyu taşıdı |
| `push'la` | 1,615 kr | `deliver-changelog-change` basıldı — yanlış prosedür, tek kelime gövde eşleşmesi |
| `PR aç` | 0 | `_words` iki harfliyi atıyor (`pr`), `[a-z0-9]+` Türkçe harfte kesiyor (`aç` → `a`): görev adı tamamen kayboldu |
| `PROJ2-1625 için PR aç` | 524 kr | prosedür yok; ilgili notlar 1363 handoff ve ntf — `proj2` ön eki her PROJ2 notuyla eşleşiyor |
| `1588 ne durumda` | 0 | işaretçi cevaplanmıyor (W-01'in gerekçesi) |
| `evet`, `devam`, `tamam` | 0 | doğru |

Sonuç: gürültü prompt'u ~2k karakter ödetiyor, gerçek görev adı sıfır getiriyor. Prosedür
seçici ve tokenizer düzelmeden (E-02) yüklü setten hiçbir kural prosedüre taşınamaz; A-04
bu prompt'ları temelde kırmızı kaydeder.

**MEMORY.md ön sınıflandırması** (~48 madde, B-02 kesinleştirir): ~12 kural (kalır),
~14 `CLAUDE.md`'nin kopyası (branch adı, yazar, attribution, draft PR, jira/bb, confluence,
cache, test verisi, bssapi, db/obs, nexus commit, branch silme, `-fm` sürüm, wrapper guard),
~9 olgu/karar (env0 açıkları, EOM/Grafana erişimi, TOP_UP env2, CI/CD ayrıntısı, MCP
kararları, i2i, test kullanıcıları, skill symlink, stash-pop dersi), ~6 prosedür (v4.1
env1 checklist, DevAssistant'tan şifre yenileme, e2e prepaid aktivasyon, worklog,
domainconfig/liquibase_manage teslimi), ~8 EOS işaretçisi (TOP_UP fiyat, 1588, 1586, 1363,
waiting for top-up, regresyon/senaryo, levy, jurisdiction).

## 1. Hedef mimari — üç kanal, her içeriğe tek ev

| Kanal | Ne zaman ulaşır | Ne taşır |
|---|---|---|
| **Yüklü** | her oturumda, koşulsuz | yalnız kural |
| **Ulaşan** | işin anında, kanca ile | prosedür (`eos-prompt.py`), dosyaya bağlı olgu (`note_inject.py`), zorlanan kuralın düzeltmesi (`wrapper_guard.py`, `commit-msg`) |
| **Aranan** | biri sorunca | referans, tarihçe, geçmiş bulgu (`eos-query.sh notes`, `eos note show`) |

Her parça tek sınıfa girer ve sınıfı evini belirler:

| Sınıf | Tanım | Ev |
|---|---|---|
| **K** kural | her zaman doğru, davranışı değiştirir, ilgili olduğu an önceden bilinemez | yüklü dosya, bir kez |
| **Z** zorlanan kural | bir kanca ihlali **maliyet oluşmadan önce** reddediyor ve mesajı düzeltmeyi söylüyor | kanca mesajı + yüklü dosyada bir satır |
| **P** prosedür | bir görevin adımları; görev prompt'ta adıyla geçer | `kind: procedure` notu |
| **O** olgu | tarihli, eskir, koda/ortama bağlı | `finding`/`decision`/`lesson` notu, dosyaya scope'lu |
| **R** referans | uzun açıklama, tablo, şablon | `finding` notu, `<kaynak> <§>: …` başlıklı, gövde birebir taşınır |
| **T** tarihçe | neden böyle oldu | changelog / ADR, hiç yüklenmez |
| **Ç** çift | başka bir evde zaten var | silinir, gerekiyorsa tek satır işaret |

Yüklü setten çıkış kuralı (§6.1 `docs/eos-graphify-audit.md` dersinin kesin hali):
bir kural yüklü setten **ancak** (a) Z sınıfındaysa veya (b) yalnız adı prompt'ta geçen
bir görev sırasında geçerliyse ve B-03'ün varış testi (`arrival.tsv`) o prompt'ta kuralın
metninin hook çıktısına girdiğini kanıtlıyorsa çıkar. İkisi de değilse yüklü kalır.
Push/branch adı, attribution, test verisi, env2, commit izni bu yüzden yüklü kalır.

## 2. Korunan kısıtlar

- **Kural yüklenir, olgu aranır.** Kişisel hafıza kuralını EOS notuna taşımak varış testini
  geçemedi (2026-09-18). Sibling repo kuralları (test-automation'a push yok,
  `ui/fm-csr-ui` yalnız lokal) EOS scope'u dışında, yüklü kalır.
- **Hiçbir şey kaybolmaz.** Taşınan bölüm birebir taşınır, kaynak→not eşlemesi
  `docs/eos-plans/context-budget-map.tsv`'de tutulur. Yüklü dosyadan kesme, yeni evi
  testini geçtikten **sonra** yapılır.
- **Parite (2026-09-24'e kadar).** Davranış değiştiren hiçbir şey yalnız Claude'a veya yalnız
  Devin'e özgü bir dosyada kalmaz. İki `atlas.md` aynı değişikliği alır. *Kullanıcı kararı
  2026-09-24:* Devin artık kendi agent dosyalarıyla çalışır, Devin tarafına dokunulmaz;
  bu tarihten sonraki satırlar yalnız Claude tarafını değiştirir.
- **Dokunulmaz:** FM repoları (servis `AGENTS.md` dahil — değişiklik kullanıcı onayı ve PR
  ister), parent repolar, nexus'ta `tools/eos/` (motor değişikliği upstream → subtree),
  kullanıcının `~/.claude/CLAUDE.md`'si.
- **Sır yok.** Notlara kimlik bilgisi girmez; hafızadaki sır konumu satırları işaret olarak
  kalır.
- **Prompt/transcript içeriği saklanmaz** (ADR-019). B-01 raporu yalnız yol, sayı ve boyut
  taşır.
- **Commit:** nexus'ta her satır bitince küçük commit; push yalnız istenince; paralel
  oturum varken `git commit -a` yok, dosya adıyla stage.

## 3. Durum tablosu — devam noktası

| Satır | Konu | Bağımlı | Durum |
|---|---|---|---|
| B-00 | `automation/context-budget.sh`: giriş noktası başına yüklü set ve token | — | DONE 2026-09-24: Claude 38,114 / Devin 12,125 (+skill 37,483); `--check` 7 dosya aşımda; `--lint-memory` 21 bulgu |
| B-01 | Transcript madenciliği: iş sırasında gerçekte ne okunuyor, kanca ne kadar getiriyor | B-00 | DONE 2026-09-24: 111 oturum; en büyük kalem değişen-dosya bildirimi (medyan 11,454/oturum); `docs/eos-evals/context-reads-baseline.md` |
| B-02 | Kural defteri `docs/eos-evals/rules.tsv`: her kural, sınıfı, evi | B-00 | DONE 2026-09-24: 296 aday, 138 kural, kontrol temiz; 2 yalnız-Devin kuralı, 7 yeni prosedür |
| B-03 | Temel ölçümler: `arrival.tsv`, yeni altın kümeler, taze oturum `rules-hold` | B-02 | DONE 2026-09-24: varış 26/53 (mevcut prosedürler 17/17, işaretçi 1/8, gürültü 8/10); rules-hold 9/9; `docs/eos-evals/context-budget-baseline.md` |
| E-01 | Motor: procedure `## Rules` bölümü, brief'te tam basılır (EOS 1.1.0) | B-02 | DONE 2026-09-24: EOS 1.1.0 (d588029), yazarken/amend'de 600 karakter tavanı |
| E-02 | Motor: prosedür seçici yalnız başlık/etiket; Unicode tokenizer; PR/CI/DB gibi kısaltmalar (EOS 1.1.0) | B-03 | DONE 2026-09-24: gürültü 8/10 → 10/10, mevcut prosedürler 17/17 korundu; altın kümelerde düşüş yok (nexus-procedures r@1 0.69 → 0.75); 1.1.1 (tek nadir kelime adlandırmaz) ve 1.1.2 (tek kelime ancak prompt'un kendisiyse) G-02'de bulunan iki yanlış eşleşmeyle eklendi |
| W-01 | `eos-prompt.py`: tam ticket key ve ayırt edici kelime ile tüm store'larda başlık/etiket araması | E-02 | DONE 2026-09-24: işaretçi 1/8 → 5/8, gürültü 10/10 korundu, en kötü 53 ms; kalan 3 konu işaretçisi C-06'ya |
| C-01 | `CLAUDE.md` ≤ 3,500 token; wrapper dizininin tek evi | B-03 | DONE 2026-09-24: 6,290 → 2,658 token; 8 bölüm nota (A-06 0 sorun), `claude-md.tsv` r@3 0.93; Claude toplamı 38,114 → 34,482 |
| C-02 | İki `atlas.md` ≤ 1,300 token | C-01 | DONE 2026-09-24: Claude 2,635 → 1,112, Devin 2,664 → 1,072; wrapper bölümleri nota (A-06 10/10); Claude toplamı 32,959 |
| C-03 | `project-intelligence/SKILL.md` ≤ 2,300 token | B-03 | DONE 2026-09-24: 5,126 → 2,211; "Capability policy" nota (A-06 11/11), `project-intelligence.tsv` r@3 1.0; Claude toplamı ~30.0k |
| C-04 | `dev-atlas/SKILL.md` ≤ 4,500 token | E-01, E-02, C-01 | DONE 2026-09-24: 17,465 → 3,420; 25 bölüm nota (A-06 36/36), 6 yeni prosedür; varış prosedür 33/35; **Claude toplamı 15,997 ≤ 16,000 (A-01)** |
| C-05 | Devin kural dosyası: `global_rules.md` ≤ 800 (`graphify.md` manual, yük değil) | C-01, C-03 | DONE 2026-09-24: 2,769 → 478; eski içerik tek nota (A-06); Devin skill'li toplam 13,013 (A-02) |
| C-06 | `MEMORY.md` ≤ 1,000 token; 32 konu dosyası sınıfına göre taşınır | W-01, E-01, E-02 | DONE 2026-09-24: 3,429 → 699, lint 0; 16 konu dosyası + eski indeks nota (sha256 doğrulamalı); **Claude toplamı 13,274**; varış 53/53 |
| W-02 | `note_inject.py`: yüklü dosyaya enjeksiyon yok; tavan B-01 verisiyle | B-01 | DONE 2026-09-24 (kod): talimat dosyası 8,092 → 0, doküman dokunuşu 8,092 → 663, pom.xml 7,153 → 1,091 karakter; oturum p95 hedefi G-02'nin `--reads` tekrarında ölçülür |
| W-03 | dev-atlas yan sayfaları (> 10k karakter), B-01'in kısmi okuduğu gösterilenler notlara | B-01, C-04 | DONE 2026-09-24: version-upgrade 51,451 → 1,410, db-config-scripts 11,016 → 1,556, eom-grafana 12,053 → 1,758 karakter; 20 bölüm nota (A-06 77/77) |
| W-04 | `open-items.md`, journeys, scenarios README — öneri, **kullanıcı kararı** | B-01 | DONE (kullanıcı: "hepsini EOS'a"; 44 not, `bc13d3e`) |
| W-05 | Servis `AGENTS.md` — ölçüm ve öneri, **kullanıcı onayı + FM PR** | B-00 | DONE, planlanandan farklı: FM PR yok; AGENTS.md EOS'a kopyalanır (`0a22376`, `a08306c`, `06d869e`) |
| W-06 | Değişen-dosya bildirimleri: yüklü dosyalar seyrek ve tek seferde değişir | B-01 | BEKLİYOR: yeni oturumlar birikince `--reads` ile ölçülür (mekanizma C satırlarında uygulandı) |
| G-01 | Bütçe kapısı: nexus pre-commit + SessionStart'ta tek satır uyarı | C-01…C-06 | DONE 2026-09-24: pre-commit kapısı (A-09 4/4), SessionStart hafıza satırı, `check-agent-docs.sh` 5. bölüm |
| G-02 | Taze oturum değerlendirmesi: `rules-hold` + `where-did-we-leave-off` tekrar | C-06, W-02 | DONE 2026-09-24: rules-hold 9/9 (temel 9/9), where-did-we-leave-off iki yarı hook'tan, 0 araç; alt ajan maliyeti 86–91k → 66–68k |
| G-03 | Kapanış raporu, tablo son sayılarla | G-02 | DONE 2026-09-24: `docs/eos-evals/context-budget-report.md` |

Sıra: B → E-01, E-02, W-01 → C-01…C-06 → W-02…W-05 → G. C-03 ve W-02, bağımlılıkları bitince
paralel yapılabilir. Satırı alan oturum `eos work add . --title "context-budget <satır>" --claim`
ile sahiplenir.

## 4. Kabul kontrolleri

| Kod | Kontrol | Nasıl |
|---|---|---|
| A-01 | Claude atlas koşulsuz yük ≤ 16,000 token | `context-budget.sh` |
| A-02 | Devin atlas, üç skill uygulanmış toplam ≤ 16,000 token (koşulsuz kısım zaten 12,125) | `context-budget.sh --entry devin-atlas` |
| A-03 | Kural defterindeki her kural beyan ettiği evde bulunur; yüklü sette çift 0 (defterde gerekçesiyle beyan edilenler hariç) | `automation/tests/test-rule-homes.sh` |
| A-04 | Varış: P'ye taşınan kurallar için `arrival.tsv` %100; işaretçi prompt'ları ≥ %90; gürültü prompt'ları ("devam", "evet", "planı incele") 0 karakter | `automation/context-budget.sh --arrival` |
| A-05 | Erişim: yeni kümeler (`dev-atlas.tsv`, `project-intelligence.tsv`, `memory.tsv`) `eos note eval` recall@3 ≥ 0.90; mevcut kümelerde (`nexus`, `nexus-procedures`, `svc-cpq-ordercapture`) düşüş ≤ 0.03 | `eos note eval` |
| A-06 | Kayıp yok: eşleme dosyasındaki her bölümün not gövdesi kaynakla normalize edilmiş halde aynı | `automation/tests/test-moved-sections.sh` |
| A-07 | `MEMORY.md` ≤ 1,000 token; lint: tarihli olgu, EOS işaretçisi, `CLAUDE.md` kopyası 0 satır | `context-budget.sh --lint-memory` |
| A-08 | Yüklü setteki bir dosyaya dokunuş 0 karakter enjeksiyon; oturum başına enjeksiyon medyanı W-02'de yazılan hedefin altında | `test-note-inject` + B-01 betiği tekrar |
| A-09 | Bütçeyi aşan yüklü dosya commit'i dosya tablosuyla reddedilir | `automation/tests/test-context-budget-gate.sh` |
| A-10 | Taze oturum: `rules-hold`'da temelde tutulan hiçbir kural sonra kaçmaz; `where-did-we-leave-off`'un iki yarısı ilk eylem komutundan önce cevaplanır | G-02 raporu |
| A-11 | `check-agent-docs.sh` yeşil; `automation/tests/*` yeşil; E-01/E-02 için EOS pytest + `check-clean.sh` temiz | ilgili komutlar |
| A-12 | Aranan kanal gerçekten aranıyor: G-02'de en az bir görev taşınmış bir R bölümünü gerektirir ve oturum onu `eos note show`/`eos-query.sh notes` ile açar; "brief dışında EOS'a soran oturum" oranı temeldeki %41'in (45/111, B-01) altına düşmez | G-02 raporu + `context-budget.sh --reads` |
| A-13 | Devin varışı: `arrival.tsv`'nin P satırları, hook yerine atlas INTAKE komutu (`eos brief . --task`) ile de aynı çapayı getirir | `context-budget.sh --arrival --via intake` |

## 5. Satırlar

### B — önce ölç

**B-00 — `automation/context-budget.sh`.** Giriş noktaları: `claude-atlas` (ana oturum),
`claude-atlas-subagent`, `devin-atlas`. Her biri için yüklü dosya listesi, karakter,
token (÷ 3.5), hedef ve fark. `--check` bütçe aşımında çıkış kodu 1, `--lint-memory` A-07,
`--json`. Üç soruyu ölçerek cevaplar, varsayım yapmaz: (1) atlas alt ajan olarak
çağrıldığında `CLAUDE.md` yükleniyor mu — yüklenmiyorsa alt ajanın bilmesi gereken
kurallar `atlas.md`'de kalır ve defterde "beyanlı çift" olur; (2) Devin
`global_rules.md`/`graphify.md`'yi her zaman mı yüklüyor; (3) Devin servis `AGENTS.md`'yi
nexus'tan açılan oturumda yüklüyor mu. Kabul: çıktı §0 tablosunu yeniden üretir.

*Sonuç (2026-09-24):* betik `claude-atlas`, `devin-atlas`, `devin-service` giriş
noktalarını ölçer; `claude-atlas-subagent` ayrı giriş olmadı çünkü (1) aynı set: araçsız bir
atlas alt ajanı `CLAUDE.md`, `MEMORY.md`, `~/.claude/CLAUDE.md`, atlas gövdesi ve dev-atlas
§10'dan kelimesi kelimesine alıntı yaptı. (2) Devin `global_rules.md`'yi always-on,
`graphify.md`'yi `manual` yükler; atlas'ında skill önyüklemesi yok. (3) Servis `AGENTS.md`
yalnız oturum servis dizininde başlarsa always-on; nexus'tan başlayan oturumda yok. Test:
`automation/tests/test-context-budget.sh` (13/13).

**B-01 — transcript madenciliği.** `~/.claude/projects/-<host>/`
altındaki 110 transcript (586 MB) üzerinde bir kez çalışan betik; çıktı yalnız yol/sayı/boyut:
(a) `.md` Read çağrıları — dosya, kaç oturumda, toplam karakter, kısmi mi tam mı okundu;
(b) kanca başına enjekte edilen karakter (oturum medyanı, p95); (c) enjekte edilen notun
sonra açılıp açılmadığı, alıntılanıp alıntılanmadığı veya scope dosyasının düzenlenip
düzenlenmediği (fayda vekili); (d) `eos-query.sh`/`eos note show` çıktı boyutları. Devin
oturum kayıtları bulunamazsa rapor bunu söyler, tahmin etmez. Çıktı
`docs/eos-evals/context-reads-baseline.md`. W-02, W-03, W-04 kararlarını bu rapor verir.

**B-02 — kural defteri.** `docs/eos-evals/rules.tsv`: `id`, çapa metni (grep ile bulunacak
kısa ifade), sınıf (K/Z/P/O/R/T/Ç), şu anki evleri, hedef ev, varış prompt'u (P için).
Kaynak: §0'daki yedi yüklü dosya + Devin'in iki kural dosyası + 32 hafıza dosyası; her
"never/always/must/asla/her zaman" cümlesi ve kanca mesajları. Kabul: yüklü setteki her
zorunluluk cümlesi deftere girmiş (betik sayar, fark 0).

*Sonuç (2026-09-24):* `automation/context-budget.sh --rules check|skeleton`
(`automation/lib/rule_ledger.py`). Aday = kod bloğu dışındaki, zorunluluk kelimesi taşıyan
her cümle; `MEMORY.md`'de her madde. 296 aday → `docs/eos-evals/rules.tsv`'de 306 satır, 138
ayrı kural: K 116, Ç (`D`) 83, N 45, R 21, P 19, O 14, Z 5, T 3. Kontrol: kapsanmayan 0,
eskimiş çapa 0, iki evli yüklü kural 0, kanonik satırı olmayan kural 0. Çift kontrolü bugünkü
dosyaya değil **hedef eve** bakar (`home`); iki platformun `atlas.md`'si ve kullanıcının
global dosyası `declared` notuyla muaf. Defterin ortaya çıkardıkları:
- **Yalnız Devin'de yaşayan iki kural.** `build-after-change` ("her kod değişikliğinden
  sonra `mvn.sh build/test`") ve `review-twice` yalnız `global_rules.md`'de zorunluluk
  cümlesi; dev-atlas'ta yalnız §9 kontrol listesinde ve §5.1'de. `CLAUDE.md`'nin "nothing
  behaviour-changing may live in that file alone" kuralına aykırı; C-04 ikisini indekse alır.
- **Yeni prosedürler (7):** `start-an-fm-task`, `open-a-pr-on-an-fm-service`,
  `commit-and-push-an-fm-change`, `log-work-to-jira`, `verify-an-endpoint`,
  `run-e2e-prepaid-activation`, `refresh-local-credentials`. P satırları `arrival`
  sütununda prompt'larını taşır (B-03 `arrival.tsv`'nin tohumu).
- **Yanlış hafıza.** `MEMORY.md`'nin "atlas.md preloads skills like Devin's atlas" satırı
  B-00'a göre yanlış: Devin atlas'ı skill önyüklemiyor. C-06 düzeltir.
- **Eskimiş talimat.** dev-atlas §0.5 "EOS scores a note on its title and tags only, never
  the body" — retrieval çalışmasından beri gövde de puanlanıyor. R notuna taşınırken düzeltilir.

**B-03 — temel ölçümler.** Değişiklikten önce, aynı araçla:
- `docs/eos-evals/golden/arrival.tsv`: prompt → hook çıktısında bulunması gereken çapa.
  Defterdeki her P kuralı için en az iki prompt (biri Türkçe), her işaretçi için bir prompt
  ("1588 ne durumda", "levy konusu", "topup fiyatı"), 10 gürültü prompt'u.
- Yeni altın kümeler: `dev-atlas.tsv`, `project-intelligence.tsv`, `memory.tsv` (taşınacak
  her bölümün cevapladığı sorular). Temel skor kaydedilir (şu an bölümler dosyada olduğu
  için düşük çıkması beklenir).
- `docs/eos-evals/scenarios/rules-hold.md`: taze oturuma verilen 6–8 prompt, her biri bir
  kuralı çiğnemeye davet eder (yeni branch'e push, test koşma, FM repoda düzeltme, PR
  açıklaması, config script, worklog, test sonrası özet). Oturum komutları **çalıştırmadan**
  yazar; defterdeki çapaya göre puanlanır. Temel rapor
  `docs/eos-evals/rules-hold-baseline.md`.

*Sonuç (2026-09-24):* `docs/eos-evals/context-budget-baseline.md`. Üç sapma var:
- **Yeni altın kümeler notlarıyla birlikte yazılır.** Taşınacak bölümlerin kümeleri, cevap
  verecek notlar yazılırken ilgili C satırında yazılır. Bugün skorları tanım gereği sıfır olurdu.
  B-03 bunun yerine mevcut üç kümenin skorunu kaydetti (A-05'in referansı).
- **Senaryo TSV oldu.** Senaryo `docs/eos-evals/scenarios/rules-hold.tsv`'ye yazıldı: prompt,
  kural kimlikleri, "must" ve "must not" düzenli ifadeleri. Rapor ortak dosyada.
- **Oturumlar atlas alt ajanıyla koşuldu.** Her prompt ayrı bir atlas alt ajanına (sonnet), o
  prompt'un bugünkü hook bağlamı başa eklenerek verildi.

Varış hook yoluyla 26/53, intake yoluyla da 26/53. Kurallar 9/9 tutuldu; puanlayıcıdaki iki
yanlış pozitif elle doğrulanıp düzeltildi, raporda yazılı.

### E / W-01 — teslim önkoşulları (yüklü setten bir şey çıkmadan önce)

**E-01 — procedure `## Rules` (EOS upstream, 1.1.0).** Bugün brief prosedürün
prerequisites satırını kesiyor (`take-a-pr-to-merge`: "…a rejected push prints the regex
to f…"). Kesilen kural varmamış kuraldır. Değişiklik: `notes.procedure_rules`; brief bu
bölümü prosedür başlığının hemen altında **tam** basar ve bütçe kırpmasından muaf tutar;
muafiyetin sınırı yazma anında: 600 karakterden uzun `## Rules` reddedilir. ADR-023'e ek.
Testler: STEP_CHARS'tan uzun kural satırı sağ çıkar; `budget=40` kuralları yine basar;
601 karakter reddedilir. Upstream'de VERSION bump → push → `git subtree pull --prefix
tools/eos eos main --squash` → `install-eos-cli.sh` + `ensure-eos-mcp.sh --all`.

**E-02 — prosedür seçici ve tokenizer (EOS upstream, 1.1.0).** Ölçülen üç kusur §0'da.
Değişiklik: (a) `brief.best_procedure` yalnız başlık ve etiketlerde eşleşir — ilgili notlar
için H-02'de konan kuralın aynısı; gövde eşleşmesi aramada kalır, istenmemiş bağlama girmez;
(b) `notes._WORD` Unicode harf sınıfına geçer (`[^\W_]+` + `casefold`), böylece `aç`, `koş`,
`planı` kesilmez; (c) `_words` iki harfli **büyük harfli** kısaltmaları tutar (`PR`, `CI`,
`DB`, `PO`), küçük harfli iki harfliler atılmaya devam eder. Kabul: §0 tablosundaki altı
prompt beklenen sonucu verir; `nexus-procedures.tsv` 15/16'nın altına düşmez; mevcut
altın kümelerde recall@3 düşüşü ≤ 0.03. Aynı sürüm ve subtree akışında E-01 ile birlikte.

*Sonuç (E-01 + E-02, EOS 1.1.0, 2026-09-24):* upstream `d588029` + `d4bf63b`, nexus'a
subtree ile alındı (`0f7c74b`). Plana göre iki sapma var:
- **(d) Anahtar tek kelime:** `NAME-123` artık tek kelime sayılıyor (numarası da ayrıca
  sayılıyor). Tek başına kalan ön ek eşleşme kanıtı sayılmıyor. Ölçülen hata: `PROJ2-1700`
  prompt'u 1588'in devam notunu getiriyordu.
- **(e) İkinci kabul yolu:** Başlık/etiket kapsaması tek başına yetmedi. Başka dildeki dolgu
  kelimeleri ("bunu", "ekle") notlarda nadir olduğu için "upgrade"ten daha ağır çıktı ve
  "bunu env1 upgrade'ine ekle" 0.147'de kaldı. Bu yüzden ikinci bir kabul yolu eklendi:
  başlık/etiketin taşıdığı ağırlık, yedi notta birden az geçen bir kelimenin ağırlığına
  (log 7) ulaşırsa prosedür adlandırılmış sayılıyor; küçük mağazada eşik √N notta bir.
  Ayrıca ASCII dışı harf taşıyan iki harfli kelimeler de tutuluyor ("aç").

Ölçümler:
- Varış 26/53 → 28/53: gürültü 8/10 → 10/10, mevcut prosedürler 17/17 korundu.
- Altın kümeler: nexus r@1 0.82 → 0.82, nexus-procedures 0.69 → 0.75, svc-cpq-ordercapture
  0.95 → 0.95.
- Upstream pytest ve nexus EOS testleri yeşil.

Planlanan prosedürlerin prompt'ları artık yanlış prosedür değil, çoğunlukla ya hiçbir şey
basıyor ya da en yakın mevcut prosedürü basıyor (`PR aç` → `take-a-pr-to-merge`). Doğru
prosedür C-04'te yazılınca, onun etiketleri ("aç") seçimi yapacak.

**W-01 — tüm store'larda başlık/etiket araması.** `eos-prompt.py` bugün yalnız nexus'u ve
adı geçen servisleri brief'liyor; servis store'undaki bir görev notu ("PROJ2-1588",
"levy") prompt servisi adlandırmazsa gelmez. `MEMORY.md`'deki işaretçi satırlarının işi
buydu. Ekleme: prompt'taki ticket key'leri (`FM|PROJ1|PROJ2|PROJ3|OMT|AT-\d+`, **tam
key** — bugün `proj2` ön eki tek başına eşleşip 1363'ün notlarını 1625'e getiriyor) ve
yüksek IDF'li kelimeler, 506 notun yalnız başlık ve etiketlerinde aranır; en çok 3 satır
(başlık + `eos note show` komutu), mevcut 6,000 karakter tavanı ve oturum içi tekrar yok
kuralı içinde. Kabul: `arrival.tsv` işaretçi prompt'ları ≥ %90, gürültü prompt'ları 0
karakter, ek gecikme ≤ 300 ms.

*Sonuç (2026-09-24):* `automation/hooks/eos-prompt.py`'ye NOTES ELSEWHERE bloğu eklendi.
Blok, briefing verilmemiş store'lardan en çok 3 not getiriyor, en yeni not önce.
- **Anahtar eşleşmesi:** Prompt'taki tam anahtar (`PROJ2-1586`) ya da 4 ve daha fazla haneli
  numara (`1588`), notun başlığında veya etiketlerinde geçiyorsa not gelir.
- **Nadir kelime eşleşmesi:** Başlık/etiketteki bir kelime tüm not metinlerinde en fazla 3
  notta geçiyorsa not gelir.

Plandaki "yüksek IDF'li kelime" kuralı **ölçülüp bırakıldı**. Başlık frekansı, etiket frekansı
ve IDF eşiği, görev prompt'larına her seferinde 1–3 ilgisiz not getirdi: `PR aç` "Changeset
merge gate…", `release page` "svc-document-engine builds…" getirdi. Sebep şu: 290 başlıkta
"page", "work", "push" gibi kelimelerin her biri yalnız 3–4 notta geçiyor. Ancak tüm metinde
en fazla 3 not eşiği gürültüyü sıfırladı.

Tokenizer EOS 1.1.0 ile aynı; `test-eos-hooks.sh` ikisini aynı cevaplara bağlıyor (25/25).

Ölçüm:
- İşaretçiler 1/8 → **5/8** (1588 ×2, 1586, 1363, levy).
- Gürültü 10/10 korundu; prosedür prompt'larının medyan çıktısı değişmedi (1,508 karakter).
- En kötü gecikme 53 ms.

**%90 hedefi tutmadı; kalan 3 işaretçi:**
- "topup fiyatı" — dil farkı: not başlığı "price".
- "Waiting for Top-Up" — "waiting" 62 notta geçiyor.
- "jurisdiction … statüsüz" — "jurisdiction" 14 notta geçiyor.

Üçü de kelimeyle güvenle ayrılamıyor. C-06 bunlar için ya hedef notlara kullanıcının kendi
kelimelerini nadir etiket olarak ekler, ya da işaretçiyi `MEMORY.md`'de tek satır olarak
bırakır (beyanlı istisna). A-04'ün işaretçi hedefi buna göre C-06'da yeniden ölçülür.

### C — yüklü set

Her C satırı aynı dört adımla yapılır: (1) yeni evleri yaz (not/prosedür, eşleme
satırıyla), (2) A-03/A-04/A-05/A-06 testlerini koş, (3) ancak geçince yüklü dosyadan kes —
**tek değişiklikte**, parça parça değil (W-06: dosyayı yüklemiş her açık oturum her ara hâlin
farkını öder), (4) `context-budget.sh` ile ölç, sayıyı tabloya yaz.

**C-01 — `CLAUDE.md` 6,290 → ≤ 3,500.** Bölüm hedefleri (karakter): "FIRST: branch…"
2,967 aynen kalır; "Two knowledge tools" 5,368 → ~1,200 (iki komut + hangi soruya hangisi,
ayrıntı project-intelligence notlarına); "Local builds and tests" 5,338 + Confluence 605 +
"When a tool looks missing" 1,675 → tek **wrapper dizini** ~2,500 (ihtiyaç → komut, satır
başına bir wrapper; ayrıntı zaten dev-nexus §8 notlarında); "Product version upgrade" 2,333
→ ~300 (`upgrade-product-version` prosedürü prompt ile gelir); Cache 455 → tek satır kural
(env0/env1 cache temizliği için sormadan izin); Config SQL 479 → işaret satırı (id kuralı
dev-atlas §10'da, `config-ids-from-service`; defterde tek ev); "Java review points" 293 →
silinir (dev-atlas indeksinde). Wrapper
dizininin tek evi burası olur: `atlas.md` tablosu (5,648) ve dev-atlas "Environment
Databases" (3,667) buraya işaret eder. Şart sağlandı: B-00 atlas alt ajanının
`CLAUDE.md`'yi yüklediğini gösterdi.

*Sonuç (2026-09-24):* `CLAUDE.md` 22,015 → 9,304 karakter (6,290 → **2,658** token).

Ne kaldı:
- Push/branch bölümü aynen kaldı. Defterde evi `CLAUDE.md` olan iki kural eklendi: "Decide the
  name, do not ask" ve "Never delete a branch" (ikisi de dev-atlas'tan; oradaki kopyaları C-04
  siler).
- Dil, commit ve test verisi kuralları kaldı.
- EOS/Graphify tek komut bloğu oldu.
- Wrapper'lar 22 satırlık tek bir tabloda toplandı.

Taşınanlar: 8 bölüm `nexus CLAUDE.md: …` başlıklı notlara birebir taşındı. `--moved` ile
doğrulandı (0 sorun), eşleme `docs/eos-plans/context-budget-map.tsv`'de.

Kontroller:
- Defter: 21 satır notlarına işaret ediyor, 4 yeni satır eklendi; kontrol temiz (not
  evlerindeki çapalar da doğrulanıyor).
- `check-agent-docs.sh`: her wrapper bir giriş dosyasında adlandırılıyor, her alt komut
  belgelenmiş.
- Altın küme `claude-md.tsv` (14 soru): r@1 0.86, r@3 0.93. Kaçan iki soruda başka, ilgili bir
  not önde (localhost Mongo notu, dev-nexus 8.9a ntf notu).
- Varış ve diğer kümeler değişmedi.

Canlı gözlem: "start here" notunu `CLAUDE.md`'ye bağlamak (scope) iki soruna yol açtı. Dosyaya
yapılan ilk yazımda not bayat sayıldı ve bağlama enjekte edildi (~1.9k karakter). Scope
kaldırıldı. Kural şu: yüklü bir dosyaya bağlanan not, o dosyanın her düzenlemesinde bayatlar ve
her dokunuşta yeniden gelir. W-02'nin yüklü dosya koruması bu yüzden gerekli.

**C-02 — iki `atlas.md` 2,635 → ≤ 1,300.** Yalnız kompozisyon (hangi skill ne zaman),
teslim döngüsü, çıktı biçimi, INTAKE'in `eos brief` adımı. Wrapper tablosu → `CLAUDE.md`.
"Behavior" maddeleri `CLAUDE.md`'de varsa silinir (alt ajan da `CLAUDE.md`'yi yüklüyor —
B-00). `.claude/` ve `.devin/` kopyası aynı commit'te. Devin kopyası skill önyüklemediği
için "hangi skill ne zaman" satırları onda asıl yükü taşır; kısaltırken korunur.

**C-03 — `project-intelligence` 5,126 → ≤ 2,300.** "Choosing between EOS and Graphify"
(10,558 karakter, dosyanın %59'u) → `project-intelligence: EOS vs Graphify — <soru>`
başlıklı R notları; yerinde ≤ 1,200 karakterlik karar tablosu (soru → komut → ölçülmüş
maliyet). Protokol, kök neden kapısı, güvenlik sınırı, tamamlanma sözleşmesi kalır.

**C-04 — `dev-atlas` 17,465 → ≤ 4,500.** İndekste kalan (~13,800 karakter): §10 Iron
Rules, §5.1 review points, §1.0 intake kapısı ve §3 onay kapısı (kural), iş akışı iskeleti
(§0–§9 her faz tek satır + kapısı), bölüm haritası, ve bugün yalnız Devin'in
`global_rules.md`'sinde zorunlu olan `build-after-change` ile `review-twice` (B-02).
Branch/commit adı kuralları, "decide, do not ask" ve "never delete a branch" `CLAUDE.md`
en üstüne taşınır (push iş ortasında olur, prompt adlandırmaz; defterde evleri `CLAUDE.md`).
Taşınanlar:
- **P:** §1.1–1.4 ("start an FM task": Jira, stash, master sync, branch), §7.1–7.4
  ("open a PR on an FM service", pre-PR kapısı dahil — mevcut `take-a-pr-to-merge` ile
  sınırı: açmak ↔ merge'e götürmek), §7.5 ("log work to Jira": önce o günün iki sitedeki
  worklog'ları, -0400), §8 ("close a task in EOS"), §6 ("verify an endpoint with sanitized
  curl", mevcut workflow'a işaret), §9 checklist → "start an FM task" success bölümü.
  Görev içi kurallar E-01'in `## Rules` bölümüne. Devin için bu prosedürler hook'la değil
  atlas INTAKE adımıyla gelir (H-07'nin "daha zayıf mekanizma" notu); A-13 bunu ölçer ve
  INTAKE adımını atlayan bir Devin oturumu kuralı görmez — kabul edilen sınır, §7'de.
- **R:** §0.1, §0.2, Environment Databases, §0.4, §0.5, §0.6, §2, §3 şablonu, §5 dosya
  yapısı → `dev-atlas <§>: …` notları, scope'u belgelediği script/dosya.

*Sonuç (2026-09-24):* `dev-atlas/SKILL.md` 61,126 → 11,971 karakter (17,465 → **3,420** token).

İndekste kalanlar:
- iş sırası tablosu (§0–§9, her adım kapısıyla);
- §1.0 intake kapısı (birebir);
- "After every code change": `mvn.sh build`/`test` ve ikinci okuma. B-02'nin bulduğu, bugüne
  kadar yalnız Devin'in `global_rules.md`'sinde zorunlu olan iki kural;
- §5 hexagonal hard rules, §5.1 review points, PR başlığı ve fail-closed kuralları, "Do not
  open the PR with a known gate failure";
- §10 Iron Rules (birebir), §11.

Taşınanlar: 25 bölüm `dev-atlas <§>: …` notlarına birebir taşındı (`--moved` 36/36). Bash
yorumlarını başlık sanmayan, kod bloklarını atlayan ayrıştırıcıyla yapıldı.

Yeni 6 prosedür: `start-an-fm-task`, `open-a-pr-on-an-fm-service`,
`commit-and-push-an-fm-change`, `log-work-to-jira`, `verify-an-endpoint`,
`run-e2e-prepaid-activation`. Görev içi kurallar `## Rules`'da; etiketlerde kullanıcının
kelimeleri var. İki ayar yapıldı:
- open-a-pr'dan "merge-olmasın" etiketi çıkarıldı, take-a-pr'a "götür" eklendi;
- start'ın başlığına "branch aç" eklendi.

Plandaki "close a task in EOS" prosedürü yazılmadı. §8'in kuralları Stop hook'un (note-gate)
zorladığı Z sınıfı ve kullanıcı bunu adıyla istemiyor; §8 R notu olarak duruyor.

Ölçüm:
- **Varış:** hook yoluyla 32/53 → 48/53; prosedür 17/35 → 33/35. Kalan 2 prosedür C-06'nın
  `refresh-local-credentials`'ı. Devin'in intake yolunda prosedür 33/35.
- **Altın kümeler (A-05, recall@3):**
  - hiçbir kümede düşüş yok (nexus 0.97 → 1.0);
  - `dev-atlas.tsv` 14 soru, r@3 1.0;
  - recall@1 düştü (nexus 0.82 → 0.73, claude-md 0.86 → 0.79), çünkü yeni dev-atlas notları
    dev-nexus notlarıyla ilk sıra için yarışıyor. Kabul kriteri r@3 olduğu için kırmızı
    değil, ama izleniyor.
- **Defter:** 79 satır notlarına taşındı, 1 kopya silindi; kontrol temiz.
- **Claude koşulsuz toplamı: 15,997 token.** `MEMORY.md` (C-06) daha küçülmeden A-01 hedefi
  (≤ 16,000) tuttu.

Canlı gözlem (W-02): nexus store ~50 not büyüdü. Artık bir dosyaya ilk dokunuşta
note_inject'in digest katmanı store'daki bütün başlıkları listeliyor, ~2.5k karakter.
Store büyüdükçe bu da büyür; W-02 digest'e bir tavan koymalı.

**C-05 — Devin kural dosyaları.** `global_rules.md` 2,769 → ≤ 800: yalnız Devin'e özgü
mekanik (hook yokluğu → INTAKE'te `eos brief`, `/devin-delegate` onayı); geri kalanı
`CLAUDE.md`/skill'lerde zaten var (`CLAUDE.md` "nothing behaviour-changing may live in that
file alone" kuralı). `graphify.md` `manual` olduğu için yük değil (B-00); yalnız C-03'ün karar
tablosuyla çelişen satırı varsa düzeltilir. Windsurf'ün boş `global_rules.md`'si 0 token;
dokunulmaz (kullanıcının ev dizini).

**C-06 — hafıza 3,568 → ≤ 1,000.** B-02 sınıflarına göre: K satırları tek satır olarak
kalır (konu dosyası yalnız tek satıra sığmayan kural için); Ç satırları silinir; O → servis
veya nexus store'unda not (Devin de görür); P → prosedür notu (`refresh local credentials
from DevAssistant`, `run the e2e prepaid activation case`, v4.1 checklist →
`upgrade-product-version`'a adım); EOS işaretçileri W-01 varış testi geçince silinir.
Taşınan konu dosyası notu doğrulandıktan sonra silinir. `MEMORY.md` başına tek satır:
"Rules only. Facts → `eos note add`, procedures → `eos procedure new`." Harness'ın otomatik
hafıza yazımı bunu bozabilir; G-01'in SessionStart uyarısı bu yüzden var.

*Sonuç C-05 (2026-09-24):* `global_rules.md` 9,691 → 1,672 karakter (2,769 → **478** token).
Kalanlar Devin'e özgü mekanikler:
- skill'ler adla yüklenir (`/devin:<ad>`, `@atlas` yok); hiçbiri önyüklenmez;
- nexus/FM işinde sıra: `project-intelligence` → dev-atlas/dev-nexus;
- hook yok, bu yüzden görevin ilk komutu `eos brief --task`;
- skill listesi ve MCP listesi.

Eski dört bölüm (genel Java kontrol listesi, isimlendirme tablosu, paket düzeni, örnek akış)
birebir tek bir nota taşındı. Örnek akış artık olmayan skill'leri ve ham `mvn checkstyle`
komutunu öğretiyordu. Devin'in skill'li toplamı 37,483 → **13,013** (A-02).

*Sonuç C-06 (2026-09-24):* `MEMORY.md` 12,003 → 2,447 karakter (3,429 → **699** token), lint 0 bulgu.

Ne kaldı:
- 16 kural satırı ve bir işaretçi satırı.
- "Declared exceptions" başlığı altında W-01'in ulaşamadığı üç konu işaretçisi: topup fiyatı,
  Waiting for Top-Up, jurisdiction statüsüz. Lint bu başlığın altını saymaz.
- Kişisel kalanlar (repoya taşınmadı): şifre yenileme akışı, Sonar token konumu, EOM/Grafana
  erişimi, repodaki düz metin kimliklerin yerini söyleyen env0 dosyası. Kimliğe yakın hiçbir şey
  paylaşılan nexus notlarına girmedi.

Taşınanlar:
- 16 konu dosyası birebir `memory: …` notlarına taşındı, sonra silindi. Hafıza git dışında
  olduğu için eşlemede kaynak yerine gövdenin sha256'sı tutuluyor ve `--moved` onu doğruluyor.
- Eski `MEMORY.md`'nin tamamı da bir nota taşındı; satır içindeki olgular kaybolmadı.

Sapmalar:
- `refresh-local-credentials` prosedürü yazılmadı: kullanıcı kararı "kimlikler nexus'a
  yazılmaz". Varış, yüklü hafıza satırıyla sağlanıyor.
- `arrival.tsv`'ye yüklü dosyadan gelen satırlar için **loaded** türü eklendi (5 satır).

Varış: hook yoluyla **53/53** (A-04). Intake yoluyla 50/53: prosedür 33/33 (A-13); işaretçi
2/5, çünkü NOTES ELSEWHERE bloğu yalnız hook'ta.

**Claude koşulsuz toplamı: 38,114 → 13,274 token (−%65).** `context-budget.sh --check` artık 0 dönüyor.

### W — iş sırası

**W-02 — `note_inject.py`.** B-01: 43 oturumda medyan 1,501, p95 6,689 token; blokların
%67'si o oturumda sonradan düzenlenen dosyaya gitti; yüklü dosyaya giden blok 0 (manuel
denemedeki 8,286 karakterlik `CLAUDE.md` enjeksiyonu gerçek oturumda hiç olmadı). Hedef:
p95 ≤ 4,000, medyan ≤ 1,500 ve "sonradan düzenlendi" oranı ≥ %67 korunur. İlk aday en
pahalı yol `pom.xml` (11 kez, 9,455 token): sürüm notları body yerine digest. Yüklü dosya
koruması ucuz olduğu için eklenir, önceliği düşük. Doğrulama `--reads` tekrarıyla.

*Sonuç (2026-09-24, kod):* C satırlarından sonra ölçüldü. Nexus store ~70 not büyüdüğü için
her ilk dokunuş bütçenin tamamına (8,092 karakter) çıkıyordu. `CLAUDE.md`'ye dokunmak 87 başlık
getiriyordu, üstelik en eskisi önce. Üç değişiklik yapıldı:
1. **Talimat dosyası sessiz.** `CLAUDE.md`, `SKILL.md`, `AGENTS.md`, `MEMORY.md`, agent ve rules
   dosyasına dokunuş hiçbir şey enjekte etmez: 8,092 → **0**.
2. **Digest tavanı.** Dokunuş başına servis başına en çok 8 başlık gelir; sıra: aynı dizine
   bağlı notlar, sonra en yeniler. Kalanlar tek bir "… N more" satırıyla sayılır. Doküman
   dokunuşu 8,092 → **663**.
3. **Toplu tablo gövde almaz.** api-inventory ve repo-topology notları yalnız başlık olarak
   gelir. B-01'in en pahalı yolu `pom.xml` 7,153 → **1,091**.

Elle yazılmış, dosyaya bağlı notlar gövde almaya devam ediyor. Bir Java factory dokunuşunda iki
gövde geliyor, amaçlanan davranış bu. `automation/tests/test-note-inject.sh` 9/9. Oturum başına
p95 hedefi yeni oturumlar biriktikçe G-02'nin `--reads` tekrarında ölçülecek.

**W-03 — dev-atlas yan sayfaları.** B-01'e göre yalnız okunanlar: `db-config-scripts.md`
(22.7k token, 9 oturum, iki yoldan), `version-upgrade.md` (16.8k, 6 oturum),
`eom-grafana-access.md` (8.7k, 3 oturum). Bunlar bölüm başına R/P notlarına, dosya indeks
olur. `dev-rules.md`, `hexagonal-template.md`, `test-template.md`, `castlemock.md` ilk 20'de
yok — taşınmaz (kazanç yok); yalnız `dev-rules.md`'nin kuralları B-02 defterinden geçer
(dev-atlas ile çift beklenir). Önce script'lerin bu yollara yaptığı atıflar
`automation/search.sh` ile bulunur ve korunur.

**W-04 — dokümanlar (kullanıcı kararı).** B-01: `journeys/10-top-up.md` 18.5k token, 4
oturumda tam okuma — aday; `open-items.md` ilk 20'de yok, öncelik değil. Kullanıcı seçmeden
taşınmaz.

*Sonuç (2026-09-24):* kullanıcı hepsini EOS'a taşımayı seçti. Gerekçesi: bu tür bilgi zaten
araştırma sırasında yeniden eklenir. Taşınanlar: dört journey, `open-items.md` ve scenarios
README; toplam 44 not. Dosyalar indekse döndü ve bütün başlıkları ile not işaretlerini koruyor.
Taşımalar birebir, eşlemesi map'te; `--moved` 121/121.
- `golden/journeys.tsv` r@1 1.0; `verify-journey-docs.py` 0 hata.
- Konu kelimesi eşiği sabit sayıdan store payına (%1, en az 3) geçti. Sebep: taşınan
  dokümanlar "levy"yi dört nota yaydı ve işaretçi kırıldı (`fee0ac8`).

**W-05 — servis `AGENTS.md` (kullanıcı onayı + FM PR).** B-00: yalnız servis dizininde
başlayan Devin oturumu yükler (medyan 1,028, en büyük 8,486 token). Öncelik en büyük
dörde (config-manager, crm-batch, crm-activity, crm-customerinformation; toplam ~28.7k);
servis başına dev-atlas ile çift olan bölümler (Commit & Branch Rules, Critical
Rules) ve olgular için öneri — kısa işaret + servis store'unda notlar. FM repolarına
kullanıcı istemeden commit/PR yok. `check-agent-docs.sh` bugün 18 `AGENTS.md`'nin hepsinde
4–6 ham Maven satırı uyarısı veriyor: bu dosyalar Devin'e `mvn` çalıştırmasını söylüyor, yani
`CLAUDE.md`'nin wrapper kuralıyla çelişiyor. Öneriye dahil.

*Sonuç (2026-09-24):* kullanıcı kararıyla başka bir yol izlendi: Devin'e ve servis
repolarına dokunulmadı, çünkü bu değişiklik servis repolarında commit/push gerektiriyordu.
Onun yerine servis `AGENTS.md`'si Claude'a EOS üzerinden ulaşıyor:
- `automation/agents-md-to-eos.sh`: her `##` bölümü servisin store'unda bir not olur
  (`<servis> AGENTS.md: <başlık>`, `source: agents-md`, gövde birebir). Değişen bölüm
  amend edilir, silinen bölümün notu silinir, değişiklik yoksa hiçbir şey yazılmaz.
  Servis reposuna yazmaz.
- `ensure-eos-mcp.sh` her taramadan sonra betiği çalıştırır; hata olursa yalnız uyarı basar.
- `eos-prompt.py` SERVICE GUIDE bloğu: prompt bir servisi adlandırınca o servisin bölüm
  başlıklarını ve önce açılacak notu (Critical Rules) listeler, ham Maven satırlarını
  `automation/mvn.sh`'ye yönlendirir. Oturumda bir kez, mevcut 6,000 karakter tavanı içinde.
- `note_inject.py` bu notları digest'te en sona alır. Tek seferde içe aktarıldıkları için
  aynı en yeni tarihi taşıyorlar ve aksi hâlde servisin her digest'ini dolduruyorlardı.
  Düzeltme olmadan test kırılıyor.

Sonuç: 16 servis, 163 not. svc-common ve fm-customer-credit-management'ın EOS store'u yok,
atlandı. İkinci koşu 0 yazım. Servis repolarında yeni değişiklik yok.
Ölçümler:
- Varış 54/54.
- svc-cpq-ordercapture altın kümesi 0.95 → 0.95.
- Testler: `test-agents-md-import.sh` 9/9, `test-eos-hooks.sh` 30/30, `test-note-inject.sh` 10/10.

**W-06 — değişen-dosya bildirimleri.** B-01'in en büyük kalemi: oturum başına medyan 11,454
token. Harness, oturumun bağlamındaki bir dosya dışarıdan değişince farkını gönderiyor;
yüklü dosyalar bu listenin başında (`CLAUDE.md` 27.4k, `MEMORY.md` 26.4k, dev-atlas
`SKILL.md` 17.2k). Üç adım: (1) `--reads` bildirimi kaynağına ayırır — oturumun kendi
shell komutu mu (aynı yolu anan Bash komutu, `git subtree pull`/`checkout`/`stash pop`
sonrası), başka oturum mu; (2) yüklü dosyalar C satırlarında küçülür, olgular çıkar
(`MEMORY.md`'ye otomatik hafıza yazımı azalır) — her yazım, dosyayı yüklemiş her açık
oturuma yeniden ödetiliyor; (3) C satırları her dosyayı **tek değişiklikte** yazar, parça parça
düzenlemez — paralel oturumlar her ara hâlin farkını öder. Kabul: C satırlarından ve G-01'den
sonra `--reads` tekrarında oturum başına medyan ≤ 5,700 (yarısı) ve ilk üç yolun toplamı
temelin (71.1k) yarısının altında. Sayılar ancak yeni oturumlar biriktikçe anlamlı olur;
tekrar G-02 ile birlikte yapılır.

### G — kapı ve kapanış

**G-01 — bütçe kapısı.** `.githooks-shared/pre-commit` (nexus): yüklü setten bir dosya
stage'deyse `context-budget.sh --check`; aşımda dosya tablosuyla ret. `eos-brief.py`
(SessionStart): `MEMORY.md` bütçeyi aşarsa tek satır ("MEMORY.md 1,340/1,000 token —
olguları `eos note add` ile taşı"). `check-agent-docs.sh`'e A-03 ve bütçe eklenir. H-05
öncesi dev-nexus 285k'ya bu kapı olmadığı için büyüdü.

**G-02 — taze oturum.** İşi yapmamış bir oturumla `rules-hold` ve `where-did-we-leave-off`
tekrar; raporlar temel ile yan yana `docs/eos-evals/`'a.

**G-03 — kapanış.** `docs/eos-evals/context-budget-report.md`: giriş noktası başına önce/sonra,
iş sırası medyanları, A-01…A-11 sonuçları, kalan sınırlar. Tablo son sayılarla kapanır.

## 6. Ölçüm protokolü

- Token = karakter ÷ 3.5 (H-05 temel ölçümüyle aynı); G-03'te cl100k ile bir kez çapraz
  kontrol, araç varsa.
- Her satırdan önce ve sonra `context-budget.sh`; sayı tabloya tarihle yazılır.
- Erişim yalnız `eos note eval` ile, varış yalnız `context-budget.sh --arrival` ile, kuralın
  tutulması yalnız `context-budget.sh --rules-hold` ile yargılanır;
  "bence bulur" kabul değildir.
- Transcript betiği içerik saklamaz; yalnız yol, sayı, boyut.

## 6.1 Planın en büyük riski

B-01 (111 transcript): oturumların **%41'i (45)** açılış brief'i dışında EOS'a bir şey sordu;
yarısından fazlası hiç sormadı. (`eos cost .`'un %18'i yalnız oturum kimliği taşıyan çağrıları
sayıyor.) "Olgu aranır" ilkesi ancak oturumlar gerçekten ararsa çalışır; R sınıfına taşınan
bir bölüm aranmazsa okunmadan kaybolmuş demektir. Bu yüzden: (1) H-05'te taşınan 31
dev-nexus notunun kaç oturumda açıldığı ilk ölçülebilir veri — notlar 2026-09-24'te taşındı,
o günden beri oturum birikmedi; G-02'de `--reads` ile sayılır; (2) A-12 taşıma sonrası %41'i
izler; (3) oran düşerse çare "yüklü sete geri koymak"
değil, `note_inject.py`'nin digest katmanıyla (başlık satırı, ~250 token) ilgili notu işin
anında **adıyla** göstermektir — aranmayan not, adı bilinmeyen nottur.

## 7. Bilerek kapsam dışı

- Devin'de P sınıfının varışı INTAKE adımına bağlıdır; hook eşdeğeri yoktur (A-13 ölçer,
  çözmez).
- Harness'ın kendi yükü: sistem prompt'u, araç şemaları, ertelenmiş araç listesi, MCP
  talimatları, oturum başında görünen plugin bağlayıcıları (data/engineering/
  product-management — repo ayarlarında yok, kaynağı ölçülmedi).
- Sıkıştırma (compaction) özetleri.
- Kullanıcının `~/.claude/CLAUDE.md`'si.
- Retrieval sıralaması değişikliği (A-05 yalnız gerilemeyi yakalar).
- Taşınan metnin yeniden yazılması: bölümler birebir taşınır; yalnız indeks satırları yeni
  yazılır. Nesri kısaltmak ayrı, gözden geçirilen bir iştir.

## 8. Devam protokolü

1. Bu dosyanın §3 tablosu tek durum kaynağıdır; `context-budget.sh` güncel sayıdır.
2. Satır alınır: `eos work add . --title "context-budget <satır>" --claim`.
3. Satır biter: tabloya "DONE <tarih>: <ölçülen sayı>" + nexus commit (dosya adıyla
   stage); push yalnız istenince.
4. E-01 upstream'de yapılır, nexus'a subtree ile gelir; nexus'ta `tools/eos/` düzenlenmez.
5. Bir kabul kontrolü kırmızıysa satır DONE olmaz; kırmızı neden tabloya yazılır.
