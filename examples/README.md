# Examples

This directory contains small, runnable integrations for
`agent-skill-installer`.

For user-facing installation commands, see
[`docs/installing-skills.md`](../docs/installing-skills.md). For standalone
skill structure, see [`docs/authoring-skills.md`](../docs/authoring-skills.md).
For Python package publishing, see [`docs/packaging.md`](../docs/packaging.md).
For API and wrapper CLI integration, see
[`docs/api-and-wrapper-cli.md`](../docs/api-and-wrapper-cli.md).

## Demo Installer Package

`demo-installer/` is a complete Python package that carries a bundled skill and
exposes its own installer command. The `pyproject.toml` and `MANIFEST.in` files
belong to the Python package; the bundled skill itself is the `skill/`
directory containing `SKILL.md`. The demo package has its own README because
its `pyproject.toml` uses that file as package metadata.

Run it from this repository checkout without publishing anything:

```bash
python -m pip install -e .
python -m pip install -e examples/demo-installer
demo-agent-skill --no-ui install --agent codex --scope repo --target-dir /path/to/repo
demo-agent-skill --no-ui uninstall --agent codex --scope repo --target-dir /path/to/repo
```

The important files are:

- `demo-installer/pyproject.toml`: declares package metadata and the wrapper
  console script.
- `demo-installer/MANIFEST.in`: setuptools input that includes the bundled skill
  files in built distributions.
- `demo-installer/README.md`: package README used by the demo package metadata.
- `demo-installer/src/demo_agent_skill/cli.py`: creates the `SkillProject` and
  delegates to `agent_skill_installer.cli.main`.
- `demo-installer/src/demo_agent_skill/skill/SKILL.md`: the bundled skill
  payload.

## Wheel Skill Package

`wheel-skill/` is a plain Python package that carries a bundled skill in its
wheel but does not expose a wrapper command. Build the wheel, then point the
generic installer at the local artifact:

```bash
python -m build --wheel --no-isolation --outdir /tmp/wheel-agent-skill-dist examples/wheel-skill
agent-skill-installer --no-ui install \
  --wheel-file /tmp/wheel-agent-skill-dist/wheel_agent_skill-0.1.0-py3-none-any.whl \
  --agent codex \
  --scope repo \
  --target-dir /path/to/repo
```

The important files are:

- `wheel-skill/pyproject.toml`: declares package metadata for the wheel.
- `wheel-skill/MANIFEST.in`: includes the bundled skill files in the wheel.
- `wheel-skill/wheel_agent_skill/skill/SKILL.md`: the bundled skill
  payload.
- `wheel-skill/wheel_agent_skill/skill/agent-skill-installer.yaml`:
  install-time metadata for configured discoverability text and
  schema-validated hook metadata.

## Companion Wheel Skill

`companion-wheel-skill/` shows the platform executable pattern: the skill package
commits a portable launcher, and real installs replace that launcher with a file
copied from a companion wheel.

The important files are:

- `companion-wheel-skill/skill-package/companion_wheel_skill/skill/bin/demo-tool`:
  the portable launcher used by editable installs.
- `companion-wheel-skill/skill-package/companion_wheel_skill/skill/agent-skill-installer.yaml`:
  declares the companion package, local development path, and replacement copy.
- `companion-wheel-skill/native-client/`: example companion package that provides
  the file copied into real installs.

## API Install Script

`api-install/` shows direct Python API usage. It is useful for applications,
tests, or custom automation that should drive installation itself instead of
shipping a console-script wrapper.

Run it from this repository checkout:

```bash
PYTHONPATH=src python examples/api-install/install_demo_skill.py install \
  --agent codex \
  --scope repo \
  --target-dir /path/to/repo

PYTHONPATH=src python examples/api-install/install_demo_skill.py uninstall \
  --agent codex \
  --scope repo \
  --target-dir /path/to/repo
```

The script points `SkillProject.bundled_skill_source` at the adjacent
`api-install/skill/` directory and calls `Installer(project).install(...)` or
`Installer(project).uninstall(...)` directly.

The important files are:

- `api-install/install_demo_skill.py`: creates the `SkillProject` and calls the
  installer API directly.
- `api-install/skill/SKILL.md`: the filesystem skill payload used by
  `bundled_skill_source`.
