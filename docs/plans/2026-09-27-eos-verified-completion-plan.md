# Verified Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** EOS bir oturumda değişen ama son başarılı doğrulamasından sonra değişmiş işi tur sonunda bir kez yakalar ve run sonucunu `verified` / `claimed` olarak etiketler.

**Architecture:** Kapsamlar host'ta veri olarak (`verify.toml`, bilgi dizininde). Saf mantık EOS çekirdeğinde `core/verify.py`: yol → kapsam örneği, komut → hangi örnekleri temizler, olay dizisi → kirli örnekler. Hook katmanı (`core/hooks.py`) oturum durumuna `changed`/`passed` yazar ve Stop'ta kapıyı uygular; `core/executions.py` run bitişinde ledger olaylarından etiketi hesaplar.

**Tech Stack:** Python stdlib (EOS kuralı), pytest (EOS), bash (nexus testleri), Claude Code 2.1.283 hook sözleşmesi (Stop `{"decision":"block","reason":…}`).

**Spec:** `docs/eos-plans/2026-09-27-eos-verified-completion-design.md`

## Global Constraints

- Motor deterministik, stdlib, dilden bağımsız; karar yolunda LLM/embedding yok.
- Hata ya da belirsizlikte engelleme yok ("emin değilsen engelleme").
- Aynı kirli küme için tek kapı; `stop_hook_active` iken asla; yalnız ana oturumun Stop'u.
- Engelleme gerekçesi en fazla 3 kapsam, ~600 karakter.
- `eos run finish` hiçbir zaman engellenmez; etiket yalnız `outcome == "ok"` için.
- EOS değişikliği upstream'de (`<eos-repo>`), push, sonra nexus'ta `git subtree pull --prefix tools/eos eos main --squash`; `tools/eos` altında düzenleme yok.
- Commit'e dosyalar tek tek adıyla eklenir (`git add -A` yok). AI attribution yok. nexus push yok.

**Spec'e sıkılaştırma (Review Focus 2-3'ten):** `passes` tam komut metninde `search` değil; komut `&&`, `||`, `;` ve satır sonuyla parçalara ayrılır, kalıp her parçanın başına (`bash`/`sh`/ortam atamaları hariç) sabitlenir; parça pipe içeriyorsa ve komutta `pipefail` yoksa sayılmaz.

## Review Focus

1. Aynı dosya farklı yazılır (göreli, mutlak, `..` içeren desen, sembolik bağ) → aynı kapsam örneği çıkmalı. (Task 1 testi)
2. Doğrulama komutunu yalnız anan komut (`grep 'automation/mvn.sh test x' f`, `echo …`) çıkış 0 ile biter → temizlememeli. (Task 1 testi)
3. Pipe'lı doğrulama (`automation/mvn.sh test x | tail -5`) test kalsa da 0 döner → `pipefail` yoksa temizlememeli. (Task 1 testi)
4. `verify.toml` bozuk → kapı sessiz, istisna yok, `eos run finish` çalışır. (Task 1 ve Task 4 testleri)
5. Çok sayıda kirli kapsam → gerekçe 3 kapsam + `(+N more)`, ≤ 600 karakter. (Task 3 testi)

---

### Task 1: `core/verify.py` — kapsamlar ve kirli hesap

**Files:**
- Create: `<eos-repo>/core/verify.py`
- Test: `<eos-repo>/tests/test_verify.py`

**Interfaces:**
- Produces:
  - `FILENAME = "verify.toml"`, `MAX_SHOWN = 3`, `MAX_REASON = 600`
  - `@dataclass(frozen=True) class Scope: name: str; paths: tuple[str, ...]; passes: tuple[str, ...]; run: str`
  - `path_for(project_root) -> Path`
  - `load(project_root) -> list[Scope]` (yok/bozuk → `[]`)
  - `instance(scopes, root, path: str) -> str | None` (ör. `"fm-service:svc-crm-asset"`)
  - `cleared(scopes, command: str, keys: list[str]) -> list[str]`
  - `dirty(scopes, root, events: list[tuple[str, str]]) -> list[tuple[str, int]]` (olay: `("changed", yol)` ya da `("passed", anahtar)`)
  - `signature(found) -> str`, `run_hint(scopes, key) -> str`, `reason(scopes, found) -> str`

- [ ] **Step 1: Write the failing test**

