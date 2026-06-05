"""Generic installer core for projects that distribute agent skills."""

from __future__ import annotations

from .config import (
    InstallerConfig,
    InstallerConfigError,
    PlatformSpecific,
    load_installer_config,
    load_installer_config_text,
)
from .installer import GithubSource, Installer, InstallerError, SkillProject

__version__ = "0.1.3"

__all__ = [
    "Installer",
    "InstallerConfig",
    "InstallerConfigError",
    "InstallerError",
    "GithubSource",
    "PlatformSpecific",
    "SkillProject",
    "__version__",
    "load_installer_config",
    "load_installer_config_text",
]
