# Agent Skill Installer Release Notes

<!-- TOWNCRIER -->

## Agent Skill Installer 0.5.0 (2026-11-22)


### Features

- Add Gemini CLI as an experimental agent target (`--agent gemini`, included in `--agent all`), with skills, `GEMINI.md` discoverability, native runtime hooks, a `--gemini-home` override, and manifest-tracked uninstall support. Interactive CLI installs ask before enabling Gemini hooks; `--enable-gemini-hooks` approves them without a prompt for unattended installs. ([#3](https://github.com/omry/agent-skill-installer/issues/3))

### Bug Fixes

- The interactive installer now keeps invalid local and wheel sources inside the prompt and restores terminal settings after UI exits. ([#51](https://github.com/omry/agent-skill-installer/issues/51))

### API changes and deprecations

- Install Codex command hooks into native repo or global `hooks.json` files, preserving user entries and tracking exact ownership for safe reinstall and uninstall. `CodexHooks` and `ClaudeHooks` now use open event-name mappings; Python callers should access events by mapping key instead of dataclass attribute. ([#43](https://github.com/omry/agent-skill-installer/issues/43))
- Package Codex hooks as `type: skill-command` with a validated skill-relative executable and structured arguments; ASI now binds each native hook to that installation's absolute entry point, safely quotes Windows shell metacharacters, and ensures copied or packaged POSIX entry points are executable without modifying editable sources. Legacy packaged `type: command` declarations are rejected explicitly, and the exported `CodexCommandHook` schema is replaced by `CodexSkillCommandHook`. ([#45](https://github.com/omry/agent-skill-installer/issues/45))
- Add Antigravity as an experimental agent target (`--agent antigravity`, included in `--agent all`), installing skills into `.agents/skills` or `~/.gemini/config/skills`, always-on discoverability rules into `.agents/rules/<skill>.md` or `~/.gemini/config/rules/<skill>.md`, and `type: skill-command` runtime hooks into `.agents/hooks.json` or `~/.gemini/config/hooks.json` with `--antigravity-home` override and manifest-tracked uninstall support. Interactive CLI installs ask before enabling hooks; use `--enable-antigravity-hooks` to approve them in non-interactive installs. ([#47](https://github.com/omry/agent-skill-installer/issues/47))
- Runtime hooks for Codex, Gemini CLI, and Antigravity now require explicit installation approval. Interactive CLI and text UI installs default to No; unattended installs require the matching `--enable-<agent>-hooks` flag, and Python API callers must pass `enable_<agent>_hooks=True`. Previously approved unchanged hook entries survive upgrades; changed entries require renewed approval. Codex still requires its native `/hooks` trust review. ([#57](https://github.com/omry/agent-skill-installer/issues/57))
## Agent Skill Installer 0.4.0 (2026-09-01)


### Features

- Renaming a skill at install time now warns when the install directory name
  disagrees with the name declared in `SKILL.md` frontmatter, since the installer
  copies frontmatter as-is and the installed skill keeps its declared name. ([#32](https://github.com/omry/agent-skill-installer/issues/32))

### API changes and deprecations

- Installing a skill from a local wheel now resolves its declared companion wheels from the directory holding that skill wheel, with no index lookup and no source builds, so an unpublished release can ship both artifacts in one wheelhouse. A companion that is missing, mis-versioned, or incompatible with the current Python and platform now fails the install instead of resolving from PyPI. Other install sources are unchanged. Companion specs resolved this way must be ordinary distribution name and version requirements, and resolution is isolated from everything outside that directory: direct references, wheel paths, and bare wheel filenames are rejected, ambient pip environment variables and global, user, and site pip configuration are ignored, and pip runs from an empty directory so no local file can be reinterpreted as the target. ([#36](https://github.com/omry/agent-skill-installer/issues/36))
- The maintainer release command now suppresses successful subprocess output by
  default; pass `verbose=true` for live command output. Publication prints the
  exact GitHub Actions run when available and otherwise links to the Publish
  workflow without treating a missing run URL as a failed dispatch. The
  protected PyPI approval is available near the start after a quick status
  check, while publication still waits for release validation to pass;
  already-published recovery runs skip the no-op approval. ([#40](https://github.com/omry/agent-skill-installer/issues/40))

## Agent Skill Installer 0.3.0 (2026-06-12)


### Features

- Add platform-specific skill installation for packaged skills, allowing a platform-neutral skill package to install platform-specific companion files such as a native `arbiter-client` executable for the current platform. ([#18](https://github.com/omry/agent-skill-installer/issues/18))
- Install multiple skills from local directories, GitHub archives, PyPI packages, and wheel files, with explicit selection and rename controls. ([#1](https://github.com/omry/agent-skill-installer/issues/1))

### Bug Fixes

- Reject invalid `SKILL.md` YAML frontmatter before installing from local/editable sources, PyPI wheels, or GitHub archives. ([#8](https://github.com/omry/agent-skill-installer/issues/8))

### API changes and deprecations

- Generated install manifests for copied, wheel, PyPI, and GitHub installs now live next to `SKILL.md` as `.<skill-name>-install.json` instead of under `scripts/`. The implicit `SkillProject.bundled_skill_path` default was removed; wrapper/API authors must specify the packaged skill payload path unless they set `bundled_skill_source`. ASI temporarily discovers manifests in the old location for uninstall and ownership checks, but that compatibility is planned for removal no earlier than 2027-01-01. ([#18](https://github.com/omry/agent-skill-installer/issues/18))
- `--target-dir PATH` is now the canonical flag for choosing the directory used by directory-scoped installs. The TUI now presents these installs as directory choices and shows when the selected directory resolves to a Git or Sapling repository. ([#19](https://github.com/omry/agent-skill-installer/issues/19))
- Install targets now use explicit `global` or `dir` scope. Add `--repo` to `--scope dir` when the directory must resolve to a Git or Sapling repository root; plain directory installs use the exact target directory and do not imply automatic agent discovery. ([#24](https://github.com/omry/agent-skill-installer/issues/24))
- Copied skill installs can now opt into payload file selection with `installer.payload.include` and `installer.payload.exclude`, while the default remains installing `SKILL.md` and adjacent payload files recursively. Local companion wheels declared with `external_wheels[].editable` are now built as normal wheels from the local source path instead of using pip editable mode. ([#25](https://github.com/omry/agent-skill-installer/issues/25))


## Agent Skill Installer 0.2.0 (2026-06-10)


This release was retracted; use Agent Skill Installer 0.3.0 instead.