```python
"""core.verify: which changes are verified (ADR-028)."""
import os

from core import verify

TOML = r'''
[[scope]]
name = "svc"
paths = ["../services/{service}/src/**"]
passes = ['tools/mvn\.sh\s+(test|verify)\s+{service}\b']
run = "tools/mvn.sh test {service}"

[[scope]]
name = "scripts"
paths = ["scripts/**/*.sh"]
passes = ['scripts/tests/test-[\w-]+\.sh']
run = "the matching scripts/tests/test-*.sh"
'''


def _project(tmp_path, text=TOML):
    root = tmp_path / "ws" / "hub"
    (root / ".eos" / "knowledge").mkdir(parents=True)
    (root / ".eos" / "config.toml").write_text('[knowledge]\ndir = ".eos/knowledge"\n', encoding="utf-8")
    (root / ".eos" / "knowledge" / verify.FILENAME).write_text(text, encoding="utf-8")
    return root


def test_the_same_file_spelled_three_ways_is_one_instance(tmp_path):
    root = _project(tmp_path)
    scopes = verify.load(root)
    source = tmp_path / "ws" / "services" / "billing" / "src" / "main" / "A.java"
    source.parent.mkdir(parents=True)
    source.write_text("class A {}", encoding="utf-8")
    link = tmp_path / "link"
    os.symlink(tmp_path / "ws", link)
    spellings = ["../services/billing/src/main/A.java", str(source), str(link / "services/billing/src/main/A.java")]
    assert {verify.instance(scopes, root, s) for s in spellings} == {"svc:billing"}
    assert verify.instance(scopes, root, "docs/readme.md") is None
    assert verify.instance(scopes, root, "scripts/a/b.sh") == "scripts"


def test_only_a_command_that_runs_the_check_clears_its_instance(tmp_path):
    scopes = verify.load(_project(tmp_path))
    keys = ["svc:billing", "scripts"]
    assert verify.cleared(scopes, "cd hub && tools/mvn.sh test billing", keys) == ["svc:billing"]
    assert verify.cleared(scopes, "tools/mvn.sh test orders", keys) == []
    assert verify.cleared(scopes, "grep 'tools/mvn.sh test billing' notes.md", keys) == []
    assert verify.cleared(scopes, "echo tools/mvn.sh test billing", keys) == []
    assert verify.cleared(scopes, "tools/mvn.sh test billing | tail -5", keys) == []
    assert verify.cleared(scopes, "set -o pipefail; tools/mvn.sh test billing | tail -5", keys) == ["svc:billing"]
    assert verify.cleared(scopes, "bash scripts/tests/test-x.sh", keys) == ["scripts"]


def test_dirty_follows_the_order_of_changes_and_passes(tmp_path):
    root = _project(tmp_path)
    scopes = verify.load(root)
    a = "../services/billing/src/A.java"
    events = [("changed", a), ("passed", "svc:billing"), ("changed", "docs/x.md")]
    assert verify.dirty(scopes, root, events) == []
    events.append(("changed", a))
    assert verify.dirty(scopes, root, events) == [("svc:billing", 3)]
    assert verify.dirty(scopes, root, events + [("passed", "svc:orders")]) == [("svc:billing", 3)]


def test_a_missing_or_broken_file_means_no_scopes(tmp_path):
    assert verify.load(_project(tmp_path, "[[scope]\nname = 'x'")) == []
    root = _project(tmp_path / "other")
    (root / ".eos" / "knowledge" / verify.FILENAME).unlink()
    assert verify.load(root) == []
    assert verify.load(_project(tmp_path / "bad", "[[scope]]\nname='x'\npaths=['a/**']\npasses=['(']\nrun='r'\n")) == []


def test_the_reason_names_three_instances_and_stays_short(tmp_path):
    scopes = verify.load(_project(tmp_path))
    found = [(f"svc:s{i}", i) for i in range(6)]
    text = verify.reason(scopes, found)
    assert "svc:s0" in text and "tools/mvn.sh test s0" in text and "svc:s3" not in text
    assert "(+3 more)" in text and len(text) <= verify.MAX_REASON
    assert verify.signature(found) == verify.signature(list(reversed(found)))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd <eos-repo> && python3 -m pytest tests/test_verify.py -p no:cacheprovider`
