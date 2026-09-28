"""Orchestrator scheduler: scheduling, parallel dispatch, approval gates,
escalation. Coordination only — never runs domain work itself (Section 6).
"""
import concurrent.futures
import json
from pathlib import Path

from orchestrator.dag_engine import DAGEngine


class Scheduler:
    def __init__(self, dag: DAGEngine, agents: dict, artifact_store, global_memory,
                 agent_memory, event_bus, max_parallel_branches: int = 4,
                 auto_approve: bool = False, escalations_log: str = "logs/escalations.log"):
        self.dag = dag
        self.agents = agents
        self.artifact_store = artifact_store
        self.global_memory = global_memory
        self.agent_memory = agent_memory
        self.bus = event_bus
        self.max_parallel_branches = max_parallel_branches
        self.auto_approve = auto_approve
        self.escalations_log = Path(escalations_log)
        self.escalations_log.parent.mkdir(parents=True, exist_ok=True)
        self.rework_cycles: dict[str, int] = {}

    def run(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_parallel_branches) as pool:
            futures = {}
            while not self.dag.is_finished():
                for task_id in self.dag.ready_tasks():
                    self.dag.mark(task_id, "IN_PROGRESS")
                    agent_name = self.dag.tasks[task_id]["agent"]
                    self.bus.emit("TASK_ASSIGNED", task_id, "orchestrator", agent_name)
                    self.bus.emit("TASK_RUNNING", task_id, "orchestrator", agent_name, status="IN_PROGRESS")
                    future = pool.submit(self._run_agent, task_id, agent_name)
                    futures[future] = task_id

                if not futures:
                    if self.dag.in_progress_count() == 0:
                        break  # nothing ready, nothing running -> deadlock/blocked, stop
                    continue

                done, _ = concurrent.futures.wait(
                    futures.keys(), return_when=concurrent.futures.FIRST_COMPLETED
                )
                for future in done:
                    task_id = futures.pop(future)
                    result = future.result()
                    self._handle_result(task_id, result)

        return self.dag.summary()

    def _run_agent(self, task_id: str, agent_name: str) -> dict:
        agent = self.agents[agent_name]
        return agent.run(
            task_id=task_id,
            artifact_store=self.artifact_store,
            global_memory=self.global_memory,
            agent_memory=self.agent_memory,
            event_bus=self.bus,
        )

    def _run_rework_cycle(self, task_id: str, agent_name: str, failed_result: dict):
        """An audit/QA agent (e.g. accessibility_agent) failed its own verdict gate. Rather
        than escalating immediately, re-dispatch the agents that actually produced the artifacts
        it's unhappy with — its `rework_agents` — with the failure content as reviewer notes,
        then re-run the audit itself. Recurses (via _handle_result) up to max_rework_cycles
        before falling back to a real escalation. Runs synchronously in the scheduler thread;
        this blocks progress on this one task_id but not on other in-flight futures."""
        agent = self.agents[agent_name]
        cycle_num = self.rework_cycles[task_id]
        feedback = failed_result.get("content") or failed_result.get("error") or "No findings captured."
        notes = (
            f"Findings from {agent_name} (rework cycle {cycle_num}/{agent.max_rework_cycles}) — "
            f"address every point below before resubmitting:\n\n{feedback}"
        )

        print(f"\n>> REWORK cycle {cycle_num}/{agent.max_rework_cycles} for {task_id}: "
              f"re-dispatching {agent.rework_agents} with findings from {agent_name}\n")

        for producer_name in agent.rework_agents:
            producer = self.agents[producer_name]
            self.bus.emit("TASK_ASSIGNED", producer_name, "orchestrator", producer_name)
            self.bus.emit("TASK_RUNNING", producer_name, "orchestrator", producer_name, status="IN_PROGRESS")
            producer_result = producer.run(
                task_id=producer_name, artifact_store=self.artifact_store, global_memory=self.global_memory,
                agent_memory=self.agent_memory, event_bus=self.bus, reviewer_notes=notes,
            )
            if not producer_result["success"]:
                self._escalate(producer_name, producer_name, producer_result,
                                reason=f"Producer failed while reworking for {task_id}: "
                                       f"{producer_result.get('error')}")
                self.dag.mark(producer_name, "ESCALATED")
                self.dag.mark(task_id, "ESCALATED")
                return
            self.bus.emit("TASK_VALIDATED", producer_name, producer_name, "orchestrator",
                          status="SUCCESS", artifact_ref=producer_result.get("artifact_ref"))
            self.dag.mark(producer_name, "VALIDATED")
            self.bus.emit("NEXT_AGENT_TRIGGERED", producer_name, "orchestrator", "broadcast")

        retry_result = agent.run(
            task_id=task_id, artifact_store=self.artifact_store, global_memory=self.global_memory,
            agent_memory=self.agent_memory, event_bus=self.bus,
        )
        self._handle_result(task_id, retry_result)

    def _handle_result(self, task_id: str, result: dict):
        agent_name = self.dag.tasks[task_id]["agent"]
        task_cfg = self.dag.tasks[task_id]
        agent = self.agents[agent_name]

        if not result["success"]:
            cycles_used = self.rework_cycles.get(task_id, 0)
            if agent.rework_agents and cycles_used < agent.max_rework_cycles:
                self.rework_cycles[task_id] = cycles_used + 1
                self._run_rework_cycle(task_id, agent_name, result)
                return
            self._escalate(task_id, agent_name, result)
            self.dag.mark(task_id, "ESCALATED")
            return

        if task_cfg.get("requires_approval") and not self.auto_approve:
            approved, notes = self._request_human_approval(task_id, agent_name, result)
            if not approved:
                self.bus.emit("APPROVAL_DENIED", task_id, "human", agent_name,
                              status="REJECTED", payload={"notes": notes})
                # one rework attempt with reviewer notes
                rework = self.agents[agent_name].run(
                    task_id=task_id, artifact_store=self.artifact_store,
                    global_memory=self.global_memory, agent_memory=self.agent_memory,
                    event_bus=self.bus, reviewer_notes=notes,
                )
                if not rework["success"]:
                    self._escalate(task_id, agent_name, rework)
                    self.dag.mark(task_id, "ESCALATED")
                    return
                approved2, notes2 = self._request_human_approval(task_id, agent_name, rework)
                if not approved2:
                    self._escalate(task_id, agent_name, rework, reason=f"Denied twice: {notes2}")
                    self.dag.mark(task_id, "REJECTED")
                    return
                result = rework
            self.bus.emit("APPROVAL_GRANTED", task_id, "human", agent_name, status="APPROVED")

        self.bus.emit(
            "TASK_VALIDATED", task_id, agent_name, "orchestrator",
            status="SUCCESS", artifact_ref=result.get("artifact_ref"),
        )
        self.dag.mark(task_id, "VALIDATED")
        self.bus.emit("NEXT_AGENT_TRIGGERED", task_id, "orchestrator", "broadcast")

    def _request_human_approval(self, task_id: str, agent_name: str, result: dict) -> tuple[bool, str]:
        self.bus.emit("APPROVAL_REQUESTED", task_id, agent_name, "human",
                      artifact_ref=result.get("artifact_ref"))
        if self.auto_approve:
            return True, ""
        print(f"\n{'=' * 70}\nAPPROVAL REQUIRED: {agent_name} ({task_id})\n{'=' * 70}")
        print(result["content"][:2000])
        print("=" * 70)
        answer = input("Approve this artifact? [y/N]: ").strip().lower()
        if answer == "y":
            return True, ""
        notes = input("Rejection notes for the agent (used for one rework attempt): ").strip()
        return False, notes

    def _escalate(self, task_id: str, agent_name: str, result: dict, reason: str | None = None):
        escalation_target = self.agents[agent_name].escalation_target
        self.bus.emit(
            "ESCALATION_RAISED", task_id, agent_name, escalation_target,
            status="ESCALATED",
            payload={"reason": reason or result.get("error", "unknown failure")},
        )
        with self.escalations_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "task_id": task_id,
                "agent": agent_name,
                "escalation_target": escalation_target,
                "reason": reason or result.get("error", "unknown failure"),
            }) + "\n")
        print(f"\n!! ESCALATION: task={task_id} agent={agent_name} "
              f"-> {escalation_target}: {reason or result.get('error')}\n")
