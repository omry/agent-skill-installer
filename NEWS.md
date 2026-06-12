# Agent Skill Installer Release Notes

<!-- TOWNCRIER -->

## Agent Skill Installer 0.2.1 (2026-06-12)


No significant changes.


## Agent Skill Installer 0.2.0 (2026-06-10)


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
