"""Artifact memory: immutable, append-only, versioned outputs.

Every artifact entry matches Section 5.3 of the Forge AI spec:
  artifact_id, version, author_agent, timestamp, parent_artifact, dependencies, uri, checksum

Agents never overwrite an artifact. A "change" is a new artifact_id whose
parent_artifact points at the prior version of the same key, with an
incremented version number.
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path


class ArtifactNotFound(RuntimeError):
    pass


class ArtifactStore:
    def __init__(self, root: str = "artifacts"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "_index.json"
        self._index: list[dict] = self._load_index()

    def _load_index(self) -> list[dict]:
        if self.index_path.exists():
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        return []

    def _save_index(self):
        self.index_path.write_text(json.dumps(self._index, indent=2), encoding="utf-8")

    def latest_entry(self, key: str) -> dict | None:
        matches = [e for e in self._index if e["key"] == key]
        if not matches:
            return None
        return max(matches, key=lambda e: e["version"])

    def get_content(self, key: str, version: int | None = None) -> str:
        entry = self.latest_entry(key) if version is None else self._entry_at(key, version)
        if entry is None:
            raise ArtifactNotFound(f"No artifact found for key={key!r} version={version!r}")
        return Path(entry["uri"]).read_text(encoding="utf-8")

    def _entry_at(self, key: str, version: int) -> dict | None:
        for e in self._index:
            if e["key"] == key and e["version"] == version:
                return e
        return None

    def put(self, key: str, content: str, author_agent: str, dependency_keys: list[str] | None = None,
            verdict: str | None = None) -> dict:
        """`verdict` is optional metadata ("PASS"/"FAIL"/None) — a FAIL is still persisted as a
        real version so an agent's findings survive even when it doesn't pass its own gate."""
        prior = self.latest_entry(key)
        version = (prior["version"] + 1) if prior else 1

        key_dir = self.root / key
        key_dir.mkdir(parents=True, exist_ok=True)
        uri = key_dir / f"v{version}.md"
        uri.write_text(content, encoding="utf-8")

        dependencies = []
        for dep_key in dependency_keys or []:
            dep_entry = self.latest_entry(dep_key)
            if dep_entry:
                dependencies.append(dep_entry["artifact_id"])

        entry = {
            "artifact_id": str(uuid.uuid4()),
            "key": key,
            "version": version,
            "author_agent": author_agent,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "parent_artifact": prior["artifact_id"] if prior else None,
            "dependencies": dependencies,
            "uri": str(uri),
            "checksum": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "verdict": verdict,
        }
        self._index.append(entry)
        self._save_index()
        return entry

    def lineage(self, artifact_id: str) -> list[dict]:
        """Walk parent_artifact pointers back to the root version."""
        by_id = {e["artifact_id"]: e for e in self._index}
        chain = []
        current = by_id.get(artifact_id)
        while current:
            chain.append(current)
            current = by_id.get(current["parent_artifact"])
        return list(reversed(chain))
