from __future__ import annotations

import argparse
from dataclasses import replace
from email.parser import Parser
import json
import shutil
import shlex
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Sequence

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
    PlatformSpecific,
    PlatformSelectorConfig,
    SELECTOR_FILE_NAME,
    load_installer_config,
    load_installer_config_text,
    load_platform_selector_config,
    load_platform_selector_config_text,
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
    install_target,
    local_skill_source_for_candidate,
    manifest_path,
    normalize_agents,
    parse_github_url,
    published_pypi_versions,
    read_manifest,
    render_platform_template,
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
    "_validated_pypi_resolved_package",
    "_validated_pypi_version",
    "_validated_pypi_wheel_path",
    "_validated_pypi_project",
    "_validated_pypi_projects",
    "_validated_pypi_temp_dir",
]
VALIDATED_WHEEL_FIELDS = [
    "_validated_wheel_file",
    "_validated_wheel_project",
    "_validated_wheel_projects",
]
VALIDATED_GITHUB_FIELDS = [
    "_validated_github_source",
    "_validated_github_archive_path",
    "_validated_github_project",
    "_validated_github_projects",
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
    "src_skills",
    "all_src_skills",
    "renames",
    "dst_skill",
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
SOURCE_SKILL_ALL = "__agent_skill_installer_all_source_skills__"
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
        dest="skill_name",
        help="Override the installed skill directory name.",
    )
    install.add_argument(
        "--dst-skill",
        dest="dst_skill",
        help="Install the single selected source skill under this name.",
    )
    install.add_argument(
        "--src-skill",
        dest="src_skills",
        action="append",
        metavar="NAME",
        help="Include this source skill. Repeat to include more than one.",
    )
    install.add_argument(
        "--all-src-skills",
        action="store_true",
        help="Install every discovered source skill, including future additions.",
    )
    install.add_argument(
        "--rename",
        dest="renames",
        action="append",
        metavar="SRC:DST",
        help="Include source skill SRC and install it as DST. Repeatable.",
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
        for src_skill in getattr(args, "src_skills", None) or []:
            parts.extend(["--src-skill", src_skill])
        if getattr(args, "all_src_skills", False):
            parts.append("--all-src-skills")
        for rename in getattr(args, "renames", None) or []:
            parts.extend(["--rename", rename])
        dst_skill = getattr(args, "dst_skill", None)
        if dst_skill:
            parts.extend(["--dst-skill", dst_skill])

    skill_name = getattr(args, "skill_name", None)
    if command == "uninstall" and not skill_name:
        return None
    if skill_name and command == "install":
        parts.extend(["--skill-name", skill_name])
    elif skill_name:
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
        "src_skills": None,
        "all_src_skills": False,
        "renames": None,
        "dst_skill": None,
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
    requested_package = args.pypi_package
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
        resolved_package, wheel_path = resolve_platform_specific_pypi_wheel(
            requested_package,
            wheel_path,
            version,
            Path(temp_dir.name),
        )
        projects = read_pypi_projects(
            args,
            wheel_path,
            version,
            pypi_project_name=resolved_package,
        )
    except Exception:
        temp_dir.cleanup()
        raise
    args.pypi_version = version
    args._validated_pypi_package = requested_package
    args._validated_pypi_resolved_package = resolved_package
    args._validated_pypi_version = version
    args._validated_pypi_wheel_path = wheel_path
    args._validated_pypi_project = projects[0]
    args._validated_pypi_projects = projects
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
) -> tuple[Path, list[SkillProject]] | None:
    if getattr(args, "_validated_pypi_package", None) != args.pypi_package:
        return None
    if getattr(args, "_validated_pypi_version", None) != version:
        return None
    wheel_path = getattr(args, "_validated_pypi_wheel_path", None)
    projects = getattr(args, "_validated_pypi_projects", None)
    if not isinstance(wheel_path, Path) or not wheel_path.is_file():
        return None
    if not isinstance(projects, list) or not all(
        isinstance(project, SkillProject) for project in projects
    ):
        return None
    return wheel_path, projects


def validate_wheel_file(args: argparse.Namespace) -> None:
    wheel_path = args.wheel_file.expanduser().resolve()
    if not wheel_path.is_file():
        raise InstallerError(f"wheel file does not exist: {wheel_path}")
    wheel_path = resolve_platform_specific_wheel_file(wheel_path)
    projects = read_pypi_projects(args, wheel_path, None)
    args.wheel_file = wheel_path
    args._validated_wheel_file = wheel_path
    args._validated_wheel_project = projects[0]
    args._validated_wheel_projects = projects


