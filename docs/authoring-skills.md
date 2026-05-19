# Authoring Skills

This guide is for people writing a skill directory or a GitHub repository that
can be installed by `agent-skill-installer`.

A skill is just a directory with `SKILL.md` and any supporting files it needs.
It does not need `pyproject.toml` or `MANIFEST.in` unless you also want to
publish it as a Python package.

## Directory Layout

A minimal standalone skill:

```text
my-skill/
  SKILL.md
  agent-skill-installer.yaml  # Optional, see below
  scripts/
    helper.py
```

A repository can also keep the skill under `skill/`:

```text
my-skill-repo/
  skill/
    SKILL.md
    agent-skill-installer.yaml  # Optional, see below
    scripts/
      helper.py
```

Local and GitHub installs detect either root `SKILL.md` or `skill/SKILL.md`.
All supporting files in the skill directory are installed with the skill.

## Skill Metadata

`SKILL.md` may include simple YAML front matter. The installer reads only
`name`, `description`, and `version`:

```markdown
---
name: my-skill
description: Use this skill when working on my project workflows.
version: 0.1.0
---

# My Skill

Instructions for the agent go here.
```

`name` becomes the default installed skill directory name. `description` becomes
the fallback discoverability text. `version` is recorded in the internal install
manifest. All three fields are optional; if they are omitted, the installer uses
the source name, package name, selected version, or a generated description as
appropriate.

## Optional Installer Config

`agent-skill-installer.yaml` is optional. Put it next to `SKILL.md` when you
want to customize the discoverability block written to `AGENTS.md` or
`CLAUDE.md`.

```yaml
installer:
  version: 1
  agents:
    codex:
      instructions:
        title: My Skill
        body: Use this skill when working on my project workflows.
    claude:
      instructions:
        title: My Skill
        body: Use this skill when working on my project workflows.
```

When this file is omitted, the installer writes a default discoverability block
using the skill name and description. The default trigger text is `$<skill_name>`
for Codex and `/<skill_name>` for Claude Code.

Only `installer.version: 1` is currently supported. Unknown fields are rejected
in typed config sections so typos are caught during install. `installer.shared`
and per-agent `hooks_direct` are escape hatches for shared or backend-specific
data.

## Local Testing

From a repository or directory that contains `SKILL.md` or `skill/SKILL.md`,
run:

```bash
agent-skill-installer --no-ui install \
  --skill-path . \
  --editable \
  --agent codex \
  --scope repo \
  --repo /path/to/test-repo
```

Then inspect `/path/to/test-repo/.codex/skills/<skill_name>` and
`/path/to/test-repo/AGENTS.md`. Local installs default to symlinks, so edits to
the source skill directory are immediately visible from the installed skill
path. Use `--copy` when you want to test the exact copied snapshot an installer
would leave behind.

Uninstall after testing:

```bash
agent-skill-installer --no-ui uninstall \
  --skill-name my-skill \
  --agent codex \
  --scope repo \
  --repo /path/to/test-repo
```

## GitHub Installs

For GitHub distribution without PyPI, publish the same skill directory in a
repository. The installer can install from repository root, from `skill/`, or
from an explicit tree path:

```bash
agent-skill-installer --no-ui install \
  --github-url https://github.com/OWNER/REPO/tree/main/skill \
  --agent all \
  --scope global
```

Use the [Packaging And API](packaging-and-api.md) guide only when you want to
ship the skill inside a Python wheel, expose a custom installer command, or call
the installer from Python code.
