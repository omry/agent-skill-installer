# Agent Skill Installer Release Notes

<!-- TOWNCRIER -->

## Agent Skill Installer 0.2.0 (2026-06-06)


### Features

- Add platform-specific skill selector packages through `agent-skill-selector.yaml`, letting one install target choose the correct platform-specific skill payload. Includes a runnable example. ([#18](https://github.com/omry/agent-skill-installer/issues/18))
- Install multiple skills from local directories, GitHub archives, PyPI packages, and wheel files, with explicit selection and rename controls. ([#1](https://github.com/omry/agent-skill-installer/issues/1))

### Bug Fixes

- Reject invalid `SKILL.md` YAML frontmatter before installing from local/editable sources, PyPI wheels, or GitHub archives. ([#8](https://github.com/omry/agent-skill-installer/issues/8))

### API changes and deprecations

- `--target-dir PATH` is now the canonical flag for directory-scoped installs; `--repo` remains supported as a compatibility alias. The TUI now presents these installs as directory choices and shows when the selected directory resolves to a Git or Sapling repository. ([#19](https://github.com/omry/agent-skill-installer/issues/19))