def validated_wheel_file(
    args: argparse.Namespace,
) -> tuple[Path, list[SkillProject]] | None:
    wheel_path = getattr(args, "_validated_wheel_file", None)
    projects = getattr(args, "_validated_wheel_projects", None)
    if not isinstance(wheel_path, Path) or not wheel_path.is_file():
        return None
    if not isinstance(projects, list) or not all(
        isinstance(project, SkillProject) for project in projects
    ):
        return None
    current = args.wheel_file.expanduser().resolve()
    if current != wheel_path:
        return None
    return wheel_path, projects


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
        projects = read_github_projects(args, archive_path, source)
    except Exception:
        temp_dir.cleanup()
        raise
    args._validated_github_source = source
    args._validated_github_archive_path = archive_path
    args._validated_github_project = projects[0]
    args._validated_github_projects = projects
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
) -> tuple[Path, list[SkillProject]] | None:
    if getattr(args, "_validated_github_source", None) != source:
        return None
    archive_path = getattr(args, "_validated_github_archive_path", None)
    projects = getattr(args, "_validated_github_projects", None)
    if not isinstance(archive_path, Path) or not archive_path.is_file():
        return None
    if not isinstance(projects, list) or not all(
        isinstance(project, SkillProject) for project in projects
    ):
        return None
    return archive_path, projects


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


def source_skill_choices(projects: Sequence[SkillProject]) -> list[dict[str, object]]:
    choices: list[dict[str, object]] = [
        {
            "name": "All source skills",
            "description": "Install every discovered source skill.",
            "value": SOURCE_SKILL_ALL,
            "kind": "all",
        }
    ]
    choices.extend(
        {
            "name": source_skill_name(project),
            "description": project.description,
            "value": source_skill_name(project),
            "kind": "skill",
        }
        for project in projects
    )
    return choices


def install_source_projects_for_ui(args: argparse.Namespace) -> list[SkillProject]:
    source = selected_install_source(args)
    if source == "local" and args.skill_path is not None:
        return read_local_projects(args)
    if source == "wheel":
        validated = validated_wheel_file(args)
        return [] if validated is None else validated[1]
    if source == "github" and args.github_url is not None:
        parsed = parse_github_url(
            args.github_url,
            ref=args.github_ref,
            path=args.github_path,
        )
        validated = validated_github_download(args, parsed)
        return [] if validated is None else validated[1]
    if source == "pypi" and args.pypi_package is not None:
        try:
            version = resolve_pypi_version(args)
        except InstallerError:
            return []
        validated = validated_pypi_download(args, version)
        return [] if validated is None else validated[1]
    return []


def source_selection_is_explicit(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "all_src_skills", False)
        or getattr(args, "src_skills", None)
        or getattr(args, "renames", None)
    )


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

    projects = install_source_projects_for_ui(args)
    if len(projects) > 1 and not source_selection_is_explicit(args):
        def source_skill_preview(selected_values: object) -> str | None:
            selected = (
                list(selected_values)
                if isinstance(selected_values, (list, tuple, set))
                else [str(selected_values)]
            )
            if not selected:
                return None
            if SOURCE_SKILL_ALL in {str(value) for value in selected}:
                return preview_command(args, command="install", all_src_skills=True)
            return preview_command(
                args,
                command="install",
                src_skills=[str(value) for value in selected],
            )

        def source_skill_summary(selected_values: object) -> str:
            selected = (
                list(selected_values)
                if isinstance(selected_values, (list, tuple, set))
                else [str(selected_values)]
            )
            if SOURCE_SKILL_ALL in {str(value) for value in selected}:
                return "Installing all source skills"
            count = len(selected)
            plural = "" if count == 1 else "s"
            return f"Installing {count} selected source skill{plural}"

        selected_skills = run_prompt(
            ["src_skills", "all_src_skills"],
            lambda: prompter.checkbox(
                "Select source skills",
                source_skill_choices(projects),
                command_preview_builder=source_skill_preview,
                summary_builder=source_skill_summary,
                empty_message="Choose at least one source skill.",
                accept_highlighted_on_empty=False,
            ),
        )
        if selected_skills == PROMPT_BACK:
            return PROMPT_BACK
        selected = [str(value) for value in selected_skills]
        if not selected:
            raise UsageError("choose at least one source skill")
        if SOURCE_SKILL_ALL in set(selected):
            args.all_src_skills = True
            args.src_skills = None
        else:
            args.src_skills = selected
            args.all_src_skills = False
    elif projects:
        select_source_projects(args, projects)
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
            or install_source_skill_selection_needs_ui(args)
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


def install_source_skill_selection_needs_ui(args: argparse.Namespace) -> bool:
    if source_selection_is_explicit(args):
        return False
    if selected_install_source(args) in {"pypi", "wheel", "github"}:
        return True
    if getattr(args, "skill_path", None) is None:
        return False
    try:
        return len(read_local_projects(args)) > 1
    except (InstallerError, UsageError):
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
    source_skill_path: str | None = None,
) -> SkillProject:
    metadata = skill_metadata(skill_text)
    source_skill_name = normalize_skill_name(metadata.get("name") or fallback_name)
    resolved_name = normalize_skill_name(skill_name or source_skill_name)
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
        source_skill_name=source_skill_name,
        source_skill_path=source_skill_path,
    )


