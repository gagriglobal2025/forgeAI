#!/usr/bin/env python3
"""Entry point: boots the Forge AI workforce and runs the SDLC DAG end to end.

Usage:
    python run.py                        # real run, calls Anthropic (default provider), prompts for approvals
    python run.py --provider openai      # same, but calls OpenAI instead (needs OPENAI_API_KEY)
    python run.py --dry-run              # no API key needed, stub responses, verifies wiring only
    python run.py --auto-approve         # skip interactive approval gates (still calls the real API)
"""
import argparse
import sys

import yaml

from agents.registry import load_agents
from llm.client import LLMClient, LLMError
from memory.agent_memory import AgentMemory
from memory.artifact_store import ArtifactStore
from memory.global_memory import GlobalMemory
from orchestrator.dag_engine import DAGEngine
from orchestrator.event_bus import EventBus
from orchestrator.scheduler import Scheduler


def load_yaml(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(description="Run the Forge AI autonomous SDLC workforce.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Use stub LLM responses instead of calling the Anthropic API.")
    parser.add_argument("--auto-approve", action="store_true",
                        help="Skip interactive human approval gates.")
    parser.add_argument("--project-brief", default="configs/project.yaml")
    parser.add_argument("--provider", default=None, choices=["anthropic", "openai"],
                        help="Override the default LLM provider.")
    parser.add_argument("--model", default=None, help="Override the default model.")
    args = parser.parse_args()

    orchestrator_cfg = load_yaml("configs/orchestrator.yaml")["orchestrator"]
    memory_cfg = load_yaml("configs/memory.yaml")["memory"]
    workflow_cfg = load_yaml("configs/workflow.yaml")["workflow"]
    project_brief = load_yaml(args.project_brief)

    unresolved = [k for k, v in project_brief.items() if isinstance(v, str) and v.startswith("{{")]
    if unresolved and not args.dry_run:
        print(f"Warning: {args.project_brief} still has unfilled placeholders: {unresolved}")
        print("The agents will receive these literally. Fill in configs/project.yaml for a real run.\n")

    provider = args.provider or orchestrator_cfg["default_provider"]
    default_model = orchestrator_cfg["default_models"][provider]
    try:
        llm = LLMClient(model=args.model or default_model, provider=provider, dry_run=args.dry_run)
    except LLMError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    global_memory = GlobalMemory(
        path=memory_cfg["global"]["path"],
        write_roles=memory_cfg["global"]["write_roles"],
    )
    global_memory.seed_from_project_brief(project_brief)

    agent_memory = AgentMemory(
        debug_dir=memory_cfg["agent_local"]["path"] if memory_cfg["agent_local"].get("persist_debug_copy") else None
    )
    artifact_store = ArtifactStore(root=memory_cfg["artifact"]["path"])
    event_bus = EventBus()
    event_bus.subscribe(lambda msg: print(f"[{msg['event']:<20}] {msg['from_agent']:<22} -> "
                                          f"{msg['to_agent']:<22} ({msg['task_id']})"))

    agents = load_agents("configs/agents.yaml", llm)
    dag = DAGEngine(workflow_cfg)

    scheduler = Scheduler(
        dag=dag,
        agents=agents,
        artifact_store=artifact_store,
        global_memory=global_memory,
        agent_memory=agent_memory,
        event_bus=event_bus,
        max_parallel_branches=orchestrator_cfg["max_parallel_branches"],
        auto_approve=args.auto_approve or args.dry_run,
    )

    print(f"\nStarting workflow: {workflow_cfg['name']} "
          f"({'DRY RUN' if args.dry_run else 'LIVE'}, provider={llm.provider}, model={llm.model})\n")

    final_status = scheduler.run()

    print("\n" + "=" * 70)
    print("FINAL TASK STATUS")
    print("=" * 70)
    for task_id, status in final_status.items():
        print(f"  {task_id:<25} {status}")

    failed = [t for t, s in final_status.items() if s in ("ESCALATED", "REJECTED", "BLOCKED")]
    if failed:
        print(f"\n{len(failed)} task(s) did not complete cleanly: {failed}")
        print("See logs/escalations.log for details.")
        sys.exit(2)

    print("\nAll tasks validated. Artifacts written under artifacts/, "
          "generated prompts under prompts/generated/.")


if __name__ == "__main__":
    main()
