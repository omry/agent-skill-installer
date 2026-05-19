# Demo Agent Skill

This example is a complete skill-carrying Python package. It bundles a demo
skill under `src/demo_agent_skill/_skill` and exposes a `demo-agent-skill`
console script that delegates to `agent-skill-installer`.

From the repository root:

```bash
python -m pip install -e .
python -m pip install -e examples/demo-installer
demo-agent-skill --no-ui install --agent all --scope repo --repo /path/to/repo
demo-agent-skill --no-ui uninstall --agent all --scope repo --repo /path/to/repo
```

For a real skill package, replace the package metadata, `SkillProject` values,
and `_skill` payload with your own project details.
