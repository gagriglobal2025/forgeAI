"""Loads configs/agents.yaml and instantiates one BaseAgent per entry."""
import yaml

from agents.base_agent import BaseAgent


def load_agents(agents_yaml_path: str, llm_client) -> dict[str, BaseAgent]:
    with open(agents_yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    agents = {}
    for config in data["agents"]:
        agent = BaseAgent(config, llm_client)
        agents[agent.name] = agent
    return agents
