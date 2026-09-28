"""Global memory: project metadata, tech stack, standards, shared decisions.

Read access is open to every agent. Write access is restricted to the roles
listed in configs/memory.yaml (write_roles) — this mirrors Section 5.1 of the
Forge AI spec, where Global Memory is read-by-all but write-restricted.
"""
import json
from pathlib import Path


class PermissionError_(RuntimeError):
    pass


class GlobalMemory:
    def __init__(self, path: str, write_roles: list[str]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.write_roles = set(write_roles)
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {}

    def _save(self):
        self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def read(self) -> dict:
        return dict(self._data)

    def write(self, agent_name: str, updates: dict):
        if agent_name not in self.write_roles:
            raise PermissionError_(
                f"{agent_name} is not permitted to write to global memory "
                f"(allowed: {sorted(self.write_roles)})"
            )
        self._data.update(updates)
        self._save()

    def seed_from_project_brief(self, project_brief: dict):
        """Called once at startup — the project brief is the seed content of global memory."""
        self._data.update(project_brief)
        self._save()
