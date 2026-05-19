from __future__ import annotations

import argparse
from pathlib import Path

from agent_skill_installer import Installer, SkillProject
from agent_skill_installer.cli import format_status_line


EXAMPLE_DIR = Path(__file__).resolve().parent

PROJECT = SkillProject(
    package_name="api-demo-agent-skill",
    import_name="api_demo_agent_skill",
    version="0.1.0",
    skill_name="api-demo-agent-skill",
    description="Use this API demo skill to verify direct installer usage.",
    bundled_skill_source=EXAMPLE_DIR / "skill",
)


def split_agents(value: str) -> list[str]:
    if value == "all":
        return ["codex", "claude"]
    agents = [item.strip() for item in value.split(",")]
    if any(agent not in {"codex", "claude"} for agent in agents):
        raise argparse.ArgumentTypeError("agent must be codex, claude, or all")
    return agents


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install the API demo skill.")
    parser.add_argument("command", choices=["install", "uninstall"])
    parser.add_argument("--agent", required=True, type=split_agents)
    parser.add_argument("--scope", required=True, choices=["repo", "global"])
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--codex-home", type=Path)
    parser.add_argument("--claude-home", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    installer = Installer(PROJECT)
    if args.command == "install":
        results = installer.install(
            args.agent,
            args.scope,
            repo=args.repo,
            codex_home=args.codex_home,
            claude_home=args.claude_home,
        )
    else:
        results = installer.uninstall(
            args.agent,
            args.scope,
            repo=args.repo,
            codex_home=args.codex_home,
            claude_home=args.claude_home,
        )

    for result in results:
        print(format_status_line(result, color=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
