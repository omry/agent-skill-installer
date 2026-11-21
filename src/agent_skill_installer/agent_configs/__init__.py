from __future__ import annotations

from dataclasses import dataclass

from .claude import (
    ClaudeAgentConfig,
    ClaudeHook,
    ClaudeHookMatcher,
    ClaudeHooks,
    ClaudeRequires,
    materialize_claude_hooks,
)
from .codex import (
    CodexAgentConfig,
    CodexHookMatcher,
    CodexHooks,
    CodexRequires,
    CodexSkillCommandHook,
    materialize_codex_hooks,
)
from .common import AgentInstructions
from .gemini import (
    GeminiAgentConfig,
    GeminiHookMatcher,
    GeminiHooks,
    GeminiRequires,
    GeminiSkillCommandHook,
    materialize_gemini_hooks,
)


@dataclass
class AgentConfigs:
    codex: CodexAgentConfig | None = None
    claude: ClaudeAgentConfig | None = None
    gemini: GeminiAgentConfig | None = None


__all__ = [
    "AgentConfigs",
    "AgentInstructions",
    "ClaudeAgentConfig",
    "ClaudeHook",
    "ClaudeHookMatcher",
    "ClaudeHooks",
    "ClaudeRequires",
    "CodexAgentConfig",
    "CodexHookMatcher",
    "CodexHooks",
    "CodexRequires",
    "CodexSkillCommandHook",
    "GeminiAgentConfig",
    "GeminiHookMatcher",
    "GeminiHooks",
    "GeminiRequires",
    "GeminiSkillCommandHook",
    "materialize_claude_hooks",
    "materialize_codex_hooks",
    "materialize_gemini_hooks",
]
