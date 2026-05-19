# Agent Skill Installer

`agent-skill-installer` provides the `agent_skill_installer` Python package, a generic
library for projects that distribute agent skills. A project supplies metadata
for its own skill package, and this library handles installation into supported
agent homes or repositories.

The core is intentionally project-neutral. It does not ship a skill, and it does
not know about Agent Workflow DSL or any other specific skill.

## What It Provides

- Codex and Claude Code install targets.
- `global` and `repo` scopes.
- Bundled skill copy installs.
- Editable installs from a local checkout containing `skill/SKILL.md` or a root
  `SKILL.md`.
- PyPI wheel installs that extract only the project skill payload.
- Install manifests for ownership checks and uninstall cleanup.
- Discoverability hook blocks with project-specific markers.
- A reusable non-interactive CLI entry point for project-specific wrappers.

## Library Usage

```python
from agent_skill_installer import Installer, SkillProject

PROJECT = SkillProject(
    package_name="my-agent-skill",
    import_name="my_agent_skill",
    version="1.2.3",
    skill_name="my-agent-skill",
    description="Use this skill for my project workflows.",
)

Installer(PROJECT).install(["codex"], "global")
Installer(PROJECT).install(["claude"], "repo")
Installer(PROJECT).uninstall(["codex"], "global")
```

By default, bundled installs read skill files from
`my_agent_skill/_skill` inside the project package. Tests or development tools
can point `bundled_skill_source` at a filesystem directory.

## Project CLI Wrapper

Projects can expose their own console script by delegating to the generic CLI:

```python
from agent_skill_installer import SkillProject
from agent_skill_installer.cli import main as installer_main

from . import __version__

PROJECT = SkillProject(
    package_name="my-agent-skill",
    import_name="my_agent_skill",
    version=__version__,
    skill_name="my-agent-skill",
    description="Use this skill for my project workflows.",
)


def main(argv=None):
    return installer_main(argv, project=PROJECT)
```

The resulting CLI supports commands such as:

```bash
my-agent-skill --no-ui install --agent all --scope global
my-agent-skill --no-ui install --editable --agent codex --scope repo
my-agent-skill --no-ui install --pypi-version 1.2.3 --agent claude --scope global
my-agent-skill --no-ui uninstall --agent all --scope global
```

## Status

This repository was extracted from the installer surface in `omry/awd`. The
first goal is a standalone, generic core. AWD should integrate through a thin
project-specific adapter after this package is confirmed.
