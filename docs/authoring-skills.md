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
  agent-skill-installer.yaml  # Optional install-time metadata, see below
  scripts/
    helper.py
```

A repository can also keep the skill under `skill/`:

```text
my-skill-repo/
  skill/
    SKILL.md
    agent-skill-installer.yaml  # Optional install-time metadata, see below
    scripts/
      helper.py
```

Local and GitHub installs detect either root `SKILL.md` or `skill/SKILL.md`.
Runtime supporting files in the skill directory are installed with the skill;
installer metadata files are consumed during install and omitted from copied
installs.

## Skill Metadata

`SKILL.md` may include YAML frontmatter at the top of the Markdown file. This
frontmatter is real YAML metadata delimited by `---`; it is not a Markdown
comment. If present, it must parse as a YAML mapping. The installer validates
the frontmatter before installing or replacing a skill, so syntax errors such
as an unquoted `: ` inside a plain scalar fail early instead of producing a
skill that Codex or Claude Code cannot load.

The installer reads only `name`, `description`, and `version`:

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
manifest. All three fields are optional for the installer; if they are omitted,
the installer uses the source name, package name, selected version, or a
generated description as appropriate. Some agents may require frontmatter fields
for their own skill discovery, so published Codex skills should include valid
`name` and `description` fields.

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

The installer loads this file with OmegaConf, resolves interpolations, rejects
unknown fields in typed sections, and merges the result into the local dataclass
schema. For copied installs from a local directory, GitHub archive, or wheel,
the installer consumes this metadata file but does not copy it into the
installed skill directory. The schema shape is:

```python
@dataclass
class InstallerConfig:
    installer: InstallerRoot = field(default_factory=InstallerRoot)

@dataclass
class InstallerRoot:
    version: int = 1
    shared: dict[str, Any] = field(default_factory=dict)
    agents: AgentConfigs = field(default_factory=AgentConfigs)

@dataclass
class AgentConfigs:
    codex: CodexAgentConfig | None = None
    claude: ClaudeAgentConfig | None = None

@dataclass
class CodexAgentConfig:
    version: int = 1
    requires: CodexRequires = field(default_factory=CodexRequires)
    instructions: AgentInstructions | None = None
    hooks: CodexHooks = field(default_factory=CodexHooks)
    hooks_direct: dict[str, Any] = field(default_factory=dict)

@dataclass
class ClaudeAgentConfig:
    version: int = 1
    requires: ClaudeRequires = field(default_factory=ClaudeRequires)
    instructions: AgentInstructions | None = None
    hooks: ClaudeHooks = field(default_factory=ClaudeHooks)
    hooks_direct: dict[str, Any] = field(default_factory=dict)

@dataclass
class AgentInstructions:
    title: str
    body: str

@dataclass
class CodexCommandHook:
    type: str = "command"
    command: str
    timeout: int | None = None
    statusMessage: str | None = None

@dataclass
class CodexHookMatcher:
    matcher: str | None = None
    hooks: list[CodexCommandHook] = field(default_factory=list)

@dataclass
class CodexHooks:
    SessionStart: list[CodexHookMatcher] = field(default_factory=list)
    PreToolUse: list[CodexHookMatcher] = field(default_factory=list)
    PermissionRequest: list[CodexHookMatcher] = field(default_factory=list)
    PostToolUse: list[CodexHookMatcher] = field(default_factory=list)
    UserPromptSubmit: list[CodexHookMatcher] = field(default_factory=list)
    Stop: list[CodexHookMatcher] = field(default_factory=list)

@dataclass
class ClaudeHook:
    type: str = "command"
    command: str | None = None
    timeout: int | None = None
    url: str | None = None
    prompt: str | None = None
    tool: str | None = None
    args: dict[str, Any] | None = None

@dataclass
class ClaudeHookMatcher:
    matcher: str | None = None
    hooks: list[ClaudeHook] = field(default_factory=list)

@dataclass
class ClaudeHooks:
    SessionStart: list[ClaudeHookMatcher] = field(default_factory=list)
    PreToolUse: list[ClaudeHookMatcher] = field(default_factory=list)
    PostToolUse: list[ClaudeHookMatcher] = field(default_factory=list)
    Notification: list[ClaudeHookMatcher] = field(default_factory=list)
    Stop: list[ClaudeHookMatcher] = field(default_factory=list)
    SubagentStop: list[ClaudeHookMatcher] = field(default_factory=list)
    UserPromptSubmit: list[ClaudeHookMatcher] = field(default_factory=list)
    PreCompact: list[ClaudeHookMatcher] = field(default_factory=list)
```

`instructions` is used today to write the discoverability block. `hooks` and
`hooks_direct` are parsed and schema-validated when present, so packaged skills
can carry hook metadata, but this installer does not yet write Codex or Claude
runtime hook settings.

You can check a config file directly from Python:

```bash
python -c 'from agent_skill_installer import load_installer_config; load_installer_config("agent-skill-installer.yaml")'
```

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
