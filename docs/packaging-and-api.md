# Packaging And API

This guide is for maintainers who want to publish a skill as a Python package,
provide a project-specific installer command, or call the installer from Python.

If you only need a local or GitHub-installable skill directory, start with
[Authoring Skills](authoring-skills.md) instead.

## Publish A Skill In A Wheel

For generic PyPI installs, a package only needs to publish a wheel containing a
bundled `SKILL.md` and any related skill files. The package does not need to
depend on `agent-skill-installer` unless it exposes its own wrapper command or
uses the Python API.

A typical setuptools layout:

```text
your-skill-package/
  MANIFEST.in
  pyproject.toml
  src/
    your_skill_package/
      __init__.py
      _skill/
        SKILL.md
        agent-skill-installer.yaml  # Optional install-time metadata
        agents/
          openai.yaml
        scripts/
          helper.py
```

`pyproject.toml` and `MANIFEST.in` are Python packaging files. They are not part
of the skill payload. The skill payload is the `_skill/` directory.

The generic PyPI installer detects a bundled `SKILL.md` in the wheel. The
preferred location is `your_skill_package/_skill/SKILL.md`.

Minimal package metadata:

```toml
[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "your-skill-package"
version = "1.2.3"
description = "Agent skill for my project workflows"
readme = "README.md"
requires-python = ">=3.10"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools]
include-package-data = true
```

For setuptools, include the bundled skill payload in the wheel with
`MANIFEST.in`:

```text
recursive-include src/your_skill_package/_skill *
```

If you use another build backend, configure the equivalent package-data rule so
the built wheel contains `SKILL.md`, `agent-skill-installer.yaml` when you use
one, and any related files.

Before release, run a minimal package check:

```bash
python -m build
python -m twine check dist/*
python -m pip install dist/your_skill_package-1.2.3-py3-none-any.whl
```

Before publishing, verify the generic installer against the local wheel file:

```bash
agent-skill-installer --no-ui install \
  --wheel-file dist/your_skill_package-1.2.3-py3-none-any.whl \
  --agent codex \
  --scope repo
agent-skill-installer --no-ui uninstall \
  --skill-name your-skill-package \
  --agent codex \
  --scope repo
```

After publishing, verify the generic installer against the published wheel:

```bash
agent-skill-installer --version
agent-skill-installer --no-ui install \
  --pypi-package your-skill-package \
  --pypi-version 1.2.3 \
  --agent codex \
  --scope repo
agent-skill-installer --no-ui uninstall \
  --skill-name your-skill-package \
  --agent codex \
  --scope repo
```

See [`examples/wheel-skill/`](../examples/wheel-skill/) for a minimal package
that exists only to publish or locally install a wheel-packaged skill.

During install, the installer validates `SKILL.md` YAML frontmatter before
copying or linking the skill. If the wheel includes `agent-skill-installer.yaml`,
the installer also validates it against the local dataclass schema and uses it
for generated install-time configuration. The metadata file is not copied into
the installed skill directory. Use the local wheel check above before publishing
so metadata and config typos fail against the same artifact you plan to release.

## Platform-Specific Skills

Packages that need platform-dependent behavior can publish one selector package
that installs different skill versions on different platforms. The selector
contains `agent-skill-selector.yaml`; each resolved target contains the
platform-specific skill payload and optional `agent-skill-installer.yaml`
install-time metadata.

Use this pattern when the same public package should install a different skill
payload for different operating systems or CPU architectures. See
[Platform-Specific Skills](platform-specific-skills.md) for the selector file
format, package layouts, local development flow, and PyPI or wheel-file install
behavior.

## Optional Wrapper CLI

A package may expose its own installer command for branded UX, custom defaults,
or direct integration with the shared installer UI. In that case, add a runtime
dependency and delegate to `agent_skill_installer.cli.main`.

```toml
[project]
name = "your-skill-package"
version = "1.2.3"
dependencies = [
  "agent-skill-installer>=0.1.0",
]

[project.scripts]
your-skill-package = "your_skill_package.cli:main"
```

Example `src/your_skill_package/cli.py`:

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

Important `SkillProject` fields:

| Field | Purpose |
| --- | --- |
| `package_name` | Distribution name and internal install-manifest ownership name. Usually the PyPI project name. |
| `import_name` | Import package that contains the bundled skill files. |
| `version` | Version recorded in install manifests and shown by wrapper CLIs. Keep it aligned with the package version. |
| `skill_name` | Installed skill directory name and default discoverability trigger. |
| `description` | Text used in default discoverability hook blocks. |
| `bundled_skill_path` | Package-relative bundled skill directory. Defaults to `_skill`. |
| `bundled_skill_source` | Optional filesystem directory for tests or custom development tooling. |
| `pypi_project_name` | Optional PyPI project name when it differs from `package_name`. |
| `hook_blocks` | Optional per-agent custom discoverability blocks keyed by `codex` or `claude`. |
| `manifest_package_aliases` | Optional old package names accepted when migrating existing installs. |
| `marker_slug_override` | Optional discoverability marker slug for preserving hook markers during renames. |

## Python API

Applications, tests, or custom automation can import `Installer` directly:

```python
from agent_skill_installer import Installer, SkillProject

PROJECT = SkillProject(
    package_name="your-skill-package",
    import_name="your_skill_package",
    version="1.2.3",
    skill_name="your-skill-package",
    description="Use this skill for my project workflows.",
)

installer = Installer(PROJECT)
installer.install(["codex"], "global")
installer.install(["claude"], "repo")
installer.uninstall(["codex"], "global")
```

By default, bundled installs read skill files from
`your_skill_package/_skill` inside the project package. Tests or development
tools can point `bundled_skill_source` at a filesystem directory:

```python
from pathlib import Path

PROJECT = SkillProject(
    package_name="api-demo-agent-skill",
    import_name="api_demo_agent_skill",
    version="0.1.0",
    skill_name="api-demo-agent-skill",
    description="Use this API demo skill.",
    bundled_skill_source=Path("skill"),
)
```

The API also exposes inspection helpers through `Installer`:

```python
installer.discover_managed_installations()
installer.inspect_installations()
installer.published_pypi_versions()
```

See [`examples/demo-installer/`](../examples/demo-installer/) for a wrapper CLI
and [`examples/api-install/`](../examples/api-install/) for direct API usage.
