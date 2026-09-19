"""Index business journeys: the documents that describe them, and the configured
step chains that implement them.

An index extension (see core/extensions.py). Enable it per project with:

    [index]
    extensions = ["tools/eos/extensions/journeys.py"]

    [journeys]
    docs = "docs/journeys"

## What a journey is here

Some systems do not implement a business flow in code at all. They implement a
*chain* of named steps in configuration -- a row per step, naming a component
by bean name -- and a coordinator looks each one up at run time and runs it in
order. Reading the code alone then tells you what a step *could* do and never
which steps run, in what order, in which service. The chain is the program.

So this extension indexes three things and joins them:

  journey_doc       what someone wrote down about a journey, as prose
  journey_snapshot  when a chain configuration was exported, and from where
  journey_step      one configured step, resolved to the service that runs it
  flow_step         the flow-level rows a chain's steps hang off

Step ownership is the part worth explaining. A step names a bean, not a
service, so which service runs it is inferred from which checked-out tree
declares a class for that bean -- by class name, by the same name with a
lowercase initial, and by any explicit `@Component("name")` value. Where the
row also carries an implementation class, that class's package is a second
signal. The two together resolve the common ambiguity: several services
declaring a same-named class, only one of which actually runs the step.

Every resolution is recorded as such. `journey_step.resolution` says *how* a
row was attributed -- `bean`, `impl_cls`, `shared`, `ambiguous`, `elsewhere`,
`unresolved`, `no-bean` -- and `candidates` lists what else it could have been,
so an attribution can be disbelieved without re-deriving it. `owned` is the
narrow question "does this project run this step", which is what a query
usually wants.

## Layout this expects

    <repo>/<docs>/*.md                     journey documents, front matter below
    <repo>/<docs>/_snapshots/<env>/chains.json
    <repo>/<docs>/_snapshots/<env>/flow-steps.json   (optional)
    <repo>/<docs>/_snapshots/<env>/_meta.json        (optional)
    <workspace>/<implementations>/<project>/         the services themselves
    <workspace>/<upstream>/<project>/                optional upstream overlay

`<repo>` is the repository that holds this project's notes -- journeys and
notes are shared through the same repository -- and `<workspace>` is its
parent. A journey document is front matter plus prose:

    ---
    journey: topup
    services: [acme-wallet-topup]
    bis: [TOP_UP]
    flows: [TOP_UP]
    verified_on: env1
    ---

    # Top-up -- end to end

Only documents naming this project in `services` are indexed into it.

## Configuration

Every key in `[journeys]` is optional; the defaults are below. Names are
matched with hyphens removed, so `upstream-wallet-topup` resolves to
`acme-wallet-topup` under `upstream_prefix = "upstream-"`.

    docs                   = "docs/journeys"
    implementations        = "microservices"
    upstream               = "parent-microservices"
    upstream_prefix        = ""
    implementation_prefix  = ""
    class_suffix           = "Command.java"
    shared_suffix          = "-common"
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from core import index
from core.lib.config_io import ConfigIO

SCHEMA = """
CREATE TABLE journey_snapshot (
    env TEXT PRIMARY KEY,
    generated_at TEXT,
    scope TEXT,
    schema_name TEXT,
    generated_by TEXT
);
CREATE TABLE journey_step (
    env TEXT NOT NULL,
    cmd_config_id TEXT,
    bi TEXT,
    flow TEXT,
    state TEXT,
    phase TEXT,
    sort_id INTEGER,
    bean_name TEXT,
    cmd_short_code TEXT,
    impl_cls TEXT,
    config_active INTEGER,
    def_active INTEGER,
    next_struct INTEGER,
    managed_by TEXT,
    cmd_def_id TEXT,
    updated_at TEXT,
    updated_by TEXT,
    owner TEXT,
    resolution TEXT NOT NULL,
    candidates TEXT NOT NULL,
    owned INTEGER NOT NULL
);
CREATE INDEX journey_step_by_flow ON journey_step(env, bi, flow, state, phase, sort_id);
CREATE TABLE flow_step (
    env TEXT NOT NULL,
    config_id TEXT,
    bi TEXT,
    flow TEXT,
    state TEXT,
    sale_channel_id TEXT,
    sort_id INTEGER,
    active INTEGER,
    optional INTEGER,
    checkout INTEGER,
    next_struct INTEGER,
    visibility_class TEXT,
    managed_by TEXT
);
CREATE INDEX flow_step_by_flow ON flow_step(env, bi, flow, sort_id);
CREATE TABLE journey_doc (
    name TEXT PRIMARY KEY,
    journey TEXT,
    title TEXT,
    services TEXT,
    bis TEXT,
    flows TEXT,
    verified_on TEXT,
    verified_at TEXT,
    verified_by TEXT,
    body TEXT NOT NULL,
    sha256 TEXT NOT NULL
);
"""

COUNTS = {"journey steps": "SELECT COUNT(*) FROM journey_step WHERE owned = 1"}

_CHAIN_COLUMNS = (
    "cmd_config_id", "bi", "flow", "state", "phase", "sort_id", "bean_name", "cmd_short_code",
    "impl_cls", "config_active", "def_active", "next_struct", "managed_by", "cmd_def_id",
    "updated_at", "updated_by",
)
_FLOW_COLUMNS = (
    "config_id", "bi", "flow", "state", "sale_channel_id", "sort_id", "active", "optional",
    "checkout", "next_struct", "visibility_class", "managed_by",
)
_SNAPSHOT_FILES = ("chains.json", "flow-steps.json", "_meta.json")

_EXPLICIT_BEAN = re.compile(r'@Component\s*\(\s*(?:value\s*=\s*)?"([^"]+)"')
_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
# .worktrees holds transient per-ticket checkouts of the same repo; indexing
# them makes a bean resolve to several copies of one class.
_PRUNED_DIRS = frozenset({".git", ".worktrees", ".eos", "node_modules", "target"})


@dataclasses.dataclass(frozen=True)
class _Settings:
    docs: str = "docs/journeys"
    implementations: str = "microservices"
    upstream: str = "parent-microservices"
    upstream_prefix: str = ""
    implementation_prefix: str = ""
    class_suffix: str = "Command.java"
    shared_suffix: str = "-common"


@dataclasses.dataclass(frozen=True)
class _Beans:
    """What the checked-out trees declare, to tell who runs a chain step."""

    by_name: dict  # bean name -> projects with a class for it, implementation first
    by_class: dict  # fully qualified class name -> projects declaring it
    services: tuple  # every project those trees map to


def _settings(root: Path) -> _Settings:
    config = root / ".eos" / "config.toml"
    section = ConfigIO.read_toml(config).get("journeys", {}) if config.is_file() else {}
    fields = {field.name for field in dataclasses.fields(_Settings)}
    unknown = sorted(set(section) - fields)
    if unknown:
        raise ValueError(f"[journeys] in {config}: unknown key(s) {', '.join(unknown)}")
    values = {key: section[key] for key in fields if key in section}
    for key, value in values.items():
        if not isinstance(value, str):
            raise ValueError(f"[journeys] {key} in {config} must be a string")
    return _Settings(**values)


def _journeys_root(notes_dir: Path, settings: _Settings) -> tuple[Path, Path] | None:
    """(journeys directory, repository) if the repository holding the notes has one.

    Notes and journeys are shared through the same repository, so the search
    stops at the first directory with a `.git` and never wanders further up.
    The repository's parent is the workspace the implementation trees sit in.
    """
    for directory in (notes_dir, *notes_dir.parents):
        if (directory / ".git").exists():
            journeys = directory / settings.docs
            return (journeys, directory) if journeys.is_dir() else None
    return None


# --- the extension contract -------------------------------------------------------


def sources(root: Path, notes_dir: Path):
    """Identity of everything `load` reads, cheap enough to take on every staleness check.

    The command classes behind step ownership are stat()ed, not read: they are
    hundreds of files across every tree, and a checkout that only touches their
    mtimes costs one needless rebuild -- far cheaper than hashing them all on
    every query.
    """
    settings = _settings(root)
    yield ("settings", *dataclasses.astuple(settings))
    found = _journeys_root(notes_dir, settings)
    if found is None:
        return
    journeys, repo = found
    for path in sorted(journeys.glob("*.md")):
        yield ("doc", path.name, index.file_digest(path))
    snapshots = journeys / "_snapshots"
    envs = sorted(path for path in snapshots.iterdir() if path.is_dir()) if snapshots.is_dir() else []
    for env_dir in envs:
        for name in _SNAPSHOT_FILES:
            yield ("snapshot", env_dir.name, name, index.file_digest(env_dir / name))
    workspace = repo.parent
    implementations = workspace / settings.implementations
    if envs and root.parent == implementations:
        yield ("projects", *sorted(path.name for path in implementations.iterdir() if path.is_dir()))
        for base, _, path in _class_files(implementations, workspace / settings.upstream, settings):
            yield ("class", base.name, path.relative_to(base).as_posix(), *_stat(path))


def load(build) -> None:
    settings = _settings(build.root)
    notes_dir = build.notes_dir
    if notes_dir is None:
        return
    found = _journeys_root(notes_dir, settings)
    if found is None:
        return
    journeys, repo = found
    build.meta["journeys_dir"] = str(journeys)
    _load_docs(build, journeys)
    _load_snapshots(build, journeys, repo.parent, settings)


# --- documents ---------------------------------------------------------------------


def _load_docs(build, journeys: Path) -> None:
    service = build.root.name
    for path in sorted(journeys.glob("*.md")):
        try:
            raw, digest = build.read(path)
        except OSError as exc:
            build.issue("journey", path.name, f"unreadable ({exc}); not indexed")
            continue
        text = index.text_of(raw)
        fields = index.front_matter(text)
        services = _inline_list(fields.get("services"))
        if service not in services:
            continue
        body = text[4:].partition("\n---\n")[2].strip()
        title = index.heading_of(body)
        build.conn.execute(
            "INSERT INTO journey_doc(name, journey, title, services, bis, flows, verified_on, verified_at, "
            "verified_by, body, sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (path.name, fields.get("journey"), title, ",".join(services),
             ",".join(_inline_list(fields.get("bis"))), ",".join(_inline_list(fields.get("flows"))),
             fields.get("verified_on"), fields.get("verified_at"), fields.get("verified_by"), body, digest),
        )
        build.search("journey", path.name, title or path.name, body)


# --- snapshots ---------------------------------------------------------------------


def _load_snapshots(build, journeys: Path, workspace: Path, settings: _Settings) -> None:
    snapshots = journeys / "_snapshots"
    if not snapshots.is_dir():
        return
    implementations = workspace / settings.implementations
    # Steps are attributed to projects by folder name; a directory that is not
    # one of them (the workspace itself, a worktree) runs none.
    is_service = build.root.parent == implementations
    service = build.root.name
    beans = None
    for env_dir in sorted(path for path in snapshots.iterdir() if path.is_dir()):
        env = env_dir.name
        try:
            chains = _json_array(build, env_dir / "chains.json")
            flows_path = env_dir / "flow-steps.json"
            flows = _json_array(build, flows_path) if flows_path.exists() else []
            meta_path = env_dir / "_meta.json"
            meta = json.loads(build.read(meta_path)[0]) if meta_path.exists() else {}
        except (OSError, ValueError) as exc:
            # Snapshots are regenerated in place; a reader can catch one mid-write.
            build.issue("journey", env, f"snapshot unreadable ({exc}); journey steps not indexed")
            continue
        meta = meta if isinstance(meta, dict) else {}
        build.conn.execute(
            "INSERT INTO journey_snapshot(env, generated_at, scope, schema_name, generated_by) VALUES (?, ?, ?, ?, ?)",
            (env, _scalar(meta.get("generated_at")), _scalar(meta.get("scope")), _scalar(meta.get("schema")),
             _scalar(meta.get("generated_by"))),
        )
        if not is_service:
            continue
        if beans is None:
            beans = _bean_index(implementations, workspace / settings.upstream, settings)
        _insert_steps(build, env, chains, flows, beans, service, settings)


def _json_array(build, path: Path) -> list:
    rows = json.loads(build.read(path)[0])
    if not isinstance(rows, list):
        raise ValueError(f"{path.name} is not a JSON array")
    return [row for row in rows if isinstance(row, dict)]


# --- bean ownership ----------------------------------------------------------------


def _squash(name: str) -> str:
    return name.replace("-", "").lower()


def _implementation_for(upstream_repo: str, projects: list[str], settings: _Settings) -> str:
    """upstream-rim -> acme-rim, matched ignoring hyphens, so upstream-searchintegrator finds acme-search-integrator."""
    name = upstream_repo.removeprefix(settings.upstream_prefix)
    for candidate in projects:
        if _squash(candidate.removeprefix(settings.implementation_prefix)) == _squash(name):
            return candidate
    return settings.implementation_prefix + name


def _class_files(implementations: Path, upstream: Path, settings: _Settings):
    """(tree root, is_upstream, path) of every candidate class in both trees."""
    for base, is_upstream in ((implementations, False), (upstream, True)):
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            current = Path(dirpath)
            dirnames[:] = sorted(
                name for name in dirnames
                if name not in _PRUNED_DIRS and not (name == "test" and current.name == "src")
            )
            if current == base:
                continue
            for filename in sorted(filenames):
                if filename.endswith(settings.class_suffix):
                    yield base, is_upstream, current / filename


def _bean_index(implementations: Path, upstream: Path, settings: _Settings) -> _Beans:
    """Bean name -> projects whose tree declares a class for it, implementation overlay first.

    A coordinator looks a chain step up by bean name, so the project holding
    the bean is the one that runs the step. A bean is indexed by the class
    name, the class name with a lowercase initial, and any explicit
    `@Component("x")` value -- a snapshot uses all three spellings. Each class
    is also indexed by its fully qualified name, which is what a step's
    `impl_cls` holds.
    """
    projects = sorted(path.name for path in implementations.iterdir() if path.is_dir()) \
        if implementations.is_dir() else []
    by_name: dict[str, list[str]] = defaultdict(list)
    by_class: dict[str, list[str]] = defaultdict(list)
    services = set(projects)
    for base, is_upstream, path in _class_files(implementations, upstream, settings):
        tree = path.relative_to(base).parts[0]
        service = _implementation_for(tree, projects, settings) if is_upstream else tree
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "class " not in text:
            continue
        services.add(service)
        stem = path.name[: -len(".java")] if path.name.endswith(".java") else path.stem
        package = _PACKAGE.search(text)
        qualified = f"{package.group(1)}.{stem}" if package else stem
        if service not in by_class[qualified]:
            by_class[qualified].append(service)
        for bean in (stem, stem[0].lower() + stem[1:], *_EXPLICIT_BEAN.findall(text)):
            if service not in by_name[bean]:
                by_name[bean].append(service)
    return _Beans(by_name=dict(by_name), by_class=dict(by_class), services=tuple(sorted(services)))


def _is_shared_library(service: str, settings: _Settings) -> bool:
    # A library every project links -- a bean found only there says nothing
    # about who runs the step.
    return bool(settings.shared_suffix) and service.endswith(settings.shared_suffix)


def _package_names_service(impl_cls, service: str, settings: _Settings) -> bool:
    """Whether a class's package spells the project: com.example.app.cpq.ordercapture.X -> acme-cpq-ordercapture."""
    if not isinstance(impl_cls, str):
        return False
    wanted = _squash(service.removeprefix(settings.implementation_prefix))
    segments = [segment.lower() for segment in impl_cls.split(".")[:-1]]
    for start in range(len(segments)):
        joined = ""
        for segment in segments[start:]:
            joined += segment
            if joined == wanted:
                return True
            if len(joined) >= len(wanted):
                break
    return False


