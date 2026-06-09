from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from difflib import get_close_matches
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

from omegaconf import MISSING, OmegaConf
from omegaconf.errors import OmegaConfBaseException


CONFIG_FILE_NAME = "agent-skill-installer.yaml"


class InstallerConfigError(Exception):
    pass


@dataclass
class AgentInstructions:
    title: str = MISSING
    body: str = MISSING


@dataclass
class CodexRequires:
    codex: str | None = None


@dataclass
class ClaudeRequires:
    claude_code: str | None = None


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


@dataclass
class CodexHooks:
    SessionStart: list[CodexHookMatcher] = field(default_factory=list)
    PreToolUse: list[CodexHookMatcher] = field(default_factory=list)
    PermissionRequest: list[CodexHookMatcher] = field(default_factory=list)
    PostToolUse: list[CodexHookMatcher] = field(default_factory=list)
    UserPromptSubmit: list[CodexHookMatcher] = field(default_factory=list)
    Stop: list[CodexHookMatcher] = field(default_factory=list)


@dataclass
class CodexAgentConfig:
    version: int = 1
    requires: CodexRequires = field(default_factory=CodexRequires)
    instructions: AgentInstructions | None = None
    hooks: CodexHooks = field(default_factory=CodexHooks)
    hooks_direct: dict[str, Any] = field(default_factory=dict)


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


@dataclass
class ClaudeHooks:
    SessionStart: list[ClaudeHookMatcher] = field(default_factory=list)
    PreToolUse: list[ClaudeHookMatcher] = field(default_factory=list)
    PostToolUse: list[ClaudeHookMatcher] = field(default_factory=list)
    Notification: list[ClaudeHookMatcher] = field(default_factory=list)
    Stop: list[ClaudeHookMatcher] = field(default_factory=list)
    SubagentStop: list[ClaudeHookMatcher] = field(default_factory=list)
    UserPromptSubmit: list[ClaudeHookMatcher] = field(default_factory=list)
    PreCompact: list[ClaudeHookMatcher] = field(default_factory=list)


@dataclass
class ClaudeAgentConfig:
    version: int = 1
    requires: ClaudeRequires = field(default_factory=ClaudeRequires)
    instructions: AgentInstructions | None = None
    hooks: ClaudeHooks = field(default_factory=ClaudeHooks)
    hooks_direct: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentConfigs:
    codex: CodexAgentConfig | None = None
    claude: ClaudeAgentConfig | None = None


@dataclass
class PackageContext:
    version: str = ""


@dataclass
class ExternalWheelCopy:
    wheel_path: str = MISSING
    skill_path: str = MISSING
    executable: bool = False
    replace: bool = False


@dataclass
class ExternalWheelSource:
    package: str = MISSING
    editable: str | None = None
    copies: list[ExternalWheelCopy] = field(default_factory=list)


@dataclass
class PayloadFiles:
    include: list[str] = field(default_factory=lambda: ["**"])
    exclude: list[str] = field(default_factory=list)


@dataclass
class InstallerRoot:
    version: int = 1
    payload: PayloadFiles = field(default_factory=PayloadFiles)
    shared: dict[str, Any] = field(default_factory=dict)
    external_wheels: list[ExternalWheelSource] = field(default_factory=list)
    agents: AgentConfigs = field(default_factory=AgentConfigs)


@dataclass
class InstallerConfig:
    installer: InstallerRoot = field(default_factory=InstallerRoot)
    package: PackageContext = field(default_factory=PackageContext)


def _display_path(path: Path | str) -> str:
    return str(path)


def _format_config_error(path: Path | str, error: Exception) -> str:
    return f"syntax error in remote {_display_path(path)}: {error}"


def _optional_inner(annotation: object) -> object:
    origin = get_origin(annotation)
    if origin not in (UnionType, Union):
        return annotation
    args = [arg for arg in get_args(annotation) if arg is not type(None)]
    return args[0] if len(args) == 1 else annotation


def _unexpected_field_message(path: str, key: str, allowed: set[str]) -> str:
    message = f"unexpected field in {path}.{key}"
    suggestions = get_close_matches(key, sorted(allowed), n=1)
    if suggestions:
        message += f"; did you mean {suggestions[0]}?"
    return message