Expected: FAIL with `ImportError: cannot import name 'verify'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Which work is verified: scopes changed after their last passing check (ADR-028).

A project declares, in `verify.toml` beside its notes, which files belong to
which scope and which command counts as that scope's check. A change to a
file in a scope is verified once a command matching the scope's `passes`
exits 0 after it. Everything here is pure: paths and commands in, instances
out. A missing or malformed file declares nothing, and nothing is gated.
"""
from __future__ import annotations

import dataclasses
import os
import re
from pathlib import Path

from core import notes

FILENAME = "verify.toml"
MAX_SHOWN = 3
MAX_REASON = 600
_PLACEHOLDER = re.compile(r"\{([A-Za-z_]\w*)\}")
_SEGMENTS = re.compile(r"&&|\|\||;|\n")
_LEAD = r"\s*(?:\w+=\S*\s+)*(?:(?:bash|sh|env)\s+)?"


@dataclasses.dataclass(frozen=True)
class Scope:
    name: str
    paths: tuple[str, ...]
    passes: tuple[str, ...]
    run: str


def path_for(project_root: str | Path) -> Path:
    return notes.notes_dir(project_root) / FILENAME


def load(project_root: str | Path) -> list[Scope]:
    """The declared scopes; [] when the file is absent or malformed."""
    path = path_for(project_root)
    if not path.is_file():
        return []
    try:
        from core.lib.config_io import ConfigIO

        data = ConfigIO.read_toml(path)
        scopes = []
        for entry in data.get("scope") or []:
            paths = tuple(p for p in entry.get("paths") or [] if isinstance(p, str))
            passes = tuple(p for p in entry.get("passes") or [] if isinstance(p, str))
            name, run = str(entry.get("name") or ""), str(entry.get("run") or "")
            if not (name and paths and passes):
                continue
            for pattern in passes:
                re.compile(_fill(pattern, {key: "x" for key in _PLACEHOLDER.findall(pattern)}))
            scopes.append(Scope(name, paths, passes, run))
        return scopes
    except Exception:  # noqa: BLE001 - a broken file declares nothing
        return []


def _fill(pattern: str, values: dict, escape: bool = True) -> str:
    return _PLACEHOLDER.sub(lambda m: re.escape(values.get(m.group(1), "")) if escape
                            else values.get(m.group(1), m.group(0)), pattern)


def _real(root: Path, path: str) -> str:
    full = path if os.path.isabs(path) else os.path.join(str(root), path)
    return os.path.realpath(full).replace(os.sep, "/")


def _glob(root: Path, pattern: str) -> re.Pattern:
    full = pattern if os.path.isabs(pattern) else os.path.join(str(root), pattern)
    full = os.path.normpath(full).replace(os.sep, "/")
    literal = re.split(r"[*{]", full, maxsplit=1)[0]
    head = literal.rsplit("/", 1)[0] if "/" in literal else literal
    full = os.path.realpath(head).replace(os.sep, "/") + full[len(head):]
    out, index = [], 0
    while index < len(full):
        if full.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif full.startswith("**", index):
            out.append(".*")
            index += 2
        elif full[index] == "*":
            out.append("[^/]*")
            index += 1
        elif (found := _PLACEHOLDER.match(full, index)) is not None:
            out.append(f"(?P<{found.group(1)}>[^/]+)")
            index = found.end()
        else:
            out.append(re.escape(full[index]))
            index += 1
    return re.compile("".join(out))


def _names(scope: Scope) -> list[str]:
    return sorted({name for pattern in scope.paths for name in _PLACEHOLDER.findall(pattern)})


def instance(scopes: list[Scope], root: str | Path, path: str) -> str | None:
    """`<scope>` or `<scope>:<value>…` for the first scope whose paths match."""
    if not path:
        return None
    full = _real(Path(root), path)
    for scope in scopes:
        for pattern in scope.paths:
            found = _glob(Path(root), pattern).fullmatch(full)
            if found is not None:
                values = found.groupdict()
                return scope.name + "".join(f":{values[name]}" for name in sorted(values))
    return None


def _values(scope: Scope, key: str) -> dict:
    return dict(zip(_names(scope), key.split(":")[1:]))


def _runs(command: str, regex: str) -> bool:
    for segment in _SEGMENTS.split(command):
        head, piped = segment.split("|", 1)[0], "|" in segment
        if re.match(_LEAD + regex, head) and (not piped or "pipefail" in command):
            return True
    return False


def cleared(scopes: list[Scope], command: str, keys: list[str]) -> list[str]:
    """The instances among `keys` that this command (which exited 0) checks."""
    by_name = {scope.name: scope for scope in scopes}
    done = []
    for key in keys:
        scope = by_name.get(key.split(":", 1)[0])
        if scope is not None and any(_runs(command, _fill(p, _values(scope, key))) for p in scope.passes):
            done.append(key)
    return done


def dirty(scopes: list[Scope], root: str | Path, events: list[tuple[str, str]]) -> list[tuple[str, int]]:
    """Instances whose last change comes after their last pass, with that change's index."""
    last_change: dict[str, int] = {}
    last_pass: dict[str, int] = {}
    for index, (kind, value) in enumerate(events):
        if kind == "changed":
            key = instance(scopes, root, value)
            if key is not None:
                last_change[key] = index
        elif kind == "passed":
            last_pass[value] = index
    return [(key, at) for key, at in last_change.items() if last_pass.get(key, -1) < at]


def signature(found: list[tuple[str, int]]) -> str:
    return "|".join(f"{key}@{at}" for key, at in sorted(found))


def run_hint(scopes: list[Scope], key: str) -> str:
    scope = next((s for s in scopes if s.name == key.split(":", 1)[0]), None)
    return _fill(scope.run, _values(scope, key), escape=False) if scope else ""


def reason(scopes: list[Scope], found: list[tuple[str, int]]) -> str:
    lines = ["EOS: changed after its last passing check, so not verified yet:"]
    lines += [f"- {key}: {run_hint(scopes, key)}" for key, _ in found[:MAX_SHOWN]]
    if len(found) > MAX_SHOWN:
        lines.append(f"- (+{len(found) - MAX_SHOWN} more)")
    lines.append("Run the check, or say in your answer that this is not verified. Asked once.")
    return "\n".join(lines)[:MAX_REASON]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd <eos-repo> && python3 -m pytest tests/test_verify.py -p no:cacheprovider`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
cd <eos-repo>
git add core/verify.py tests/test_verify.py
git commit -m "feat(verify): scopes, instances and the dirty-since-green computation"
```

---

### Task 2: Hook'lar değişikliği ve geçen doğrulamayı kaydeder

**Files:**
- Modify: `<eos-repo>/core/hooks.py` (`SETTINGS`, `_post_tool`, yeni `_verify_events`)
- Test: `<eos-repo>/tests/test_hooks.py`

**Interfaces:**
- Consumes: `verify.load`, `verify.dirty`, `verify.cleared` (Task 1)
- Produces: `hooks._verify_events(session) -> list[tuple[str, str]]`; oturum durumu satırları `{"changed": path}` ve `{"passed": key}`; açık run'a `kind="verified", tool="verify", ref=key` olayı; `SETTINGS` içinde `"verify"`.

- [ ] **Step 1: Write the failing test** (dosyanın sonuna)

```python
# --- verified completion (ADR-028) ---------------------------------------------------

