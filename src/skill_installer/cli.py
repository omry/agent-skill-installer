from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path
from typing import Sequence

from .installer import (
    AGENTS,
    SCOPES,
    InstallResult,
    Installer,
    InstallerError,
    SkillProject,
    describe_target,
)


class UsageError(Exception):
    pass


VERSION_CHANGE_COLORS = {
    "upgrade": "32",
    "downgrade": "31",
}


TARGET_ALL = "all"


def build_parser(project: SkillProject) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=project.package_name,
        description=f"Install or uninstall the {project.skill_name} skill.",
    )
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Accepted for project CLIs that expose only non-interactive mode.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {project.version}",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show installed skill, source, and hook paths.",
    )

    subparsers = parser.add_subparsers(dest="command")
    for command in ("install", "uninstall"):
        subparser = subparsers.add_parser(
            command,
            help=f"{command.capitalize()} the skill and discoverability hook.",
        )
        subparser.add_argument(
            "--verbose",
            action="store_true",
            default=argparse.SUPPRESS,
            help="Show installed skill, source, and hook paths.",
        )
        subparser.add_argument(
            "--agent",
            required=True,
            metavar="AGENT[,AGENT...]",
            help="Agent integration to target: codex, claude, or all.",
        )
        subparser.add_argument(
            "--scope",
            required=True,
            choices=SCOPES,
            help="Install for the current repository or for the current user.",
        )
        subparser.add_argument(
            "--repo",
            type=Path,
            help="Repository path to use with --scope repo. Defaults to cwd.",
        )
        subparser.add_argument(
            "--codex-home",
            type=Path,
            help="Codex home directory for global scope. Defaults to ~/.codex.",
        )
        subparser.add_argument(
            "--claude-home",
            type=Path,
            help="Claude Code home directory for global scope. Defaults to ~/.claude.",
        )
        subparser.add_argument(
            "--home",
            type=Path,
            help=argparse.SUPPRESS,
        )
        if command == "install":
            subparser.add_argument(
                "--force",
                action="store_true",
                help="Replace an existing unowned skill directory.",
            )
            subparser.add_argument(
                "--editable",
                action="store_true",
                default=False,
                help="Install symlinks to this checkout's skill files.",
            )
            subparser.add_argument(
                "--pypi-version",
                metavar="VERSION",
                help=(
                    f"Download the {project.pypi_name} wheel from PyPI and "
                    "install its bundled skill files without installing the package."
                ),
            )
    return parser


