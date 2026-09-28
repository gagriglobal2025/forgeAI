# Forge AI — Autonomous Multi-Agent SDLC Workforce (Runnable Implementation)

A working implementation of the [Forge AI multi-agent specification](.) — 20 config-driven
agents plus an Orchestrator, wired end to end through a real dependency DAG, calling an LLM API
to actually produce artifacts. Anthropic (Claude) and OpenAI are both supported as providers —
pick one with `--provider`, or set it once in `configs/orchestrator.yaml`.

This is a **generic scaffold**: `configs/project.yaml` holds `{{PLACEHOLDER}}` values by
default. Fill it in with a real project brief before a live run, or use `--dry-run` to verify
the wiring first without an API key.

## Quick start

```bash
pip install -r requirements.txt

# 1. Verify the DAG/orchestrator wiring with no API key and no cost:
python run.py --dry-run

# 2. Fill in a real project brief:
#    edit configs/project.yaml (see the commented example at the bottom of that file)

# 3. Run for real, with whichever provider you have credits/access on:
export ANTHROPIC_API_KEY=sk-ant-...      # PowerShell: $env:ANTHROPIC_API_KEY = "sk-ant-..."
python run.py                            # uses configs/orchestrator.yaml: default_provider

# or, to use OpenAI instead:
export OPENAI_API_KEY=sk-...             # PowerShell: $env:OPENAI_API_KEY = "sk-..."
python run.py --provider openai
```

### Switching providers

`configs/orchestrator.yaml` has `default_provider` (`anthropic` or `openai`) and a
`default_models` map with one entry per provider. `--provider` on the command line overrides the
config default for a single run; `--model` overrides the model within whichever provider is
active. `llm/client.py` only imports the SDK for the provider actually in use (lazy import), so
having both `anthropic` and `openai` in `requirements.txt` doesn't require both API keys — only
the one matching whichever provider you run with needs to be set.

`python run.py` will pause twice for a human approval gate (charter, and pre-deploy) — you'll
see the artifact printed and a `y/N` prompt. Pass `--auto-approve` to skip both gates.

## What actually runs

Every agent is one config entry in [`configs/agents.yaml`](configs/agents.yaml) plus the shared
[`agents/base_agent.py`](agents/base_agent.py) runtime — there is no per-agent code file. The
base agent:

1. Pulls the latest validated content for each artifact key it declares as an input.
2. Fills the master prompt template (`prompts/template.md`, Section 13.1 of the spec) from the
   agent's spec fields and calls Claude.
3. If the agent is a validating role (`verdict_required: true` — Accessibility, the QA tiers,
   Security), parses a trailing `VERDICT: PASS/FAIL` line; a FAIL consumes one retry and feeds
   the failure back into the next attempt's prompt.
4. Writes the output as a new immutable, versioned entry in the artifact store
   ([`memory/artifact_store.py`](memory/artifact_store.py)), with full lineage
   (`parent_artifact`, `dependencies`, checksum).
5. Emits `TASK_COMPLETED` / `ARTIFACT_PUBLISHED` (or `TASK_FAILED`) on the event bus.

The [`orchestrator/scheduler.py`](orchestrator/scheduler.py) dispatches ready tasks in parallel
(`ThreadPoolExecutor`, sized by `orchestrator.yaml: max_parallel_branches`), handles the two
human-approval gates declared in `workflow.yaml`, and escalates (logs to
`logs/escalations.log`, emits `ESCALATION_RAISED`) whenever an agent exhausts its retries.

## Task graph

The 20 agents run in the dependency order below (see [`configs/workflow.yaml`](configs/workflow.yaml)).
Frontend and backend branches run in parallel once the architecture is published:

```
ceo_agent (approval gate)
  -> product_agent -> architect_agent
       -> ui_agent -+                    -> database_agent
       -> ux_agent -+-> accessibility_agent -> frontend_lead --+
                                                                 -> qa_lead -> unit_test_agent
       -> business_logic_agent -> api_agent -----------------+  -> integration_agent -> e2e_agent
                                -> repository_agent -> backend_lead                     -> security_lead
                                                                                          -> devops_lead (approval gate)
                                                                                               -> documentation_agent
                                                                                                    -> project_manager_agent
```

## Installed skills (UI Agent, Accessibility Agent, Architect Agent, test-tier agents)

