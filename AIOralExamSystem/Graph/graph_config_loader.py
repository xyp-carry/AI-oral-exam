from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from AIOralExamSystem.Agent.General_Agent import GeneralAgent
from AIOralExamSystem.Agent.QuestionSetter import QuestionSetterAgent
from AIOralExamSystem.Graph.Base_graph import BaseGraph, Edge, NodeFunction, NodeId


AgentClass = type
AgentRegistry = dict[str, AgentClass]


AGENT_REGISTRY: AgentRegistry = {
    "general": GeneralAgent,
    "question_setter": QuestionSetterAgent,
}


def register_agent(name: str, agent_class: AgentClass) -> None:
    agent_name = str(name or "").strip()
    if not agent_name:
        raise ValueError("agent registry name is required")
    AGENT_REGISTRY[agent_name] = agent_class


class GraphConfigLoader:
    """Build BaseGraph from a config and an agent registry.

    Config example:
        {
            "edges": [("module", "question")],
            "nodes": {
                "question": {
                    "type": "agent",
                    "agent": "question_setter",
                    "init": {"document_scope": "/root/AI-Oral-exam/docs"},
                    "execute": {
                        "module_name": "$data.module_name",
                        "module_content": "$data.module_content",
                        "document_refs": "$data.document_refs",
                    },
                }
            },
        }
    """

    def __init__(
        self,
        config: Mapping[str, Any],
        model_settings: Mapping[str, Any] | None = None,
        agent_registry: Mapping[str, AgentClass] | None = None,
        node_functions: Mapping[NodeId, NodeFunction] | None = None,
    ):
        self.config = dict(config or {})
        self.model_settings = dict(model_settings or {})
        self.agent_registry = dict(agent_registry or AGENT_REGISTRY)
        self.node_functions = dict(node_functions or {})

    def build(self) -> BaseGraph:
        edges = self.load_edges()
        node_functions = self.build_node_functions()
        return BaseGraph(edges, node_functions)

    def load_edges(self) -> list[Edge]:
        raw_edges = self.config.get("edges") or self.config.get("graph_list") or []
        edges: list[Edge] = []
        for item in raw_edges:
            if not isinstance(item, Sequence) or isinstance(item, (str, bytes)) or len(item) != 2:
                raise ValueError(f"Graph edge must contain exactly 2 nodes: {item!r}")
            edges.append((item[0], item[1]))
        return edges

    def build_node_functions(self) -> dict[NodeId, NodeFunction]:
        node_functions: dict[NodeId, NodeFunction] = dict(self.node_functions)
        node_configs = self.config.get("nodes") or {}
        if not isinstance(node_configs, Mapping):
            raise ValueError("config['nodes'] must be a mapping")

        for node_id, node_config in node_configs.items():
            if node_id in node_functions:
                continue
            if not isinstance(node_config, Mapping):
                raise ValueError(f"Node config for {node_id!r} must be a mapping")
            node_functions[node_id] = self.build_node_function(node_id, node_config)

        return node_functions

    def build_node_function(self, node_id: NodeId, node_config: Mapping[str, Any]) -> NodeFunction:
        node_type = str(node_config.get("type") or "agent").strip()
        if node_type == "agent":
            return self.build_agent_node(node_id, node_config)
        raise ValueError(f"Unsupported node type for {node_id!r}: {node_type!r}")

    def build_agent_node(self, node_id: NodeId, node_config: Mapping[str, Any]) -> NodeFunction:
        agent_name = str(node_config.get("agent") or "").strip()
        if not agent_name:
            raise ValueError(f"Agent node {node_id!r} requires an agent name")
        if agent_name not in self.agent_registry:
            raise ValueError(f"Unknown agent {agent_name!r}; registered agents: {sorted(self.agent_registry)}")

        agent_class = self.agent_registry[agent_name]
        init_kwargs = self.resolve_static_mapping(node_config.get("init") or {})
        agent_model_settings = self.build_agent_model_settings(node_config)
        agent = agent_class(model_settings=agent_model_settings, **init_kwargs)
        execute_mapping = node_config.get("execute") or {}
        if not isinstance(execute_mapping, Mapping):
            raise ValueError(f"Agent node {node_id!r} execute config must be a mapping")

        async def node(state: dict[str, Any]) -> dict[str, Any]:
            kwargs = self.resolve_state_mapping(execute_mapping, state)
            result = await agent.execute(**kwargs)
            return {"data": result}

        return node

    def build_agent_model_settings(self, node_config: Mapping[str, Any]) -> dict[str, Any]:
        node_model_settings = node_config.get("model_settings") or {}
        if not isinstance(node_model_settings, Mapping):
            raise ValueError("node model_settings must be a mapping")
        model_settings = dict(self.model_settings)
        model_settings.update(node_model_settings)
        return model_settings

    def resolve_static_mapping(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: self.resolve_static_mapping(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve_static_mapping(item) for item in value]
        return value

    def resolve_state_mapping(self, value: Any, state: Mapping[str, Any]) -> Any:
        if isinstance(value, str) and value.startswith("$"):
            return self.resolve_state_path(value, state)
        if isinstance(value, Mapping):
            return {key: self.resolve_state_mapping(item, state) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve_state_mapping(item, state) for item in value]
        return value

    def resolve_state_path(self, expression: str, state: Mapping[str, Any]) -> Any:
        path = expression[1:].strip()
        if not path:
            raise ValueError("empty state expression is not allowed")
        if path == "state":
            return dict(state)
        if path == "node_id":
            return state.get("node_id")

        parts = path.split(".")
        if parts[0] == "state":
            current: Any = state
            parts = parts[1:]
        elif parts[0] == "data":
            current = state.get("data")
            parts = parts[1:]
        else:
            current = state.get(parts[0])
            parts = parts[1:]

        for part in parts:
            current = self.resolve_path_part(current, part, expression)
        return current

    @staticmethod
    def resolve_path_part(current: Any, part: str, expression: str) -> Any:
        if isinstance(current, Mapping):
            if part not in current:
                raise KeyError(f"Path {expression!r} missing key {part!r}")
            return current[part]
        if isinstance(current, list):
            try:
                return current[int(part)]
            except (ValueError, IndexError) as exc:
                raise KeyError(f"Path {expression!r} has invalid list index {part!r}") from exc
        if hasattr(current, part):
            return getattr(current, part)
        raise KeyError(f"Path {expression!r} cannot resolve part {part!r}")


def build_graph_from_config(
    config: Mapping[str, Any],
    model_settings: Mapping[str, Any] | None = None,
    agent_registry: Mapping[str, AgentClass] | None = None,
    node_functions: Mapping[NodeId, NodeFunction] | None = None,
) -> BaseGraph:
    return GraphConfigLoader(
        config=config,
        model_settings=model_settings,
        agent_registry=agent_registry,
        node_functions=node_functions,
    ).build()