def _validate_unknown_fields(
    value: object,
    annotation: object,
    path: str,
) -> None:
    annotation = _optional_inner(annotation)
    if annotation is Any or value is None:
        return

    origin = get_origin(annotation)
    if origin is list:
        item_type = get_args(annotation)[0] if get_args(annotation) else Any
        if isinstance(value, list):
            for index, item in enumerate(value):
                _validate_unknown_fields(item, item_type, f"{path}[{index}]")
        return
    if origin is dict:
        return

    if not is_dataclass(annotation) or not isinstance(value, dict):
        return

    hints = get_type_hints(annotation)
    allowed = {field.name for field in fields(annotation)}
    for key, item in value.items():
        if key not in allowed:
            raise InstallerConfigError(_unexpected_field_message(path, key, allowed))
        _validate_unknown_fields(item, hints.get(key, Any), f"{path}.{key}")


def _validate_supported_versions(config: InstallerConfig, path: Path | str) -> None:
    if config.installer.version != 1:
        raise InstallerConfigError(
            f"unsupported installer.version in {_display_path(path)}: "
            f"{config.installer.version}; supported versions: 1"
        )
    agents = config.installer.agents
    if agents.codex is not None and agents.codex.version != 1:
        raise InstallerConfigError(
            f"unsupported installer.agents.codex.version in {_display_path(path)}: "
            f"{agents.codex.version}; supported versions: 1"
        )
    if agents.claude is not None and agents.claude.version != 1:
        raise InstallerConfigError(
            f"unsupported installer.agents.claude.version in {_display_path(path)}: "
            f"{agents.claude.version}; supported versions: 1"
        )


def _build_config(
    loaded: Any,
    source: Path | str,
    schema_type: type[InstallerConfig],
    *,
    package_version: str | None = None,
) -> InstallerConfig:
    try:
        if schema_type is InstallerConfig and package_version is not None:
            context = OmegaConf.create({"package": {"version": package_version}})
            loaded = OmegaConf.merge(context, loaded)
        OmegaConf.resolve(loaded)
        resolved = OmegaConf.to_container(loaded, resolve=True)
        try:
            _validate_unknown_fields(resolved, schema_type, "config")
        except InstallerConfigError as error:
            raise InstallerConfigError(_format_config_error(source, error)) from error
        schema = OmegaConf.structured(schema_type)
        merged = OmegaConf.merge(schema, loaded)
        OmegaConf.resolve(merged)
        config = OmegaConf.to_object(merged)
    except OmegaConfBaseException as error:
        raise InstallerConfigError(_format_config_error(source, error)) from error
    except OSError as error:
        raise InstallerConfigError(f"failed to read {_display_path(source)}: {error}") from error

    assert isinstance(config, schema_type)
    if isinstance(config, InstallerConfig):
        _validate_supported_versions(config, source)
    return config


def _build_installer_config(
    loaded: Any,
    source: Path | str,
    *,
    package_version: str | None = None,
) -> InstallerConfig:
    config = _build_config(
        loaded,
        source,
        InstallerConfig,
        package_version=package_version,
    )
    assert isinstance(config, InstallerConfig)
    return config


def load_installer_config(
    path: Path | str,
    *,
    package_version: str | None = None,
) -> InstallerConfig:
    source = Path(path)
    try:
        loaded = OmegaConf.load(source)
    except OmegaConfBaseException as error:
        raise InstallerConfigError(_format_config_error(source, error)) from error
    except OSError as error:
        raise InstallerConfigError(f"failed to read {_display_path(source)}: {error}") from error
    return _build_installer_config(
        loaded,
        source,
        package_version=package_version,
    )


def load_installer_config_text(
    text: str,
    *,
    source: Path | str = CONFIG_FILE_NAME,
    package_version: str | None = None,
) -> InstallerConfig:
    try:
        loaded = OmegaConf.create(text)
    except OmegaConfBaseException as error:
        raise InstallerConfigError(_format_config_error(source, error)) from error
    config = _build_config(
        loaded,
        source,
        InstallerConfig,
        package_version=package_version,
    )
    assert isinstance(config, InstallerConfig)
    return config
