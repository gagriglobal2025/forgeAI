"""Event bus: the only channel agents/orchestrator use to communicate.

Implements the message schema and canonical event names from Section 4 of
the Forge AI spec. Every message is appended to logs/events.log (JSONL) for
audit/replay, and dispatched synchronously to any subscribers.
"""
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

EVENTS = {
    "TASK_CREATED", "TASK_ASSIGNED", "TASK_RUNNING", "TASK_BLOCKED",
    "TASK_COMPLETED", "TASK_FAILED", "TASK_VALIDATED", "TASK_REJECTED",
    "ARTIFACT_PUBLISHED", "ARTIFACT_SUPERSEDED",
    "ESCALATION_RAISED", "ESCALATION_RESOLVED",
    "APPROVAL_REQUESTED", "APPROVAL_GRANTED", "APPROVAL_DENIED",
    "NEXT_AGENT_TRIGGERED",
}


class EventBus:
    def __init__(self, log_path: str = "logs/events.log"):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._subscribers = []
        self._lock = threading.Lock()

    def subscribe(self, callback):
        self._subscribers.append(callback)

    def emit(self, event: str, task_id: str, from_agent: str, to_agent: str = "orchestrator",
             status: str = "PENDING", artifact_ref: dict | None = None, payload: dict | None = None):
        if event not in EVENTS:
            raise ValueError(f"Unknown event type: {event}")

        message = {
            "message_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "from_agent": from_agent,
            "to_agent": to_agent,
            "task_id": task_id,
            "event": event,
            "status": status,
            "artifact_ref": artifact_ref,
            "payload": payload or {},
        }
        with self._lock:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(message) + "\n")
        for callback in self._subscribers:
            callback(message)
        return message
