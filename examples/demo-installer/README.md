# Demo Agent Skill

This example is a complete Python package that carries a skill. It bundles a
demo skill under `src/demo_agent_skill/_skill` and exposes a `demo-agent-skill`
console script that delegates to `agent-skill-installer`.

The package has `pyproject.toml` and `MANIFEST.in` because it is a Python
distribution. A standalone skill directory only needs `SKILL.md` and any helper
files the skill uses.

See [Packaging And API](../../docs/packaging-and-api.md) for the packaging
pattern this demo uses.

From the repository root:

```bash
python -m pip install -e .
python -m pip install -e examples/demo-installer
demo-agent-skill --no-ui install --agent all --scope repo --repo /path/to/repo
demo-agent-skill --no-ui uninstall --agent all --scope repo --repo /path/to/repo
```

For a real skill package, replace the package metadata, `SkillProject` values,
and `_skill` payload with your own project details.
