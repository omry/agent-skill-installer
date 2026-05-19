# Agent Skill Installer

|  | Description |
| --- | --- |
| Project | [![PyPI version](https://badge.fury.io/py/agent-skill-installer.svg)](https://badge.fury.io/py/agent-skill-installer) [![Downloads](https://img.shields.io/pypi/dm/agent-skill-installer.svg)](https://pypi.org/project/agent-skill-installer/) [![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](https://pypi.org/project/agent-skill-installer/) [![License](https://img.shields.io/pypi/l/agent-skill-installer.svg)](LICENSE) |
| Status | [![CI](https://github.com/omry/agent-skill-installer/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/omry/agent-skill-installer/actions/workflows/ci.yml) [![Publish](https://github.com/omry/agent-skill-installer/actions/workflows/publish.yml/badge.svg)](https://github.com/omry/agent-skill-installer/actions/workflows/publish.yml) |

`agent-skill-installer` installs agent skills for Codex and Claude Code from
local skill directories, GitHub repositories, or PyPI wheels. It supports repo
and global install scopes, writes discoverability blocks into the agent hook
files, and records enough install state to safely upgrade or uninstall skills it
owns.

Additional agent targets or installer functionality are open for discussion,
and pull requests are welcome.

## Install

```bash
python -m pip install agent-skill-installer
```

Run the interactive installer:

```bash
agent-skill-installer
```

![Interactive install source picker](docs/images/ui-install.png)

Use `--no-ui` for scripts:

```bash
agent-skill-installer --no-ui install \
  --skill-path ./my-skill \
  --editable \
  --agent codex \
  --scope repo
```

## Documentation

| Audience | Start here |
| --- | --- |
| Installing a skill | [Installing Skills](docs/installing-skills.md) |
| Writing a skill directory or GitHub skill repo | [Authoring Skills](docs/authoring-skills.md) |
| Publishing a skill on PyPI or embedding the installer API | [Packaging And API](docs/packaging-and-api.md) |

## Examples

The [`examples/`](examples/README.md) directory contains runnable integrations:

- `examples/demo-installer/` is a complete Python package that carries a bundled
  skill and exposes a project-specific wrapper command.
- `examples/wheel-skill/` is a plain wheel-packaged skill for generic
  `--wheel-file` installs.
- `examples/api-install/` shows direct Python API usage without a wrapper
  console script.
