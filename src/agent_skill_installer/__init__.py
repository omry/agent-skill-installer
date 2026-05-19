"""Generic installer core for projects that distribute agent skills."""

from __future__ import annotations

from .config import (
    InstallerConfig,
    InstallerConfigError,
    load_installer_config,
    load_installer_config_text,
)
from .installer import Installer, InstallerError, SkillProject

__version__ = "0.1.0"

__all__ = [
    "Installer",
    "InstallerConfig",
    "InstallerConfigError",
    "InstallerError",
    "SkillProject",
    "__version__",
    "load_installer_config",
    "load_installer_config_text",
]