def quote_command(parts: Sequence[object]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def split_agent_arg(value: str) -> list[str]:
    raw_parts = value.split(",")
    parts = [part.strip() for part in raw_parts]
    if any(not part for part in parts):
        raise UsageError(f"unknown agent target: {value}")
    if TARGET_ALL in parts:
        if len(parts) > 1:
            raise UsageError("--agent all cannot be combined with explicit agents")
        return list(AGENTS)

    agents: list[str] = []
    for agent in parts:
        if agent not in AGENTS:
            raise UsageError(f"unknown agent target: {agent}")
        if agent not in agents:
            agents.append(agent)
    if not agents:
        raise UsageError("choose at least one agent")
    return agents


def build_no_ui_command(
    project: SkillProject,
    command: str,
    *,
    agent: str,
    scope: str,
    repo: Path | None = None,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
    force: bool = False,
    editable: bool = False,
    pypi_version: str | None = None,
    verbose: bool = False,
) -> str:
    parts: list[object] = [project.package_name, "--no-ui", command]
    if verbose:
        parts.append("--verbose")
    if command == "install":
        if force:
            parts.append("--force")
        if editable:
            parts.append("--editable")
        elif pypi_version:
            parts.extend(["--pypi-version", pypi_version])
    parts.extend(["--agent", agent, "--scope", scope])
    if scope == "repo" and repo is not None:
        parts.extend(["--repo", repo])
    if scope == "global":
        selected = split_agent_arg(agent)
        if "codex" in selected and codex_home is not None:
            parts.extend(["--codex-home", codex_home])
        if "claude" in selected and claude_home is not None:
            parts.extend(["--claude-home", claude_home])
    return quote_command(parts)


def style_text(text: str, code: str, *, color: bool) -> str:
    if not color:
        return text
    return f"\033[{code}m{text}\033[0m"


def version_suffix(result: InstallResult) -> str:
    if result.version is None:
        return ""
    if result.action == "uninstall":
        return f" version {result.version}"

    details: list[str] = []
    if result.version_change == "upgrade":
        details.append(f"upgraded from {result.previous_version}")
    elif result.version_change == "downgrade":
        details.append(f"downgraded from {result.previous_version}")
    if result.install_mode == "editable":
        details.append("editable")
    elif result.install_mode == "pypi":
        details.append("PyPI wheel")

    suffix = f" version {result.version}"
    if details:
        suffix += f" ({', '.join(details)})"
    return suffix


def format_status_line(result: InstallResult, *, color: bool) -> str:
    line = (
        f"{result.status}: {describe_target(result.agent, result.scope)}"
        f"{version_suffix(result)}"
    )
    if result.action == "install":
        line += f" at {result.skill_dir}"
    color_code = VERSION_CHANGE_COLORS.get(result.version_change or "")
    if color_code is None:
        return line
    return style_text(line, color_code, color=color)


def print_results(results: Sequence[InstallResult], *, verbose: bool = False) -> None:
    color = sys.stdout.isatty()
    for result in results:
        print(format_status_line(result, color=color))
        if not verbose:
            continue
        print(f"  skill: {result.skill_dir}")
        if result.source_dir is not None:
            print(f"  source: {result.source_dir}")
        print(f"  hook:  {result.hook_path}")


def prepare_args(args: argparse.Namespace) -> None:
    if args.command is None:
        raise UsageError("choose install or uninstall")
    if not hasattr(args, "force"):
        args.force = False
    if not hasattr(args, "home"):
        args.home = None
    if not hasattr(args, "codex_home"):
        args.codex_home = None
    if not hasattr(args, "claude_home"):
        args.claude_home = None
    if not hasattr(args, "editable"):
        args.editable = False
    if not hasattr(args, "pypi_version"):
        args.pypi_version = None
    if not hasattr(args, "verbose"):
        args.verbose = False
    if args.command == "install":
        if args.editable and args.pypi_version is not None:
            raise UsageError("--editable and --pypi-version cannot be used together")
        if args.pypi_version is not None:
            args.pypi_version = args.pypi_version.strip()
            if not args.pypi_version:
                raise UsageError("--pypi-version must not be empty")


def run(project: SkillProject, args: argparse.Namespace) -> list[InstallResult]:
    agents = split_agent_arg(args.agent)
    repo = args.repo if args.scope == "repo" else None
    installer = Installer(project)
    if args.command == "install":
        return installer.install(
            agents,
            args.scope,
            repo=repo,
            home=args.home,
            codex_home=args.codex_home,
            claude_home=args.claude_home,
            force=args.force,
            editable=args.editable,
            pypi_version=args.pypi_version,
        )
    if args.command == "uninstall":
        return installer.uninstall(
            agents,
            args.scope,
            repo=repo,
            home=args.home,
            codex_home=args.codex_home,
            claude_home=args.claude_home,
        )
    raise UsageError(f"unknown command: {args.command}")


def print_pypi_install_attempt(project: SkillProject, args: argparse.Namespace) -> None:
    if args.command != "install" or getattr(args, "pypi_version", None) is None:
        return
    print(
        f"Installing from PyPI: {project.pypi_name}=={args.pypi_version}",
        file=sys.stderr,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    project: SkillProject,
) -> int:
    parser = build_parser(project)
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        prepare_args(args)
        print_pypi_install_attempt(project, args)
        results = run(project, args)
    except UsageError as error:
        parser.exit(2, f"{parser.prog}: error: {error}\n")
    except InstallerError as error:
        parser.exit(1, f"{parser.prog}: error: {error}\n")

    print_results(results, verbose=args.verbose)
    return 0
