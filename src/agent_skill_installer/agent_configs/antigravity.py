from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .common import AgentInstructions


@dataclass
class AntigravityRequires:
    antigravity: str | None = None


@dataclass
class AntigravitySkillCommandHook:
    type: str = "skill-command"
    # Optional in the loader so install-time validation can report the full path.
    executable: str | None = None
    args: list[str] = field(default_factory=list)
    timeout: int | None = None
    # Parse legacy declarations only to produce an explicit migration error.
    command: str | None = None


@dataclass
class AntigravityHookMatcher:
    matcher: str | None = None
    hooks: list[AntigravitySkillCommandHook] = field(default_factory=list)


AntigravityHooks = dict[str, list[AntigravityHookMatcher]]


@dataclass
class AntigravityAgentConfig:
    version: int = 1
    requires: AntigravityRequires = field(default_factory=AntigravityRequires)
    instructions: AgentInstructions | None = None
    hooks: AntigravityHooks = field(default_factory=dict)
    hooks_direct: dict[str, Any] = field(default_factory=dict)


def materialize_antigravity_hooks(config: AntigravityAgentConfig) -> None:
    config.hooks = {
        event: [
            group
            if isinstance(group, AntigravityHookMatcher)
            else AntigravityHookMatcher(
                matcher=group.get("matcher"),
                hooks=[
                    hook
                    if isinstance(hook, AntigravitySkillCommandHook)
                    else AntigravitySkillCommandHook(**hook)
                    for hook in group.get("hooks", [])
                ],
            )
            for group in groups
        ]
        for event, groups in config.hooks.items()
    }
