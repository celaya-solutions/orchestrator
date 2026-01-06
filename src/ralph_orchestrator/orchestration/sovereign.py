from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence

from ..agents.base import AgentRequest, AgentResponse
from ..agents.sentinel import SentinelAgent
from ..logging_config import RalphLogger

MergeStrategy = str
AgentExecutor = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]] | Dict[str, Any]]


@dataclass(frozen=True)
class AgentInfo:
    name: str
    role: str
    permissions: Dict[str, Any]

    @classmethod
    def from_mapping(cls, data: Dict[str, Any]) -> "AgentInfo":
        if not isinstance(data, dict):
            raise ValueError("available_agents entries must be objects")
        name = data.get("name")
        role = data.get("role", "")
        permissions = data.get("permissions") or {}
        if not isinstance(name, str) or not name.strip():
            raise ValueError("agent name must be a non-empty string")
        if not isinstance(role, str):
            raise ValueError("agent role must be a string")
        if not isinstance(permissions, dict):
            raise ValueError("permissions must be an object")
        return cls(name=name, role=role, permissions=permissions)


@dataclass(frozen=True)
class ExecutionPlanItem:
    agent: str
    task: str
    priority: int
    expected_output: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent": self.agent,
            "task": self.task,
            "priority": int(self.priority),
            "expected_output": self.expected_output,
        }


@dataclass(frozen=True)
class OrchestratorRequest:
    request_id: str
    intent: str
    context: Dict[str, Any]
    available_agents: List[AgentInfo]
    constraints: Dict[str, Any]

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "OrchestratorRequest":
        if not isinstance(payload, dict):
            raise ValueError("Input must be a JSON object")
        request_id = payload.get("request_id")
        intent = payload.get("intent")
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request_id must be a non-empty string")
        if not isinstance(intent, str) or not intent.strip():
            raise ValueError("intent must be a non-empty string")
        context = payload.get("context") or {}
        constraints = payload.get("constraints") or {}
        if not isinstance(context, dict):
            raise ValueError("context must be a JSON object")
        if not isinstance(constraints, dict):
            raise ValueError("constraints must be a JSON object")
        raw_agents = payload.get("available_agents") or []
        if not isinstance(raw_agents, list):
            raise ValueError("available_agents must be an array")
        agents = [AgentInfo.from_mapping(item) for item in raw_agents]
        return cls(
            request_id=request_id,
            intent=intent,
            context=context,
            available_agents=agents,
            constraints=constraints,
        )


