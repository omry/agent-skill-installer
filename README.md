# Agent Skill Installer

`agent-skill-installer` provides the `agent-skill-installer` command and the
`agent_skill_installer` Python package for projects that distribute agent
skills. The command can install skills from PyPI wheels, GitHub archives, or
local `SKILL.md` directories; the library exposes the same installer machinery
for custom integrations.

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
- GitHub archive installs from repository root, `skill/`, or an explicit tree
  path.
- Install manifests for ownership checks and uninstall cleanup.
- Discoverability hook blocks with project-specific markers.
- A generic console app with a text UI, no-UI mode, and reusable CLI functions
  for custom tools.

## Command Usage

Run the generic UI and choose an external source:

```bash
agent-skill-installer
```

The generic command installs from local repos or skill directories, PyPI
packages, and GitHub repos. Use `--no-ui` for scripts.

When you run the interactive command from a development checkout that contains
`SKILL.md` or `skill/SKILL.md`, the UI shows the local repo or skill directory
source first and uses that detected path as the default. This is the fastest
development-mode path for testing a skill before publishing it.

Install a published skill package by extracting its bundled skill payload from a
wheel:

```bash
agent-skill-installer --no-ui install \
  --pypi-package your-skill-package \
  --pypi-version 1.2.3 \
  --agent all \
  --scope global
```

The interactive PyPI package and GitHub URL prompts remember the 10 most recent
successful installs for each source in
`~/.agent-skill-installer/recent-installations.json` and offer them in
filterable dropdowns. If that state file cannot be read, the installer ignores
it and continues with an empty recent list.

After a PyPI package and version are selected in the UI, the installer downloads
the wheel to a temporary directory and validates that it contains a bundled
`SKILL.md`. The final install reuses that same downloaded wheel.

Install directly from GitHub without cloning:

```bash
agent-skill-installer --no-ui install \
  --github-url https://github.com/OWNER/REPO/tree/main/skill \
  --agent codex \
  --scope repo
```

Install a local repo or skill directory:

```bash
agent-skill-installer --no-ui install \
  --local-repo ./your-skill-package \
  --skill-name your-skill-package \
  --agent codex \
  --scope repo
```

Uninstall a skill installed by the generic command:

```bash
agent-skill-installer --no-ui uninstall \
  --skill-name your-skill-package \
  --agent codex \
  --scope repo
```

## Library Usage

Projects may also import `Installer` directly for tests, custom tooling, or
applications that want to drive installation themselves:

```python
from agent_skill_installer import Installer, SkillProject

PROJECT = SkillProject(
    package_name="your-skill-package",
    import_name="your_skill_package",
    version="1.2.3",
    skill_name="your-skill-package",
    description="Use this skill for my project workflows.",
)

Installer(PROJECT).install(["codex"], "global")
Installer(PROJECT).install(["claude"], "repo")
Installer(PROJECT).uninstall(["codex"], "global")
```

By default, bundled installs read skill files from
`your_skill_package/_skill` inside the project package. Tests or development tools
can point `bundled_skill_source` at a filesystem directory.

## Use In Your Skill Package

`agent-skill-installer` can install directly from generic sources, so a skill
package does not need to expose its own installer command. Packages that still
want a project-specific command may wrap the shared CLI helpers.

### Package Layout

A typical setuptools project looks like this:

```text
your-skill-package/
  MANIFEST.in
  pyproject.toml
  src/
    your_skill_package/
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
looks for that directory at `your_skill_package/_skill`, where `your_skill_package` is
the `SkillProject.import_name` value and `_skill` is the default
`SkillProject.bundled_skill_path`.

### Package Configuration

The skill package should depend on this library and include the bundled skill
files in its wheel:

```toml
[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "your-skill-package"
version = "1.2.3"
description = "Install the your-skill-package skill for supported agents"
readme = "README.md"
requires-python = ">=3.10"
dependencies = [
  "agent-skill-installer>=0.1.0",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools]
include-package-data = true
```

For setuptools, include the bundled skill payload with `MANIFEST.in`:

```text
recursive-include src/your_skill_package/_skill *
```

If you use another build backend, configure the equivalent package-data rule so
the built wheel contains `your_skill_package/_skill/SKILL.md` and any related skill
files.

### Project Metadata

Put the project version somewhere importable by the wrapper:

```python
"""My agent skill package."""

__version__ = "1.2.3"
```

If you want a project-specific wrapper, create a file such as
`src/your_skill_package/cli.py`:

```python
from __future__ import annotations

from agent_skill_installer import SkillProject
from agent_skill_installer.cli import main as installer_main

from . import __version__


PROJECT = SkillProject(
    package_name="your-skill-package",
    import_name="your_skill_package",
    version=__version__,
    skill_name="your-skill-package",
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

Install directly from a local repo or skill directory while developing:

```bash
agent-skill-installer --no-ui install \
  --local-repo ./your-skill-package \
  --skill-name your-skill-package \
  --agent all \
  --scope repo
agent-skill-installer --no-ui uninstall \
  --skill-name your-skill-package \
  --agent all \
  --scope repo
```

Use `--scope repo` from inside the repository where the agent should discover
the skill, or pass `--repo PATH`.

The interactive generic command also detects local development sources. From a
directory containing `SKILL.md` or `skill/SKILL.md`, run:

```bash
agent-skill-installer
```

The first install source option will be local development mode, with the local
skill path already selected as the default.

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

After publishing your skill package to PyPI, users can install the bundled skill
directly from the published wheel:

```bash
agent-skill-installer --no-ui install \
  --pypi-package your-skill-package \
  --agent all \
  --scope global
```

Pin a specific published wheel version when desired:

```bash
agent-skill-installer --no-ui install \
  --pypi-package your-skill-package \
  --pypi-version 1.2.3 \
  --agent all \
  --scope global
```

That mode downloads the named wheel and extracts only the bundled skill payload,
without installing that downloaded wheel into the current Python environment.

### GitHub Source Installs

The generic command can also install the skill payload directly from a GitHub
archive:

```bash
agent-skill-installer --no-ui install \
  --github-url https://github.com/example/your-skill-package \
  --agent all \
  --scope global
```

By default this uses the `main` ref and looks for either `skill/SKILL.md` or a
root `SKILL.md`. For a specific ref or nested skill directory, use either a tree
URL or explicit flags:

```bash
agent-skill-installer --no-ui install \
  --github-url https://github.com/example/your-skill-package/tree/v1/packages/skill \
  --agent codex \
  --scope repo

agent-skill-installer --no-ui install \
  --github-url https://github.com/example/your-skill-package \
  --github-ref v1 \
  --github-path packages/skill \
  --agent codex \
  --scope repo
```

Before release, a minimal package check is:

```bash
python -m build
python -m twine check dist/*
python -m pip install dist/your_skill_package-1.2.3-py3-none-any.whl
```

Then verify the generic command against the published package:

```bash
agent-skill-installer --version
agent-skill-installer --no-ui install \
  --pypi-package your-skill-package --pypi-version 1.2.3 \
  --agent codex --scope repo
agent-skill-installer --no-ui uninstall \
  --skill-name your-skill-package --agent codex --scope repo
```

## Status

This is a standalone, generic installer. Skill packages can rely on the
`agent-skill-installer` command directly, or use the Python APIs for custom
integration.
