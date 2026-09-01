# Agent Skill Installer Release Notes

<!-- TOWNCRIER -->

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
  workflow without treating a missing run URL as a failed dispatch. ([#40](https://github.com/omry/agent-skill-installer/issues/40))

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