class SovereignOrchestrator:
    """Deterministic orchestrator that plans and dispatches without domain reasoning."""

    allowed_merge_strategies: Sequence[MergeStrategy] = ("compare", "vote", "rank", "summarize")

    def __init__(
        self,
        agent_registry: Optional[Mapping[str, AgentExecutor]] = None,
        parallel_default: bool = True,
        merge_strategy_default: MergeStrategy = "compare",
    ) -> None:
        self.logger = RalphLogger.get_logger("ralph.orchestrator.sovereign")
        self.parallel_default = parallel_default
        self.merge_strategy_default = merge_strategy_default
        if agent_registry is None:
            sentinel = SentinelAgent()

            async def _sentinel_exec(payload: Dict[str, Any]) -> Dict[str, Any]:
                request = AgentRequest.from_mapping(payload)
                response = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: sentinel.run(request)
                )
                return response.to_dict()

            self.agent_registry: Dict[str, AgentExecutor] = {"Sentinel": _sentinel_exec}
        else:
            self.agent_registry = dict(agent_registry)

    def _segment_intent(self, intent: str) -> List[str]:
        parts = re.split(r"[.;!?]+|\band\b", intent)
        tasks = [part.strip() for part in parts if part.strip()]
        return tasks or [intent.strip()]

    def _choose_merge_strategy(self, constraints: Dict[str, Any], parallel: bool) -> MergeStrategy:
        requested = constraints.get("merge_strategy")
        if isinstance(requested, str) and requested in self.allowed_merge_strategies:
            return requested
        if parallel:
            return "compare"
        return self.merge_strategy_default

    def _build_plan(self, request: OrchestratorRequest) -> Dict[str, Any]:
        tasks = self._segment_intent(request.intent)
        agents = request.available_agents

        plan_items: List[ExecutionPlanItem] = []
        escalation_required = False

        if not agents:
            escalation_required = True
        else:
            for idx, task in enumerate(tasks):
                agent = agents[idx % len(agents)]
                expected_output = f"{agent.role or agent.name} deliverable for '{task}'"
                plan_items.append(
                    ExecutionPlanItem(
                        agent=agent.name,
                        task=task,
                        priority=min(5, 1 + idx),
                        expected_output=expected_output,
                    )
                )

        parallel_flag = bool(request.constraints.get("parallel", self.parallel_default)) and len(
            plan_items
        ) > 1
        merge_strategy = self._choose_merge_strategy(request.constraints, parallel_flag)

        plan_dicts = [item.to_dict() for item in plan_items]
        result = {
            "request_id": request.request_id,
            "plan": plan_dicts,
            "parallel": parallel_flag,
            "merge_strategy": merge_strategy,
            "escalation_required": escalation_required or not plan_items,
            "notes": "deterministic routing; no agent overlap",
        }
        self.logger.info("Plan generated: %s", json.dumps(result, separators=(",", ":")))
        return result

    async def _dispatch_step(
        self,
        step: ExecutionPlanItem,
        request: OrchestratorRequest,
        agents_by_name: Dict[str, AgentInfo],
    ) -> Dict[str, Any]:
        agent_info = agents_by_name.get(step.agent)
        if agent_info is None:
            return {"agent": step.agent, "error": "agent not available"}
        executor = self.agent_registry.get(step.agent)
        if executor is None:
            return {"agent": step.agent, "error": "agent not registered"}
        payload = {
            "agent_name": agent_info.name,
            "task": step.task,
            "context": request.context,
            "permissions": agent_info.permissions,
        }
        try:
            result = executor(payload)
            if asyncio.iscoroutine(result):
                return await result
            return result  # type: ignore[return-value]
        except Exception as exc:
            return {"agent": step.agent, "error": str(exc)}

    async def _dispatch_plan(
        self, request: OrchestratorRequest, plan: List[ExecutionPlanItem], parallel: bool
    ) -> List[Dict[str, Any]]:
        agents_by_name = {agent.name: agent for agent in request.available_agents}
        if not plan:
            return []
        if parallel:
            return await asyncio.gather(
                *[self._dispatch_step(step, request, agents_by_name) for step in plan]
            )
        results: List[Dict[str, Any]] = []
        for step in plan:
            results.append(await self._dispatch_step(step, request, agents_by_name))
        return results

    def _merge_responses(
        self, responses: List[Dict[str, Any]], merge_strategy: MergeStrategy
    ) -> Dict[str, Any]:
        errors = [resp for resp in responses if "error" in resp]
        merged = {
            "merge_strategy": merge_strategy,
            "responses": responses,
            "errors": errors,
        }
        self.logger.info("Merge result: %s", json.dumps(merged, separators=(",", ":")))
        return merged

    async def handle(self, request: OrchestratorRequest) -> Dict[str, Any]:
        plan_blob = self._build_plan(request)
        plan_items = [
            ExecutionPlanItem(
                agent=item["agent"],
                task=item["task"],
                priority=item["priority"],
                expected_output=item["expected_output"],
            )
            for item in plan_blob["plan"]
        ]

        responses: List[Dict[str, Any]] = []
        if plan_items:
            responses = await self._dispatch_plan(request, plan_items, plan_blob["parallel"])
            self.logger.info("Agent responses: %s", json.dumps(responses, separators=(",", ":")))

        merge_info = self._merge_responses(responses, plan_blob["merge_strategy"])
        escalation = plan_blob["escalation_required"] or any("error" in resp for resp in responses)

        output = {
            "request_id": plan_blob["request_id"],
            "plan": plan_blob["plan"],
            "parallel": plan_blob["parallel"],
            "merge_strategy": plan_blob["merge_strategy"],
            "escalation_required": escalation,
            "notes": plan_blob["notes"],
        }
        # Do not include merge details in output schema; logs contain full record.
        return output


def main() -> None:
    data = sys.stdin.read()
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON input: {exc}") from exc

    try:
        request = OrchestratorRequest.from_mapping(payload)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    orchestrator = SovereignOrchestrator()
    result = asyncio.run(orchestrator.handle(request))
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
