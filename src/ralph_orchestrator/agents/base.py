from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict


def _ensure_dict(value: Any, field: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    return value


@dataclass(frozen=True)
class AgentRequest:
    agent_name: str
    task: str
    context: Dict[str, Any]
    permissions: Dict[str, Any]

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any]) -> "AgentRequest":
        if not isinstance(payload, dict):
            raise ValueError("Request payload must be a JSON object")

        agent_name = payload.get("agent_name")
        task = payload.get("task")
        if not isinstance(agent_name, str) or not agent_name.strip():
            raise ValueError("agent_name must be a non-empty string")
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task must be a non-empty string")

        context = _ensure_dict(payload.get("context"), "context")
        permissions = _ensure_dict(payload.get("permissions"), "permissions")

        return cls(agent_name=agent_name, task=task, context=context, permissions=permissions)

    def to_json(self) -> str:
        return json.dumps(
            {
                "agent_name": self.agent_name,
                "task": self.task,
                "context": self.context,
                "permissions": self.permissions,
            },
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(frozen=True)
class AgentResponse:
    agent: str
    decision: str
    confidence: float
    notes: str
    escalate: bool

    def validate(self, expected_agent: str | None = None) -> None:
        if expected_agent and self.agent != expected_agent:
            raise ValueError(f"agent must be '{expected_agent}'")
        if not isinstance(self.decision, str) or not self.decision.strip():
            raise ValueError("decision must be a non-empty string")
        if not isinstance(self.notes, str):
            raise ValueError("notes must be a string")
        if not isinstance(self.confidence, (int, float)) or not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be a float between 0.0 and 1.0")
        if not isinstance(self.escalate, bool):
            raise ValueError("escalate must be a boolean")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent": self.agent,
            "decision": self.decision,
            "confidence": float(self.confidence),
            "notes": self.notes,
            "escalate": self.escalate,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_mapping(cls, payload: Dict[str, Any], expected_agent: str | None = None) -> "AgentResponse":
        if not isinstance(payload, dict):
            raise ValueError("Response payload must be a JSON object")
        required = ["agent", "decision", "confidence", "notes", "escalate"]
        for key in required:
            if key not in payload:
                raise ValueError(f"Missing '{key}' in response")
        resp = cls(
            agent=str(payload["agent"]),
            decision=str(payload["decision"]),
            confidence=float(payload["confidence"]),
            notes=str(payload["notes"]),
            escalate=bool(payload["escalate"]),
        )
        resp.validate(expected_agent=expected_agent)
        return resp


class Agent(ABC):
    """Minimal base class for stateless agents."""

    name: str

    @abstractmethod
    def run(self, request: AgentRequest) -> AgentResponse:  # pragma: no cover - interface
        ...
