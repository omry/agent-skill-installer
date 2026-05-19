from __future__ import annotations

from pathlib import Path

import pytest

from agent_skill_installer.config import InstallerConfigError, load_installer_config


def write_config(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_loads_per_agent_versions_and_interpolated_shared_data(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path / "agent-skill-installer.yaml",
        """
installer:
  version: 1
  shared:
    instructions:
      discoverability:
        title: AWD Discoverability
        body: Use AWD for gated work.
    hooks:
      prompt_nudge:
        type: command
        command: python3 .codex/hooks/awd_prompt_nudge.py
        timeout: 5
        statusMessage: Considering AWD workflow guidance
  agents:
    codex:
      version: 1
      requires:
        codex: ">=0.116.0"
      instructions: ${installer.shared.instructions.discoverability}
      hooks:
        UserPromptSubmit:
          - hooks:
              - ${installer.shared.hooks.prompt_nudge}
    claude:
      version: 1
      requires:
        claude_code: ">=2.1.141"
      instructions: ${installer.shared.instructions.discoverability}
      hooks:
        UserPromptSubmit:
          - hooks:
              - type: command
                command: python3 .claude/hooks/awd_prompt_nudge.py
                timeout: 5
""",
    )

    config = load_installer_config(config_path)

    codex = config.installer.agents.codex
    claude = config.installer.agents.claude
    assert codex is not None
    assert claude is not None
    assert codex.version == 1
    assert codex.requires.codex == ">=0.116.0"
    assert codex.instructions is not None
    assert codex.instructions.title == "AWD Discoverability"
    assert codex.hooks.UserPromptSubmit[0].hooks[0].statusMessage == (
        "Considering AWD workflow guidance"
    )
    assert claude.requires.claude_code == ">=2.1.141"
    assert claude.instructions is not None
    assert claude.instructions.body == "Use AWD for gated work."
    assert claude.hooks.UserPromptSubmit[0].hooks[0].command == (
        "python3 .claude/hooks/awd_prompt_nudge.py"
    )


def test_rejects_unknown_backend_field_with_readable_path(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path / "agent-skill-installer.yaml",
        """
installer:
  agents:
    codex:
      hooks:
        UserPromptSubmit:
          - hooks:
              - type: command
                command: python3 hook.py
                status_message: wrong spelling
""",
    )

    with pytest.raises(InstallerConfigError) as error:
        load_installer_config(config_path)

    message = str(error.value)
    assert "syntax error in remote" in message
    assert "status_message" in message
    assert "installer.agents.codex.hooks.UserPromptSubmit" in message


def test_rejects_unsupported_agent_schema_version(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path / "agent-skill-installer.yaml",
        """
installer:
  agents:
    claude:
      version: 99
""",
    )

    with pytest.raises(InstallerConfigError, match="installer.agents.claude.version"):
        load_installer_config(config_path)


def test_preserves_backend_direct_hook_escape_hatch(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path / "agent-skill-installer.yaml",
        """
installer:
  agents:
    codex:
      hooks_direct:
        FutureEvent:
          - hooks:
              - type: command
                command: python3 future.py
                futureField: accepted here
""",
    )

    config = load_installer_config(config_path)

    codex = config.installer.agents.codex
    assert codex is not None
    assert codex.hooks_direct["FutureEvent"][0]["hooks"][0]["futureField"] == (
        "accepted here"
    )