VERIFY = r'''
[[scope]]
name = "code"
paths = ["src/**"]
passes = ['make\s+test\b']
run = "make test"
'''


def _verify_project(project):
    (project / ".eos" / "knowledge" / "verify.toml").write_text(VERIFY, encoding="utf-8")
    source = project / "src" / "a.py"
    source.parent.mkdir(exist_ok=True)
    source.write_text("x = 1\n", encoding="utf-8")
    return source


def test_a_change_and_the_check_that_passes_after_it_are_recorded(project, monkeypatch, capsys):
    source = _verify_project(project)
    run = _open_run(project)
    _hook(monkeypatch, capsys, "post-tool", _payload(project, tool_name="Edit", agent_id="sub1",
                                                     tool_input={"file_path": str(source)}))
    assert hooks._verify_events("s1")[-1] == ("changed", str(source))
    _hook(monkeypatch, capsys, "post-tool-failure", _payload(project, tool_name="Bash", error="Exit code 1",
                                                             tool_input={"command": "make test"}, tool_use_id="f1"))
    assert ("passed", "code") not in hooks._verify_events("s1")
    _hook(monkeypatch, capsys, "post-tool", _payload(project, tool_name="Bash", tool_use_id="p1",
                                                     tool_input={"command": "make test"}))
    assert hooks._verify_events("s1")[-1] == ("passed", "code")
    assert [e.ref for e in executions.load(project)[-1].events if e.kind == "verified"] == ["code"]
    assert run.id == executions.load(project)[-1].id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd <eos-repo> && python3 -m pytest tests/test_hooks.py -k "recorded" -p no:cacheprovider`
Expected: FAIL with `AttributeError: module 'core.hooks' has no attribute '_verify_events'`

- [ ] **Step 3: Write minimal implementation**

`SETTINGS` satırına `"verify"` eklenir:

```python
SETTINGS = ("brief", "capture", "hints", "subagents", "loaded", "sessions", "close", "usage", "compact",
            "verify")
```

`_watch`'ın altına:

```python
def _verify_events(session: str) -> list[tuple[str, str]]:
    """The session's changes and passing checks, in order, from its state."""
    events = []
    for line in _state(session):
        if isinstance(line.get("changed"), str):
            events.append(("changed", line["changed"]))
        if isinstance(line.get("passed"), str):
            events.append(("passed", line["passed"]))
    return events
```

`_post_tool` içindeki Edit dalı (ana oturum `_watch` ile, alt ajan doğrudan `changed` yazar):

```python
    if hook.tool in EDIT_TOOLS:
        path = str(hook.tool_input.get("file_path") or hook.tool_input.get("notebook_path") or "")
        if hook.session and not failed and path:
            if main_session:
                # What the session holds now is the edited text: the new mtime is the baseline.
                _watch(hook.session, path, changed=True)
            else:
                _note_state(hook.session, changed=path)
```

Bash dalında, `rewritten` hesabından sonra yeniden yazılan dosyalar değişiklik sayılır ve geçen doğrulama kaydedilir (`output = …` satırından önce):

```python
    if main_session and cfg["hints"]:
        rewritten = _rewritten(hook.session)
        for path in rewritten:
            _note_state(hook.session, changed=path)
        if rewritten and not any(line.get("hint") == "shell-rewrite" for line in _state(hook.session)):
            ...  # (mevcut ipucu kodu değişmeden)
    if hook.session and not failed and cfg["verify"]:
        _record_passes(root, hook, run, command)
```

Yeni yardımcı (`_verify_events`'in altına):

```python
def _record_passes(root: Path, hook: Hook, run, command: str) -> None:
    from core import verify

    try:
        scopes = verify.load(root)
        if not scopes:
            return
        waiting = [key for key, _ in verify.dirty(scopes, root, _verify_events(hook.session))]
        passed = verify.cleared(scopes, command, waiting)
    except Exception:  # noqa: BLE001 - a check never fails the session
        return
    for key in passed:
        _note_state(hook.session, passed=key)
        if run is not None:
            # Its own id: the call's id already names the `ran` event, and the
            # ledger's dedup would drop a second event carrying it.
            _record(root, hook, run, kind="verified", tool="verify", ref=key, status="ok",
                    tool_use_id=f"verify:{hook.tool_use_id}:{key}" if hook.tool_use_id else None)
```

Not: `_rewritten` içindeki mevcut ipucu bloğu `rewritten` değişkenini aynen kullanmaya devam eder; yalnız `for path in rewritten` satırı eklenir.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd <eos-repo> && python3 -m pytest tests/test_hooks.py -p no:cacheprovider`
Expected: PASS (tüm test_hooks testleri)

- [ ] **Step 5: Commit**

```bash
cd <eos-repo>
git add core/hooks.py tests/test_hooks.py
git commit -m "feat(hooks): record changes and the checks that pass after them (ADR-028)"
```

---

### Task 3: Stop kapısı ve oturum özeti

**Files:**
- Modify: `<eos-repo>/core/hooks.py` (`_stop`, yeni `_verify_gate`, `_session_end`)
- Test: `<eos-repo>/tests/test_hooks.py`

**Interfaces:**
- Consumes: `hooks._verify_events` (Task 2), `verify.load/dirty/signature/reason` (Task 1)
- Produces: `hooks._verify_gate(root, hook, cfg) -> str`; oturum durumu `{"gated": signature}`; `sessions.jsonl` alanları `verify_gates: int`, `verify_after_gate: int`.

