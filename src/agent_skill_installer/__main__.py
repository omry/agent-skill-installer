from __future__ import annotations

import argparse
from email.parser import Parser
import json
import shlex
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable, Sequence

from . import __version__
from .cli import (
    AGENT_LABELS,
    BackRequested,
    PROMPT_BACK,
    Prompter,
    SCOPE_LABELS,
    TARGET_ALL,
    TextualPrompter,
    UsageError as CliUsageError,
    agent_arg_from_values,
    print_results,
    selected_agents_from_values,
    target_choices,
)
from .config import (
    CONFIG_FILE_NAME,
    InstallerConfig,
    InstallerConfigError,
    load_installer_config,
    load_installer_config_text,
)
from .installer import (
    AGENTS,
    GithubSource,
    Installer,
    InstallerError,
    InstallResult,
    InstallationStatus,
    SkillProject,
    default_repo_path,
    discover_managed_installations,
    download_github_archive,
    download_pypi_wheel,
    find_repo_root,
    github_archive_relative_path,
    github_archive_skill_prefix,
    install_target,
    local_skill_source_for_candidate,
    normalize_agents,
    parse_github_url,
    published_pypi_versions,
    running_on_tty,
    target_spec,
    validate_install_source_selection,
)


GENERIC_IMPORT_NAME = "agent_skill_installer"
RECENT_STATE_DIR_NAME = ".agent-skill-installer"
RECENT_INSTALLS_FILE_NAME = "recent-installations.json"
RECENT_PYPI_LIMIT = 10
RECENT_PYPI_KEY = "pypi_packages"
RECENT_GITHUB_KEY = "github_urls"
VALIDATED_PYPI_FIELDS = [
    "_validated_pypi_package",
    "_validated_pypi_version",
    "_validated_pypi_wheel_path",
    "_validated_pypi_project",
    "_validated_pypi_temp_dir",
]
VALIDATED_WHEEL_FIELDS = [
    "_validated_wheel_file",
    "_validated_wheel_project",
]
VALIDATED_GITHUB_FIELDS = [
    "_validated_github_source",
    "_validated_github_archive_path",
    "_validated_github_project",
    "_validated_github_temp_dir",
]
INSTALL_SOURCE_FIELDS = [
    "_selected_install_source",
    "pypi_package",
    "pypi_version",
    "wheel_file",
    "github_url",
    "github_ref",
    "github_path",
    "skill_path",
    "editable",
    *VALIDATED_PYPI_FIELDS,
    *VALIDATED_WHEEL_FIELDS,
    *VALIDATED_GITHUB_FIELDS,
]
TARGET_FIELDS = [
    "agent",
    "scope",
    "repo",
    "codex_home",
    "claude_home",
]
UNINSTALL_FIELDS = [
    "skill_name",
    "package_name",
    "agent",
    "scope",
    "repo",
    "uninstall_statuses",
]
COMMAND_FIELDS = [
    "command",
    *INSTALL_SOURCE_FIELDS,
    *TARGET_FIELDS,
    *UNINSTALL_FIELDS,
    "description",
]
PromptStep = Callable[[Sequence[str], Callable[[], object]], object]


class UsageError(Exception):
    pass


def generic_project() -> SkillProject:
    return SkillProject(
        package_name="agent-skill-installer",
        import_name=GENERIC_IMPORT_NAME,
        version=__version__,
        skill_name="agent-skill-installer",
        cli_name="agent-skill-installer",
        description="Install and uninstall agent skills.",
        installer_config=InstallerConfig(),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-skill-installer",
        description="Install or uninstall agent skills from generic sources.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Disable the interactive text UI.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show installed skill, source, and hook paths.",
    )
    subparsers = parser.add_subparsers(dest="command")

    install = subparsers.add_parser("install", help="Install a skill.")
    add_target_args(install)
    install.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing unowned skill directory.",
    )
    install.add_argument(
        "--skill-name",
        help="Override the installed skill directory name.",
    )
    install.add_argument(
        "--description",
        help="Override the default discoverability description.",
    )
    source = install.add_mutually_exclusive_group()
    source.add_argument(
        "--pypi-package",
        metavar="NAME",
        help="PyPI package containing a bundled agent skill.",
    )
    source.add_argument(
        "--wheel-file",
        "--wheel",
        dest="wheel_file",
        type=Path,
        metavar="PATH",
        help="Local wheel file containing a bundled agent skill.",
    )
    source.add_argument(
        "--github-url",
        metavar="URL",
        help="GitHub repository or tree URL containing a skill.",
    )
    source.add_argument(
        "--skill-path",
        "--local-repo",
        dest="skill_path",
        type=Path,
        metavar="PATH",
        help="Local repository or directory containing SKILL.md or skill/SKILL.md.",
    )
    install.add_argument(
        "--pypi-version",
        metavar="VERSION",
        help="PyPI package version to install. Defaults to the latest wheel release.",
    )
    install.add_argument(
        "--github-ref",
        metavar="REF",
        help="Git ref to archive when --github-url points at a repository root.",
    )
    install.add_argument(
        "--github-path",
        metavar="PATH",
        help="Skill directory inside the GitHub archive.",
    )
    local_mode = install.add_mutually_exclusive_group()
    local_mode.add_argument(
        "--editable",
        dest="editable",
        action="store_true",
        default=None,
        help=(
            "Symlink --skill-path installs to the source directory. "
            "This is the default for local installs."
        ),
    )
    local_mode.add_argument(
        "--copy",
        dest="editable",
        action="store_false",
        default=None,
        help="Copy --skill-path installs instead of symlinking them.",
    )

    uninstall = subparsers.add_parser("uninstall", help="Uninstall a skill.")
    add_target_args(uninstall)
    uninstall.add_argument(
        "--skill-name",
        help="Installed skill directory name to remove.",
    )

    return parser


def add_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--agent",
        metavar="AGENT[,AGENT...]",
        help="Agent integration to target: codex, claude, or all.",
    )
    parser.add_argument(
        "--scope",
        choices=("repo", "global"),
        help="Install for the current repository or for the current user.",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        help="Repository path to use with --scope repo. Defaults to cwd.",
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        help="Codex home directory for global scope. Defaults to ~/.codex.",
    )
    parser.add_argument(
        "--claude-home",
        type=Path,
        help="Claude Code home directory for global scope. Defaults to ~/.claude.",
    )
    parser.add_argument("--home", type=Path, help=argparse.SUPPRESS)


def command_choices() -> list[dict[str, str]]:
    return [
        {"name": "Install", "value": "install"},
        {"name": "Uninstall", "value": "uninstall"},
    ]


def install_source_choices() -> list[dict[str, str]]:
    local_choice = {
        "name": (
            "Local repo or skill directory (development mode)"
            if local_development_source() is not None
            else "Local repo or skill directory"
        ),
        "value": "local",
    }
    remote_choices = [
        {
            "name": "PyPI package wheel",
            "value": "pypi",
        },
        {
            "name": "Local wheel file",
            "value": "wheel",
        },
        {
            "name": "GitHub repository URL",
            "value": "github",
        },
    ]
    if local_development_source() is not None:
        return [local_choice, *remote_choices]
    return [*remote_choices, local_choice]


def local_install_mode_choices() -> list[dict[str, str]]:
    return [
        {
            "name": "Editable symlink (reflect source changes immediately)",
            "value": "editable",
        },
        {
            "name": "Copy files (snapshot of the current source)",
            "value": "copy",
        },
    ]


def scope_choices() -> list[dict[str, str]]:
    return [
        {"name": "User global", "value": "global"},
        {"name": "Current repository", "value": "repo"},
        {"name": "Specific directory", "value": "specific"},
    ]