def source_skill_name(project: SkillProject) -> str:
    return project.source_skill_name or project.skill_name


def legacy_target_skill_name(args: argparse.Namespace) -> str | None:
    dst_skill = getattr(args, "dst_skill", None)
    skill_name = getattr(args, "skill_name", None)
    if dst_skill is not None and skill_name is not None and dst_skill != skill_name:
        raise UsageError("--dst-skill and --skill-name cannot specify different names")
    return dst_skill or skill_name


def project_with_target_skill(project: SkillProject, skill_name: str) -> SkillProject:
    normalized = normalize_skill_name(skill_name)
    return replace(
        project,
        package_name=normalized,
        skill_name=normalized,
    )


def read_skill_project_from_dir(
    *,
    source: Path,
    fallback_name: str,
    version: str,
    skill_name: str | None = None,
    description: str | None = None,
    source_skill_path: str | None = None,
) -> SkillProject:
    skill_file = source / "SKILL.md"
    config = (
        load_installer_config(source / CONFIG_FILE_NAME)
        if (source / CONFIG_FILE_NAME).is_file()
        else None
    )
    return project_from_skill_text(
        skill_text=skill_file.read_text(),
        fallback_name=fallback_name,
        version=version,
        source_dir=source,
        installer_config=config,
        skill_name=skill_name,
        description=description,
        source_skill_path=source_skill_path,
    )


def immediate_child_skill_dirs(source: Path) -> list[Path]:
    if not source.is_dir():
        return []
    return [
        child
        for child in sorted(source.iterdir(), key=lambda item: item.name)
        if child.is_dir() and (child / "SKILL.md").is_file()
    ]


def read_local_project(args: argparse.Namespace) -> SkillProject:
    return read_local_projects(args)[0]


def read_local_projects(args: argparse.Namespace) -> list[SkillProject]:
    selected_source = args.skill_path.expanduser().resolve()
    local_source = local_skill_source_for_candidate(selected_source)
    if local_source is not None:
        local_root, source = local_source
        fallback_name = local_root.name if source.name == "skill" else source.name
        return [
            read_skill_project_from_dir(
                source=source,
                fallback_name=fallback_name,
                version="local",
                skill_name=legacy_target_skill_name(args),
                description=args.description,
                source_skill_path=(
                    source.name if source.parent == selected_source else None
                ),
            )
        ]

    child_skills = immediate_child_skill_dirs(selected_source)
    if not child_skills:
        raise InstallerError(
            "local source must contain SKILL.md or skill/SKILL.md: "
            f"{selected_source}"
        )
    return [
        read_skill_project_from_dir(
            source=source,
            fallback_name=source.name,
            version="local",
            description=args.description,
            source_skill_path=source.name,
        )
        for source in child_skills
    ]


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


def github_prefix_uses_source_fallback(
    source: GithubSource,
    skill_prefix: PurePosixPath,
    files: set[PurePosixPath],
) -> bool:
    if source.path is None:
        return skill_prefix in (PurePosixPath("skill"), PurePosixPath("."))
    return (
        skill_prefix == source.path
        and prefixed_path(source.path, "SKILL.md") in files
    )


def github_archive_files(archive: zipfile.ZipFile) -> set[PurePosixPath]:
    return {
        relative
        for info in archive.infolist()
        if not info.is_dir()
        for relative in [github_archive_relative_path(info.filename)]
        if relative is not None
    }


def child_skill_prefixes(
    files: set[PurePosixPath],
    base: PurePosixPath,
) -> list[PurePosixPath]:
    prefixes: list[PurePosixPath] = []
    base_parts = () if not base.parts else base.parts
    for path in files:
        if path.name != "SKILL.md":
            continue
        parent = path.parent
        if parent == base:
            continue
        parent_parts = parent.parts
        if parent_parts[: len(base_parts)] != base_parts:
            continue
        if len(parent_parts) != len(base_parts) + 1:
            continue
        prefixes.append(parent)
    return sorted(set(prefixes), key=lambda item: item.as_posix())


def github_archive_skill_prefixes(
    archive: zipfile.ZipFile,
    source_path: PurePosixPath | None,
) -> list[PurePosixPath]:
    files = github_archive_files(archive)
    if source_path is not None:
        if prefixed_path(source_path, "SKILL.md") in files:
            return [source_path]
        prefixes = child_skill_prefixes(files, source_path)
        if prefixes:
            return prefixes
        raise InstallerError(
            "GitHub archive did not contain "
            f"{prefixed_path(source_path, 'SKILL.md').as_posix()} "
            f"or child skill directories under {source_path.as_posix()}"
        )
    for candidate in (PurePosixPath("skill"), PurePosixPath(".")):
        if prefixed_path(candidate, "SKILL.md") in files:
            return [candidate]
    prefixes = child_skill_prefixes(files, PurePosixPath("."))
    if prefixes:
        return prefixes
    raise InstallerError("GitHub archive did not contain SKILL.md or skill/SKILL.md")


