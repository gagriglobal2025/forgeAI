"""Dependency DAG engine.

Owns the live task graph: cycle detection at load time, per-task status
tracking, and ready-task resolution. This is the Orchestrator's core
coordination structure (Section 6 of the spec) — it never runs domain work
itself.
"""

TERMINAL_SUCCESS = {"VALIDATED"}
TERMINAL_FAILURE = {"ESCALATED", "REJECTED"}
TERMINAL = TERMINAL_SUCCESS | TERMINAL_FAILURE


class CycleDetected(RuntimeError):
    pass


class DAGEngine:
    def __init__(self, workflow: dict):
        self.name = workflow["name"]
        self.tasks: dict[str, dict] = {t["id"]: t for t in workflow["tasks"]}
        for task in self.tasks.values():
            task.setdefault("depends_on", [])
            task.setdefault("requires_approval", False)
        self.status: dict[str, str] = {tid: "PENDING" for tid in self.tasks}
        self._validate_acyclic()

    def _validate_acyclic(self):
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {tid: WHITE for tid in self.tasks}

        def visit(tid, stack):
            if color[tid] == BLACK:
                return
            if color[tid] == GRAY:
                cycle = " -> ".join(stack + [tid])
                raise CycleDetected(f"Circular dependency detected: {cycle}")
            color[tid] = GRAY
            for dep in self.tasks[tid]["depends_on"]:
                if dep not in self.tasks:
                    raise CycleDetected(f"Task {tid!r} depends on unknown task {dep!r}")
                visit(dep, stack + [tid])
            color[tid] = BLACK

        for tid in self.tasks:
            visit(tid, [])

    def ready_tasks(self) -> list[str]:
        ready = []
        for tid, task in self.tasks.items():
            if self.status[tid] != "PENDING":
                continue
            deps = task["depends_on"]
            if all(self.status[d] in TERMINAL_SUCCESS for d in deps):
                ready.append(tid)
            elif any(self.status[d] in TERMINAL_FAILURE for d in deps):
                self.status[tid] = "BLOCKED"
        return ready

    def mark(self, task_id: str, status: str):
        self.status[task_id] = status

    def in_progress_count(self) -> int:
        return sum(1 for s in self.status.values() if s == "IN_PROGRESS")

    def is_finished(self) -> bool:
        return all(s in TERMINAL or s == "BLOCKED" for s in self.status.values())

    def summary(self) -> dict[str, str]:
        return dict(self.status)