def quote_command(parts: list[object]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def build_no_ui_command(args: argparse.Namespace) -> str | None:
    command = getattr(args, "command", None)
    if command not in {"install", "uninstall"}:
        return None

    parts: list[object] = ["agent-skill-installer", "--no-ui", command]
    if command == "install":
        if getattr(args, "force", False):
            parts.append("--force")
        if getattr(args, "skill_path", None) is not None:
            parts.extend(["--skill-path", args.skill_path])
            if getattr(args, "editable", None) is True:
                parts.append("--editable")
            elif getattr(args, "editable", None) is False:
                parts.append("--copy")
        elif getattr(args, "github_url", None):
            parts.extend(["--github-url", args.github_url])
            if getattr(args, "github_ref", None):
                parts.extend(["--github-ref", args.github_ref])
            if getattr(args, "github_path", None):
                parts.extend(["--github-path", args.github_path])
        elif getattr(args, "pypi_package", None):
            parts.extend(["--pypi-package", args.pypi_package])
            if getattr(args, "pypi_version", None):
                parts.extend(["--pypi-version", args.pypi_version])
        elif getattr(args, "wheel_file", None) is not None:
            parts.extend(["--wheel-file", args.wheel_file])
        else:
            return None

    skill_name = getattr(args, "skill_name", None)
    if command == "uninstall" and not skill_name:
        return None
    if skill_name:
        parts.extend(["--skill-name", skill_name])
    description = getattr(args, "description", None)
    if command == "install" and description:
        parts.extend(["--description", description])

    agent = getattr(args, "agent", None) or TARGET_ALL
    scope = getattr(args, "scope", None) or "global"
    parts.extend(["--agent", agent, "--scope", scope])
    if scope == "repo" and getattr(args, "repo", None) is not None:
        parts.extend(["--repo", args.repo])
    if scope == "global":
        if getattr(args, "codex_home", None) is not None:
            parts.extend(["--codex-home", args.codex_home])
        if getattr(args, "claude_home", None) is not None:
            parts.extend(["--claude-home", args.claude_home])
    return quote_command(parts)


def ensure_arg_defaults(args: argparse.Namespace) -> None:
    for name, value in {
        "force": False,
        "skill_name": None,
        "description": None,
        "pypi_package": None,
        "pypi_version": None,
        "wheel_file": None,
        "github_url": None,
        "github_ref": None,
        "github_path": None,
        "skill_path": None,
        "editable": None,
        "agent": None,
        "scope": None,
        "repo": None,
        "codex_home": None,
        "claude_home": None,
        "home": None,
        "verbose": False,
        "package_name": None,
        "uninstall_statuses": None,
        "_selected_install_source": None,
    }.items():
        if not hasattr(args, name):
            setattr(args, name, value)


def copy_args(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(**vars(args))


def selected_install_source(args: argparse.Namespace) -> str | None:
    sources = [
        ("pypi", getattr(args, "pypi_package", None) is not None),
        ("wheel", getattr(args, "wheel_file", None) is not None),
        ("github", getattr(args, "github_url", None) is not None),
        ("local", getattr(args, "skill_path", None) is not None),
    ]
    selected = [name for name, enabled in sources if enabled]
    if len(selected) == 1:
        return selected[0]
    remembered = getattr(args, "_selected_install_source", None)
    if not selected and remembered in {"pypi", "wheel", "github", "local"}:
        return remembered
    return None


def install_source_count(args: argparse.Namespace) -> int:
    return sum(
        value is not None
        for value in (
            getattr(args, "pypi_package", None),
            getattr(args, "wheel_file", None),
            getattr(args, "github_url", None),
            getattr(args, "skill_path", None),
        )
    )


def clear_install_sources(args: argparse.Namespace) -> None:
    args._selected_install_source = None
    args.pypi_package = None
    args.pypi_version = None
    args.wheel_file = None
    args.github_url = None
    args.github_ref = None
    args.github_path = None
    args.skill_path = None
    args.editable = None


def local_development_source() -> tuple[Path, Path] | None:
    return local_skill_source_for_candidate(default_repo_path())


def default_skill_path() -> Path:
    source = local_development_source()
    if source is not None:
        return source[1]
    candidate = default_repo_path() / "skill"
    return candidate if candidate.exists() else default_repo_path()


def default_wheel_file() -> Path:
    dist_dir = default_repo_path() / "dist"
    wheels = sorted(dist_dir.glob("*.whl")) if dist_dir.is_dir() else []
    if wheels:
        return wheels[-1]
    return dist_dir


def preview_skill_path() -> Path:
    source = local_development_source()
    if source is None:
        return Path("./skill")
    skill_path = source[1]
    try:
        relative = skill_path.relative_to(default_repo_path())
    except ValueError:
        return skill_path
    return Path(".") if relative == Path(".") else relative


def local_summary_path(path: Path | None) -> Path:
    source_path = path or default_skill_path()
    return source_path.expanduser().resolve()


def preview_command(
    args: argparse.Namespace,
    **updates: object,
) -> str | None:
    preview_args = copy_args(args)
    for name, value in updates.items():
        setattr(preview_args, name, value)
    return build_no_ui_command(preview_args)


def editable_from_mode(mode: object | None) -> bool | None:
    if mode is None:
        return None
    if isinstance(mode, bool):
        return mode
    value = str(mode)
    if value == "editable":
        return True
    if value == "copy":
        return False
    return None


def preview_source_command(
    args: argparse.Namespace,
    source: object,
) -> str | None:
    preview_args = copy_args(args)
    clear_install_sources(preview_args)
    preview_args.command = "install"
    source_name = str(source)
    if source_name == "local":
        preview_args.skill_path = preview_skill_path()
    return build_no_ui_command(preview_args)


def preview_command_choice(
    args: argparse.Namespace,
    command: object,
) -> str | None:
    preview_args = copy_args(args)
    preview_args.command = str(command)
    if preview_args.command == "install":
        if install_source_count(preview_args) == 0:
            if local_development_source() is not None:
                preview_args.skill_path = preview_skill_path()
        preview_args.agent = preview_args.agent or TARGET_ALL
        preview_args.scope = preview_args.scope or "global"
    elif preview_args.command == "uninstall":
        preview_args.agent = preview_args.agent or TARGET_ALL
        preview_args.scope = preview_args.scope or "global"
    return build_no_ui_command(preview_args)


def install_decision_summary(
    args: argparse.Namespace,
    *,
    command: object | None = None,
    source: object | None = None,
    pypi_package: object | None = None,
    pypi_version: object | None = None,
    github_url: object | None = None,
    github_ref: object | None = None,
    wheel_file: object | None = None,
    skill_path: object | None = None,
    editable: object | None = None,
    agent: object | None = None,
    scope: object | None = None,
    repo: object | None = None,
) -> str | None:
    selected_command = (
        str(command)
        if command is not None
        else getattr(args, "command", None)
    )
    if selected_command == "uninstall":
        return "Uninstalling a skill"
    if selected_command != "install":
        return None

    if (
        source is None
        and pypi_package is None
        and pypi_version is None
        and wheel_file is None
        and github_url is None
        and github_ref is None
        and skill_path is None
        and editable is None
        and selected_install_source(args) is None
    ):
        return "Installing a skill"

    lines = [
        install_source_summary(
            args,
            source=source,
            pypi_package=pypi_package,
            pypi_version=pypi_version,
            wheel_file=wheel_file,
            github_url=github_url,
            github_ref=github_ref,
            skill_path=skill_path,
            editable=editable,
        )
    ]
    target_line = install_target_summary(
        args,
        agent=agent,
        scope=scope,
        repo=repo,
    )
    if target_line:
        lines.append(target_line)
    return "\n".join(lines)


def install_source_summary(
    args: argparse.Namespace,
    *,
    source: object | None = None,
    pypi_package: object | None = None,
    pypi_version: object | None = None,
    github_url: object | None = None,
    github_ref: object | None = None,
    wheel_file: object | None = None,
    skill_path: object | None = None,
    editable: object | None = None,
) -> str:
    selected_source = (
        str(source)
        if source is not None
        else selected_install_source(args)
    )
    if selected_source is None:
        selected_source = "local" if local_development_source() is not None else "pypi"
    if selected_source == "pypi":
        package = summary_value(
            pypi_package,
            getattr(args, "pypi_package", None),
            fallback="",
        )
        version = summary_value(
            pypi_version,
            getattr(args, "pypi_version", None),
            fallback="",
        )
        if not package:
            return "Installing from PyPI"
        if version:
            return f"Installing PyPI package {package} {version}"
        return f"Installing PyPI package {package}"
    if selected_source == "github":
        url = summary_value(
            github_url,
            getattr(args, "github_url", None),
            fallback="",
        )
        if not url:
            return "Installing from GitHub"
        ref = summary_value(
            github_ref,
            getattr(args, "github_ref", None),
            github_summary_ref(
                url,
                getattr(args, "github_path", None),
            ),
            fallback="",
        )
        name = github_summary_name(
            url,
            getattr(args, "skill_name", None),
            getattr(args, "github_path", None),
        )
        if ref:
            return f"Installing {name} from GitHub {url} at {ref}"
        return f"Installing {name} from GitHub {url}"
    if selected_source == "wheel":
        path_value = (
            Path(str(wheel_file))
            if wheel_file is not None
            else getattr(args, "wheel_file", None)
        )
        if path_value is None:
            return "Installing from a local wheel file"
        wheel_path = Path(str(path_value)).expanduser()
        return f"Installing from local wheel file {wheel_path}"
    if selected_source == "local":
        path_value = (
            Path(str(skill_path))
            if skill_path is not None
            else getattr(args, "skill_path", None)
        )
        source_path = local_summary_path(path_value)
        name = local_summary_name(source_path, getattr(args, "skill_name", None))
        explicit_editable = (
            editable_from_mode(editable)
            if editable is not None
            else getattr(args, "editable", None)
        )
        if explicit_editable is True:
            return f"Installing {name} as editable symlink from {source_path}"
        if explicit_editable is False:
            return f"Installing a copy of {name} from {source_path}"
        return f"Installing {name} from local path {source_path}"
    return "Installing a skill"


def summary_value(
    *values: object | None,
    fallback: str,
) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return fallback


def github_summary_name(
    url: str,
    skill_name: str | None,
    github_path: str | None,
) -> str:
    if skill_name:
        return skill_name
    try:
        source = parse_github_url(url, path=github_path)
    except InstallerError:
        return "skill"
    if source.path is not None and source.path.name:
        return source.path.name
    return source.repo


def github_summary_ref(
    url: str,
    github_path: str | None,
) -> str | None:
    try:
        return parse_github_url(url, path=github_path).version_label
    except InstallerError:
        return None


def local_summary_name(path: Path | None, skill_name: str | None) -> str:
    if skill_name:
        return skill_name
    if path is None:
        return "skill"
    if path.name:
        return path.name
    return "skill"


def install_target_summary(
    args: argparse.Namespace,
    *,
    agent: object | None = None,
    scope: object | None = None,
    repo: object | None = None,
) -> str | None:
    selected_agent = (
        str(agent)
        if agent is not None
        else getattr(args, "agent", None)
    )
    selected_scope = (
        str(scope)
        if scope is not None
        else getattr(args, "scope", None)
    )
    if not selected_agent or not selected_scope:
        return None

    try:
        agents = normalize_agents([selected_agent])
    except InstallerError:
        return None

    targets = [
        install_target_label(
            args,
            agent_name,
            selected_scope,
            repo=repo,
        )
        for agent_name in agents
    ]
    return f"Into {', '.join(targets)}"


def install_target_label(
    args: argparse.Namespace,
    agent_name: str,
    scope: str,
    *,
    repo: object | None = None,
) -> str:
    label = (
        "Claude"
        if agent_name == "claude"
        else AGENT_LABELS.get(agent_name, agent_name)
    )
    if scope == "repo":
        repo_path = target_repo_path(args, repo)
        return f"{label} {repo_path.name or 'repository'}"
    if scope == "global":
        return f"{label} Global"
    return f"{label} {scope}"


def target_repo_path(
    args: argparse.Namespace,
    repo: object | None,
) -> Path:
    if repo is not None:
        return Path(str(repo))
    existing_repo = getattr(args, "repo", None)
    if existing_repo is not None:
        return Path(str(existing_repo))
    return repo_root_for_ui(args) or default_repo_path()


def pypi_project_for_versions(package_name: str) -> SkillProject:
    return SkillProject(
        package_name=package_name,
        import_name=GENERIC_IMPORT_NAME,
        version="0",
        skill_name=package_name,
        description="",
        pypi_project_name=package_name,
    )


def generic_pypi_version_choices(
    package_name: str,
    *,
    limit: int = 10,
) -> list[dict[str, str]]:
    try:
        versions = published_pypi_versions(
            pypi_project_for_versions(package_name),
            limit=limit,
        )
    except InstallerError:
        versions = []

    return [
        {
            "name": version,
            "value": version,
        }
        for version in versions
    ]


def required_generic_pypi_version_choices(
    package_name: str,
    *,
    limit: int = 10,
) -> list[dict[str, str]]:
    package = package_name.strip()
    if not package:
        raise UsageError("PyPI package name must not be empty")
    try:
        versions = published_pypi_versions(
            pypi_project_for_versions(package),
            limit=limit,
        )
    except InstallerError as error:
        message = str(error)
        if "HTTP Error 404" in message or "404: Not Found" in message:
            raise UsageError(f"PyPI package not found: {package}") from error
        raise UsageError(f"could not read PyPI metadata for {package}: {message}") from error
    if not versions:
        raise UsageError(f"no wheel releases found on PyPI for {package}")
    return [
        {
            "name": version,
            "value": version,
        }
        for version in versions
    ]


def recent_installations_path(home: Path | None = None) -> Path:
    base = (home or Path.home()).expanduser()
    return base / RECENT_STATE_DIR_NAME / RECENT_INSTALLS_FILE_NAME


def load_recent_state(home: Path | None = None) -> dict[str, object]:
    try:
        data = json.loads(recent_installations_path(home).read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_recent_values(key: str, home: Path | None = None) -> list[str]:
    try:
        data = load_recent_state(home)
        values = data.get(key)
        if not isinstance(values, list):
            return []
        recent: list[str] = []
        for value in values:
            if not isinstance(value, str):
                continue
            item = value.strip()
            if item and item not in recent:
                recent.append(item)
            if len(recent) >= RECENT_PYPI_LIMIT:
                break
        return recent
    except Exception:
        return []


def remember_recent_value(
    key: str,
    value: str,
    *,
    home: Path | None = None,
) -> None:
    item = value.strip()
    if not item:
        return
    values = [
        existing
        for existing in load_recent_values(key, home)
        if existing != item
    ]
    values.insert(0, item)
    data = load_recent_state(home)
    data[key] = values[:RECENT_PYPI_LIMIT]
    try:
        path = recent_installations_path(home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    except Exception:
        return


def load_recent_pypi_packages(home: Path | None = None) -> list[str]:
    return load_recent_values(RECENT_PYPI_KEY, home)


def remember_recent_pypi_package(
    package_name: str,
    *,
    home: Path | None = None,
) -> None:
    remember_recent_value(RECENT_PYPI_KEY, package_name, home=home)


def load_recent_github_urls(home: Path | None = None) -> list[str]:
    return load_recent_values(RECENT_GITHUB_KEY, home)


def remember_recent_github_url(
    github_url: str,
    *,
    home: Path | None = None,
) -> None:
    remember_recent_value(RECENT_GITHUB_KEY, github_url, home=home)


def recent_pypi_package_choices(
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    return value_choices(load_recent_pypi_packages(getattr(args, "home", None)))


def recent_github_url_choices(
    args: argparse.Namespace,
) -> list[dict[str, str]]:
    return value_choices(load_recent_github_urls(getattr(args, "home", None)))


def value_choices(values: list[str]) -> list[dict[str, str]]:
    return [{"name": value, "value": value} for value in values]


def validate_pypi_skill_package(args: argparse.Namespace) -> None:
    version = resolve_pypi_version(args)
    cleanup_validated_pypi_download(args)
    temp_dir = tempfile.TemporaryDirectory(
        prefix="agent-skill-installer-validate-pypi-"
    )
    try:
        wheel_path = download_pypi_wheel(
            pypi_project_for_versions(args.pypi_package),
            version,
            Path(temp_dir.name),
        )
        project = read_pypi_project(args, wheel_path, version)
    except Exception:
        temp_dir.cleanup()
        raise
    args.pypi_version = version
    args._validated_pypi_package = args.pypi_package
    args._validated_pypi_version = version
    args._validated_pypi_wheel_path = wheel_path
    args._validated_pypi_project = project
    args._validated_pypi_temp_dir = temp_dir


def validate_selected_pypi_skill_package(
    args: argparse.Namespace,
    *,
    package: str,
    version: str | None,
) -> None:
    validation_args = copy_args(args)
    validation_args.pypi_package = package
    validation_args.pypi_version = version
    validate_pypi_skill_package(validation_args)
    cleanup_validated_pypi_download(args)
    args.pypi_package = validation_args.pypi_package
    args.pypi_version = validation_args.pypi_version
    for name in VALIDATED_PYPI_FIELDS:
        setattr(args, name, getattr(validation_args, name))


def cleanup_validated_pypi_download(args: argparse.Namespace) -> None:
    temp_dir = getattr(args, "_validated_pypi_temp_dir", None)
    if temp_dir is not None:
        temp_dir.cleanup()
    for name in VALIDATED_PYPI_FIELDS:
        if hasattr(args, name):
            delattr(args, name)


def validated_pypi_download(
    args: argparse.Namespace,
    version: str,
) -> tuple[Path, SkillProject] | None:
    if getattr(args, "_validated_pypi_package", None) != args.pypi_package:
        return None
    if getattr(args, "_validated_pypi_version", None) != version:
        return None
    wheel_path = getattr(args, "_validated_pypi_wheel_path", None)
    project = getattr(args, "_validated_pypi_project", None)
    if not isinstance(wheel_path, Path) or not wheel_path.is_file():
        return None
    if not isinstance(project, SkillProject):
        return None
    return wheel_path, project


def validate_wheel_file(args: argparse.Namespace) -> None:
    wheel_path = args.wheel_file.expanduser().resolve()
    if not wheel_path.is_file():
        raise InstallerError(f"wheel file does not exist: {wheel_path}")
    project = read_pypi_project(args, wheel_path, None)
    args._validated_wheel_file = wheel_path
    args._validated_wheel_project = project


def validated_wheel_file(args: argparse.Namespace) -> tuple[Path, SkillProject] | None:
    wheel_path = getattr(args, "_validated_wheel_file", None)
    project = getattr(args, "_validated_wheel_project", None)
    if not isinstance(wheel_path, Path) or not wheel_path.is_file():
        return None
    if not isinstance(project, SkillProject):
        return None
    current = args.wheel_file.expanduser().resolve()
    if current != wheel_path:
        return None
    return wheel_path, project


def cleanup_validated_wheel_file(args: argparse.Namespace) -> None:
    for name in VALIDATED_WHEEL_FIELDS:
        if hasattr(args, name):
            delattr(args, name)


def validate_github_skill_source(args: argparse.Namespace) -> None:
    cleanup_validated_github_download(args)
    source = parse_github_url(
        args.github_url,
        ref=args.github_ref,
        path=args.github_path,
    )
    temp_dir = tempfile.TemporaryDirectory(
        prefix="agent-skill-installer-validate-github-"
    )
    try:
        archive_path = download_github_archive(source, Path(temp_dir.name))
        project = read_github_project(args, archive_path, source)
    except Exception:
        temp_dir.cleanup()
        raise
    args._validated_github_source = source
    args._validated_github_archive_path = archive_path
    args._validated_github_project = project
    args._validated_github_temp_dir = temp_dir


def validate_selected_github_skill_source(
    args: argparse.Namespace,
    *,
    url: str,
) -> None:
    validation_args = copy_args(args)
    validation_args.github_url = url
    validate_github_skill_source(validation_args)
    cleanup_validated_github_download(args)
    args.github_url = validation_args.github_url
    args.github_ref = validation_args.github_ref
    args.github_path = validation_args.github_path
    for name in VALIDATED_GITHUB_FIELDS:
        setattr(args, name, getattr(validation_args, name))


def cleanup_validated_github_download(args: argparse.Namespace) -> None:
    temp_dir = getattr(args, "_validated_github_temp_dir", None)
    if temp_dir is not None:
        temp_dir.cleanup()
    for name in VALIDATED_GITHUB_FIELDS:
        if hasattr(args, name):
            delattr(args, name)


def validated_github_download(
    args: argparse.Namespace,
    source: GithubSource,
) -> tuple[Path, SkillProject] | None:
    if getattr(args, "_validated_github_source", None) != source:
        return None
    archive_path = getattr(args, "_validated_github_archive_path", None)
    project = getattr(args, "_validated_github_project", None)
    if not isinstance(archive_path, Path) or not archive_path.is_file():
        return None
    if not isinstance(project, SkillProject):
        return None
    return archive_path, project


def repo_root_for_ui(args: argparse.Namespace) -> Path | None:
    repo = getattr(args, "repo", None)
    return (
        find_repo_root(repo)
        if repo is not None
        else find_repo_root(default_repo_path())
    )


def repo_from_status(status: InstallationStatus) -> Path | None:
    if status.scope != "repo":
        return None
    if status.hook_path is not None:
        return status.hook_path.parent
    if status.skill_dir is not None and len(status.skill_dir.parents) >= 3:
        return status.skill_dir.parents[2]
    return None


def installation_name(status: InstallationStatus) -> str:
    if status.skill_name:
        return status.skill_name
    if status.skill_dir is not None:
        return status.skill_dir.name
    return "unknown-skill"


def installation_choice_name(status: InstallationStatus) -> str:
    target = (
        f"{AGENT_LABELS.get(status.agent, status.agent)} "
        f"{SCOPE_LABELS.get(status.scope, status.scope)}"
    )
    source = installed_source_phrase(status)
    return f"{installation_name(status)} - {target}{source}"


def installation_choice_description(status: InstallationStatus) -> str:
    lines: list[str] = []
    if status.skill_dir is not None:
        lines.append(str(status.skill_dir))
    if status.hook_path is not None:
        lines.append(f"Hook: {status.hook_path}")
    if status.source_url is not None:
        source = status.source_url
        if status.source_ref is not None:
            source += f" @ {status.source_ref}"
        if status.source_path is not None:
            source += f" / {status.source_path}"
        lines.append(f"Source: {source}")
    elif status.source_path is not None:
        lines.append(f"Source: {status.source_path}")
    elif status.package_name is not None:
        lines.append(f"Package: {status.package_name}")
    return "\n".join(lines)


def installed_skill_choices(
    statuses: list[InstallationStatus],
) -> list[dict[str, str]]:
    return [
        {
            "name": installation_choice_name(status),
            "description": installation_choice_description(status),
            "value": str(index),
        }
        for index, status in enumerate(statuses)
    ]


def uninstall_skill_key(status: InstallationStatus) -> tuple[str, str]:
    skill_name = installation_name(status)
    return skill_name, status.package_name or skill_name


def grouped_uninstall_statuses(
    statuses: list[InstallationStatus],
) -> dict[tuple[str, str], list[InstallationStatus]]:
    groups: dict[tuple[str, str], list[InstallationStatus]] = {}
    for status in statuses:
        groups.setdefault(uninstall_skill_key(status), []).append(status)
    return groups


def installed_skill_group_choices(
    groups: dict[tuple[str, str], list[InstallationStatus]],
) -> list[dict[str, str]]:
    choices: list[dict[str, str]] = []
    for index, ((skill_name, package_name), statuses) in enumerate(groups.items()):
        target_count = len(statuses)
        plural = "" if target_count == 1 else "s"
        choices.append(
            {
                "name": skill_name,
                "description": (
                    f"{target_count} installed target{plural}\n"
                    f"Package: {package_name}"
                ),
                "value": str(index),
            }
        )
    return choices


def installed_source_phrase(status: InstallationStatus) -> str:
    if status.install_mode == "github":
        ref = status.source_ref or status.version
        return f" - GitHub ref {ref}" if ref else " - GitHub"
    if status.install_mode == "pypi":
        return f" - PyPI version {status.version}" if status.version else " - PyPI"
    if status.install_mode == "wheel":
        return f" - wheel version {status.version}" if status.version else " - wheel"
    if status.install_mode == "editable":
        return f" - editable version {status.version}" if status.version else " - editable"
    if status.version:
        return f" - version {status.version}"
    return ""


def uninstall_target_choice_name(status: InstallationStatus) -> str:
    target = (
        f"{AGENT_LABELS.get(status.agent, status.agent)} "
        f"{SCOPE_LABELS.get(status.scope, status.scope)}"
    )
    return f"  {target}{installed_source_phrase(status)}"


def uninstall_target_choices(
    statuses: list[InstallationStatus],
) -> list[dict[str, object]]:
    skill_name = installation_name(statuses[0])
    choices: list[dict[str, object]] = [
        {
            "name": skill_name,
            "value": f"skill:{skill_name}",
            "disabled": True,
            "kind": "group",
        },
        {
            "name": "  All installed targets",
            "description": "Uninstall this skill from every listed target.",
            "value": TARGET_ALL,
            "kind": "all",
        },
    ]
    choices.extend(
        {
            "name": uninstall_target_choice_name(status),
            "description": installation_choice_description(status),
            "value": str(index),
            "kind": "target",
        }
        for index, status in enumerate(statuses)
    )
    return choices


def agent_arg_for_statuses(statuses: list[InstallationStatus]) -> str:
    selected = [
        agent
        for agent in AGENTS
        if any(status.agent == agent for status in statuses)
    ]
    if set(selected) == set(AGENTS):
        return TARGET_ALL
    return ",".join(selected)


def build_uninstall_command_for_statuses(
    args: argparse.Namespace,
    statuses: list[InstallationStatus],
) -> str | None:
    if not statuses:
        return None

    grouped_commands: list[str] = []
    groups: dict[tuple[str, str, str, str], list[InstallationStatus]] = {}
    for status in statuses:
        repo = repo_from_status(status)
        groups.setdefault(
            (
                installation_name(status),
                status.package_name or installation_name(status),
                status.scope,
                str(repo or ""),
            ),
            [],
        ).append(status)

    for group_statuses in groups.values():
        preview_args = copy_args(args)
        status = group_statuses[0]
        apply_uninstall_status(preview_args, status)
        preview_args.agent = agent_arg_for_statuses(group_statuses)
        command = build_no_ui_command(preview_args)
        if command:
            grouped_commands.append(command)

    return "\n".join(grouped_commands) if grouped_commands else None


def matching_uninstall_statuses(args: argparse.Namespace) -> list[InstallationStatus]:
    repo = getattr(args, "repo", None) or repo_root_for_ui(args)
    statuses = discover_managed_installations(
        generic_project(),
        repo=repo,
        home=getattr(args, "home", None),
        codex_home=getattr(args, "codex_home", None),
        claude_home=getattr(args, "claude_home", None),
    )
    skill_name = getattr(args, "skill_name", None)
    if skill_name is not None:
        normalized = normalize_skill_name(skill_name)
        statuses = [
            status
            for status in statuses
            if installation_name(status) == normalized
        ]
    agent = getattr(args, "agent", None)
    if agent is not None:
        selected_agents = set(normalize_agents([agent]))
        statuses = [
            status
            for status in statuses
            if status.agent in selected_agents
        ]
    scope = getattr(args, "scope", None)
    if scope is not None:
        statuses = [
            status
            for status in statuses
            if status.scope == scope
        ]
    return sorted(
        statuses,
        key=lambda status: (
            installation_name(status),
            status.agent,
            status.scope,
            str(status.skill_dir or ""),
        ),
    )


def apply_uninstall_status(
    args: argparse.Namespace,
    status: InstallationStatus,
) -> None:
    skill_name = normalize_skill_name(installation_name(status))
    args.skill_name = skill_name
    args.package_name = status.package_name or skill_name
    args.agent = status.agent
    args.scope = status.scope
    if status.scope == "repo":
        args.repo = repo_from_status(status) or getattr(args, "repo", None)


def apply_uninstall_statuses(
    args: argparse.Namespace,
    statuses: list[InstallationStatus],
) -> None:
    if not statuses:
        raise UsageError("choose at least one installed target")
    args.uninstall_statuses = statuses
    apply_uninstall_status(args, statuses[0])
    same_scope = {status.scope for status in statuses}
    same_repo = {str(repo_from_status(status) or "") for status in statuses}
    if len(same_scope) == 1 and len(same_repo) == 1:
        args.agent = agent_arg_for_statuses(statuses)


def complete_install_with_ui(
    args: argparse.Namespace,
    prompter: Prompter,
    *,
    prompt_step: PromptStep | None = None,
) -> object | None:
    def run_prompt(
        fields: Sequence[str],
        prompt: Callable[[], object],
    ) -> object:
        if prompt_step is None:
            return prompt()
        return prompt_step(fields, prompt)

    source = selected_install_source(args)
    source_was_prompted = getattr(args, "_selected_install_source", None) == source
    if source is None:
        if install_source_count(args) > 1:
            clear_install_sources(args)
        selected_source = run_prompt(
            INSTALL_SOURCE_FIELDS,
            lambda: prompter.select(
                "Install source",
                install_source_choices(),
                command_preview_builder=lambda value: preview_source_command(args, value),
                summary_builder=lambda value: install_source_summary(args, source=value),
            ),
        )
        if selected_source == PROMPT_BACK:
            return PROMPT_BACK
        source = str(selected_source)
        source_was_prompted = True
        clear_install_sources(args)
        args._selected_install_source = source

    if source == "pypi":
        version_choices: list[dict[str, str]] | None = None
        if args.pypi_package is None:
            package_choices = recent_pypi_package_choices(args)
            default_package = (
                package_choices[0]["value"]
                if package_choices
                else ""
            )

            def validate_pypi_package(value: str) -> str | None:
                nonlocal version_choices
                package_name = value.strip() or default_package
                if not package_name:
                    return "PyPI package name must not be empty"
                try:
                    version_choices = required_generic_pypi_version_choices(
                        package_name
                    )
                except UsageError as error:
                    return str(error)
                return None

            package_result = run_prompt(
                INSTALL_SOURCE_FIELDS,
                lambda: prompter.version(
                    "PyPI package name",
                    default_package,
                    package_choices,
                    command_preview_builder=lambda value: preview_command(
                        args,
                        command="install",
                        pypi_package=str(value).strip() or default_package or None,
                    ),
                    summary_builder=lambda value: install_decision_summary(
                        args,
                        command="install",
                        source="pypi",
                        pypi_package=str(value).strip() or default_package or None,
                    ),
                    validator=validate_pypi_package,
                ),
            )
            if package_result == PROMPT_BACK:
                return PROMPT_BACK
            package = str(package_result).strip()
            if not package:
                raise UsageError("PyPI package name must not be empty")
            args.pypi_package = package
        if source_was_prompted and args.pypi_version is None:
            if version_choices is None:
                version_choices = required_generic_pypi_version_choices(
                    args.pypi_package
                )
            default_version = (
                version_choices[0]["value"]
                if version_choices
                else ""
            )

            def validate_pypi_version(value: str) -> str | None:
                version = str(value).strip() or default_version or None
                try:
                    validate_selected_pypi_skill_package(
                        args,
                        package=args.pypi_package,
                        version=version,
                    )
                except (InstallerError, InstallerConfigError, UsageError) as error:
                    return str(error)
                return None

            version_result = run_prompt(
                ["pypi_version", *VALIDATED_PYPI_FIELDS],
                lambda: prompter.version(
                    "PyPI package version",
                    default_version,
                    version_choices,
                    command_preview_builder=lambda value: preview_command(
                        args,
                        command="install",
                        pypi_version=str(value).strip() or default_version or None,
                    ),
                    summary_builder=lambda value: install_decision_summary(
                        args,
                        command="install",
                        source="pypi",
                        pypi_version=str(value).strip() or default_version,
                    ),
                    validator=validate_pypi_version,
                ),
            )
            if version_result == PROMPT_BACK:
                return PROMPT_BACK
            version = str(version_result).strip()
            args.pypi_version = version or None
        version = resolve_pypi_version(args)
        if validated_pypi_download(args, version) is None:
            validate_pypi_skill_package(args)
    elif source == "wheel":
        if args.wheel_file is None:
            wheel_file = run_prompt(
                ["wheel_file", *VALIDATED_WHEEL_FIELDS],
                lambda: prompter.path(
                    "Wheel file",
                    default_wheel_file(),
                    command_preview_builder=lambda value: preview_command(
                        args,
                        command="install",
                        wheel_file=Path(str(value)),
                    ),
                    summary_builder=lambda value: install_decision_summary(
                        args,
                        command="install",
                        source="wheel",
                        wheel_file=Path(str(value)),
                    ),
                ),
            )
            if wheel_file == PROMPT_BACK:
                return PROMPT_BACK
            args.wheel_file = wheel_file
        if validated_wheel_file(args) is None:
            validate_wheel_file(args)
    elif source == "github":
        if args.github_url is None:
            url_choices = recent_github_url_choices(args)
            default_url = (
                url_choices[0]["value"]
                if url_choices
                else ""
            )

            def validate_github_url(value: str) -> str | None:
                url = str(value).strip() or default_url
                if not url:
                    return "GitHub URL must not be empty"
                try:
                    validate_selected_github_skill_source(args, url=url)
                except (InstallerError, InstallerConfigError, UsageError) as error:
                    return str(error)
                return None

            url_result = run_prompt(
                ["github_url", "github_ref", "github_path", *VALIDATED_GITHUB_FIELDS],
                lambda: prompter.version(
                    "GitHub repository URL",
                    default_url,
                    url_choices,
                    command_preview_builder=lambda value: preview_command(
                        args,
                        command="install",
                        github_url=str(value).strip() or default_url or None,
                    ),
                    summary_builder=lambda value: install_decision_summary(
                        args,
                        command="install",
                        source="github",
                        github_url=str(value).strip() or default_url or None,
                    ),
                    validator=validate_github_url,
                ),
            )
            if url_result == PROMPT_BACK:
                return PROMPT_BACK
            url = str(url_result).strip()
            if not url:
                raise UsageError("GitHub URL must not be empty")
            args.github_url = url
        source = parse_github_url(
            args.github_url,
            ref=args.github_ref,
            path=args.github_path,
        )
        if validated_github_download(args, source) is None:
            validate_github_skill_source(args)
    elif source == "local":
        if args.skill_path is None:
            skill_path = run_prompt(
                ["skill_path"],
                lambda: prompter.path(
                    "Local repo or skill directory",
                    default_skill_path(),
                    command_preview_builder=lambda value: preview_command(
                        args,
                        command="install",
                        skill_path=Path(str(value)),
                    ),
                    summary_builder=lambda value: install_decision_summary(
                        args,
                        command="install",
                        source="local",
                        skill_path=Path(str(value)),
                    ),
                ),
            )
            if skill_path == PROMPT_BACK:
                return PROMPT_BACK
            args.skill_path = skill_path
        if args.editable is None:
            install_mode = run_prompt(
                ["editable"],
                lambda: prompter.select(
                    "Local install mode",
                    local_install_mode_choices(),
                    command_preview_builder=lambda value: preview_command(
                        args,
                        command="install",
                        editable=editable_from_mode(value),
                    ),
                    summary_builder=lambda value: install_decision_summary(
                        args,
                        command="install",
                        source="local",
                        editable=value,
                    ),
                ),
            )
            if install_mode == PROMPT_BACK:
                return PROMPT_BACK
            args.editable = editable_from_mode(install_mode)
    else:
        raise UsageError(f"unknown install source: {source}")
    return None


def complete_install_targets_with_ui(
    args: argparse.Namespace,
    prompter: Prompter,
    *,
    prompt_step: PromptStep | None = None,
) -> object | None:
    def run_prompt(
        fields: Sequence[str],
        prompt: Callable[[], object],
    ) -> object:
        if prompt_step is None:
            return prompt()
        return prompt_step(fields, prompt)

    if args.agent is None:
        def agent_preview(selected_values: object) -> str | None:
            values = (
                list(selected_values)
                if isinstance(selected_values, (list, tuple, set))
                else [str(selected_values)]
            )
            if not values:
                return None
            selected_agents_from_values(values)
            return preview_command(
                args,
                agent=agent_arg_from_values(values),
                scope=getattr(args, "scope", None) or "global",
            )

        def agent_summary(selected_values: object) -> str | None:
            values = (
                list(selected_values)
                if isinstance(selected_values, (list, tuple, set))
                else [str(selected_values)]
            )
            if not values:
                return install_decision_summary(args, command="install")
            return install_decision_summary(
                args,
                command="install",
                agent=agent_arg_from_values(values),
                scope=getattr(args, "scope", None) or "global",
            )

        selected_values = run_prompt(
            TARGET_FIELDS,
            lambda: prompter.checkbox(
                "Select agents",
                target_choices(),
                command_preview_builder=agent_preview,
                summary_builder=agent_summary,
                default_values=[TARGET_ALL],
            ),
        )
        if selected_values == PROMPT_BACK:
            return PROMPT_BACK
        args.agent = agent_arg_from_values(selected_values)

    if args.scope is None:
        def scope_preview(scope: object) -> str | None:
            selected_scope = str(scope)
            if selected_scope == "specific":
                return None
            repo = repo_root_for_ui(args) if selected_scope == "repo" else None
            return preview_command(
                args,
                scope=selected_scope,
                repo=repo,
            )

        def scope_summary(scope: object) -> str | None:
            selected_scope = str(scope)
            if selected_scope == "specific":
                return install_decision_summary(args, command="install")
            repo = repo_root_for_ui(args) if selected_scope == "repo" else None
            return install_decision_summary(
                args,
                command="install",
                scope=selected_scope,
                repo=repo,
            )

        scope_result = run_prompt(
            ["scope", "repo"],
            lambda: prompter.select(
                "Install location",
                scope_choices(),
                command_preview_builder=scope_preview,
                summary_builder=scope_summary,
                submit_label="Install",
            ),
        )
        if scope_result == PROMPT_BACK:
            return PROMPT_BACK
        selected_scope = str(scope_result)
        if selected_scope == "specific":
            repo_result = run_prompt(
                ["scope", "repo"],
                lambda: prompter.path(
                    "Repository path",
                    default_repo_path(),
                    command_preview_builder=lambda value: preview_command(
                        args,
                        scope="repo",
                        repo=Path(str(value)),
                    ),
                    summary_builder=lambda value: install_decision_summary(
                        args,
                        command="install",
                        scope="repo",
                        repo=Path(str(value)),
                    ),
                    submit_label="Install",
                ),
            )
            if repo_result == PROMPT_BACK:
                return PROMPT_BACK
            args.scope = "repo"
            args.repo = repo_result
        else:
            args.scope = selected_scope

    if args.scope == "repo" and args.repo is None:
        repo = repo_root_for_ui(args)
        if repo is not None:
            args.repo = repo
        else:
            repo_result = run_prompt(
                ["repo"],
                lambda: prompter.path(
                    "Repository path",
                    default_repo_path(),
                    command_preview_builder=lambda value: preview_command(
                        args,
                        scope="repo",
                        repo=Path(str(value)),
                    ),
                    summary_builder=lambda value: install_decision_summary(
                        args,
                        command="install",
                        scope="repo",
                        repo=Path(str(value)),
                    ),
                    submit_label="Install",
                ),
            )
            if repo_result == PROMPT_BACK:
                return PROMPT_BACK
            args.repo = repo_result
    return None


def complete_uninstall_with_ui(
    args: argparse.Namespace,
    prompter: Prompter,
    *,
    prompt_step: PromptStep | None = None,
) -> object | None:
    def run_prompt(
        fields: Sequence[str],
        prompt: Callable[[], object],
    ) -> object:
        if prompt_step is None:
            return prompt()
        return prompt_step(fields, prompt)

    if (
        args.skill_name is not None
        and args.agent is not None
        and args.scope is not None
    ):
        return None

    statuses = matching_uninstall_statuses(args)
    if not statuses:
        raise UsageError("no installed skills were found for the selected target roots")

    groups = grouped_uninstall_statuses(statuses)
    group_values = list(groups.values())
    if args.skill_name is None:
        def uninstall_group_preview(selected_group: object) -> str | None:
            selected = str(selected_group)
            if not selected.isdigit():
                return None
            index = int(selected)
            if index >= len(group_values):
                return None
            return build_uninstall_command_for_statuses(args, group_values[index])

        def uninstall_group_summary(selected_group: object) -> str:
            selected = str(selected_group)
            if selected.isdigit():
                index = int(selected)
                if index < len(group_values):
                    return f"Uninstalling {installation_name(group_values[index][0])}"
            return "Uninstalling a skill"

        selected_skill = run_prompt(
            UNINSTALL_FIELDS,
            lambda: prompter.select(
                "Skills installed by Agent Skill Installer",
                installed_skill_group_choices(groups),
                command_preview_builder=uninstall_group_preview,
                summary_builder=uninstall_group_summary,
            ),
        )
        if selected_skill == PROMPT_BACK:
            return PROMPT_BACK
        target_statuses = group_values[int(str(selected_skill))]
    else:
        target_statuses = (
            group_values[0]
            if args.skill_name is None
            else statuses
        )

    def uninstall_preview(selected_values: object) -> str | None:
        selected = (
            list(selected_values)
            if isinstance(selected_values, (list, tuple, set))
            else [str(selected_values)]
        )
        if TARGET_ALL in {str(value) for value in selected}:
            selected_statuses = target_statuses
        else:
            selected_statuses = [
                target_statuses[int(value)]
                for value in selected
                if str(value).isdigit()
            ]
        return build_uninstall_command_for_statuses(args, selected_statuses)

    default_values = ["0"] if target_statuses else None
    selected_targets = run_prompt(
        UNINSTALL_FIELDS,
        lambda: prompter.checkbox(
            f"Select targets to uninstall for {installation_name(target_statuses[0])}",
            uninstall_target_choices(target_statuses),
            command_preview_builder=uninstall_preview,
            default_values=default_values,
            submit_label="Uninstall",
        ),
    )
    if selected_targets == PROMPT_BACK:
        return PROMPT_BACK
    if TARGET_ALL in {str(value) for value in selected_targets}:
        selected_statuses = target_statuses
    else:
        selected_statuses = [
            target_statuses[int(value)]
            for value in selected_targets
            if str(value).isdigit()
        ]
    apply_uninstall_statuses(args, selected_statuses)
    return None


def complete_with_ui(
    args: argparse.Namespace,
    prompter: Prompter | None = None,
) -> argparse.Namespace:
    ensure_arg_defaults(args)
    prompter = prompter or TextualPrompter(generic_project())
    print("agent-skill-installer")

    missing = object()
    history: list[Callable[[], None]] = []

    def capture(fields: Sequence[str]) -> dict[str, object]:
        return {
            field: getattr(args, field, missing)
            for field in fields
        }

    def restore(snapshot: dict[str, object]) -> None:
        for field, value in snapshot.items():
            if value is missing:
                if hasattr(args, field):
                    delattr(args, field)
            else:
                setattr(args, field, value)

    def prompt_step(
        fields: Sequence[str],
        prompt: Callable[[], object],
    ) -> object:
        snapshot = capture(fields)
        try:
            result = prompt()
        except BackRequested:
            if not history:
                raise KeyboardInterrupt from None
            history.pop()()
            return PROMPT_BACK
        history.append(lambda snapshot=snapshot: restore(snapshot))
        return result

    while True:
        if args.command is None:
            command = prompt_step(
                COMMAND_FIELDS,
                lambda: prompter.select(
                    "What would you like to do?",
                    command_choices(),
                    command_preview_builder=lambda selected_command: preview_command_choice(
                        args,
                        selected_command,
                    ),
                    summary_builder=lambda selected_command: install_decision_summary(
                        args,
                        command=selected_command,
                    ),
                ),
            )
            if command == PROMPT_BACK:
                continue
            args.command = str(command)
            continue

        if args.command == "install":
            result = complete_install_with_ui(
                args,
                prompter,
                prompt_step=prompt_step,
            )
            if result == PROMPT_BACK:
                continue
            result = complete_install_targets_with_ui(
                args,
                prompter,
                prompt_step=prompt_step,
            )
            if result == PROMPT_BACK:
                continue
            break
        if args.command == "uninstall":
            result = complete_uninstall_with_ui(
                args,
                prompter,
                prompt_step=prompt_step,
            )
            if result == PROMPT_BACK:
                continue
            break
        raise UsageError(f"unknown command: {args.command}")

    return args


def needs_ui(args: argparse.Namespace) -> bool:
    ensure_arg_defaults(args)
    if args.command is None:
        return True
    if args.command == "install":
        return (
            install_source_count(args) != 1
            or (
                getattr(args, "skill_path", None) is not None
                and getattr(args, "editable", None) is None
            )
            or args.agent is None
            or args.scope is None
        )
    if args.command == "uninstall":
        return (
            args.skill_name is None
            or args.agent is None
            or args.scope is None
        )
    return False


def require_noninteractive_args(args: argparse.Namespace) -> None:
    ensure_arg_defaults(args)
    if args.command is None:
        raise UsageError("choose install or uninstall")
    if args.command not in {"install", "uninstall"}:
        raise UsageError(f"unknown command: {args.command}")
    if args.agent is None:
        raise UsageError("--agent is required when the text UI is disabled")
    normalize_agents([args.agent])
    if args.scope is None:
        raise UsageError("--scope is required when the text UI is disabled")

    if args.command == "install":
        selected_sources = [
            name
            for name, value in (
                ("--pypi-package", args.pypi_package),
                ("--wheel-file", args.wheel_file),
                ("--github-url", args.github_url),
                ("--skill-path", args.skill_path),
            )
            if value is not None
        ]
        if len(selected_sources) != 1:
            raise UsageError(
                "choose exactly one install source: "
                "--pypi-package, --wheel-file, --github-url, or --skill-path"
            )
        if args.editable is not None and args.skill_path is None:
            flag = "--editable" if args.editable else "--copy"
            raise UsageError(f"{flag} requires --skill-path")
        if args.pypi_package is not None:
            args.pypi_package = args.pypi_package.strip()
            if not args.pypi_package:
                raise UsageError("--pypi-package must not be empty")
        if args.pypi_version is not None:
            args.pypi_version = args.pypi_version.strip()
            if not args.pypi_version:
                raise UsageError("--pypi-version must not be empty")
        if args.wheel_file is not None:
            if not str(args.wheel_file).strip():
                raise UsageError("--wheel-file must not be empty")
        if args.github_url is not None:
            args.github_url = args.github_url.strip()
            if not args.github_url:
                raise UsageError("--github-url must not be empty")
        if args.github_ref is not None:
            args.github_ref = args.github_ref.strip()
            if not args.github_ref:
                raise UsageError("--github-ref must not be empty")
        if args.github_path is not None:
            args.github_path = args.github_path.strip()
            if not args.github_path:
                raise UsageError("--github-path must not be empty")
        if args.github_url is None and (
            args.github_ref is not None or args.github_path is not None
        ):
            raise UsageError("--github-ref and --github-path require --github-url")
    else:
        if args.skill_name is None:
            raise UsageError("--skill-name is required when the text UI is disabled")
        args.skill_name = normalize_skill_name(args.skill_name)


def normalize_skill_name(value: str) -> str:
    skill_name = value.strip()
    if (
        not skill_name
        or skill_name in {".", ".."}
        or "/" in skill_name
        or "\\" in skill_name
    ):
        raise InstallerError(f"invalid skill name: {value!r}")
    return skill_name


def unquote_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def skill_metadata(skill_text: str) -> dict[str, str]:
    if not skill_text.startswith("---\n"):
        return {}
    end = skill_text.find("\n---", 4)
    if end == -1:
        return {}
    metadata: dict[str, str] = {}
    for line in skill_text[4:end].splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = key.strip()
        if key in {"name", "description", "version"}:
            metadata[key] = unquote_scalar(value)
    return metadata


def description_from_skill_text(skill_text: str, skill_name: str) -> str:
    metadata = skill_metadata(skill_text)
    description = metadata.get("description")
    if description:
        return description
    for paragraph in skill_text.split("\n\n"):
        text = paragraph.strip()
        if not text or text.startswith("---") or text.startswith("#"):
            continue
        return " ".join(text.split())
    return f"Use the {skill_name} agent skill."


def project_from_skill_text(
    *,
    skill_text: str,
    fallback_name: str,
    version: str,
    source_dir: Path | None = None,
    installer_config: InstallerConfig | None = None,
    skill_name: str | None = None,
    description: str | None = None,
    pypi_project_name: str | None = None,
) -> SkillProject:
    metadata = skill_metadata(skill_text)
    resolved_name = normalize_skill_name(
        skill_name or metadata.get("name") or fallback_name
    )
    return SkillProject(
        package_name=resolved_name,
        import_name=GENERIC_IMPORT_NAME,
        version=metadata.get("version") or version,
        skill_name=resolved_name,
        cli_name="agent-skill-installer",
        description=description or description_from_skill_text(skill_text, resolved_name),
        installer_config=installer_config or InstallerConfig(),
        bundled_skill_source=source_dir,
        pypi_project_name=pypi_project_name,
    )


def read_local_project(args: argparse.Namespace) -> SkillProject:
    selected_source = args.skill_path.expanduser().resolve()
    local_source = local_skill_source_for_candidate(selected_source)
    if local_source is None:
        raise InstallerError(
            "local source must contain SKILL.md or skill/SKILL.md: "
            f"{selected_source}"
        )
    local_root, source = local_source
    skill_file = source / "SKILL.md"
    config = (
        load_installer_config(source / CONFIG_FILE_NAME)
        if (source / CONFIG_FILE_NAME).is_file()
        else None
    )
    fallback_name = local_root.name if source.name == "skill" else source.name
    return project_from_skill_text(
        skill_text=skill_file.read_text(),
        fallback_name=fallback_name,
        version="local",
        source_dir=source,
        installer_config=config,
        skill_name=args.skill_name,
        description=args.description,
    )


def prefixed_path(prefix: PurePosixPath, name: str) -> PurePosixPath:
    return prefix / name if prefix.parts else PurePosixPath(name)


def read_prefixed_text(
    archive: zipfile.ZipFile,
    prefix: PurePosixPath,
    name: str,
    relative_path_for: Callable[[str], PurePosixPath | None],
) -> str | None:
    target = prefixed_path(prefix, name)
    for info in archive.infolist():
        if relative_path_for(info.filename) == target:
            return archive.read(info).decode()
    return None


def github_fallback_name(source: GithubSource) -> str:
    if source.path is not None and source.path.name != "skill":
        return source.path.name
    return source.repo


def read_github_project(
    args: argparse.Namespace,
    archive_path: Path,
    source: GithubSource,
) -> SkillProject:
    with zipfile.ZipFile(archive_path) as archive:
        skill_prefix = github_archive_skill_prefix(archive, source.path)
        skill_text = read_prefixed_text(
            archive,
            skill_prefix,
            "SKILL.md",
            github_archive_relative_path,
        )
        if skill_text is None:
            raise InstallerError("GitHub archive did not contain SKILL.md")
        config_text = read_prefixed_text(
            archive,
            skill_prefix,
            CONFIG_FILE_NAME,
            github_archive_relative_path,
        )
    config = (
        load_installer_config_text(config_text, source=f"{source.url}/{CONFIG_FILE_NAME}")
        if config_text is not None
        else None
    )
    return project_from_skill_text(
        skill_text=skill_text,
        fallback_name=github_fallback_name(source),
        version=source.version_label,
        installer_config=config,
        skill_name=args.skill_name,
        description=args.description,
    )


def wheel_skill_prefix(archive: zipfile.ZipFile) -> PurePosixPath:
    candidates: list[PurePosixPath] = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts or path.name != "SKILL.md":
            continue
        candidates.append(path.parent)
    if not candidates:
        raise InstallerError("wheel did not contain a bundled SKILL.md")
    candidates.sort(key=lambda item: (item.name != "_skill", len(item.parts)))
    return candidates[0]


def wheel_filename_metadata(wheel_path: Path) -> tuple[str, str]:
    name = wheel_path.name
    stem = name[:-4] if name.endswith(".whl") else wheel_path.stem
    parts = stem.split("-")
    if len(parts) >= 2:
        return parts[0].replace("_", "-"), parts[1]
    return wheel_path.stem.replace("_", "-"), "local"


def wheel_archive_metadata(archive: zipfile.ZipFile) -> dict[str, str]:
    for info in archive.infolist():
        if info.is_dir():
            continue
        path = PurePosixPath(info.filename)
        if len(path.parts) != 2:
            continue
        if not path.parts[0].endswith(".dist-info") or path.name != "METADATA":
            continue
        text = archive.read(info).decode("utf-8", errors="replace")
        message = Parser().parsestr(text)
        return {
            "name": (message.get("Name") or "").strip(),
            "version": (message.get("Version") or "").strip(),
        }
    return {}


def read_pypi_project(
    args: argparse.Namespace,
    wheel_path: Path,
    version: str | None,
) -> SkillProject:
    fallback_package, fallback_version = wheel_filename_metadata(wheel_path)
    try:
        with zipfile.ZipFile(wheel_path) as archive:
            prefix = wheel_skill_prefix(archive)
            archive_metadata = wheel_archive_metadata(archive)
            skill_text = read_prefixed_text(archive, prefix, "SKILL.md", PurePosixPath)
            if skill_text is None:
                raise InstallerError("wheel did not contain SKILL.md")
            config_text = read_prefixed_text(
                archive,
                prefix,
                CONFIG_FILE_NAME,
                PurePosixPath,
            )
    except zipfile.BadZipFile as error:
        raise InstallerError(f"wheel file is not a valid zip file: {wheel_path}") from error

    package_name = (
        getattr(args, "pypi_package", None)
        or archive_metadata.get("name")
        or fallback_package
    )
    wheel_version = version or archive_metadata.get("version") or fallback_version

    config = (
        load_installer_config_text(
            config_text,
            source=f"{package_name} wheel/{prefix.as_posix()}/{CONFIG_FILE_NAME}",
        )
        if config_text is not None
        else None
    )
    import_name = prefix.parts[0] if prefix.parts else GENERIC_IMPORT_NAME
    bundled_skill_path = (
        PurePosixPath(*prefix.parts[1:]).as_posix()
        if len(prefix.parts) > 1
        else "_skill"
    )
    metadata = skill_metadata(skill_text)
    skill_name = normalize_skill_name(
        args.skill_name
        or metadata.get("name")
        or package_name
    )
    return SkillProject(
        package_name=skill_name,
        import_name=import_name,
        version=metadata.get("version") or wheel_version,
        skill_name=skill_name,
        cli_name="agent-skill-installer",
        description=args.description or description_from_skill_text(skill_text, skill_name),
        installer_config=config or InstallerConfig(),
        bundled_skill_path=bundled_skill_path,
        pypi_project_name=getattr(args, "pypi_package", None),
    )


def resolve_pypi_version(args: argparse.Namespace) -> str:
    if args.pypi_version is not None:
        version = args.pypi_version.strip()
        if not version:
            raise InstallerError("--pypi-version must not be empty")
        return version
    project = SkillProject(
        package_name=args.pypi_package,
        import_name=GENERIC_IMPORT_NAME,
        version="0",
        skill_name=args.pypi_package,
        description="",
        pypi_project_name=args.pypi_package,
    )
    versions = published_pypi_versions(project, limit=1)
    if not versions:
        raise InstallerError(f"no wheel releases found on PyPI for {args.pypi_package}")
    return versions[0]


def install_for_targets(
    project: SkillProject,
    args: argparse.Namespace,
    *,
    editable_source_dir: Path | None = None,
    github_source: GithubSource | None = None,
    github_archive_path: Path | None = None,
    pypi_version: str | None = None,
    pypi_wheel_path: Path | None = None,
) -> list[InstallResult]:
    validate_install_source_selection(
        editable=editable_source_dir is not None,
        pypi_version=pypi_version,
        wheel_path=pypi_wheel_path if pypi_version is None else None,
        github_source=github_source,
    )
    repo = args.repo if args.scope == "repo" else None
    return [
        install_target(
            project,
            target_spec(
                project,
                agent,
                args.scope,
                repo=repo,
                home=args.home,
                codex_home=args.codex_home,
                claude_home=args.claude_home,
            ),
            force=args.force,
            editable_source_dir=editable_source_dir,
            pypi_version=pypi_version,
            pypi_wheel_path=pypi_wheel_path,
            github_source=github_source,
            github_archive_path=github_archive_path,
        )
        for agent in normalize_agents([args.agent])
    ]


def run_install(args: argparse.Namespace) -> list[InstallResult]:
    if args.github_url is None and (
        args.github_ref is not None or args.github_path is not None
    ):
        raise InstallerError("--github-ref and --github-path require --github-url")
    if args.pypi_package is None and args.pypi_version is not None:
        raise InstallerError("--pypi-version requires --pypi-package")

    if args.skill_path is not None:
        project = read_local_project(args)
        if project.bundled_skill_source is None:
            raise InstallerError("local install source was not resolved")
        editable_source_dir = (
            project.bundled_skill_source
            if getattr(args, "editable", None) is not False
            else None
        )
        return install_for_targets(
            project,
            args,
            editable_source_dir=editable_source_dir,
        )

    if args.wheel_file is not None:
        validated_wheel = validated_wheel_file(args)
        if validated_wheel is not None:
            wheel_path, project = validated_wheel
        else:
            wheel_path = args.wheel_file.expanduser().resolve()
            if not wheel_path.is_file():
                raise InstallerError(f"wheel file does not exist: {wheel_path}")
            project = read_pypi_project(args, wheel_path, None)
        return install_for_targets(
            project,
            args,
            pypi_wheel_path=wheel_path,
        )

    if args.github_url is not None:
        source = parse_github_url(
            args.github_url,
            ref=args.github_ref,
            path=args.github_path,
        )
        validated_download = validated_github_download(args, source)
        if validated_download is not None:
            archive_path, project = validated_download
            results = install_for_targets(
                project,
                args,
                github_source=source,
                github_archive_path=archive_path,
            )
        else:
            with tempfile.TemporaryDirectory(
                prefix="agent-skill-installer-github-"
            ) as temp_dir:
                archive_path = download_github_archive(source, Path(temp_dir))
                project = read_github_project(args, archive_path, source)
                results = install_for_targets(
                    project,
                    args,
                    github_source=source,
                    github_archive_path=archive_path,
                )
        remember_recent_github_url(args.github_url, home=args.home)
        return results

    assert args.pypi_package is not None
    version = resolve_pypi_version(args)
    validated_download = validated_pypi_download(args, version)
    if validated_download is not None:
        wheel_path, project = validated_download
        results = install_for_targets(
            project,
            args,
            pypi_version=version,
            pypi_wheel_path=wheel_path,
        )
    else:
        download_project = SkillProject(
            package_name=args.pypi_package,
            import_name=GENERIC_IMPORT_NAME,
            version=version,
            skill_name=args.pypi_package,
            description="",
            pypi_project_name=args.pypi_package,
        )
        with tempfile.TemporaryDirectory(
            prefix="agent-skill-installer-pypi-"
        ) as temp_dir:
            wheel_path = download_pypi_wheel(
                download_project,
                version,
                Path(temp_dir),
            )
            project = read_pypi_project(args, wheel_path, version)
            results = install_for_targets(
                project,
                args,
                pypi_version=version,
                pypi_wheel_path=wheel_path,
            )
    remember_recent_pypi_package(args.pypi_package, home=args.home)
    return results


def run_uninstall(args: argparse.Namespace) -> list[InstallResult]:
    selected_statuses = getattr(args, "uninstall_statuses", None)
    if selected_statuses:
        results: list[InstallResult] = []
        for status in selected_statuses:
            target_args = copy_args(args)
            target_args.uninstall_statuses = None
            apply_uninstall_status(target_args, status)
            results.extend(run_uninstall(target_args))
        return results

    skill_name = normalize_skill_name(args.skill_name)
    package_name = getattr(args, "package_name", None) or skill_name
    project = SkillProject(
        package_name=package_name,
        import_name=GENERIC_IMPORT_NAME,
        version="0",
        skill_name=skill_name,
        cli_name="agent-skill-installer",
        description=f"Use the {skill_name} agent skill.",
    )
    return Installer(project).uninstall(
        [args.agent],
        args.scope,
        repo=args.repo if args.scope == "repo" else None,
        home=args.home,
        codex_home=args.codex_home,
        claude_home=args.claude_home,
    )


def run(args: argparse.Namespace) -> list[InstallResult]:
    if args.command == "install":
        return run_install(args)
    if args.command == "uninstall":
        return run_uninstall(args)
    raise InstallerError(f"unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    try:
        ensure_arg_defaults(args)
        if not args.no_ui and running_on_tty() and needs_ui(args):
            complete_with_ui(args)
        else:
            require_noninteractive_args(args)
        results = run(args)
    except (KeyboardInterrupt, BackRequested):
        print("\nCancelled.", file=sys.stderr)
        return 130
    except (UsageError, CliUsageError) as error:
        print(f"agent-skill-installer: error: {error}", file=sys.stderr)
        return 2
    except (InstallerError, InstallerConfigError) as error:
        print(f"agent-skill-installer: error: {error}", file=sys.stderr)
        return 1
    finally:
        cleanup_validated_pypi_download(args)
        cleanup_validated_wheel_file(args)
        cleanup_validated_github_download(args)

    print_results(results, verbose=bool(getattr(args, "verbose", False)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