def read_github_project(
    args: argparse.Namespace,
    archive_path: Path,
    source: GithubSource,
) -> SkillProject:
    return read_github_projects(args, archive_path, source)[0]


def read_github_projects(
    args: argparse.Namespace,
    archive_path: Path,
    source: GithubSource,
) -> list[SkillProject]:
    with zipfile.ZipFile(archive_path) as archive:
        projects: list[SkillProject] = []
        files = github_archive_files(archive)
        prefixes = github_archive_skill_prefixes(archive, source.path)
        for skill_prefix in prefixes:
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
                load_installer_config_text(
                    config_text,
                    source=f"{source.url}/{skill_prefix.as_posix()}/{CONFIG_FILE_NAME}",
                )
                if config_text is not None
                else None
            )
            fallback_name = (
                github_fallback_name(source)
                if len(prefixes) == 1
                and github_prefix_uses_source_fallback(source, skill_prefix, files)
                else skill_prefix.name
            )
            projects.append(
                project_from_skill_text(
                    skill_text=skill_text,
                    fallback_name=fallback_name,
                    version=source.version_label,
                    installer_config=config,
                    skill_name=(
                        legacy_target_skill_name(args) if len(prefixes) == 1 else None
                    ),
                    description=args.description,
                    source_skill_path=(
                        None
                        if not skill_prefix.parts
                        else skill_prefix.as_posix()
                    ),
                )
            )
        return projects


def wheel_skill_prefix(archive: zipfile.ZipFile) -> PurePosixPath:
    return wheel_skill_prefixes(archive)[0]


def wheel_skill_prefixes(archive: zipfile.ZipFile) -> list[PurePosixPath]:
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
    shorthand = [candidate for candidate in candidates if candidate.name == "_skill"]
    if shorthand:
        shorthand.sort(key=lambda item: len(item.parts))
        return [shorthand[0]]
    return sorted(set(candidates), key=lambda item: item.as_posix())


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


def normalize_distribution_name(name: str) -> str:
    return name.replace("_", "-").replace(".", "-").lower()


def platform_specific_wheel_name(config: PlatformSelectorConfig) -> str | None:
    platform_specific = config.platform_specific
    if platform_specific.wheel is None:
        return None
    return render_platform_template(platform_specific.wheel).strip()


def platform_specific_local_path(
    platform_specific: PlatformSpecific,
    config_dir: Path,
) -> Path:
    if platform_specific.local_path is None:
        raise InstallerError(
            "platform_specific.local_path is required for local installs"
        )
    rendered = render_platform_template(platform_specific.local_path).strip()
    if not rendered:
        raise InstallerError("platform_specific.local_path must not be empty")
    path = Path(rendered)
    if path.is_absolute():
        raise InstallerError("platform_specific.local_path must be relative")
    return (config_dir / path).expanduser().resolve()


def local_selector_candidates(selected_source: Path) -> list[Path]:
    candidates: list[Path] = []
    local_source = local_skill_source_for_candidate(selected_source)
    if local_source is not None:
        candidates.append(local_source[1] / SELECTOR_FILE_NAME)
    candidates.extend(
        [
            selected_source / SELECTOR_FILE_NAME,
            selected_source / "skill" / SELECTOR_FILE_NAME,
        ]
    )
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        unique.append(candidate)
    return unique


def platform_specific_local_target(selected_source: Path) -> Path | None:
    selected = selected_source.expanduser().resolve()
    for config_path in local_selector_candidates(selected):
        if not config_path.is_file():
            continue
        config = load_platform_selector_config(config_path)
        platform_specific = config.platform_specific
        wheel_name = platform_specific_wheel_name(config)
        if wheel_name and normalize_distribution_name(selected.name) == (
            normalize_distribution_name(wheel_name)
        ):
            return None
        target = platform_specific_local_path(platform_specific, config_path.parent)
        if target == selected or target == config_path.parent.resolve():
            return None
        return target
    return None


def ensure_platform_specific_local_target_resolved(target: Path) -> None:
    next_target = platform_specific_local_target(target)
    if next_target is not None:
        raise InstallerError(
            "platform_specific local target was not resolved after one dispatch: "
            f"{target} resolves to {next_target}"
        )


