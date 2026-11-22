# Installing Skills

This guide is for people who want to install a skill for Codex, Claude Code,
Gemini CLI, or Antigravity. You do not need to create a Python package to use
the installer.

Gemini CLI and Antigravity integrations are **experimental**. Antigravity has
had limited manual testing; Gemini CLI has not yet had manual testing. Check
the installed skills, discoverability, and hooks in the agent before relying
on them.

Choose targets by runtime behavior:

| Target | Best fit | Discoverability | Runtime hooks |
| --- | --- | --- | --- |
| Codex | OpenAI coding-agent workflows | `AGENTS.md` | Native `hooks.json` |
| Claude Code | Claude Code workflows | `CLAUDE.md` | Parsed for compatibility; not installed |
| Gemini CLI (experimental) | Gemini terminal workflows | `GEMINI.md` | `settings.json` `hooks` |
| Antigravity (experimental) | Antigravity CLI or IDE workflows | Per-skill `always_on` rules | Native `hooks.json` |

Use one target, a comma-separated list, or `--agent all`. The target controls
where the skill, discoverability instructions, and optional runtime hooks are
written; it does not change the skill payload itself.

## Raw Skills And Codex Plugins

ASI installs raw agent skills: directories containing `SKILL.md`, together with
the supporting files in that skill directory. A Codex plugin is a separate
Codex distribution format with a `.codex-plugin/plugin.json` manifest. Plugins
may bundle skills, MCP server configuration, app mappings, lifecycle hooks,
marketplace metadata, screenshots, or icons.

