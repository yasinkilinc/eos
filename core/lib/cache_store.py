"""Incremental scan cache stored inside .eos/data/cache/.

Caches file mtime/size/hash per scan to enable incremental rescans, plus
serialized FileSemantic models so unchanged files can be reused without
re-parsing, keeping the knowledge graph complete across incremental runs.
"""
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional

from core.knowledge.semantic import Export, FileSemantic, Import, Symbol


# Bump whenever the serialized FileSemantic shape changes in a way that makes
# an older entry wrong rather than merely incomplete. An entry written before
# `role` existed classifies as though the file carried no annotation, and one
# written before Import.level existed reads back as level 0, which resolves
# `from .. import x` as if it were `from . import x`. Neither file ever changes,
# so without this the wrong answer is cached forever.
#
# A mismatch drops the whole cache, which costs one full parse -- 1.7 s for
# 4,000 files -- and is the only way to be sure no stale shape survives.
_CACHE_FORMAT = 2

_FORMAT_KEY = "__format__"


class CacheStore:
    """Stores file mtime/size/hash per scan to enable incremental rescans.

    Also stores serialized FileSemantic under the `semantic` key of each
    file entry so unchanged files can be restored without re-parsing.
    """

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._store_path = self.cache_dir / "file_cache.json"
        self._data: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self._store_path.exists():
            return
        with open(self._store_path, "r", encoding="utf-8") as f:
            try:
                raw = json.load(f)
            except json.JSONDecodeError:
                return
        if not isinstance(raw, dict) or raw.get(_FORMAT_KEY) != _CACHE_FORMAT:
            return
        self._data = {k: v for k, v in raw.items() if k != _FORMAT_KEY}

    def save(self) -> None:
        payload = {_FORMAT_KEY: _CACHE_FORMAT}
        payload.update(self._data)
        with open(self._store_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    def get(self, rel_path: str) -> Optional[Dict[str, Any]]:
        return self._data.get(rel_path)

    def is_changed(self, rel_path: str, mtime: float, size: int, content: bytes) -> bool:
        entry = self._data.get(rel_path)
        if not entry:
            return True
        if entry.get("mtime") != mtime or entry.get("size") != size:
            return True
        digest = hashlib.sha256(content).hexdigest()
        return entry.get("sha256") != digest

    def update(self, rel_path: str, mtime: float, size: int, content: bytes) -> None:
        self._data[rel_path] = {
            "mtime": mtime,
            "size": size,
            "sha256": hashlib.sha256(content).hexdigest(),
            "semantic": self._serialize_semantic(self._data.get(rel_path, {}).get("semantic")),
        }

    def store_semantic(self, rel_path: str, file_sem: FileSemantic) -> None:
        entry = self._data.setdefault(rel_path, {})
        entry["semantic"] = self._serialize_semantic(file_sem)

    def get_semantic(self, rel_path: str) -> Optional[FileSemantic]:
        entry = self._data.get(rel_path)
        if not entry:
            return None
        sem = entry.get("semantic")
        if not sem:
            return None
        return self._deserialize_semantic(sem)

    def remove_missing(
        self, present_paths: set[str], parent_labels_scanned: set[str] | None = None
    ) -> None:
        """Delete stale entries, scoped to what this run actually walked.

        A plain scan only walks the project's own root, so its present_paths
        never contains any "@parent:<label>/" key -- without this scoping,
        pruning by bare absence would delete every parent-linked entry a
        prior `--with-parents` run indexed, on every scan that forgets the
        flag. `parent_labels_scanned` names which linked roots this run
        covered; entries under any other label are left untouched.
        """
        from core.links import PARENT_PREFIX

        scanned = parent_labels_scanned or set()
        for key in list(self._data.keys()):
            if key in present_paths:
                continue
            if key.startswith(PARENT_PREFIX):
                label = key[len(PARENT_PREFIX):].split("/", 1)[0]
                if label not in scanned:
                    continue
            del self._data[key]

    @staticmethod
    def _serialize_semantic(file_sem: Any) -> Dict[str, Any]:
        if file_sem is None:
            return {}
        if isinstance(file_sem, FileSemantic):
            return {
                "path": file_sem.path,
                "language": file_sem.language,
                "doc": file_sem.doc,
                "role": file_sem.role,
                "imports": [asdict(i) for i in file_sem.imports],
                "exports": [asdict(e) for e in file_sem.exports],
                "symbols": [asdict(s) for s in file_sem.symbols],
            }
        if isinstance(file_sem, dict):
            return file_sem
        return {}

    @staticmethod
    def _deserialize_semantic(data: Dict[str, Any]) -> Optional[FileSemantic]:
        if not data:
            return None
        try:
            return FileSemantic(
                path=data.get("path", ""),
                language=data.get("language", "unknown"),
                doc=data.get("doc"),
                role=data.get("role"),
                imports=[Import(**i) for i in data.get("imports", [])],
                exports=[Export(**e) for e in data.get("exports", [])],
                symbols=[Symbol(**s) for s in data.get("symbols", [])],
            )
        except Exception:
            return None