- [ ] **Step 1: Write the failing test**

```python
def test_stop_asks_once_about_a_change_not_checked_since(project, monkeypatch, capsys):
    source = _verify_project(project)
    _hooks_config(project, "close = false\n")
    _hook(monkeypatch, capsys, "post-tool", _payload(project, tool_name="Edit", tool_input={"file_path": str(source)}))
    first = json.loads(_hook(monkeypatch, capsys, "stop", _payload(project)).out)
    assert first["decision"] == "block" and "code: make test" in first["reason"]
    assert _hook(monkeypatch, capsys, "stop", _payload(project)).out == ""          # same set: once
    assert _hook(monkeypatch, capsys, "stop", _payload(project, stop_hook_active=True)).out == ""
    _hook(monkeypatch, capsys, "post-tool", _payload(project, tool_name="Bash", tool_use_id="t9",
                                                     tool_input={"command": "make test"}))
    _hook(monkeypatch, capsys, "session-end", _payload(project, reason="other"))
    entry = json.loads((project / ".eos" / "data" / "sessions.jsonl").read_text().splitlines()[-1])
    assert entry["verify_gates"] == 1 and entry["verify_after_gate"] == 1


def test_stop_is_silent_without_scopes_or_for_unscoped_files(project, monkeypatch, capsys):
    _hooks_config(project, "close = false\n")
    notes_file = project / "notes.md"
    notes_file.write_text("x", encoding="utf-8")
    _hook(monkeypatch, capsys, "post-tool", _payload(project, tool_name="Edit", tool_input={"file_path": str(notes_file)}))
    assert _hook(monkeypatch, capsys, "stop", _payload(project)).out == ""        # no verify.toml
    _verify_project(project)
    assert _hook(monkeypatch, capsys, "stop", _payload(project)).out == ""        # notes.md is in no scope


def test_the_verify_gate_and_the_open_run_gate_share_one_block(project, monkeypatch, capsys):
    source = _verify_project(project)
    run = _open_run(project)
    _hook(monkeypatch, capsys, "post-tool", _payload(project, tool_name="Edit", tool_input={"file_path": str(source)}))
    reason = json.loads(_hook(monkeypatch, capsys, "stop", _payload(project)).out)["reason"]
    assert "not verified yet" in reason and run.id in reason
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd <eos-repo> && python3 -m pytest tests/test_hooks.py -k "stop_asks_once_about or verify_gate or silent_without_scopes" -p no:cacheprovider`
Expected: FAIL (`json.decoder.JSONDecodeError` — Stop henüz kapı üretmiyor)

- [ ] **Step 3: Write minimal implementation**

`_stop` yeniden (mevcut açık-run/iş kapısı davranışı korunur, gerekçeler tek blokta birleşir):

```python
def _verify_gate(root: Path, hook: Hook, cfg: dict) -> str:
    """Once per set: the scopes this session changed after their last passing check."""
    if not cfg["verify"] or hook.stop_active or not hook.session or hook.agent_id:
        return ""
    from core import verify

    try:
        scopes = verify.load(root)
        found = verify.dirty(scopes, root, _verify_events(hook.session)) if scopes else []
    except Exception:  # noqa: BLE001 - cannot tell, do not block
        return ""
    if not found:
        return ""
    mark = verify.signature(found)
    if any(line.get("gated") == mark for line in _state(hook.session)):
        return ""
    _note_state(hook.session, gated=mark)
    return verify.reason(scopes, found)


def _stop(root: Path, hook: Hook, cfg: dict, agent: str | None) -> str:
    if cfg["usage"] and hook.transcript and hook.session:
        _fold_usage(root, hook)
    reasons = [gate] if (gate := _verify_gate(root, hook, cfg)) else []
    if cfg["close"] and not hook.stop_active and hook.session and not hook.agent_id:
        from core import executions, work

        held = [item for item in work.items(root) if item.status == work.ACTIVE
                and any(holder.get("session") == hook.session for holder in item.holders)]
        runs = [run for run in executions.load(root) if run.open and run.session == hook.session]
        _note_state(hook.session, held=len(held), open_runs=len(runs))
        lines = []
        if held:
            lines.append(f"This session still holds {len(held)} work item(s); the next session reads them "
                         "as work in progress:")
            lines += [f"  {item.id}  {item.title}" for item in held[:5]]
            lines.append(f'Close each the way that is true: eos work done|block|drop {root} <id> --note/--reason "…"')
        if runs:
            lines.append(f"This session left {len(runs)} run(s) open; a failed one leaves no lesson until finished:")
            lines += [f"  {run.id}  {run.title}" for run in runs[:5]]
            lines.append(f"Finish each: eos run finish {root} <id> --outcome ok|failed|abandoned "
                         '(failed needs --lesson "…")')
        if lines:
            reasons.append("\n".join(lines))
    if not reasons:
        return ""
    # A Stop hook blocks through its JSON answer, so this one still exits 0;
    # the harness sets stop_hook_active on the next stop and it lets go.
    return json.dumps({"decision": "block", "reason": "\n\n".join(reasons)}, ensure_ascii=False)
```

`_session_end` içindeki `entry` sözlüğüne eklenir:

