"""Runtime updater — manifest diff + deterministic file replacement.

Copies the canonical `core/` runtime into a project's `.eos/runtime/`
directory, replacing only changed files (by SHA-256 hash). The `data/`
directory is never touched by updates.

No backup of the previous runtime is kept. The runtime is a copy of a
canonical source that is tracked in git (`tools/eos/core/`), so a rollback
is `git checkout` plus one `eos update`, not a directory to restore -- and
the timestamped copies this used to leave behind accumulated in every
project's gitignored `.eos/` where nobody ever read them.
"""
import hashlib
import json
import shutil
from pathlib import Path
from typing import Dict, List, Tuple


class Updater:
    """Diff-and-replace updater for the eos-core runtime."""

    def __init__(self, canonical_core: Path, runtime_dir: Path):
        self.canonical = canonical_core.resolve()
        self.runtime = runtime_dir.resolve()

    # --- manifest -----------------------------------------------------------

    @staticmethod
    def compute_manifest(root: Path) -> Dict[str, str]:
        """Map rel_path -> sha256 for every .py file under root (excluding caches)."""
        manifest: Dict[str, str] = {}
        for path in sorted(root.rglob("*.py")):
            if any(part in {"__pycache__"} for part in path.parts):
                continue
            rel = path.relative_to(root).as_posix()
            manifest[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        return manifest

    @staticmethod
    def diff_manifests(local: Dict[str, str], canonical: Dict[str, str]) -> Tuple[List[str], List[str], List[str]]:
        """Return (added, changed, removed) rel paths comparing local -> canonical."""
        added = [k for k in canonical if k not in local]
        changed = [k for k in canonical if k in local and local[k] != canonical[k]]
        removed = [k for k in local if k not in canonical]
        return added, changed, removed

    # --- update -------------------------------------------------------------

    def update(self, dry_run: bool = False) -> Dict[str, any]:
        if not self.canonical.exists():
            raise FileNotFoundError(f"Canonical core not found: {self.canonical}")

        self.runtime.mkdir(parents=True, exist_ok=True)
        local_manifest_path = self.runtime / "manifest.json"
        local_manifest: Dict[str, str] = {}
        if local_manifest_path.exists():
            try:
                local_manifest = json.loads(local_manifest_path.read_text(encoding="utf-8")).get("files", {})
            except Exception:
                local_manifest = {}

        canonical_manifest = self.compute_manifest(self.canonical)
        added, changed, removed = self.diff_manifests(local_manifest, canonical_manifest)
        to_copy = set(added + changed)

        result: Dict[str, any] = {
            "added": added,
            "changed": changed,
            "removed": removed,
            "unchanged": [k for k in canonical_manifest if k in local_manifest and local_manifest[k] == canonical_manifest[k]],
            "dry_run": dry_run,
        }

        if dry_run:
            result["would_copy"] = sorted(to_copy)
            result["would_delete"] = sorted(removed)
            return result

        # 1. Remove files no longer in canonical runtime.
        for rel in removed:
            target = self.runtime / rel
            if target.exists():
                target.unlink()

        # 2. Copy added/changed files.
        for rel in to_copy:
            src = self.canonical / rel
            dst = self.runtime / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        # 3. Update manifest.json + VERSION.
        (self.runtime / "manifest.json").write_text(
            json.dumps({"version": self._read_version(), "files": canonical_manifest}, indent=2),
            encoding="utf-8",
        )
        version_path = self.canonical.parent / "core" / "VERSION"
        if not version_path.exists():
            # fallback: canonical IS core/
            version_path = self.canonical / "VERSION"
        if version_path.exists():
            shutil.copy2(version_path, self.runtime / "VERSION")

        return result

    def _read_version(self) -> str:
        for candidate in (self.canonical / "VERSION", self.canonical.parent / "VERSION"):
            if candidate.exists():
                return candidate.read_text(encoding="utf-8").strip()
        return "unknown"