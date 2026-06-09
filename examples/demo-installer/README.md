# Demo Agent Skill

This example is a complete Python package that carries a skill. It bundles a
demo skill under `src/demo_agent_skill/skill` and exposes a `demo-agent-skill`
console script that delegates to `agent-skill-installer`.

The package has `pyproject.toml` and `MANIFEST.in` because it is a Python
distribution. A standalone skill directory only needs `SKILL.md` and any helper
files the skill uses.

See [Packaging](../../docs/packaging.md) for publishing this kind of bundled
skill package, and [API And Wrapper CLI](../../docs/api-and-wrapper-cli.md) for
the wrapper command pattern this demo uses.

From the repository root:

```bash
python -m pip install -e .
python -m pip install -e examples/demo-installer
demo-agent-skill --no-ui install --agent all --scope dir --repo --target-dir /path/to/repo
demo-agent-skill --no-ui uninstall --agent all --scope dir --repo --target-dir /path/to/repo
```

For a real skill package, replace the package metadata, `SkillProject` values,
and `skill` payload with your own project details.