def _services_named_by(impl_cls: str, beans: _Beans, settings: _Settings) -> set[str]:
    """Projects an impl_cls points at: those declaring exactly that class, or whose name its package spells.

    A shared library counts only through an exact class: "common" is a package
    segment in nearly every project.
    """
    named = set(beans.by_class.get(impl_cls, ()))
    named.update(
        service for service in beans.services
        if not _is_shared_library(service, settings) and _package_names_service(impl_cls, service, settings)
    )
    return named


def _configured_elsewhere(row: dict, beans: _Beans, settings: _Settings) -> bool:
    """Whether a step's impl_cls names a class no checked-out tree declares, in a package no checked-out project spells."""
    bean, impl_cls = row.get("bean_name"), row.get("impl_cls")
    if not isinstance(bean, str) or not bean or not isinstance(impl_cls, str) or not impl_cls:
        return False
    return not _services_named_by(impl_cls, beans, settings)


def _resolve_owner(row: dict, beans: _Beans, settings: _Settings) -> tuple[str | None, str, str]:
    bean = row.get("bean_name")
    if not isinstance(bean, str) or not bean:
        return None, "no-bean", ""
    by_name = beans.by_name
    hits = by_name.get(bean) or by_name.get(bean[0].lower() + bean[1:]) or by_name.get(bean[0].upper() + bean[1:]) or []
    if not hits:
        return None, "unresolved", ""
    candidates = ",".join(hits)
    owners = [service for service in hits if not _is_shared_library(service, settings)]
    if not owners:
        return None, "shared", candidates
    impl_cls = row.get("impl_cls")
    if not isinstance(impl_cls, str) or not impl_cls:
        return (owners[0], "bean", candidates) if len(owners) == 1 else (None, "ambiguous", candidates)
    # impl_cls is a leftover of an older addressing style, but where it is set it
    # names the class the step was configured with, and a bean name alone also
    # matches same-named classes in projects that never run the step.
    named = _services_named_by(impl_cls, beans, settings)
    if not named:
        return None, "elsewhere", candidates
    matching = [service for service in owners if service in named]
    if len(owners) == 1:
        # A shared-library class says nothing against the one project declaring the bean.
        if matching or all(_is_shared_library(service, settings) for service in named):
            return owners[0], "bean", candidates
        return None, "ambiguous", candidates
    if len(matching) == 1:
        return matching[0], "impl_cls", candidates
    return None, "ambiguous", candidates


