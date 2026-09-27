# Tasarım: "bitti" ama bitmemiş — doğrulanmış bitiş (EOS, 2026-09-27)

Brainstorming (superpowers `brainstorming`, mimari yol) çıktısı. Onaylanan bölümler: bileşenler,
veri akışı ve gürültü kontrolü, eşleme ve test planı.

## Amaç ve başarı ölçütü

**Kullanıcının seçtiği:** EOS'un bir sonraki döneminin ölçütü ajanın iş kalitesi; en çok yoran
hata "bitti" denip bitmemiş iş (doğrulanmamış test, push, PR iddiası).

**Bugünkü durum (ölçüldü 2026-09-27):** 62 run'ın 56'sı `ok`, 3'ü `failed` — sonucu ajan beyan
ediyor, hiçbir şey doğrulamıyor. Prosedürlerin `Success` bölümü metin; alt ajan atıfları sayılıyor,
varlıkları denetlenmiyor. Hook'lar her düzenlemeyi (`changed`) ve komut çıkış kodunu zaten kaydediyor.

**Başarı ölçütü:**
1. Doğrulanmış bitiş oranı: `verified` / (`verified` + `claimed`) run sonuçları.
2. Kapıdan sonra davranış: kapı başına doğrulama koşuldu mu, yoksa "doğrulanmadı" denerek mi bitti.
3. Yanlış engelleme: sıfıra yakın (eşlenmemiş dosyada kapı yok; aynı küme için ikinci kapı yok).

**Kısıtlar:** motor deterministik ve dilden bağımsız (dil mantığı yok, LLM yok); Claude'a özgü
kısım plugin'de; teslimat hook'la; yanlış engelleme en kötü sonuç (not kapısıyla aynı ilke);
iddia ölçmeden yapılmaz.

**Seçilen yaklaşım:** A + B. A: tur sonunda "son yeşilden beri kirli" kapısı. B: run sonucunda
`verified`/`claimed` ayrımı. Reddedilen: C (son mesajdaki iddiaları dil kalıplarıyla yakalamak —
dile bağlı, yanlış alarm riski); A ve B boşluk bırakırsa yeniden ele alınır.

## 1. Bileşenler

| Birim | Yer | Görev | Bağımlılık |
|---|---|---|---|
| `verify.toml` | host bilgi dizini (nexus: `.devin/knowledge/nexus/verify.toml`), `capabilities.toml`'un yanında | kapsamlar: dosyalar (glob), doğrulama sayılan komut (regex), önerilecek komut | yok (veri) |
| `core/verify.py` | EOS çekirdeği | `load(root)`; `scope_of(path)` → `(scope, örnek)`; `passes(command, scope, örnek)`; `dirty(events)` → son geçen doğrulamasından sonra değişen kapsam örnekleri | stdlib |
| hook katmanı | EOS `core/hooks.py` (plugin üzerinden) | `post-tool`: `changed` (mevcut) + başarılı komut `passed`; açık run'a `verified` olayı; `stop`: kapı | `core/verify.py` |
| run sonucu | EOS `core/executions.py` + CLI | `finish`: run olaylarından `verified`/`claimed`; `run list --stats` oranı | `core/verify.py` |

`verify.toml` biçimi:

```toml
[[scope]]
name = "fm-service"
paths = ["../microservices/{service}/src/**"]
passes = ['automation/mvn\.sh\s+(test|verify)\s+{service}\b']
run = "automation/mvn.sh test {service}"
```

## 2. Veri akışı

1. **Düzenleme.** Edit/Write ya da mtime'dan anlaşılan kabuk yeniden yazımı → oturum durumu
   `changed=<yol>`. Alt ajanın düzenlemesi de sayılır (aynı oturum).
2. **Doğrulama.** Çıkış kodu 0 bir komut bir kapsamın `passes` kalıbına uyarsa → durum
   `passed=<kapsam:örnek>`; açık run varsa ledger'a `kind="verified", tool=<kapsam>, ref=<örnek>`.
   Başarısız komut hiçbir şeyi temizlemez (PostToolUseFailure).
3. **Tur sonu (Stop, ana oturum).** Durum katlanır; son değişikliği son `passed`'ından sonra gelen
   kapsam örnekleri kirli. Kirli varsa ve bu küme için kapı verilmediyse bir kez engeller
   (`{"decision": "block", "reason": …}`), gerekçe en fazla 3 kapsam ve `run` komutu, ~600 karakter:
   "X değişti, son geçen doğrulama bundan önce: doğrula ya da cevabında açıkça doğrulanmadı de."
   Durum `gated=<imza>`; imza = kirli örnekler + her birinin son değişiklik sırası.
