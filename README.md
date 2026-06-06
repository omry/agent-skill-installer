# Agent Skill Installer

|  | Description |
| --- | --- |
| Project | [![PyPI version](https://badge.fury.io/py/agent-skill-installer.svg)](https://badge.fury.io/py/agent-skill-installer)[![Downloads](https://pepy.tech/badge/agent-skill-installer/month)](https://pepy.tech/project/agent-skill-installer) [![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)](https://pypi.org/project/agent-skill-installer/) [![License](https://img.shields.io/pypi/l/agent-skill-installer.svg)](https://github.com/omry/agent-skill-installer/blob/main/LICENSE) |
| Status | [![CI](https://github.com/omry/agent-skill-installer/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/omry/agent-skill-installer/actions/workflows/ci.yml) [![Publish](https://github.com/omry/agent-skill-installer/actions/workflows/publish.yml/badge.svg)](https://github.com/omry/agent-skill-installer/actions/workflows/publish.yml) |
| Docs and support | [![Zulip chat](https://img.shields.io/badge/chat-Zulip-2e77d0?logo=zulip)](https://hydra-framework.zulipchat.com/#narrow/channel/agent-skill-installer) |

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

![Interactive install source picker](https://raw.githubusercontent.com/omry/agent-skill-installer/main/docs/images/ui-install.png)

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
| Installing a skill | [Installing Skills](https://github.com/omry/agent-skill-installer/blob/main/docs/installing-skills.md) |
| Writing a skill directory or GitHub skill repo | [Authoring Skills](https://github.com/omry/agent-skill-installer/blob/main/docs/authoring-skills.md) |
| Publishing a skill on PyPI or embedding the installer API | [Packaging And API](https://github.com/omry/agent-skill-installer/blob/main/docs/packaging-and-api.md) |
| Publishing a skill that installs platform-specific variants | [Platform-Specific Skills](https://github.com/omry/agent-skill-installer/blob/main/docs/platform-specific-skills.md) |
| Maintaining releases | [Maintainer Guide](https://github.com/omry/agent-skill-installer/blob/main/docs/maintainer-guide.md) |

## Examples

The [`examples/`](https://github.com/omry/agent-skill-installer/blob/main/examples/README.md) directory contains runnable integrations:

- `examples/demo-installer/` is a complete Python package that carries a bundled
  skill and exposes a project-specific wrapper command.
- `examples/wheel-skill/` is a plain wheel-packaged skill for generic
  `--wheel-file` installs.
- `examples/platform-specific-skill/` demonstrates a selector wheel that
  resolves to a platform-specific skill payload.
- `examples/api-install/` shows direct Python API usage without a wrapper
  console script.
