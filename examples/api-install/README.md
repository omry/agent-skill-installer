# API Install Example

This example installs a skill by calling the Python API directly. It does not
define a package console script.

From the repository root:

```bash
PYTHONPATH=src python examples/api-install/install_demo_skill.py install \
  --agent codex \
  --scope repo \
  --repo /path/to/repo

PYTHONPATH=src python examples/api-install/install_demo_skill.py uninstall \
  --agent codex \
  --scope repo \
  --repo /path/to/repo
```

The script creates a `SkillProject` with `bundled_skill_source` pointing at the
adjacent `skill/` directory, then calls `Installer(project).install(...)` or
`Installer(project).uninstall(...)`.