```python
                 "verify_gates": sum(1 for line in lines if line.get("gated")),
                 "verify_after_gate": sum(1 for index, line in enumerate(lines) if line.get("gated")
                                          and any(later.get("passed") for later in lines[index + 1:])),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd <eos-repo> && python3 -m pytest tests/test_hooks.py -p no:cacheprovider`
Expected: PASS (mevcut `test_stop_asks_once_about_runs_left_open` ve `test_stop_close_off_in_config_never_blocks` dahil)

- [ ] **Step 5: Commit**

```bash
cd <eos-repo>
git add core/hooks.py tests/test_hooks.py
git commit -m "feat(hooks): stop once on work changed after its last passing check"
```

---

### Task 4: Run sonucu `verified` / `claimed`

**Files:**
- Modify: `<eos-repo>/core/executions.py` (`Record`, `finish`, `load_path`, yeni `outcome_source`)
- Modify: `<eos-repo>/core/eos.py` (`cmd_run_finish`, `cmd_run_list`, `run list` parser `--stats`)
- Test: `<eos-repo>/tests/test_executions.py`

**Interfaces:**
- Consumes: `verify.load/instance/dirty` (Task 1); ledger olayları `kind="changed"` (ref = yol) ve `kind="verified", tool="verify"` (ref = anahtar, Task 2)
- Produces: `Record.outcome_source: str | None`; `executions.outcome_source(project_root, record) -> tuple[str | None, list[str]]`; finish satırında `"outcome_source"`; `eos run list --stats`.

- [ ] **Step 1: Write the failing test** (`tests/test_executions.py` sonuna; dosyanın mevcut `tmp_path` proje kurulumunu kullanır — yoksa aşağıdaki `_project` yeterli)

```python
def _verified_project(tmp_path):
    import subprocess, sys
    from pathlib import Path

    root = tmp_path / "proj"
    root.mkdir()
    eos = [sys.executable, str(Path(__file__).resolve().parents[1] / "core" / "eos.py")]
    assert subprocess.run(eos + ["init", str(root), "--no-ai"], capture_output=True).returncode == 0
    (root / ".eos" / "knowledge").mkdir(parents=True, exist_ok=True)
    (root / ".eos" / "knowledge" / "verify.toml").write_text(
        "[[scope]]\nname = \"code\"\npaths = [\"src/**\"]\npasses = ['make\\s+test\\b']\nrun = \"make test\"\n",
        encoding="utf-8")
    return root, eos


def test_a_finished_run_says_whether_its_changes_were_checked(tmp_path, monkeypatch):
    import subprocess
    from core import executions

    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    root, eos = _verified_project(tmp_path)
    a = executions.start(root, "checked", session="s1")
    executions.event(root, a.id, kind="changed", ref="src/a.py")
    executions.event(root, a.id, kind="verified", tool="verify", ref="code")
    assert executions.finish(root, a.id, outcome="ok").outcome_source == "verified"
    b = executions.start(root, "claimed", session="s2")
    executions.event(root, b.id, kind="verified", tool="verify", ref="code")
    executions.event(root, b.id, kind="changed", ref="src/a.py")
    assert executions.finish(root, b.id, outcome="ok").outcome_source == "claimed"
    c = executions.start(root, "reading", session="s3")
    executions.event(root, c.id, kind="changed", ref="docs/x.md")
    assert executions.finish(root, c.id, outcome="ok").outcome_source is None
    stats = subprocess.run(eos + ["run", "list", str(root), "--stats"], capture_output=True, text=True).stdout
    assert "verified 1, claimed 1, nothing to verify 1" in stats and "verified rate 50%" in stats


def test_finish_works_when_the_scopes_file_is_broken(tmp_path, monkeypatch):
    from core import executions

    monkeypatch.setenv("EOS_STATE_DIR", str(tmp_path / "state"))
    root, _ = _verified_project(tmp_path)
    (root / ".eos" / "knowledge" / "verify.toml").write_text("[[scope]\n", encoding="utf-8")
    run = executions.start(root, "r", session="s1")
    executions.event(root, run.id, kind="changed", ref="src/a.py")
    assert executions.finish(root, run.id, outcome="ok").outcome_source is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd <eos-repo> && python3 -m pytest tests/test_executions.py -k "checked or broken" -p no:cacheprovider`
Expected: FAIL with `AttributeError: 'Record' object has no attribute 'outcome_source'`

- [ ] **Step 3: Write minimal implementation**

`Record`'a alan (`lesson`'ın altına):

```python
    # For outcome "ok": "verified" when every scope the run changed passed its
    # check after the last change, "claimed" when one did not, None when the
    # run changed nothing a check covers (ADR-028).
    outcome_source: str | None = None
```