Four skills are installed via the [skills.sh](https://www.skills.sh) CLI, all symlinked into
`.claude/skills/` for this Claude Code session:

| Skill | Source | Wired to | Role |
|---|---|---|---|
| `frontend-design` | `anthropics/claude-code` (official) | `ui_agent` | Generator guidance — distinctive visual design (palette, type, layout, motion) |
| `web-design-guidelines` | `vercel-labs/agent-skills` (official) | `accessibility_agent` | Auditor ruleset — a11y, forms, focus states, animation, i18n, anti-patterns |
| `improve-codebase-architecture` | `mattpocock/skills` | `architect_agent` | Design philosophy — deep vs. shallow modules, seams, the deletion test |
| `tdd` | `mattpocock/skills` | `unit_test_agent`, `integration_agent` | Test-quality discipline — behavior over implementation, anti-patterns, mocking boundaries |

None of these installs affect the generated system by themselves: every Forge AI agent calls the
Anthropic API directly rather than going through Claude Code's Skill tool, so each skill's
guidance had to be inlined into a prompt explicitly via `configs/agents.yaml`'s
`skill_references` field, loaded by `agents/base_agent.py`'s `_load_skill_guidance()` (strips
YAML frontmatter and any leading HTML comment, then appends the rest under an `ADDITIONAL SKILL
GUIDANCE` section). How each one got there differs, because the three skills aren't the same
shape:

- **`ui_agent`** references `frontend-design/SKILL.md` directly — it's a generator skill and
  its prose *is* the guidance to follow.
- **`accessibility_agent`** references `web-design-guidelines/GUIDELINES.md`, a vendored
  snapshot, not the installed `SKILL.md`. That skill's real rules live at a remote URL
  (`vercel-labs/web-interface-guidelines`) it instructs a live `WebFetch` for on every
  invocation — Forge AI agents have no tool-call loop, so the fetch was done once by hand and
  the result vendored locally. Re-fetch and replace that file periodically for rule updates.
- **`architect_agent`** references `improve-codebase-architecture/GUIDANCE.md`, a *distillation*,
  not a vendored copy. The installed skill (`disable-model-invocation: true`) is a full
  Claude-Code workflow — it spawns an Explore subagent, mines `git log` for hot spots, writes and
  opens an HTML report, then chains into sibling skills (`/grilling`, `/codebase-design`,
  `/domain-modeling`) for an interactive back-and-forth with a human over an existing, evolving
  codebase. None of that mechanism works inside a single-shot API completion with nothing built
  yet. What was extracted instead is the underlying design philosophy it judges architecture
  by — deep vs. shallow modules, the deletion test, seam/adapter/locality vocabulary — so
  `architect_agent` is held to the same standard when it first proposes the design, rather than
  having it reviewed after the fact.

- **`unit_test_agent`** and **`integration_agent`** both reference `tdd/GUIDANCE.md`, which
  merges the installed skill's `SKILL.md`, `tests.md`, and `mocking.md` — all static prose/
  examples, no tool calls, so nothing needed vendoring from elsewhere. One adaptation was still
  required: the installed skill says to confirm test seams with the user in conversation
  ("Ask: 'What's the public interface, and which seams should we test?'"); the distilled version
  instead has the agent read seams directly from the architecture artifact's
  `interface_contracts`, since there's no human turn to ask mid-task.

  **This does not make the pipeline test-first.** The skill's other core rule — "red before
  green," write the failing test before the implementation — conflicts with the DAG as built:
  `business_logic_agent`, `api_agent`, `repository_agent`, and `ui_agent` all run to completion
  before `unit_test_agent` runs (see `configs/workflow.yaml`). What's wired in is the part of the
  skill that still applies regardless of ordering: tests that verify behavior through public
  interfaces, avoid the tautological/implementation-coupled/horizontal-slicing anti-patterns, and
  mock only at real system boundaries. Making it genuinely test-first would mean inserting a
  test-authoring task before each implementation task per domain — that's a DAG restructuring,
  not a prompt change, and hasn't been done; ask if you want that instead.

All four are visible in `prompts/generated/{ui_agent,accessibility_agent,architect_agent,unit_test_agent,integration_agent}__*.md`
after any run — no other agent is affected. To apply a skill to a different agent, add its file
path to that agent's `skill_references` list in `configs/agents.yaml`. If the skill's real content
lives at a remote URL, or the skill is itself a multi-tool workflow rather than static guidance,
fetch/distill it into a plain guidance file the same way first — don't point `skill_references`
at a `SKILL.md` that assumes tool access Forge AI agents don't have.

## Design simplifications (read before extending)

- **Project Manager Agent** is specced (Section 3.2) as producing the task graph itself. Since
  `workflow.yaml` *is* that task graph — declared up front so `dag_engine.py` can reject cycles
  before anything runs — the PM agent's runtime role is narrowed to a closing status rollup that
  runs last and accounts for the whole executed run.
