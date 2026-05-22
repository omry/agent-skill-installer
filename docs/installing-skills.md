# Installing Skills

This guide is for people who want to install a skill for Codex or Claude Code.
You do not need to create a Python package to use the installer.

## Install The Installer

```bash
python -m pip install agent-skill-installer
```

Run the text UI from a terminal:

```bash
agent-skill-installer
```

When the UI starts from a checkout that contains `SKILL.md` or
`skill/SKILL.md`, it offers that local source first. PyPI package and GitHub URL
prompts remember the 10 most recent successful installs in
`~/.agent-skill-installer/recent-installations.json`.

Use `--no-ui` in scripts or non-interactive shells. In no-UI mode, `install`
requires exactly one install source, an agent target, and a scope. `uninstall`
requires a skill name, an agent target, and a scope.

## Install Sources

Install from PyPI:

```bash
agent-skill-installer --no-ui install \
  --pypi-package your-skill-package \
  --agent all \
  --scope global
```

By default, PyPI installs use the latest wheel release that contains a bundled
`SKILL.md`. Pin a version with `--pypi-version`:

```bash
agent-skill-installer --no-ui install \
  --pypi-package your-skill-package \
  --pypi-version 1.2.3 \
  --agent codex \
  --scope repo
```

PyPI source installs download the wheel to a temporary directory and extract the
skill payload from that wheel. They do not install the downloaded skill package
into the current Python environment.

Install from a local wheel file:

```bash
agent-skill-installer --no-ui install \
  --wheel-file dist/your_skill_package-1.2.3-py3-none-any.whl \
  --agent codex \
  --scope repo
```

Local wheel installs read the bundled `SKILL.md` from the wheel file without
installing the Python package into the current environment. This is useful for
testing the exact artifact you plan to publish.

Install from GitHub:

```bash
agent-skill-installer --no-ui install \
  --github-url https://github.com/OWNER/REPO \
  --agent codex \
  --scope repo
```

Repository-root GitHub installs use the `main` ref by default and look for
`skill/SKILL.md` first, then root `SKILL.md`. You can point at a tree URL:

```bash
agent-skill-installer --no-ui install \
  --github-url https://github.com/OWNER/REPO/tree/v1/packages/my-skill \
  --agent claude \
  --scope global
```

Or use explicit ref and path flags:

```bash
agent-skill-installer --no-ui install \
  --github-url https://github.com/OWNER/REPO \
  --github-ref v1 \
  --github-path packages/my-skill \
  --agent codex \
  --scope repo
```

Install from a local skill directory or repository:

```bash
agent-skill-installer --no-ui install \
  --skill-path ./my-skill \
  --editable \
  --agent codex \
  --scope repo
```

`--skill-path` accepts a directory that contains `SKILL.md`, a repository that
contains `skill/SKILL.md`, or a parent directory whose immediate children are
skill directories. Local installs default to editable symlinks, so changes are
visible immediately during development. Use `--copy` to install a snapshot of
the current files instead. `--local-repo` is an alias for `--skill-path`.

When a source contains more than one skill, non-interactive installs require an
explicit selection:

```bash
agent-skill-installer --no-ui install \
  --skill-path ./skills-root \
  --src-skill skill-one \
  --src-skill skill-two \
  --agent codex \
  --scope repo
```

Use `--all-src-skills` to intentionally install every discovered source skill,
including skills added to that source in the future. Use `--rename SRC:DST` to
select and rename a source skill, or `--src-skill SRC --dst-skill DST` to rename
one explicitly selected source skill. The older `--skill-name` option remains a
compatibility alias for single-skill installs.

Use `--description` to override the default discoverability text when the skill
does not provide its own installer config.

## Targets

Use `--agent codex`, `--agent claude`, `--agent codex,claude`, or
`--agent all`. Use `--scope repo` for the current repository or `--scope global`
for the current user.

| Agent | Scope | Skill directory | Hook file |
| --- | --- | --- | --- |
| Codex | `repo` | `<repo>/.codex/skills/<skill_name>` | `<repo>/AGENTS.md` |
| Codex | `global` | `~/.codex/skills/<skill_name>` | `~/.codex/AGENTS.md` |
| Claude Code | `repo` | `<repo>/.claude/skills/<skill_name>` | `<repo>/CLAUDE.md` |
| Claude Code | `global` | `~/.claude/skills/<skill_name>` | `~/.claude/CLAUDE.md` |

For repo scope, pass `--repo PATH` to install into a repository other than the
current working directory. For global scope, pass `--codex-home PATH` or
`--claude-home PATH` to override the default agent home directories.

In the text UI, the install location screen offers user global, current
repository when one is detected, and specific directory. The specific directory
choice prompts for a repository path and installs with repo scope.
When the install source is a local repository or skill directory, the UI also
asks whether to install an editable symlink or a copied snapshot. If the source
contains multiple skills, the UI prompts for source skill selection and defaults
to no selected skills.

## Uninstall

Uninstall a skill installed by this tool:

```bash
agent-skill-installer --no-ui uninstall \
  --skill-name my-skill \
  --agent codex \
  --scope repo
```

Uninstall uses the install manifest written by the installer. It removes the
installed skill directory or symlink, the matching discoverability block, and
directories the installer created when they become empty.

## Ownership And Safety

Each install writes an internal JSON manifest. This file is generated by the
installer, not supplied by the user or skill author. Normal installs write it
under the installed skill as `scripts/.<skill_name>-install.json`; symlinked
local installs write a sidecar file named `.<skill_name>-install.json` next to
the symlink.

The manifest records the owning package or skill name, version, installed
files, hook markers, source details, source skill identity when it differs from
the installed name, and created directories. Reinstalls replace previously owned
installs. If an existing skill directory has no matching manifest, the installer
refuses to replace it unless you pass `--force`.

Pass `--verbose` to print installed skill, source, and hook paths.