Yeni fonksiyon (`finish`'in üstüne):

```python
def outcome_source(project_root: str | Path, record: Record | None) -> tuple[str | None, list[str]]:
    """("verified" | "claimed" | None, the instances still unverified), from the run's events."""
    from core import verify

    if record is None:
        return None, []
    scopes = verify.load(project_root)
    if not scopes:
        return None, []
    events = []
    for entry in record.events:
        if entry.kind == "changed" and entry.ref:
            events.append(("changed", entry.ref))
        elif entry.kind == "verified" and entry.tool == "verify" and entry.ref:
            events.append(("passed", entry.ref))
    touched = {verify.instance(scopes, project_root, value) for kind, value in events if kind == "changed"}
    if not touched - {None}:
        return None, []
    left = [key for key, _ in verify.dirty(scopes, project_root, events)]
    return ("claimed" if left else "verified"), left
```

`finish` içinde, `commit, _ = work.git_head(project_root)` satırından önce ve finish satırı:

```python
    source, _ = outcome_source(project_root, existing) if outcome == "ok" else (None, [])
    commit, _ = work.git_head(project_root)
    at = utc_now()
    line = {"type": LINE_FINISH, "id": execution, "at": at,
            "outcome": outcome, "lesson": lesson, "commit_end": commit}
    if source:
        line["outcome_source"] = source
    _append(ledger, line)
```

`load_path`'in finish dalına:

```python
                record.commit_end = data.get("commit_end")
                record.outcome_source = data.get("outcome_source")
```

`core/eos.py` — `cmd_run_finish`:

```python
    print(_run_line(record))
    if record.outcome_source == "claimed":
        _, left = executions.outcome_source(args.path, record)
        print(f"claimed: {', '.join(left)} changed after its last passing check")
    return 0
```

`cmd_run_list` — filtrelerden sonra, `found = found[-args.limit:]` satırından önce:

```python
    if args.stats:
        done = [r for r in found if r.outcome == "ok"]
        verified = sum(1 for r in done if r.outcome_source == "verified")
        claimed = sum(1 for r in done if r.outcome_source == "claimed")
        rate = f"{verified / (verified + claimed):.0%}" if verified + claimed else "n/a"
        print(f"ok runs {len(done)}: verified {verified}, claimed {claimed}, "
              f"nothing to verify {len(done) - verified - claimed}; verified rate {rate}")
        return 0
```

Parser (`run_list_p` argümanlarının sonuna):

```python
    run_list_p.add_argument("--stats", action="store_true",
                            help="verified / claimed counts of ok runs (ADR-028)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd <eos-repo> && python3 -m pytest tests/test_executions.py -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd <eos-repo>
git add core/executions.py core/eos.py tests/test_executions.py
git commit -m "feat(runs): label ok runs verified or claimed from their events (ADR-028)"
```

---

### Task 5: EOS 1.6.0 sürümü

**Files:**
- Create: `<eos-repo>/docs/decisions/028-verified-completion.md`
- Modify: `README.md` ("What changed in 1.6"), `core/VERSION` (1.6.0), `plugin/.claude-plugin/plugin.json` ("1.6.0"), `plugin/skills/eos/SKILL.md` (`tools/build-plugin.py` üretir)

**Interfaces:**
- Consumes: Task 1-4
- Produces: yayımlanmış EOS 1.6.0 (GitHub `main`)

- [ ] **Step 1: ADR-028'i yaz** — bölümler: Status (Accepted 2026-09-27, extends ADR-027), Context (62 run'ın 56'sı ok, hepsi beyan; kullanıcının en çok yorulduğu hata), Decision (`verify.toml`, `core/verify.py`, Stop kapısı bir kez, run etiketi; `passes` parça başına sabit, pipe'ta `pipefail` şartı), Consequences (eşleme gerekir; eşlenmemiş iş susar; `claimed` bir suç değil bir etiket; ölçüm `run list --stats` ve `sessions.jsonl`).

- [ ] **Step 2: README'ye "What changed in 1.6" bölümü** — üç madde: tur sonu kapısı, run etiketi ve `--stats`, `verify.toml` biçimi (tek örnek kapsamla).

- [ ] **Step 3: Sürüm ve plugin kopyası**

```bash
cd <eos-repo>
python3 - <<'PY'
for p, a, b in (("core/VERSION", "1.5.1", "1.6.0"),
                ("plugin/.claude-plugin/plugin.json", '"version": "1.5.1"', '"version": "1.6.0"')):
    s = open(p).read(); assert a in s; open(p, "w").write(s.replace(a, b, 1))
PY
python3 tools/build-plugin.py
```

- [ ] **Step 4: Tam paket ve temizlik**

Run: `python3 -m pytest -p no:cacheprovider && bash tools/check-clean.sh`
Expected: tüm testler PASS, `check-clean: clean`

- [ ] **Step 5: Commit ve push**

```bash
git add docs/decisions/028-verified-completion.md README.md core/VERSION plugin/.claude-plugin/plugin.json plugin/skills/eos/SKILL.md
git commit -m "release: 1.6.0 -- verified completion (ADR-028)"
git push origin main
```

---

### Task 6: nexus kapsamları ve kurulum

**Files:**
- Create: `<host>/.devin/knowledge/nexus/verify.toml`
- Create: `<host>/automation/tests/test-verify-scopes.sh`

**Interfaces:**
- Consumes: EOS 1.6.0 (`tools/eos/core/verify.py`)
- Produces: nexus'ta etkin kapı

- [ ] **Step 1: Subtree çek ve CLI'ı kur**

```bash
cd <host>
git stash push -m "settings.local during subtree pull" -- .claude/settings.local.json
git subtree pull --prefix tools/eos eos main --squash -m "chore(eos): pull EOS 1.6.0"
git stash pop
bash automation/install-eos-cli.sh && eos --version
```
Expected: `eos 1.6.0`

- [ ] **Step 2: Failing test yaz** (`automation/tests/test-verify-scopes.sh`)

