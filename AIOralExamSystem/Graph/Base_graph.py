from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable, Hashable, Iterable, Mapping, Sequence
from typing import Annotated, Any, TypedDict


NodeId = Hashable
Edge = tuple[NodeId, NodeId]
NodeFunction = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
FINISH_NODE = "__finish__"


def merge_node_outputs(
    left: Mapping[NodeId, Any] | None,
    right: Mapping[NodeId, Any] | None,
) -> dict[NodeId, Any]:
    merged: dict[NodeId, Any] = {}
    if left:
        merged.update(left)
    if right:
        merged.update(right)
    return merged


def merge_context_events(
    left: list[dict[str, Any]] | None,
    right: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    return [*(left or []), *(right or [])]


class BaseGraphState(TypedDict, total=False):
    data: Any
    node_outputs: Annotated[dict[NodeId, Any], merge_node_outputs]
    context_events: Annotated[list[dict[str, Any]], merge_context_events]


class ParsedGraph(TypedDict):
    nodes: list[NodeId]
    edges: list[Edge]
    incoming: dict[NodeId, list[NodeId]]
    outgoing: dict[NodeId, list[NodeId]]
    entry_nodes: list[NodeId]
    terminal_nodes: list[NodeId]
    layers: list[list[NodeId]]


class BaseGraph:
    """LangGraph base graph built from an external edge list.

    External graph data format:
        [("A", "B"), ["A", "C"], ("B", "D"), ("C", "D")]

    Node functions are provided from the outside. The node input state always
    contains data:
    - indegree == 0: initial state data.
    - indegree == 1: one upstream data value.
    - indegree > 1: a list of upstream data values in edge-list order.
    """

    def __init__(
        self,
        graph_list: Iterable[Sequence[NodeId]],
        node_functions: Mapping[NodeId, NodeFunction] | None = None,
        *,
        nodes: Iterable[NodeId] | None = None,
    ):
        self.parsed_graph = self.parse_graph_list(graph_list, nodes=nodes)
        self.nodes = self.parsed_graph["nodes"]
        self.edges = self.parsed_graph["edges"]
        self.incoming = self.parsed_graph["incoming"]
        self.outgoing = self.parsed_graph["outgoing"]
        self.entry_nodes = self.parsed_graph["entry_nodes"]
        self.terminal_nodes = self.parsed_graph["terminal_nodes"]
        self.layers = self.parsed_graph["layers"]
        self.graph_nodes: dict[NodeId, NodeFunction] = {}
        self.node_names = self._build_node_names()
        self._compiled_graph = None

        for node_id, node_function in (node_functions or {}).items():
            self.add_node(node_id, node_function)

    @staticmethod
    def parse_graph_list(
        graph_list: Iterable[Sequence[NodeId]],
        *,
        nodes: Iterable[NodeId] | None = None,
    ) -> ParsedGraph:
        edges: list[Edge] = []
        parsed_nodes: list[NodeId] = []
        seen_nodes: set[NodeId] = set()
        seen_edges: set[Edge] = set()

        for node_id in nodes or ():
            if node_id in seen_nodes:
                raise ValueError(f"Duplicate node: {node_id!r}")
            parsed_nodes.append(node_id)
            seen_nodes.add(node_id)

        for item in graph_list:
            if len(item) != 2:
                raise ValueError(f"Graph edge must contain exactly 2 nodes: {item!r}")
            source, target = item[0], item[1]
            if source == target:
                raise ValueError(f"Self-loop is not allowed: {item!r}")
            if (source, target) in seen_edges:
                raise ValueError(f"Duplicate edge: {item!r}")
            seen_edges.add((source, target))
            edges.append((source, target))

            for node_id in (source, target):
                if node_id not in seen_nodes:
                    parsed_nodes.append(node_id)
                    seen_nodes.add(node_id)

        if not parsed_nodes:
            raise ValueError("graph must contain at least one node.")

        incoming: dict[NodeId, list[NodeId]] = {node_id: [] for node_id in parsed_nodes}
        outgoing: dict[NodeId, list[NodeId]] = {node_id: [] for node_id in parsed_nodes}
        for source, target in edges:
            incoming[target].append(source)
            outgoing[source].append(target)

        entry_nodes = [node_id for node_id in parsed_nodes if not incoming[node_id]]
        terminal_nodes = [node_id for node_id in parsed_nodes if not outgoing[node_id]]
        layers = BaseGraph._build_layers(parsed_nodes, incoming, outgoing)

        return {
            "nodes": parsed_nodes,
            "edges": edges,
            "incoming": incoming,
            "outgoing": outgoing,
            "entry_nodes": entry_nodes,
            "terminal_nodes": terminal_nodes,
            "layers": layers,
        }

    def add_node(self, node_id: NodeId, node_function: NodeFunction) -> None:
        if node_id not in self.nodes:
            raise ValueError(f"Node {node_id!r} is not declared by graph_list.")
        self.graph_nodes[node_id] = node_function
        self._compiled_graph = None

    async def arun(
        self, state: Mapping[str, Any] | Any = None, *, include_state: bool = False
    ) -> dict[str, Any]:
        input_state: BaseGraphState
        if isinstance(state, Mapping):
            input_state = dict(state)
        else:
            input_state = {"data": state}

        graph = self.compile()
        output_state = await graph.ainvoke(input_state)
        if include_state:
            return dict(output_state)
        return {"data": output_state.get("data")}

    def compile(self):
        self._validate_node_functions()
        if self._compiled_graph is None:
            self._compiled_graph = self.build_graph()
        return self._compiled_graph

    def build_graph(self):
        from langgraph.graph import END, START, StateGraph

        graph = StateGraph(BaseGraphState)

        for node_id in self.nodes:
            graph.add_node(self.node_names[node_id], self._make_node(node_id))

        graph.add_node(FINISH_NODE, self.finish_node)

        for entry_node in self.entry_nodes:
            graph.add_edge(START, self.node_names[entry_node])

        for node_id in self.nodes:
            parents = self.incoming[node_id]
            if not parents:
                continue
            if len(parents) == 1:
                graph.add_edge(self.node_names[parents[0]], self.node_names[node_id])
            else:
                graph.add_edge([self.node_names[parent] for parent in parents], self.node_names[node_id])

        if len(self.terminal_nodes) == 1:
            graph.add_edge(self.node_names[self.terminal_nodes[0]], FINISH_NODE)
        else:
            graph.add_edge([self.node_names[node_id] for node_id in self.terminal_nodes], FINISH_NODE)

        graph.add_edge(FINISH_NODE, END)
        return graph.compile()

    async def finish_node(self, state: BaseGraphState) -> BaseGraphState:
        node_outputs = state.get("node_outputs") or {}
        terminal_data = [node_outputs[node_id] for node_id in self.terminal_nodes]
        if len(terminal_data) == 1:
            return {"data": terminal_data[0]}
        return {"data": terminal_data}

    def _make_node(self, node_id: NodeId) -> NodeFunction:
        async def graph_node(state: BaseGraphState) -> BaseGraphState:
            node_state = self._build_node_state(node_id, state)
            result = self.graph_nodes[node_id](node_state)
            if not hasattr(result, "__await__"):
                raise TypeError("BaseGraph only supports async node functions.")

            output_state = await result
            if not isinstance(output_state, dict):
                raise TypeError("Node function must return a dict, for example: {'data': state}.")

            update: BaseGraphState = {
                "node_outputs": {node_id: self._extract_data(output_state)}
            }
            if "context_events" in output_state:
                update["context_events"] = list(output_state["context_events"])
            return update

        return graph_node

    def _build_node_state(self, node_id: NodeId, state: BaseGraphState) -> dict[str, Any]:
        node_state = dict(state)
        node_outputs = state.get("node_outputs") or {}
        parents = self.incoming[node_id]

        if not parents:
            node_state["data"] = state.get("data")
        elif len(parents) == 1:
            node_state["data"] = node_outputs[parents[0]]
        else:
            node_state["data"] = [node_outputs[parent] for parent in parents]

        node_state["upstream_outputs"] = {
            parent: node_outputs[parent] for parent in parents
        }
        node_state["node_id"] = node_id
        return node_state

    def _validate_node_functions(self) -> None:
        missing_nodes = [node_id for node_id in self.nodes if node_id not in self.graph_nodes]
        if missing_nodes:
            raise ValueError(f"Missing node functions: {missing_nodes!r}")

    def _build_node_names(self) -> dict[NodeId, str]:
        node_names: dict[NodeId, str] = {}
        used_names: set[str] = {FINISH_NODE}
        for node_id in self.nodes:
            node_name = str(node_id)
            if node_name in used_names:
                raise ValueError(f"Duplicate LangGraph node name: {node_name!r}")
            node_names[node_id] = node_name
            used_names.add(node_name)
        return node_names

    @staticmethod
    def _extract_data(output_state: Mapping[str, Any]) -> Any:
        if "data" in output_state:
            return output_state["data"]
        return dict(output_state)

    @staticmethod
    def _build_layers(
        nodes: Sequence[NodeId],
        incoming: Mapping[NodeId, Sequence[NodeId]],
        outgoing: Mapping[NodeId, Sequence[NodeId]],
    ) -> list[list[NodeId]]:
        indegree = {node_id: len(incoming[node_id]) for node_id in nodes}
        ready = deque(node_id for node_id in nodes if indegree[node_id] == 0)
        layers: list[list[NodeId]] = []
        visited_count = 0

        while ready:
            layer = list(ready)
            ready.clear()
            layers.append(layer)
            visited_count += len(layer)

            for node_id in layer:
                for child in outgoing[node_id]:
                    indegree[child] -= 1
                    if indegree[child] == 0:
                        ready.append(child)

        if visited_count != len(nodes):
            raise ValueError("Graph contains a cycle.")
        return layers
    