def wheel_platform_selector_config(
    wheel_path: Path,
    package_name: str,
) -> PlatformSelectorConfig | None:
    try:
        with zipfile.ZipFile(wheel_path) as archive:
            configs: list[tuple[PurePosixPath, PlatformSelectorConfig]] = []
            for info in archive.infolist():
                if info.is_dir():
                    continue
                path = PurePosixPath(info.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise InstallerError(f"unsafe path in PyPI wheel: {info.filename}")
                if path.name != SELECTOR_FILE_NAME:
                    continue
                text = archive.read(info).decode("utf-8")
                config = load_platform_selector_config_text(
                    text,
                    source=f"{package_name} wheel/{path.as_posix()}",
                )
                configs.append((path.parent, config))
    except UnicodeDecodeError as error:
        raise InstallerError(
            f"{SELECTOR_FILE_NAME} is not valid UTF-8 in wheel: {wheel_path}"
        ) from error
    except zipfile.BadZipFile as error:
        raise InstallerError(f"wheel file is not a valid zip file: {wheel_path}") from error
    if not configs:
        return None
    configs.sort(key=lambda item: item[0].as_posix())
    return configs[0][1]


def matching_sibling_wheel(
    selector_wheel: Path,
    package_name: str,
    version: str | None,
) -> Path | None:
    wanted_name = normalize_distribution_name(package_name)
    wanted_version = version.strip() if version else None
    matches: list[tuple[bool, Path]] = []
    for candidate in sorted(selector_wheel.parent.glob("*.whl")):
        if candidate == selector_wheel:
            continue
        candidate_name, candidate_version = wheel_filename_metadata(candidate)
        if normalize_distribution_name(candidate_name) != wanted_name:
            continue
        exact_version = wanted_version is not None and candidate_version == wanted_version
        if wanted_version is None or exact_version:
            matches.append((exact_version, candidate))
    if not matches:
        return None
    matches.sort(key=lambda item: (not item[0], item[1].name))
    return matches[0][1]


def resolve_platform_specific_wheel_file(
    wheel_path: Path,
    *,
    version: str | None = None,
) -> Path:
    current_path = wheel_path.expanduser().resolve()
    fallback_package, fallback_version = wheel_filename_metadata(current_path)
    try:
        with zipfile.ZipFile(current_path) as archive:
            metadata = wheel_archive_metadata(archive)
    except zipfile.BadZipFile as error:
        raise InstallerError(f"wheel file is not a valid zip file: {current_path}") from error
    package_name = metadata.get("name") or fallback_package
    config = wheel_platform_selector_config(current_path, package_name)
    if config is None:
        return current_path
    target_package = platform_specific_wheel_name(config)
    if target_package is None:
        return current_path
    if normalize_distribution_name(target_package) == normalize_distribution_name(
        package_name
    ):
        return current_path
    target_version = version or metadata.get("version") or fallback_version
    target = matching_sibling_wheel(current_path, target_package, target_version)
    if target is None:
        raise InstallerError(
            "platform-specific wheel was not found next to selector wheel: "
            f"{target_package} {target_version}"
        )
    target_config = wheel_platform_selector_config(target, target_package)
    if target_config is not None:
        next_package = platform_specific_wheel_name(target_config)
        if next_package is not None and normalize_distribution_name(
            next_package
        ) != normalize_distribution_name(target_package):
            raise InstallerError(
                "platform_specific target was not resolved after one dispatch: "
                f"{target_package} resolves to {next_package}"
            )
    return target


def resolve_platform_specific_pypi_wheel(
    package_name: str,
    wheel_path: Path,
    version: str,
    download_dir: Path,
) -> tuple[str, Path]:
    config = wheel_platform_selector_config(wheel_path, package_name)
    if config is None:
        return package_name, wheel_path
    target_package = platform_specific_wheel_name(config)
    if target_package is None:
        return package_name, wheel_path
    if normalize_distribution_name(target_package) == normalize_distribution_name(
        package_name
    ):
        return package_name, wheel_path
    project = SkillProject(
        package_name=target_package,
        import_name=GENERIC_IMPORT_NAME,
        version=version,
        skill_name=target_package,
        description="",
        pypi_project_name=target_package,
    )
    target_wheel = download_pypi_wheel(project, version, download_dir)
    target_config = wheel_platform_selector_config(target_wheel, target_package)
    if target_config is not None:
        next_package = platform_specific_wheel_name(target_config)
        if next_package is not None and normalize_distribution_name(
            next_package
        ) != normalize_distribution_name(target_package):
            raise InstallerError(
                "platform_specific target was not resolved after one dispatch: "
                f"{target_package} resolves to {next_package}"
            )
    return target_package, target_wheel


def wheel_prefix_uses_package_fallback(prefix: PurePosixPath) -> bool:
    return not prefix.parts or prefix.name == "_skill"


def read_pypi_project(
    args: argparse.Namespace,
    wheel_path: Path,
    version: str | None,
) -> SkillProject:
    return read_pypi_projects(args, wheel_path, version)[0]


def read_pypi_projects(
    args: argparse.Namespace,
    wheel_path: Path,
    version: str | None,
    *,
    pypi_project_name: str | None = None,
) -> list[SkillProject]:
    fallback_package, fallback_version = wheel_filename_metadata(wheel_path)
    try:
        with zipfile.ZipFile(wheel_path) as archive:
            prefixes = wheel_skill_prefixes(archive)
            archive_metadata = wheel_archive_metadata(archive)
            project_inputs = []
            for prefix in prefixes:
                skill_text = read_prefixed_text(archive, prefix, "SKILL.md", PurePosixPath)
                if skill_text is None:
                    raise InstallerError("wheel did not contain SKILL.md")
                config_text = read_prefixed_text(
                    archive,
                    prefix,
                    CONFIG_FILE_NAME,
                    PurePosixPath,
                )
                project_inputs.append((prefix, skill_text, config_text))
    except zipfile.BadZipFile as error:
        raise InstallerError(f"wheel file is not a valid zip file: {wheel_path}") from error

    package_name = (
        pypi_project_name
        or getattr(args, "pypi_package", None)
        or archive_metadata.get("name")
        or fallback_package
    )
    wheel_version = version or archive_metadata.get("version") or fallback_version

    projects: list[SkillProject] = []
    for prefix, skill_text, config_text in project_inputs:
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
        fallback_name = (
            package_name
            if wheel_prefix_uses_package_fallback(prefix)
            else prefix.name
        )
        source_name = normalize_skill_name(metadata.get("name") or fallback_name)
        target_name = (
            legacy_target_skill_name(args)
            if len(project_inputs) == 1
            else None
        ) or source_name
        projects.append(
            SkillProject(
                package_name=target_name,
                import_name=import_name,
                version=metadata.get("version") or wheel_version,
                skill_name=target_name,
                cli_name="agent-skill-installer",
                description=args.description
                or description_from_skill_text(skill_text, target_name),
                installer_config=config or InstallerConfig(),
                bundled_skill_path=bundled_skill_path,
                pypi_project_name=pypi_project_name
                or getattr(args, "pypi_package", None),
                source_skill_name=source_name,
                source_skill_path=prefix.as_posix() if prefix.parts else None,
            )
        )
    return projects


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


def parse_rename_specs(values: Sequence[str] | None) -> dict[str, str]:
    renames: dict[str, str] = {}
    for value in values or []:
        src, separator, dst = value.partition(":")
        if not separator:
            raise UsageError(f"--rename must use SRC:DST syntax: {value!r}")
        src_name = normalize_skill_name(src)
        dst_name = normalize_skill_name(dst)
        existing = renames.get(src_name)
        if existing is not None and existing != dst_name:
            raise UsageError(f"--rename specified conflicting targets for {src_name}")
        renames[src_name] = dst_name
    return renames


def duplicate_values(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def source_skill_map(projects: Sequence[SkillProject]) -> dict[str, SkillProject]:
    names = [source_skill_name(project) for project in projects]
    duplicates = duplicate_values(names)
    if duplicates:
        joined = ", ".join(sorted(duplicates))
        raise UsageError(f"duplicate source skill names: {joined}")
    return dict(zip(names, projects, strict=True))


def multiple_source_selection_error(projects: Sequence[SkillProject]) -> UsageError:
    names = [source_skill_name(project) for project in projects]
    lines = [
        "multiple source skills are available; choose explicit source skills, "
        "or opt in to all current and future source skills:",
    ]
    lines.extend(f"  {name}" for name in names)
    if names:
        lines.append(f"Use --src-skill {names[0]} to select one skill.")
    lines.append("Use --all-src-skills to install every discovered source skill.")
    return UsageError("\n".join(lines))


def selected_source_names(
    args: argparse.Namespace,
    projects: Sequence[SkillProject],
) -> list[str]:
    by_source = source_skill_map(projects)
    renames = parse_rename_specs(getattr(args, "renames", None))
    requested_src = [
        normalize_skill_name(value)
        for value in (getattr(args, "src_skills", None) or [])
    ]
    missing = sorted(
        {
            name
            for name in [*requested_src, *renames.keys()]
            if name not in by_source
        }
    )
    if missing:
        available = ", ".join(sorted(by_source))
        raise UsageError(
            f"unknown source skill(s): {', '.join(missing)}. "
            f"Available source skills: {available}"
        )

    if getattr(args, "all_src_skills", False):
        selected = list(by_source)
    else:
        selected = []
    for name in [*requested_src, *renames.keys()]:
        if name not in selected:
            selected.append(name)

    if not selected:
        if len(projects) == 1:
            return [source_skill_name(projects[0])]
        raise multiple_source_selection_error(projects)
    return selected


def select_source_projects(
    args: argparse.Namespace,
    projects: Sequence[SkillProject],
) -> list[SkillProject]:
    if not projects:
        raise UsageError("no source skills were found")

    renames = parse_rename_specs(getattr(args, "renames", None))
    dst_skill = getattr(args, "dst_skill", None)
    legacy_skill_name = getattr(args, "skill_name", None)
    if (
        dst_skill is not None
        and legacy_skill_name is not None
        and dst_skill != legacy_skill_name
    ):
        raise UsageError("--dst-skill and --skill-name cannot specify different names")
    if dst_skill is not None and renames:
        raise UsageError("--dst-skill cannot be combined with --rename")
    if dst_skill is not None:
        requested = getattr(args, "src_skills", None) or []
        if getattr(args, "all_src_skills", False) or len(requested) != 1:
            example = source_skill_name(projects[0])
            raise UsageError(
                "--dst-skill requires exactly one --src-skill so the rename "
                "stays stable if the source later adds skills.\n"
                f"Use --src-skill {example} --dst-skill {dst_skill}, or "
                f"--rename {example}:{dst_skill}."
            )

    by_source = source_skill_map(projects)
    selected_names = selected_source_names(args, projects)
    target_override = dst_skill or legacy_skill_name
    selected: list[SkillProject] = []
    for name in selected_names:
        project = by_source[name]
        target_name = renames.get(name)
        if target_name is None and target_override is not None:
            target_name = target_override
        if target_name is not None:
            project = project_with_target_skill(project, target_name)
        selected.append(project)

    duplicate_targets = duplicate_values(project.skill_name for project in selected)
    if duplicate_targets:
        raise UsageError(
            "multiple selected source skills map to the same target skill name: "
            + ", ".join(sorted(duplicate_targets))
        )
    duplicate_markers = duplicate_values(project.marker_slug for project in selected)
    if duplicate_markers:
        raise UsageError(
            "multiple selected source skills map to the same discoverability marker: "
            + ", ".join(sorted(duplicate_markers))
        )
    return selected


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


def remove_snapshot_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def snapshot_install_paths(projects: Sequence[SkillProject], args: argparse.Namespace):
    temp_dir = tempfile.TemporaryDirectory(
        prefix="agent-skill-installer-rollback-"
    )
    records: list[tuple[Path, str, Path | str | None, bool]] = []
    seen: set[Path] = set()
    for project in projects:
        for agent in normalize_agents([args.agent]):
            spec = target_spec(
                project,
                agent,
                args.scope,
                repo=args.repo if args.scope == "repo" else None,
                home=args.home,
                codex_home=args.codex_home,
                claude_home=args.claude_home,
            )
            for path in (
                spec.skill_dir,
                manifest_path(project, spec.skill_dir),
                spec.skill_dir.parent / project.sidecar_manifest_name,
                spec.hook_path,
            ):
                if path in seen:
                    continue
                seen.add(path)
                if path.is_symlink():
                    records.append((path, "symlink", str(path.readlink()), path.is_dir()))
                elif path.is_dir():
                    backup = Path(temp_dir.name) / str(len(records))
                    shutil.copytree(path, backup, symlinks=True)
                    records.append((path, "dir", backup, False))
                elif path.is_file():
                    backup = Path(temp_dir.name) / str(len(records))
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, backup)
                    records.append((path, "file", backup, False))
                else:
                    records.append((path, "missing", None, False))
    return temp_dir, records


def restore_install_paths(
    records: Sequence[tuple[Path, str, Path | str | None, bool]],
) -> None:
    for path, _kind, _backup, _target_is_directory in sorted(
        records,
        key=lambda item: len(item[0].parts),
        reverse=True,
    ):
        if path.exists() or path.is_symlink():
            remove_snapshot_path(path)
    for path, kind, backup, target_is_directory in records:
        if kind == "missing":
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if kind == "symlink":
            assert isinstance(backup, str)
            path.symlink_to(backup, target_is_directory=target_is_directory)
        elif kind == "dir":
            assert isinstance(backup, Path)
            shutil.copytree(backup, path, symlinks=True)
        elif kind == "file":
            assert isinstance(backup, Path)
            shutil.copy2(backup, path)


def validate_install_plan(
    projects: Sequence[SkillProject],
    args: argparse.Namespace,
) -> None:
    seen_targets: set[tuple[str, str, Path]] = set()
    for project in projects:
        for agent in normalize_agents([args.agent]):
            spec = target_spec(
                project,
                agent,
                args.scope,
                repo=args.repo if args.scope == "repo" else None,
                home=args.home,
                codex_home=args.codex_home,
                claude_home=args.claude_home,
            )
            key = (agent, args.scope, spec.skill_dir)
            if key in seen_targets:
                raise InstallerError(f"duplicate install target: {spec.skill_dir}")
            seen_targets.add(key)
            if spec.skill_dir.exists() or spec.skill_dir.is_symlink():
                manifest = read_manifest(project, spec.skill_dir)
                if manifest is None and not args.force:
                    raise InstallerError(
                        f"refusing to replace unowned skill directory: {spec.skill_dir}"
                    )


def install_project_for_targets(
    project: SkillProject,
    args: argparse.Namespace,
    *,
    local_editable: bool = False,
    github_source: GithubSource | None = None,
    github_archive_path: Path | None = None,
    pypi_version: str | None = None,
    pypi_wheel_path: Path | None = None,
) -> list[InstallResult]:
    editable_source_dir = (
        project.bundled_skill_source
        if local_editable
        else None
    )
    project_github_source = github_source
    if github_source is not None and project.source_skill_path is not None:
        project_github_source = replace(
            github_source,
            path=PurePosixPath(project.source_skill_path),
        )
    return install_for_targets(
        project,
        args,
        editable_source_dir=editable_source_dir,
        github_source=project_github_source,
        github_archive_path=github_archive_path,
        pypi_version=pypi_version,
        pypi_wheel_path=pypi_wheel_path,
    )


def install_projects_for_targets(
    projects: Sequence[SkillProject],
    args: argparse.Namespace,
    *,
    local_editable: bool = False,
    github_source: GithubSource | None = None,
    github_archive_path: Path | None = None,
    pypi_version: str | None = None,
    pypi_wheel_path: Path | None = None,
) -> list[InstallResult]:
    validate_install_plan(projects, args)
    if len(projects) == 1:
        return install_project_for_targets(
            projects[0],
            args,
            local_editable=local_editable,
            github_source=github_source,
            github_archive_path=github_archive_path,
            pypi_version=pypi_version,
            pypi_wheel_path=pypi_wheel_path,
        )

    temp_dir, records = snapshot_install_paths(projects, args)
    try:
        results: list[InstallResult] = []
        for project in projects:
            results.extend(
                install_project_for_targets(
                    project,
                    args,
                    local_editable=local_editable,
                    github_source=github_source,
                    github_archive_path=github_archive_path,
                    pypi_version=pypi_version,
                    pypi_wheel_path=pypi_wheel_path,
                )
            )
        return results
    except Exception as error:
        try:
            restore_install_paths(records)
        except Exception as rollback_error:
            raise InstallerError(
                "install failed and rollback was incomplete: "
                f"{rollback_error}. Original error: {error}"
            ) from error
        raise InstallerError(f"install failed; rolled back changes: {error}") from error
    finally:
        temp_dir.cleanup()


def run_install(args: argparse.Namespace) -> list[InstallResult]:
    if args.github_url is None and (
        args.github_ref is not None or args.github_path is not None
    ):
        raise InstallerError("--github-ref and --github-path require --github-url")
    if args.pypi_package is None and args.pypi_version is not None:
        raise InstallerError("--pypi-version requires --pypi-package")

    if args.skill_path is not None:
        target = platform_specific_local_target(args.skill_path)
        if target is not None:
            target_args = copy_args(args)
            target_args.skill_path = target
            ensure_platform_specific_local_target_resolved(target)
            projects = select_source_projects(target_args, read_local_projects(target_args))
            if any(project.bundled_skill_source is None for project in projects):
                raise InstallerError("local install source was not resolved")
            return install_projects_for_targets(
                projects,
                target_args,
                local_editable=getattr(target_args, "editable", None) is not False,
            )
        projects = select_source_projects(args, read_local_projects(args))
        if any(project.bundled_skill_source is None for project in projects):
            raise InstallerError("local install source was not resolved")
        return install_projects_for_targets(
            projects,
            args,
            local_editable=getattr(args, "editable", None) is not False,
        )

    if args.wheel_file is not None:
        validated_wheel = validated_wheel_file(args)
        if validated_wheel is not None:
            wheel_path, projects = validated_wheel
        else:
            wheel_path = args.wheel_file.expanduser().resolve()
            if not wheel_path.is_file():
                raise InstallerError(f"wheel file does not exist: {wheel_path}")
            wheel_path = resolve_platform_specific_wheel_file(wheel_path)
            projects = read_pypi_projects(args, wheel_path, None)
        projects = select_source_projects(args, projects)
        return install_projects_for_targets(
            projects,
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
            archive_path, projects = validated_download
            projects = select_source_projects(args, projects)
            results = install_projects_for_targets(
                projects,
                args,
                github_source=source,
                github_archive_path=archive_path,
            )
        else:
            with tempfile.TemporaryDirectory(
                prefix="agent-skill-installer-github-"
            ) as temp_dir:
                archive_path = download_github_archive(source, Path(temp_dir))
                projects = read_github_projects(args, archive_path, source)
                projects = select_source_projects(args, projects)
                results = install_projects_for_targets(
                    projects,
                    args,
                    github_source=source,
                    github_archive_path=archive_path,
                )
        remember_recent_github_url(args.github_url, home=args.home)
        return results

    assert args.pypi_package is not None
    requested_package = args.pypi_package
    version = resolve_pypi_version(args)
    validated_download = validated_pypi_download(args, version)
    if validated_download is not None:
        wheel_path, projects = validated_download
        projects = select_source_projects(args, projects)
        results = install_projects_for_targets(
            projects,
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
            resolved_package, wheel_path = resolve_platform_specific_pypi_wheel(
                requested_package,
                wheel_path,
                version,
                Path(temp_dir),
            )
            projects = read_pypi_projects(
                args,
                wheel_path,
                version,
                pypi_project_name=resolved_package,
            )
            projects = select_source_projects(args, projects)
            results = install_projects_for_targets(
                projects,
                args,
                pypi_version=version,
                pypi_wheel_path=wheel_path,
            )
    remember_recent_pypi_package(requested_package, home=args.home)
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
