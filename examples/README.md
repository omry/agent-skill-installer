# Examples

This directory contains small, runnable integrations for
`agent-skill-installer`.

## Demo Installer Package

`demo-installer/` is a complete Python package that carries a bundled skill and
exposes its own installer command.

Run it from this repository checkout without publishing anything:

```bash
python -m pip install -e .
python -m pip install -e examples/demo-installer
demo-agent-skill --no-ui install --agent codex --scope repo --repo /path/to/repo
demo-agent-skill --no-ui uninstall --agent codex --scope repo --repo /path/to/repo
```

The important files are:

- `demo-installer/pyproject.toml`: declares the wrapper console script.
- `demo-installer/MANIFEST.in`: includes the bundled skill files in built
  distributions.
- `demo-installer/src/demo_agent_skill/cli.py`: creates the `SkillProject` and
  delegates to `agent_skill_installer.cli.main`.
- `demo-installer/src/demo_agent_skill/_skill/SKILL.md`: the bundled skill
  payload.

## API Install Script

`api-install/` shows direct Python API usage. It is useful for applications,
tests, or custom automation that should drive installation itself instead of
shipping a console-script wrapper.

Run it from this repository checkout:

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

The script points `SkillProject.bundled_skill_source` at the adjacent
`api-install/skill/` directory and calls `Installer(project).install(...)` or
`Installer(project).uninstall(...)` directly.
