"""Agent memory: private per-agent scratch state.

Per Section 5.2 of the spec, this is never a source of truth for other
agents — it holds only the current task and local reasoning cache, and is
cleared on task completion. A debug copy can optionally be persisted to disk
for post-run inspection (configs/memory.yaml: persist_debug_copy).
"""
import json
from pathlib import Path


class AgentMemory:
    def __init__(self, debug_dir: str | None = None):
        self._store: dict[str, dict] = {}
        self.debug_dir = Path(debug_dir) if debug_dir else None
        if self.debug_dir:
            self.debug_dir.mkdir(parents=True, exist_ok=True)

    def set_current_task(self, agent_name: str, task_id: str, scratch: dict):
        self._store[agent_name] = {"task_id": task_id, "scratch": scratch}

    def get(self, agent_name: str) -> dict:
        return self._store.get(agent_name, {})

    def clear(self, agent_name: str):
        """Discard an agent's scratch state once its task reaches a terminal status."""
        entry = self._store.pop(agent_name, None)
        if entry and self.debug_dir:
            debug_path = self.debug_dir / f"{agent_name}.json"
            debug_path.write_text(json.dumps(entry, indent=2), encoding="utf-8")
