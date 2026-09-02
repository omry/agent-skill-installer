from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from omegaconf import MISSING

from .common import AgentInstructions


@dataclass
class CodexRequires:
    codex: str | None = None


@dataclass
class CodexCommandHook:
    type: str = "command"
    command: str = MISSING
    timeout: int | None = None
    statusMessage: str | None = None


@dataclass
class CodexHookMatcher:
    matcher: str | None = None
    hooks: list[CodexCommandHook] = field(default_factory=list)


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
                    if isinstance(hook, CodexCommandHook)
                    else CodexCommandHook(**hook)
                    for hook in group.get("hooks", [])
                ],
            )
            for group in groups
        ]
        for event, groups in config.hooks.items()
    }
