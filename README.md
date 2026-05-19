# Agent Skill Installer

`agent-skill-installer` provides the `agent_skill_installer` Python package, a generic
library for projects that distribute agent skills. A project supplies metadata
for its own skill package, and this library handles installation into supported
agent homes or repositories.

The core is intentionally project-neutral. It does not ship a skill and does
not know about any specific skill package.

## Currently Supported Agents

- Codex
- Claude Code

## Examples

The `examples/` directory contains runnable integration examples:

- [`examples/demo-installer/`](https://github.com/omry/agent-skill-installer/blob/main/examples/README.md#demo-installer-package)
  is a complete skill-carrying Python package with a bundled demo skill and a
  project-specific CLI wrapper.
- [`examples/api-install/`](https://github.com/omry/agent-skill-installer/blob/main/examples/README.md#api-install-script)
  shows how to install and uninstall a skill directly through the Python API
  without exposing a package CLI.

See [`examples/README.md`](https://github.com/omry/agent-skill-installer/blob/main/examples/README.md)
for the files to inspect and the commands to run.

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

Most projects should expose their own command-line wrapper instead of importing
`Installer` directly. Direct use is still available for tests, custom tooling,
or applications that want to drive installation themselves:

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

## Use In Your Skill Package

`agent-skill-installer` is meant to be a dependency of a package that carries a
skill. The skill package owns the user-facing package name, console script,
metadata, and bundled skill files; this library supplies the shared installer
behavior.

### Package Layout

A typical setuptools project looks like this:

```text
my-agent-skill/
  MANIFEST.in
  pyproject.toml
  src/
    my_agent_skill/
      __init__.py
      cli.py
      _skill/
        SKILL.md
        agents/
          openai.yaml
        scripts/
          helper.py
```

The bundled skill directory must contain `SKILL.md`. By default, the installer
looks for that directory at `my_agent_skill/_skill`, where `my_agent_skill` is
the `SkillProject.import_name` value and `_skill` is the default
`SkillProject.bundled_skill_path`.

### Package Configuration

The skill package should depend on this library, expose its own console script,
and include the bundled skill files in its wheel:

```toml
[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "my-agent-skill"
version = "1.2.3"
description = "Install the my-agent-skill skill for supported agents"
readme = "README.md"
requires-python = ">=3.10"
dependencies = [
  "agent-skill-installer>=0.1.0",
]

[project.scripts]
my-agent-skill = "my_agent_skill.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools]
include-package-data = true
```

For setuptools, include the bundled skill payload with `MANIFEST.in`:

```text
recursive-include src/my_agent_skill/_skill *
```

If you use another build backend, configure the equivalent package-data rule so
the built wheel contains `my_agent_skill/_skill/SKILL.md` and any related skill
files.

### Project Metadata

Put the project version somewhere importable by the wrapper:

```python
"""My agent skill package."""

__version__ = "1.2.3"
```

Then create a wrapper such as `src/my_agent_skill/cli.py`:

```python
from __future__ import annotations

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


def main(argv=None) -> int:
    return installer_main(argv, project=PROJECT)
```

The most important fields are:

| Field | Purpose |
| --- | --- |
| `package_name` | Distribution name and manifest ownership name. Usually the PyPI project name. |
| `import_name` | Import package that contains the bundled skill files. |
| `version` | Version recorded in install manifests and shown by the CLI. Keep it aligned with the package version. |
| `skill_name` | Installed skill directory name and default discoverability trigger. |
| `description` | Text used in default discoverability hook blocks. |
| `bundled_skill_path` | Package-relative bundled skill directory. Defaults to `_skill`. |
| `bundled_skill_source` | Optional filesystem directory for tests or custom development tooling. |
| `pypi_project_name` | Optional PyPI project name when it differs from `package_name`. |
| `hook_blocks` | Optional per-agent custom discoverability blocks keyed by `codex` or `claude`. |
| `manifest_package_aliases` | Optional old package names accepted when migrating existing installs. |
| `marker_slug_override` | Optional discoverability marker slug for preserving hook markers during renames. |

### Local Development

Install the skill package in editable mode and ask its wrapper to symlink the
checkout skill files:

```bash
python -m pip install -e .
my-agent-skill --no-ui install --editable --agent all --scope repo
my-agent-skill --no-ui uninstall --agent all --scope repo
```

Editable installs search upward from the current directory for either
`skill/SKILL.md` or a root `SKILL.md`. Use `--scope repo` from inside the
repository where the agent should discover the skill, or pass `--repo PATH`.

### Installed Paths

The installer supports these targets:

| Agent | Scope | Skill directory | Hook file |
| --- | --- | --- | --- |
| Codex | `repo` | `<repo>/.codex/skills/<skill_name>` | `<repo>/AGENTS.md` |
| Codex | `global` | `~/.codex/skills/<skill_name>` | `~/.codex/AGENTS.md` |
| Claude Code | `repo` | `<repo>/.claude/skills/<skill_name>` | `<repo>/CLAUDE.md` |
| Claude Code | `global` | `~/.claude/skills/<skill_name>` | `~/.claude/CLAUDE.md` |

Each install writes a manifest owned by the package. The manifest lets later
installs replace only owned files and lets uninstall remove the skill,
discoverability block, and directories the installer created.

### PyPI Release Flow

After publishing your skill package to PyPI, users can install the package and
then install the bundled skill:

```bash
python -m pip install my-agent-skill
my-agent-skill --no-ui install --agent all --scope global
```

The wrapper also supports installing the skill payload from a specific published
wheel version:

```bash
my-agent-skill --no-ui install --pypi-version 1.2.3 --agent all --scope global
```

That mode downloads the wheel named by `package_name` or `pypi_project_name` and
extracts only `my_agent_skill/_skill`, without installing that downloaded wheel
into the current Python environment.

Before release, a minimal package check is:

```bash
python -m build
python -m twine check dist/*
python -m pip install dist/my_agent_skill-1.2.3-py3-none-any.whl
```

Then verify the wrapper:

```bash
my-agent-skill --version
my-agent-skill --no-ui install --agent codex --scope repo
my-agent-skill --no-ui uninstall --agent codex --scope repo
```

## Status

This is a standalone, generic installer core. Skill packages should integrate
through thin project-specific adapters that provide their own metadata, bundled
skill files, and CLI wrappers.