- **One artifact key per agent.** Each agent's full "Artifacts Produced" list from the spec is
  collapsed to a single primary markdown artifact for wiring simplicity. To let an agent emit
  multiple distinct artifacts, call `artifact_store.put()` more than once inside a custom
  subclass of `BaseAgent` and register that subclass in `agents/registry.py` instead of the
  generic one.
- **QA/Security validation is LLM-based review, not real test execution.** With no concrete tech
  stack wired in, the Unit/Integration/E2E/Security agents critique artifacts and emit a
  PASS/FAIL verdict rather than running `pytest`/SAST tools. The natural extension point is
  `BaseAgent.run()` in `agents/base_agent.py` — replace the `llm.complete()` call for those five
  agents with a real tool invocation once a tech stack is committed in `configs/project.yaml`.
- **Rework loop on approval denial is single-shot.** A denied approval gate re-runs the same
  agent once with the reviewer's notes; a second denial escalates rather than looping
  indefinitely (mirrors the spec's "cap rework cycles" mitigation in the Risk Analysis section).
- **No checkpoint/resume.** Every `python run.py` invocation restarts the whole DAG from
  `ceo_agent`, including the charter approval gate. There's no way to resume a partially-completed
  run — if you interrupt or fix something mid-pipeline, the next run redoes every task from
  scratch (at full API cost). Worth building if full-pipeline reruns get expensive.

## Rework loop (audit agents re-dispatching their own producers)

Some agents are auditors, not producers — `accessibility_agent` reviews `ui_agent`/`ux_agent`'s
output rather than writing UI code itself. When an auditor's own `verdict_required` gate returns
`FAIL` after exhausting `max_attempts`, two things now happen instead of an immediate escalation
(`orchestrator/scheduler.py`'s `_run_rework_cycle`, config via `rework_agents` /
`max_rework_cycles` in `configs/agents.yaml`):

1. **The failing findings are still persisted as a real artifact version** (`memory/artifact_store.py`'s
   `put()` now takes an optional `verdict` — `"FAIL"` is saved, not discarded), so a failed audit's
   findings are never silently lost, even if every rework cycle is eventually exhausted.
2. **The agents named in `rework_agents` get re-dispatched** with the failure content as
   `reviewer_notes` — the same mechanism the human-approval-denial path already used — then the
   auditor re-runs against their new output. This repeats up to `max_rework_cycles` (default 2);
   only after that cap is exhausted does it fall back to a real `ESCALATION_RAISED`.

Currently only `accessibility_agent` has `rework_agents` wired (`[ui_agent, ux_agent]`) — that was
the concrete case that surfaced this gap. The same mechanism applies to any `verdict_required`
agent; `unit_test_agent`/`integration_agent`/`e2e_agent`/`security_lead` don't have it wired yet
because their "producers" are less clear-cut (e.g. `integration_agent`'s inputs come from
`frontend_lead`/`backend_lead`, which are merge-only Leads with no code of their own to revise —
reworking them would need to cascade further down to their own sub-agents, which this mechanism
doesn't do). Extend `rework_agents` per agent in `configs/agents.yaml` once you've decided the
right producer mapping for each.

Verified with a standalone test using a fake LLM client (not part of the repo) that forces
`accessibility_agent` to fail on demand: confirmed both that a resolvable failure recovers after
one rework cycle and unblocks everything downstream, and that a permanent failure still escalates
cleanly after `max_rework_cycles` without looping forever.

## Directory map

```
configs/        agents.yaml, workflow.yaml, orchestrator.yaml, memory.yaml, permissions.yaml, project.yaml
llm/            client.py — Anthropic API wrapper (+ --dry-run stub path)
memory/         global_memory.py, agent_memory.py, artifact_store.py — the 3-tier memory model
orchestrator/   event_bus.py, dag_engine.py, scheduler.py
agents/         base_agent.py (shared runtime), registry.py (loads configs/agents.yaml)
prompts/        template.md (reference copy) + generated/ (every prompt actually sent, for audit)
artifacts/      versioned output of every agent, plus _index.json (the full lineage graph)
logs/           events.log (every bus message, JSONL) + escalations.log
```

## Extending to a real tech stack

1. Fill in `configs/project.yaml` with the real brief.
2. If you want agents to write real source files instead of markdown specs, change
   `output_key`'s consumer (e.g. `ui_agent`) to write into a real project checkout path and have
   `BaseAgent` (or a subclass) run the artifact content through a code-extraction step before
   `artifact_store.put()`.
3. Wire real tool calls (`test.run`, `sast.scan`, `deploy.execute`, etc.) in place of the LLM-only
   review for the QA/Security/DevOps agents — those are the five places in `configs/agents.yaml`
   where `verdict_required: true` or `requires_human_approval: true` marks the natural seam.
