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


@dataclass
class AgentConfigs:
    codex: CodexAgentConfig | None = None
    claude: ClaudeAgentConfig | None = None


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
    "materialize_claude_hooks",
    "materialize_codex_hooks",
]
