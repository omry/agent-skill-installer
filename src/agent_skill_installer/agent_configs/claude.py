from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .common import AgentInstructions


@dataclass
class ClaudeRequires:
    claude_code: str | None = None


@dataclass
class ClaudeHook:
    type: str = "command"
    command: str | None = None
    timeout: int | None = None
    url: str | None = None
    prompt: str | None = None
    tool: str | None = None
    args: dict[str, Any] | None = None


@dataclass
class ClaudeHookMatcher:
    matcher: str | None = None
    hooks: list[ClaudeHook] = field(default_factory=list)


ClaudeHooks = dict[str, list[ClaudeHookMatcher]]


@dataclass
class ClaudeAgentConfig:
    version: int = 1
    requires: ClaudeRequires = field(default_factory=ClaudeRequires)
    instructions: AgentInstructions | None = None
    hooks: ClaudeHooks = field(default_factory=dict)
    hooks_direct: dict[str, Any] = field(default_factory=dict)


def materialize_claude_hooks(config: ClaudeAgentConfig) -> None:
    config.hooks = {
        event: [
            group
            if isinstance(group, ClaudeHookMatcher)
            else ClaudeHookMatcher(
                matcher=group.get("matcher"),
                hooks=[
                    hook if isinstance(hook, ClaudeHook) else ClaudeHook(**hook)
                    for hook in group.get("hooks", [])
                ],
            )
            for group in groups
        ]
        for event, groups in config.hooks.items()
    }