def _insert_steps(build, env: str, chains: list, flows: list, beans: _Beans,
                  service: str, settings: _Settings) -> None:
    resolved = []
    credited: Counter = Counter()
    foreign: Counter = Counter()
    for row in chains:
        group = (row.get("bi"), row.get("flow"))
        owner, resolution, candidates = _resolve_owner(row, beans, settings)
        if owner:
            credited[group] += 1
        if _configured_elsewhere(row, beans, settings):
            foreign[group] += 1
        resolved.append((row, group, owner, resolution, candidates))
    # A flow runs in one project. When at least as many of its steps name
    # classes no checked-out tree declares as are matched to a checked-out
    # project, it runs in a project that is not checked out -- flows that reuse
    # class names other projects also declare -- and none of it is credited here.
    elsewhere = {group for group, count in foreign.items() if count >= credited[group]}
    resolved = [
        (row, group, None, "elsewhere", candidates) if owner and group in elsewhere
        else (row, group, owner, resolution, candidates)
        for row, group, owner, resolution, candidates in resolved
    ]
    runs: dict[tuple, Counter] = defaultdict(Counter)
    for _, group, owner, _, _ in resolved:
        if owner:
            runs[group][owner] += 1
    # A shared-library step runs in whichever project runs the rest of its flow.
    runner = {}
    for group, counter in runs.items():
        ranked = counter.most_common(2)
        if len(ranked) == 1 or ranked[0][1] > ranked[1][1]:
            runner[group] = ranked[0][0]
    # Whole flows, not just this project's rows: a step's neighbours are what
    # make it readable, and `owned` says which rows this project runs.
    flows_here = {group for group, counter in runs.items() if service in counter}

    step_rows = []
    for row, group, owner, resolution, candidates in resolved:
        if group not in flows_here:
            continue
        if resolution == "shared":
            owner = runner.get(group)
        step_rows.append((
            env, *(_scalar(row.get(column)) for column in _CHAIN_COLUMNS),
            owner, resolution, candidates, int(owner == service),
        ))
    placeholders = ", ".join("?" * (len(_CHAIN_COLUMNS) + 5))
    build.conn.executemany(
        f"INSERT INTO journey_step(env, {', '.join(_CHAIN_COLUMNS)}, owner, resolution, candidates, owned) "
        f"VALUES ({placeholders})",
        step_rows,
    )
    build.conn.executemany(
        f"INSERT INTO flow_step(env, {', '.join(_FLOW_COLUMNS)}) VALUES ({', '.join('?' * (len(_FLOW_COLUMNS) + 1))})",
        [
            (env, *(_scalar(row.get(column)) for column in _FLOW_COLUMNS))
            for row in flows
            if (row.get("bi"), row.get("flow")) in flows_here
        ],
    )


# --- small helpers -----------------------------------------------------------------


def _inline_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip().strip("\"'") for item in value.strip().strip("[]").split(",") if item.strip()]


def _scalar(value):
    if value is None or isinstance(value, (str, int, float)):
        return value
    return json.dumps(value, ensure_ascii=False)


def _stat(path: Path) -> tuple:
    try:
        info = path.stat()
    except OSError:
        return ("-", "-")
    return (info.st_mtime_ns, info.st_size)