4. **İkinci Stop.** Aynı imza → geçer. Ajan ya doğrular (küme boşalır) ya da doğrulanmadığını söyler.
5. **Run bitişi.** `eos run finish` ledger'ı okur (hook durumunu değil): run'daki `changed`
   olayları → kapsam örnekleri; her biri son değişiklikten sonra bir `verified` olayıyla
   karşılanıyorsa `outcome_source = "verified"`, değilse `"claimed"` ve tek satır:
   `claimed: fm-service:svc-crm-asset changed after its last passing check`. Finish hiçbir zaman
   engellenmez; `failed`/`abandoned` için etiket yok.

## 3. Gürültü kontrolü ve hata durumları

- Yalnız ana oturumun Stop'u; `stop_hook_active` iken asla engellemez.
- Aynı imza için tek kapı; dosya yeniden değişirse yeni imza.
- `verify.toml` yok, dosya eşlenmemiş (belge, not, config, CLAUDE.md) → sessiz.
- Her istisna → engelleme yok ("emin değilsen engelleme").
- `[hooks] verify` (varsayılan açık; `verify.toml` yoksa zaten etkisiz).
- nexus not kapısıyla bağımsız; ikisi aynı Stop'ta engelleyebilir.

## 4. Eşleme ayrıntıları

- Yollar proje köküne göre çözülür; `..` ve mutlak yol serbest.
- Glob: `**` her derinlik, `*` tek segment (eğik çizgisiz), `{ad}` tek segmenti yakalar;
  regex'e çevrilir (fnmatch `**` bilmez).
- Yakalanan değer `passes` ve `run` içine yerleşir; `passes`'e `re.escape` ile.
- `passes` tam komut metninde `search` ile aranır; yalnız çıkış kodu 0 sayılır.
- Bir dosya birden çok kapsama uyarsa dosyadaki ilk kapsam.

**nexus ilk `verify.toml`:**

| Kapsam | Dosyalar | Doğrulama sayılan | `run` |
|---|---|---|---|
| fm-service | `../microservices/{service}/src/**` | `automation/mvn\.sh\s+(test\|verify)\s+{service}\b` | `automation/mvn.sh test {service}` |
| automation | `automation/**/*.sh`, `automation/**/*.py` | `automation/tests/test-[\w-]+\.sh` | ilgili `automation/tests/test-*.sh` |
| eos-upstream | `<eos-repo>/core/**`, `…/plugin/**`, `…/tests/**` | `\bpytest\b` | `python3 -m pytest` (EOS reposunda) |

`automation` kapsamı kaba: herhangi bir test betiği temizler. Bilinçli: betik ↔ test eşlemesi
yok; ölçümde yanlış temizleme sık çıkarsa kapsam bölünür.

## 5. Ölçüm

- `sessions.jsonl`: `verify_gates` (kapı sayısı), `verify_after_gate` (kapıdan sonra `passed`
  gelen kapı sayısı).
- `eos run list --stats`: `verified` / `claimed` sayıları ve oranı.
- İlk okuma 2026-10-03 (bağlam diyeti ölçümüyle aynı gün).

## 6. Test planı

- `tests/test_verify.py` (EOS): glob çevirisi ve yakalama; `dirty` sırası (değişiklik → geçen →
  değişiklik = kirli); başka örneğin doğrulaması temizlemez; başarısız komut temizlemez; eşlenmemiş
  yol yok sayılır.
- `tests/test_hooks.py`: Stop bir kez engeller ve gerekçeyi verir; aynı imzada ikinci Stop geçer;
  `stop_hook_active` geçer; `verify.toml` yokken geçer; alt ajan düzenlemesi sayılır; başarılı
  doğrulama açık run'a `verified` yazar; istisnada engelleme yok.
- `tests/test_executions.py`: `verified`/`claimed` etiketi; `run list --stats` oranı.
- nexus `automation/tests/test-verify-scopes.sh`: kalıplar derleniyor, örnek yollar doğru kapsama,
  örnek komutlar doğru örneği temizliyor.
- Canlı: headless oturumda bir automation betiği düzenlenip test koşulmadan bitirilir → kapı bir kez
  engeller; ajan testi koşar ya da "doğrulanmadı" der.

## Kapsam dışı

Son mesajdaki iddiaları dil kalıplarıyla yakalamak (yaklaşım C); prosedür `Success` metnini
çalıştırılabilir kontrole çevirmek (ayrı iş); git durumu taraması (kullanıcının kendi
değişikliklerini de görür).
