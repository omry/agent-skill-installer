# API And Wrapper CLI

This guide is for maintainers who want to expose a project-specific installer
command or use the installer from Python. To publish a skill as a Python
package, including platform-specific wheel layouts, see
[Packaging](packaging.md).

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
    bundled_skill_path="skill",
)


def main(argv=None) -> int:
    return installer_main(argv, project=PROJECT)
```

Wrapper commands open the text UI by default when they run in an interactive
terminal and the command is missing required choices such as the target agent or
scope. Pass `--no-ui` to disable the UI and require a complete noninteractive
command:

```bash
your-skill-package --no-ui install --agent codex --scope dir --repo
```

For wrapper commands, `--pypi` installs from the wrapper project's PyPI package
and lets pip resolve the compatible wheel:

```bash
your-skill-package --no-ui install --pypi --agent codex --scope dir --repo
```

Use `--pypi-version VERSION` only when you need an exact wrapper-project
version:

```bash
your-skill-package --no-ui install --pypi-version 1.2.3 --agent codex --scope dir --repo
```

Important `SkillProject` fields:

| Field | Purpose |
| --- | --- |
| `package_name` | Distribution name and internal install-manifest ownership name. Usually the PyPI project name. |
| `import_name` | Import package that contains the bundled skill files. |
| `version` | Version recorded in install manifests and shown by wrapper CLIs. Keep it aligned with the package version. |
| `skill_name` | Installed skill directory name and default discoverability trigger. |
| `description` | Text used in default discoverability hook blocks. |
| `bundled_skill_path` | Package-relative bundled skill directory. Required for packaged skill resources unless `bundled_skill_source` is set. |
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
    bundled_skill_path="skill",
)

installer = Installer(PROJECT)
installer.install(["codex"], "global")
installer.install(["claude"], "repo")
installer.uninstall(["codex"], "global")
```

The `bundled_skill_path` value is explicit: in the example above, bundled
installs read skill files from `your_skill_package/skill` inside the project
package. Tests or development tools can point `bundled_skill_source` at a
filesystem directory instead:

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
