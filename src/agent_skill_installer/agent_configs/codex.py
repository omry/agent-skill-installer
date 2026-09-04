from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .common import AgentInstructions


@dataclass
class CodexRequires:
    codex: str | None = None


@dataclass
class CodexSkillCommandHook:
    type: str = "skill-command"
    # Optional in the loader so install-time validation can report the full path.
    executable: str | None = None
    args: list[str] = field(default_factory=list)
    timeout: int | None = None
    statusMessage: str | None = None
    # Parse legacy declarations only to produce an explicit migration error.
    command: str | None = None


@dataclass
class CodexHookMatcher:
    matcher: str | None = None
    hooks: list[CodexSkillCommandHook] = field(default_factory=list)


CodexHooks = dict[str, list[CodexHookMatcher]]


@dataclass
class CodexAgentConfig:
    version: int = 1
    requires: CodexRequires = field(default_factory=CodexRequires)
    instructions: AgentInstructions | None = None
    hooks: CodexHooks = field(default_factory=dict)
    hooks_direct: dict[str, Any] = field(default_factory=dict)


def materialize_codex_hooks(config: CodexAgentConfig) -> None:
    config.hooks = {
        event: [
            group
            if isinstance(group, CodexHookMatcher)
            else CodexHookMatcher(
                matcher=group.get("matcher"),
                hooks=[
                    hook
                    if isinstance(hook, CodexSkillCommandHook)
                    else CodexSkillCommandHook(**hook)
                    for hook in group.get("hooks", [])
                ],
            )
            for group in groups
        ]
        for event, groups in config.hooks.items()
    }
