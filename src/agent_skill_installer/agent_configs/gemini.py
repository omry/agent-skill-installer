from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .common import AgentInstructions


@dataclass
class GeminiRequires:
    gemini_cli: str | None = None


@dataclass
class GeminiSkillCommandHook:
    type: str = "skill-command"
    # Optional in the loader so install-time validation can report the full path.
    executable: str | None = None
    args: list[str] = field(default_factory=list)
    name: str | None = None
    timeout: int | None = None
    description: str | None = None
    # Parse legacy declarations only to produce an explicit migration error.
    command: str | None = None


@dataclass
class GeminiHookMatcher:
    matcher: str | None = None
    sequential: bool | None = None
    hooks: list[GeminiSkillCommandHook] = field(default_factory=list)


GeminiHooks = dict[str, list[GeminiHookMatcher]]


@dataclass
class GeminiAgentConfig:
    version: int = 1
    requires: GeminiRequires = field(default_factory=GeminiRequires)
    instructions: AgentInstructions | None = None
    hooks: GeminiHooks = field(default_factory=dict)
    hooks_direct: dict[str, Any] = field(default_factory=dict)


def materialize_gemini_hooks(config: GeminiAgentConfig) -> None:
    config.hooks = {
        event: [
            group
            if isinstance(group, GeminiHookMatcher)
            else GeminiHookMatcher(
                matcher=group.get("matcher"),
                sequential=group.get("sequential"),
                hooks=[
                    hook
                    if isinstance(hook, GeminiSkillCommandHook)
                    else GeminiSkillCommandHook(**hook)
                    for hook in group.get("hooks", [])
                ],
            )
            for group in groups
        ]
        for event, groups in config.hooks.items()
    }
