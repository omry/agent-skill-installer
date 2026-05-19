from __future__ import annotations

import sys


def main() -> int:
    print(
        "agent-skill-installer is a library. Project-specific packages should call "
        "agent_skill_installer.cli.main(..., project=SkillProject(...)).",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
