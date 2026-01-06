"""Minimal agent framework components."""

from .base import Agent, AgentRequest, AgentResponse
from .ollama_client import OllamaJSONClient
from .sentinel import SentinelAgent, main

__all__ = [
    "Agent",
    "AgentRequest",
    "AgentResponse",
    "OllamaJSONClient",
    "SentinelAgent",
    "main",
]