```bash
#!/usr/bin/env bash
# The nexus verify scopes (EOS ADR-028): each pattern compiles, sample files land
# in the right scope, and only a command that runs the check clears it.
#
# USAGE: automation/tests/test-verify-scopes.sh
set -uo pipefail
NEXUS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python3 - "$NEXUS_DIR" <<'EOF'
import sys
root = sys.argv[1]
sys.path.insert(0, root + "/tools/eos")
from core import verify
scopes = verify.load(root)
assert [s.name for s in scopes] == ["fm-service", "automation", "eos-upstream"], [s.name for s in scopes]
key = verify.instance(scopes, root, "../microservices/svc-crm-asset/src/main/java/A.java")
assert key == "fm-service:svc-crm-asset", key
assert verify.instance(scopes, root, "docs/eos-plans/x.md") is None
assert verify.instance(scopes, root, "automation/lib/routing_corpus.py") == "automation"
assert verify.instance(scopes, root, "<eos-repo>/core/hooks.py") == "eos-upstream"
assert verify.cleared(scopes, "automation/mvn.sh test svc-crm-asset", [key]) == [key]
assert verify.cleared(scopes, "automation/mvn.sh test svc-rim", [key]) == []
assert verify.cleared(scopes, "automation/search.sh 'mvn.sh test svc-crm-asset'", [key]) == []
assert verify.cleared(scopes, "bash automation/tests/test-capabilities.sh", ["automation"]) == ["automation"]
assert verify.cleared(scopes, "cd <eos-repo> && python3 -m pytest -q", ["eos-upstream"]) == ["eos-upstream"]
print("verify-scopes: ok")
EOF
```

Run: `bash automation/tests/test-verify-scopes.sh`
Expected: FAIL (`AssertionError: []` — `verify.toml` henüz yok)

- [ ] **Step 3: `verify.toml` yaz**

```toml
# Which check makes a change verified (EOS ADR-028). A file in a scope that
# changed after the scope's last passing check stops the turn once
# ("verify it, or say it is not verified"), and an ok run is labelled
# verified or claimed. Files in no scope (docs, notes, configs) are never
# asked about. Held together by automation/tests/test-verify-scopes.sh.

[[scope]]
name = "fm-service"
paths = ["../microservices/{service}/src/**"]
passes = ['automation/mvn\.sh\s+(test|verify)\s+{service}\b']
run = "automation/mvn.sh test {service}"

# Coarse on purpose: any automation test clears it (no script-to-test map).
[[scope]]
name = "automation"
paths = ["automation/**/*.sh", "automation/**/*.py"]
passes = ['automation/tests/test-[\w-]+\.sh']
run = "the matching automation/tests/test-*.sh"

[[scope]]
name = "eos-upstream"
paths = ["<eos-repo>/core/**",
         "<eos-repo>/plugin/**",
         "<eos-repo>/tests/**"]
passes = ['python3\s+-m\s+pytest\b', 'pytest\b']
run = "python3 -m pytest (in the EOS repository)"
```

- [ ] **Step 4: Testi ve mevcut testleri koş**

Run: `bash automation/tests/test-verify-scopes.sh && bash automation/tests/test-capabilities.sh && bash automation/tests/test-routing-corpus.sh`
Expected: `verify-scopes: ok`, `0 problem(s)`, `8 passed, 0 failed`

- [ ] **Step 5: Commit**

```bash
git add .devin/knowledge/nexus/verify.toml automation/tests/test-verify-scopes.sh
git commit -m "feat(eos): nexus verify scopes -- FM services, automation, EOS upstream"
```

---

### Task 7: Canlı doğrulama ve kayıt

**Files:**
- Modify: `<host>/docs/eos-plans/2026-09-27-eos-verified-completion-design.md` (As-built bölümü)

- [ ] **Step 1: Headless oturum** — kapsamdaki bir dosyada zararsız bir değişiklik, doğrulama koşmadan bitiş:

```bash
cd <host>
env -u ANTHROPIC_API_KEY claude -p "With the Edit tool, add the line '# probe' at the end of automation/lib/routing_opportunity.py. Do nothing else." --model sonnet --max-turns 8 --output-format json < /dev/null > "$TMPDIR/verify-live.json"
```

- [ ] **Step 2: Kanıt** — oturum transcript'inde Stop hook'unun `not verified yet` gerekçesiyle bir kez engellediği, ardından modelin ya `automation/tests/test-*.sh` koştuğu ya da "doğrulanmadı" dediği; `.eos/data/sessions.jsonl` son satırında `verify_gates: 1`.

```bash
python3 -c "import json;print(json.loads(open('.eos/data/sessions.jsonl').read().splitlines()[-1])['verify_gates'])"
```
Expected: `1`

- [ ] **Step 3: Probu geri al**

```bash
git checkout -- automation/lib/routing_opportunity.py && git status --short automation/lib/routing_opportunity.py
```
Expected: boş çıktı

- [ ] **Step 4: Spec'e As-built bölümü** — her görevin durumu, canlı kanıt, ilk ölçüm günü (2026-10-03).

- [ ] **Step 5: Commit**

```bash
git add docs/eos-plans/2026-09-27-eos-verified-completion-design.md
git commit -m "docs(eos): verified completion as built -- EOS 1.6.0, live gate"
```
