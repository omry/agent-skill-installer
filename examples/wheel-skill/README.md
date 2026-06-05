# Wheel Agent Skill

This example is a plain Python wheel that carries a bundled skill but does not
provide its own installer command. It is the smallest packaging shape for a
skill you want to publish to PyPI or install from a local wheel artifact with
the generic `agent-skill-installer` command.

From the repository root:

```bash
python -m build --wheel --no-isolation --outdir /tmp/wheel-agent-skill-dist examples/wheel-skill
agent-skill-installer --no-ui install \
  --wheel-file /tmp/wheel-agent-skill-dist/wheel_agent_skill-0.1.0-py3-none-any.whl \
  --agent codex \
  --scope repo \
  --repo /path/to/repo
```

The Python package files are only there to produce the wheel. The bundled skill
files live in `src/wheel_agent_skill/_skill/`. The
`agent-skill-installer.yaml` file is install-time metadata for configured
discoverability text and schema-validated hook metadata; it is consumed from the
wheel but not copied into the installed skill directory.
