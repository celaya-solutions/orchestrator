from __future__ import annotations

import json
import sys
from typing import Any, Dict

from ..logging_config import RalphLogger
from .base import Agent, AgentRequest, AgentResponse
from .ollama_client import OllamaJSONClient


class SentinelAgent(Agent):
    name = "Sentinel"

    def __init__(self, model: str = "gemma:2b", timeout: int = 120) -> None:
        self.client = OllamaJSONClient(model=model, timeout=timeout, max_attempts=1)
        self.logger = RalphLogger.get_logger(RalphLogger.AGENT_SENTINEL)

    def _build_prompt(self, request: AgentRequest) -> str:
        contract = {
            "agent": self.name,
            "decision": "string",
            "confidence": "float 0-1",
            "notes": "string",
            "escalate": "boolean",
        }
        instruction = {
            "contract": contract,
            "rules": [
                "Respond with a single JSON object only.",
                "Use the exact keys: agent, decision, confidence, notes, escalate.",
                "Set agent to Sentinel.",
                "If task exceeds scope or data is missing, set escalate to true.",
                "Prefer abstaining over guessing; never invent data.",
                "Deterministic tone, no extra text.",
            ],
            "input": json.loads(request.to_json()),
        }
        return json.dumps(instruction, separators=(",", ":"), sort_keys=True)

    def run(self, request: AgentRequest) -> AgentResponse:
        prompt = self._build_prompt(request)
        self.logger.info("Sentinel input: %s", request.to_json())
        raw = self.client.run_json(prompt)
        if not isinstance(raw, dict):
            raise RuntimeError("Model output must be a JSON object")

        # Coerce and validate according to contract
        payload: Dict[str, Any] = {
            "agent": raw.get("agent"),
            "decision": raw.get("decision"),
            "confidence": raw.get("confidence"),
            "notes": raw.get("notes", ""),
            "escalate": raw.get("escalate"),
        }
        response = AgentResponse(
            agent=str(payload["agent"]),
            decision=str(payload["decision"]),
            confidence=float(payload["confidence"]),
            notes=str(payload.get("notes", "")),
            escalate=bool(payload["escalate"]),
        )
        response.validate(expected_agent=self.name)
        self.logger.info("Sentinel output: %s", response.to_json())
        return response


def main() -> None:
    data = sys.stdin.read()
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON input: {exc}") from exc

    try:
        request = AgentRequest.from_mapping(payload)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    agent = SentinelAgent()
    try:
        response = agent.run(request)
    except Exception as exc:
        error_payload = {
            "agent": agent.name,
            "decision": "error",
            "confidence": 0.0,
            "notes": str(exc),
            "escalate": True,
        }
        print(json.dumps(error_payload, separators=(",", ":"), sort_keys=True))
        return

    print(response.to_json())


if __name__ == "__main__":
    main()
