from __future__ import annotations

from dataclasses import dataclass

from omegaconf import MISSING


@dataclass
class AgentInstructions:
    title: str = MISSING
    body: str = MISSING