For the raw `SKILL.md` format itself, see [Codex's skill documentation](https://developers.openai.com/codex/skills).

ASI does not install a Codex plugin as a plugin. Point ASI directly at the
bundled skill directory when you want to install a raw skill; ASI does not
resolve a plugin root's `.codex-plugin/plugin.json` manifest or its skill paths.
ASI copies files in the selected raw skill directory, but it does not interpret
or register the plugin's MCP, app, hook, marketplace, or presentation metadata,
and it does not manage plugin enable/disable state. Use [Codex's plugin workflow](https://developers.openai.com/codex/plugins/build)
for a reusable Codex plugin, especially when those plugin-level features are
part of the package. ASI's plugin support remains a separate design question;
see [issue #14](https://github.com/omry/agent-skill-installer/issues/14).

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

By default, PyPI installs pass the package name to pip and use the wheel pip
resolves or builds. Add a version specifier when you want a pinned version or
range:

```bash
agent-skill-installer --no-ui install \
  --pypi-package your-skill-package==1.2.3 \
  --agent codex \
  --scope dir \
  --repo
```

Version ranges and wildcards are also supported, for example
`--pypi-package 'your-skill-package>=1.2,<2'` or
`--pypi-package 'your-skill-package==1.*'`. Pip selects the matching
distribution for the current Python and platform.

PyPI source installs run `python -m pip wheel --no-deps --wheel-dir ...` in a
temporary directory and extract the skill payload from the resulting wheel. They
do not install the downloaded skill package into the current Python environment.
If the skill metadata declares external wheels, the installer uses the same pip
wheel flow for each declared companion package, then copies only the declared
files from the resulting wheel into the installed skill directory.

Install from a local wheel file:

```bash
agent-skill-installer --no-ui install \
  --wheel-file dist/your_skill_package-1.2.3-py3-none-any.whl \
  --agent codex \
  --scope dir \
  --repo
```

Local wheel installs read the bundled `SKILL.md` from the wheel file without
installing the Python package into the current environment. This is useful for
testing the exact artifact you plan to publish.

If the skill metadata declares external wheels, a local wheel install resolves
each declared companion from the directory holding the skill wheel, and only
from that directory. ASI runs
`python -m pip wheel --no-deps --isolated --no-index --only-binary=:all: --find-links <dir>`
for the declared spec with `PIP_CONFIG_FILE` pointed at the null device, so no
index is consulted, no source distribution is built, and neither ambient pip
environment variables such as `PIP_FIND_LINKS` nor global, user, or site pip
configuration can add another location. Place both artifacts in one directory:

```
wheelhouse/your_skill_package-1.2.3-py3-none-any.whl
wheelhouse/your_companion_package-1.2.3-py3-none-manylinux_2_17_x86_64.whl
```

A companion that is missing from that directory, carries the wrong version for
the declared spec, or has no wheel compatible with the current Python and
platform fails the install instead of falling back to an index. The declared
package spec, including `${package.version}`, stays authoritative.

To keep that boundary meaningful, a companion spec resolved this way must be an
ordinary distribution name and version requirement. A direct reference such as
`companion @ https://example.com/companion.whl`, a path to a wheel, or a bare
wheel filename is rejected, because pip treats those as explicit targets that
`--no-index` does not restrict. ASI also runs pip from an empty directory it
creates for the purpose, so a target string cannot be reinterpreted as a file
that happens to sit in the caller's working directory. Index installs are
unaffected and still accept direct references.

This makes the directory a trust boundary: any wheel in it matching the declared
distribution and version is used. Install from a directory you control, and do
not point `--wheel-file` at a shared download directory when the skill declares
companions.

Other install sources are unaffected. `--pypi-package` and `--github-url`
installs still resolve companions through the configured index, and local
`--skill-path` installs are unchanged.

Install from GitHub:

```bash
agent-skill-installer --no-ui install \
  --github-url https://github.com/OWNER/REPO \
  --agent codex \
  --scope dir \
  --repo
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
  --scope dir \
  --repo
```

GitHub installs are intended for source skill directories. Installing a GitHub
source that also needs platform-specific companion wheels is not officially
supported, because that workflow usually requires building package artifacts
from the checkout. Publish a wheel-packaged skill for that case, or install from
a local checkout while developing.

Install from a local skill directory or repository:

```bash
agent-skill-installer --no-ui install \
  --skill-path ./my-skill \
  --editable \
  --agent codex \
  --scope dir \
  --repo
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
  --scope dir \
  --repo
```

Use `--all-src-skills` to intentionally install every discovered source skill,
including skills added to that source in the future. Use `--rename SRC:DST` to
select and rename a source skill, or `--src-skill SRC --dst-skill DST` to rename
one explicitly selected source skill. The older `--skill-name` option remains a
compatibility alias for single-skill installs.

Renaming changes the installed directory only. `SKILL.md` frontmatter is copied
as-is, so a rename leaves the skill declaring its original name, and the
installer warns when the two disagree:

```
agent-skill-installer: warning: installing into skill directory renamed-skill
but SKILL.md frontmatter declares name: example-skill; the installer copies
frontmatter as-is, so the installed skill keeps the declared name
```

Use `--description` to override the default discoverability text when the skill
does not provide its own installer config.

## Replace An Installed Skill Under A New Name

To replace an existing installation, select exactly one source skill and name
its old installation with `--replace-skill OLD`. Interactive CLI and text UI
installs ask for confirmation, with No selected by default. Unattended installs
must also pass `--confirm-replacement`:

```bash
agent-skill-installer --no-ui install \
  --skill-path ./my-skill \
  --skill-name new-name \
  --replace-skill old-name --confirm-replacement \
  --agent claude --scope global
```

ASI requires an existing manifest for the named old installation in every
selected target, and an unused destination name. `--force` does not bypass
these requirements. Replacement installs the new payload and removes the old
skill directory or symlink, its recorded discoverability block, and its owned
runtime hooks. User instructions and hooks remain. If the transaction fails,
ASI restores the old installation. New runtime hooks still require the
agent's normal hook approval.

Source-selection options such as `--rename SRC:DST` choose the new install name;
use `--replace-skill OLD` as well when retiring a previous installation. Without
replacement, separate installation names remain separate installs. ASI does not
infer an old installation from similar names or unowned Markdown blocks.

## Targets

### Target paths

Use `--scope global` to install into the selected agent's config directory, or
`--scope dir` to install into a directory. Add `--repo` with `--scope dir` when
the directory must be resolved to, and asserted as, a Git or Sapling repository
root. Plain directory installs use the exact target directory and do not imply
that any agent runtime will automatically discover that directory.

| Agent | Scope | Skill directory | Instructions | Runtime hooks |
| --- | --- | --- | --- | --- |
| Codex | `dir` / `global` | `<target>/.codex/skills/<skill_name>` / `~/.codex/skills/<skill_name>` | `<target>/AGENTS.md` / `~/.codex/AGENTS.md` | `<target>/.codex/hooks.json` / `~/.codex/hooks.json` |
| Claude Code | `dir` / `global` | `<target>/.claude/skills/<skill_name>` / `~/.claude/skills/<skill_name>` | `<target>/CLAUDE.md` / `~/.claude/CLAUDE.md` | Parsed only; not installed |
| Gemini CLI (experimental) | `dir` / `global` | `<target>/.gemini/skills/<skill_name>` / `~/.gemini/skills/<skill_name>` | `<target>/GEMINI.md` / `~/.gemini/GEMINI.md` | `<target>/.gemini/settings.json` / `~/.gemini/settings.json` |
| Antigravity (experimental) | `dir` / `global` | `<target>/.agents/skills/<skill_name>` / `~/.gemini/config/skills/<skill_name>` | `<target>/.agents/rules/<skill_name>.md` / `~/.gemini/config/rules/<skill_name>.md` | `<target>/.agents/hooks.json` / `~/.gemini/config/hooks.json` |

Antigravity per-skill rules must use `trigger: always_on` so the installed
discoverability block is active. If an existing rule uses another trigger, the
installer reports the conflict and leaves that rule and skill installation
unchanged. User content in a compatible `always_on` rule is preserved.

For directory targets, pass `--target-dir PATH` to install somewhere other than
the current working directory. With `--repo`, the installer walks upward from
that path to find a `.git` or `.sl` root. For global targets, pass
`--codex-home PATH`, `--claude-home PATH`, `--gemini-home PATH`, or
`--antigravity-home PATH` to override the default agent home directories.

In the text UI, the install location screen offers agent config directory,
current repository directory when one is detected, and directory.
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
  --scope dir \
  --repo
```

Uninstall uses the install manifest written by the installer. It removes the
installed skill directory or symlink, the matching discoverability block,
owned Codex, Gemini CLI, and Antigravity runtime hook entries after their last
ASI owner is gone, and directories the installer created when they become empty.

## Ownership And Safety

Each install writes an internal JSON manifest. This file is generated by the
installer, not supplied by the user or skill author. Normal installs write it
next to `SKILL.md` as `.<skill_name>-install.json`; symlinked local installs
write a sidecar file with the same name next to the symlink.

The manifest records the owning package or skill name, version, installed
files, hook markers, source details, source skill identity when it differs from
the installed name, and created directories. If the install copied files from
external wheels, the manifest records them under `external_wheels` with the
declared package spec, resolved distribution and version, selected wheel
filename, SHA256 digest, resolver method, and each `wheel_path` to installed
`skill_path` copy.
For Codex, Gemini CLI, and Antigravity hooks, the manifest also records each
exact native hook entry that ASI owns. Existing user settings and hook entries
are preserved, including an identical entry that predates installation.
Runtime hooks require explicit approval for every supported target: Codex,
Gemini CLI, and Antigravity. Interactive CLI and text UI installs ask before
installing new hooks, with No as the default. Use `--enable-codex-hooks`,
`--enable-gemini-hooks`, or `--enable-antigravity-hooks` to approve hooks for
that agent without a prompt. Non-interactive installs skip new hooks unless
the matching flag is set. Python API callers must pass
`enable_codex_hooks=True`, `enable_gemini_hooks=True`, or
`enable_antigravity_hooks=True`; all three default to `False`.

On upgrades without approval, unchanged previously approved hook entries stay
installed. Added or changed entries require approval when the install already
owns hooks. Removed declarations remove the corresponding owned entries.
Installer approval does not grant native agent trust: review and trust Codex
hooks with `/hooks`, review enabled Gemini CLI hooks with `/hooks`, and review
Antigravity hooks in `hooks.json`. Packaged hooks name a skill-relative
executable; ASI validates it against the staged skill, ensures copied and
packaged payloads are executable on POSIX, and writes the installation's
absolute executable path into a host-quoted native hook command. Editable source
entry points must already be executable and are not modified. This pins the
entry point to that installed skill, but is not a sandbox for programs the entry
point may launch.
ASI manages standalone native `hooks.json` (Codex and Antigravity) and
`settings.json` `hooks` (Gemini CLI) entries only; Codex plugin packaging
and installation is tracked separately in
[issue #14](https://github.com/omry/agent-skill-installer/issues/14).
Reinstalls replace previously owned installs. If an existing skill directory has
no matching manifest, the installer refuses to replace it unless you pass
`--force`.

Pass the global `--verbose` option before the subcommand to print installed
skill, source, instruction, and runtime-hook paths:

```bash
agent-skill-installer --no-ui --verbose install ...
```
