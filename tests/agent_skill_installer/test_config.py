from __future__ import annotations

from pathlib import Path

import pytest

from agent_skill_installer.config import (
    InstallerConfigError,
    load_installer_config,
    load_installer_config_text,
)


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
        type: skill-command
        executable: scripts/awd_prompt_nudge.py
        args: [consider]
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
    assert codex.hooks["UserPromptSubmit"][0].hooks[0].statusMessage == (
        "Considering AWD workflow guidance"
    )
    assert codex.hooks["UserPromptSubmit"][0].hooks[0].executable == (
        "scripts/awd_prompt_nudge.py"
    )
    assert codex.hooks["UserPromptSubmit"][0].hooks[0].args == ["consider"]
    assert claude.requires.claude_code == ">=2.1.141"
    assert claude.instructions is not None
    assert claude.instructions.body == "Use AWD for gated work."
    assert claude.hooks["UserPromptSubmit"][0].hooks[0].command == (
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
              - type: skill-command
                executable: scripts/tool.py
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
              - type: skill-command
                executable: scripts/future.py
                futureField: accepted here
""",
    )

    config = load_installer_config(config_path)

    codex = config.installer.agents.codex
    assert codex is not None
    assert codex.hooks_direct["FutureEvent"][0]["hooks"][0]["futureField"] == (
        "accepted here"
    )


def test_typed_hook_schema_accepts_open_event_names(tmp_path: Path) -> None:
    config = load_installer_config_text(
        """
installer:
  agents:
    codex:
      hooks:
        FutureEvent:
          - hooks:
              - type: skill-command
                executable: scripts/future.py
"""
    )

    codex = config.installer.agents.codex
    assert codex is not None
    assert codex.hooks["FutureEvent"][0].hooks[0].executable == "scripts/future.py"


def test_loads_external_wheel_copy_rules_with_package_version_context() -> None:
    config = load_installer_config_text(
        """
installer:
  external_wheels:
    - package: arbiter-client==${package.version}
      editable: ../client
      copies:
        - wheel_path: arbiter_client/bin/arbiter
          skill_path: bin/arbiter
          executable: true
          replace: true
""",
        package_version="2.0.0",
    )

    external_wheel = config.installer.external_wheels[0]
    assert external_wheel.package == "arbiter-client==2.0.0"
    assert external_wheel.editable == "../client"
    assert external_wheel.copies[0].wheel_path == "arbiter_client/bin/arbiter"
    assert external_wheel.copies[0].skill_path == "bin/arbiter"
    assert external_wheel.copies[0].executable is True
    assert external_wheel.copies[0].replace is True


def test_loads_payload_file_selection() -> None:
    config = load_installer_config_text(
        """
installer:
  payload:
    include:
      - SKILL.md
      - bin/**
    exclude:
      - bin/cache/**
"""
    )

    assert config.installer.payload.include == ["SKILL.md", "bin/**"]
    assert config.installer.payload.exclude == ["bin/cache/**"]


def test_loads_gemini_agent_config_and_hooks(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path / "agent-skill-installer.yaml",
        """
installer:
  version: 1
  shared:
    instructions:
      discoverability:
        title: Memory Workflow
        body: Use memory workflow for session snapshots.
  agents:
    gemini:
      version: 1
      requires:
        gemini_cli: ">=0.20.0"
      instructions: ${installer.shared.instructions.discoverability}
      hooks:
        PreCompress:
          - matcher: "*"
            sequential: true
            hooks:
              - type: skill-command
                executable: scripts/save_memory.py
                args: [snapshot]
                name: save-memory
                timeout: 15000
                description: Save session memory before compression
      hooks_direct:
        FutureGeminiEvent:
          - hooks:
              - type: skill-command
                executable: scripts/future.py
                futureField: accepted here
""",
    )

    config = load_installer_config(config_path)

    gemini = config.installer.agents.gemini
    assert gemini is not None
    assert gemini.version == 1
    assert gemini.requires.gemini_cli == ">=0.20.0"
    assert gemini.instructions is not None
    assert gemini.instructions.title == "Memory Workflow"
    assert gemini.instructions.body == "Use memory workflow for session snapshots."
    group = gemini.hooks["PreCompress"][0]
    assert group.matcher == "*"
    assert group.sequential is True
    hook = group.hooks[0]
    assert hook.type == "skill-command"
    assert hook.executable == "scripts/save_memory.py"
    assert hook.args == ["snapshot"]
    assert hook.name == "save-memory"
    assert hook.timeout == 15000
    assert hook.description == "Save session memory before compression"
    assert gemini.hooks_direct["FutureGeminiEvent"][0]["hooks"][0]["futureField"] == (
        "accepted here"
    )


def test_rejects_unknown_gemini_hook_field_and_unsupported_version(
    tmp_path: Path,
) -> None:
    bad_field = write_config(
        tmp_path / "bad-field.yaml",
        """
installer:
  agents:
    gemini:
      hooks:
        BeforeTool:
          - hooks:
              - type: skill-command
                executable: scripts/tool.py
                statusMessage: codex-only field
""",
    )
    with pytest.raises(InstallerConfigError) as error:
        load_installer_config(bad_field)
    assert "installer.agents.gemini.hooks.BeforeTool" in str(error.value)
    assert "statusMessage" in str(error.value)

    bad_version = write_config(
        tmp_path / "bad-version.yaml",
        """
installer:
  agents:
    gemini:
      version: 2
""",
    )
    with pytest.raises(InstallerConfigError, match="installer.agents.gemini.version"):
        load_installer_config(bad_version)


def test_loads_antigravity_agent_config_and_hooks(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path / "agent-skill-installer.yaml",
        """
installer:
  version: 1
  shared:
    instructions:
      discoverability:
        title: Review Workflow
        body: Use review workflow for code reviews.
  agents:
    antigravity:
      version: 1
      requires:
        antigravity: ">=1.0.0"
      instructions: ${installer.shared.instructions.discoverability}
      hooks:
        PreToolUse:
          - matcher: run_command
            hooks:
              - type: skill-command
                executable: scripts/gate.py
                args: [check]
                timeout: 15
        SessionStart:
          - hooks:
              - type: skill-command
                executable: scripts/init.py
                timeout: 30
      hooks_direct:
        FutureEvent:
          - hooks:
              - type: skill-command
                executable: scripts/future.py
                futureField: accepted here
""",
    )

    config = load_installer_config(config_path)

    antigravity = config.installer.agents.antigravity
    assert antigravity is not None
    assert antigravity.version == 1
    assert antigravity.requires.antigravity == ">=1.0.0"
    assert antigravity.instructions is not None
    assert antigravity.instructions.title == "Review Workflow"
    assert antigravity.instructions.body == "Use review workflow for code reviews."
    pre_tool = antigravity.hooks["PreToolUse"][0]
    assert pre_tool.matcher == "run_command"
    assert pre_tool.hooks[0].executable == "scripts/gate.py"
    assert pre_tool.hooks[0].args == ["check"]
    assert pre_tool.hooks[0].timeout == 15
    assert antigravity.hooks["SessionStart"][0].hooks[0].executable == "scripts/init.py"
    assert (
        antigravity.hooks_direct["FutureEvent"][0]["hooks"][0]["futureField"]
        == "accepted here"
    )


def test_rejects_unknown_antigravity_hook_field_and_unsupported_version(
    tmp_path: Path,
) -> None:
    bad_field = write_config(
        tmp_path / "bad-antigravity-field.yaml",
        """
installer:
  agents:
    antigravity:
      hooks:
        PreToolUse:
          - hooks:
              - type: skill-command
                executable: scripts/tool.py
                statusMessage: codex-only field
""",
    )
    with pytest.raises(InstallerConfigError) as error:
        load_installer_config(bad_field)
    assert "installer.agents.antigravity.hooks.PreToolUse" in str(error.value)
    assert "statusMessage" in str(error.value)

    bad_version = write_config(
        tmp_path / "bad-antigravity-version.yaml",
        """
installer:
  agents:
    antigravity:
      version: 2
""",
    )
    with pytest.raises(
        InstallerConfigError, match="installer.agents.antigravity.version"
    ):
        load_installer_config(bad_version)
