"""Generic installer core for projects that distribute agent skills."""

from __future__ import annotations

from .installer import Installer, InstallerError, SkillProject

__version__ = "0.1.0"

__all__ = [
    "Installer",
    "InstallerError",
    "SkillProject",
    "__version__",
]
