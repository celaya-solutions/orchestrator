# ABOUTME: Tool adapter interfaces and implementations
# ABOUTME: Provides unified interface for Claude, Gemini, Ollama, ACP, and other tools

"""Tool adapters for Ralph Orchestrator."""

from .base import ToolAdapter, ToolResponse
from .claude import ClaudeAdapter
from .gemini import GeminiAdapter
from .acp import ACPAdapter
from .ollama import OllamaAdapter
from .acp_handlers import ACPHandlers, PermissionRequest, PermissionResult, Terminal

__all__ = [
    "ToolAdapter",
    "ToolResponse",
    "ClaudeAdapter",
    "GeminiAdapter",
    "OllamaAdapter",
    "ACPAdapter",
    "ACPHandlers",
    "PermissionRequest",
    "PermissionResult",
    "Terminal",
]
