from __future__ import annotations

import json
import asyncio
import importlib.util
import os
import shlex
import subprocess
import sys
import zipfile
from argparse import Namespace
from pathlib import Path

import pytest

from agent_skill_installer import __version__
from agent_skill_installer.cli import (
    BackRequested,
    PROMPT_BACK,
    build_no_ui_command,
    command_preview_classes,
    complete_with_ui,
    format_status_line,
    install_source_choices,
    installation_option_choices,
    installation_summary_text,
    make_textual_checkbox_app,
    make_textual_select_app,
    make_textual_version_app,
    main,
    pypi_version_choices,
    target_choices,
    update_command_preview_display,
)
from agent_skill_installer.installer import (
    PYPI_METADATA_TIMEOUT_SECONDS,
    InstallationStatus,
    Installer,
    InstallerError,
    SkillProject,
    copy_pypi_wheel_skill,
    fetch_json_url,
    install_source_metadata,
    manifest_path,
    published_pypi_versions,
    read_manifest as read_raw_manifest,
)


def make_skill(path: Path, text: str = "example skill\n") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "agents").mkdir()
    (path / "scripts").mkdir()
    (path / "SKILL.md").write_text(text)
    (path / "agents" / "openai.yaml").write_text("agent: openai\n")
    (path / "scripts" / "tool.py").write_text("print('tool')\n")
    return path


def make_project(tmp_path: Path) -> SkillProject:
    return SkillProject(
        package_name="example-agent-skill",
        import_name="example_agent_skill",
        version="1.2.3",
        skill_name="example-agent-skill",
        description="Example agent skill for installer tests.",
        bundled_skill_source=make_skill(tmp_path / "bundled-skill"),
    )


def make_repo(path: Path) -> Path:
    path.mkdir()
    (path / ".git").mkdir()
    return path


def make_skill_checkout(path: Path) -> Path:
    path.mkdir()
    (path / ".git").mkdir()
    (path / "pyproject.toml").write_text(
        '[project]\nname = "example-agent-skill"\nversion = "7.8.9"\n'
    )
    make_skill(path / "skill", text="editable skill\n")
    return path


def make_root_skill_checkout(path: Path) -> Path:
    path.mkdir()
    (path / ".git").mkdir()
    make_skill(path, text="root skill\n")
    return path


def make_skill_wheel(
    path: Path,
    project: SkillProject,
    *,
    skill_text: str = "wheel skill\n",
) -> Path:
    with zipfile.ZipFile(path, "w") as wheel:
        prefix = project.wheel_skill_prefix.as_posix()
        wheel.writestr(f"{prefix}/SKILL.md", skill_text)
        wheel.writestr(f"{prefix}/agents/openai.yaml", "agent: wheel\n")
        wheel.writestr(f"{prefix}/scripts/tool.py", "print('wheel')\n")
        wheel.writestr(f"{project.import_name}/__init__.py", "__version__ = '9.9.9'\n")
        wheel.writestr(f"{project.import_name}-1.2.3.dist-info/METADATA", "ignored\n")
    return path


def read_install_manifest(project: SkillProject, skill_dir: Path) -> dict[str, object]:
    return json.loads(manifest_path(project, skill_dir).read_text())


def write_manifest(
    project: SkillProject,
    skill_dir: Path,
    manifest: dict[str, object],
) -> None:
    manifest_path(project, skill_dir).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )


def test_installs_and_uninstalls_codex_repo_scope(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    installer = Installer(project)
    repo = make_repo(tmp_path / "repo")

    result = installer.install(["codex"], "repo", repo=repo)[0]

    skill_dir = repo / ".codex" / "skills" / project.skill_name
    assert result.status == "installed"
    assert (skill_dir / "SKILL.md").read_text() == "example skill\n"
    manifest = read_install_manifest(project, skill_dir)
    assert manifest["package"] == project.package_name
    assert manifest["skill_name"] == project.skill_name
    assert manifest["package_version"] == project.version
    assert project.manifest_relative_path.as_posix() in manifest["files"]
    assert project.marker_start in (repo / "AGENTS.md").read_text()

    removed = installer.uninstall(["codex"], "repo", repo=repo)[0]

    assert removed.status == "removed"
    assert not skill_dir.exists()
    assert not (repo / "AGENTS.md").exists()


def test_installs_and_uninstalls_claude_global_scope(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    installer = Installer(project)
    claude_home = tmp_path / "claude-home"

    installer.install(["claude"], "global", claude_home=claude_home)

    skill_dir = claude_home / "skills" / project.skill_name
    assert (skill_dir / "SKILL.md").exists()
    assert project.marker_start in (claude_home / "CLAUDE.md").read_text()

    installer.uninstall(["claude"], "global", claude_home=claude_home)

    assert not claude_home.exists()


def test_global_scope_defaults_to_agent_dirs_under_user_home(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    home = tmp_path / "home"

    Installer(project).install(["codex"], "global", home=home)

    skill_dir = home / ".codex" / "skills" / project.skill_name
    assert (skill_dir / "SKILL.md").exists()
    assert (home / ".codex" / "AGENTS.md").exists()

    Installer(project).uninstall(["codex"], "global", home=home)

    assert not (home / ".codex").exists()


def test_global_scope_supports_per_agent_home_directories(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    codex_home = tmp_path / "codex-alt"
    claude_home = tmp_path / "claude-alt"

    Installer(project).install(
        ["all"],
        "global",
        codex_home=codex_home,
        claude_home=claude_home,
    )

    assert (codex_home / "skills" / project.skill_name / "SKILL.md").exists()
    assert (claude_home / "skills" / project.skill_name / "SKILL.md").exists()
    assert (codex_home / "AGENTS.md").exists()
    assert (claude_home / "CLAUDE.md").exists()

    Installer(project).uninstall(
        ["all"],
        "global",
        codex_home=codex_home,
        claude_home=claude_home,
    )

    assert not codex_home.exists()
    assert not claude_home.exists()


def test_editable_install_links_local_checkout_skill_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    checkout = make_skill_checkout(tmp_path / "checkout")
    repo = make_repo(tmp_path / "repo")
    monkeypatch.chdir(checkout)

    result = Installer(project).install(["codex"], "repo", repo=repo, editable=True)[0]

    skill_dir = repo / ".codex" / "skills" / project.skill_name
    assert result.install_mode == "editable"
    assert skill_dir.is_symlink()
    assert skill_dir.resolve() == checkout / "skill"
    assert (skill_dir / "SKILL.md").read_text() == "editable skill\n"
    manifest = read_install_manifest(project, skill_dir)
    assert manifest["manifest_path"] == str(
        repo / ".codex" / "skills" / project.sidecar_manifest_name
    )
    assert manifest["source_dir"] == str(checkout / "skill")


def test_editable_install_requires_local_checkout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)

    with pytest.raises(InstallerError, match="requires running from a git or sl checkout"):
        Installer(project).install(["codex"], "repo", repo=repo, editable=True)


def test_pypi_wheel_install_extracts_only_project_skill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    wheel = make_skill_wheel(tmp_path / "example.whl", project, skill_text="pypi\n")

    monkeypatch.setattr(
        "agent_skill_installer.installer.download_pypi_wheel",
        lambda _project, _version, _download_dir: wheel,
    )

    result = Installer(project).install(
        ["codex"],
        "repo",
        repo=repo,
        pypi_version="2.0.0",
    )[0]

    skill_dir = repo / ".codex" / "skills" / project.skill_name
    assert result.install_mode == "pypi"
    assert result.version == "2.0.0"
    assert (skill_dir / "SKILL.md").read_text() == "pypi\n"
    assert not (skill_dir / project.import_name / "__init__.py").exists()


def test_copy_pypi_wheel_skill_extracts_only_bundled_skill(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    wheel = make_skill_wheel(tmp_path / "example.whl", project)
    skill_dir = tmp_path / "skill"

    copied = copy_pypi_wheel_skill(project, wheel, skill_dir)

    assert copied == [
        "SKILL.md",
        "agents/openai.yaml",
        "scripts/tool.py",
    ]
    assert (skill_dir / "SKILL.md").read_text() == "wheel skill\n"
    assert (skill_dir / "agents" / "openai.yaml").read_text() == "agent: wheel\n"
    assert not (skill_dir / project.import_name / "__init__.py").exists()
    assert not (skill_dir / f"{project.import_name}-1.2.3.dist-info").exists()


def test_copy_pypi_wheel_skill_rejects_missing_skill(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    wheel = tmp_path / "empty.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"{project.import_name}/__init__.py", "")

    with pytest.raises(InstallerError, match="did not contain"):
        copy_pypi_wheel_skill(project, wheel, tmp_path / "skill")


def test_install_rejects_editable_and_pypi_version_together(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")

    with pytest.raises(InstallerError, match="cannot be combined"):
        Installer(project).install(
            ["codex"],
            "repo",
            repo=repo,
            editable=True,
            pypi_version="1.2.3",
        )


def test_install_source_metadata_requires_vcs_repo(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    checkout = make_skill_checkout(tmp_path / "checkout")
    (checkout / ".git").rmdir()
    monkeypatch.chdir(checkout)
    monkeypatch.setattr(
        "agent_skill_installer.installer.find_repo_root",
        lambda _start=None: None,
    )

    metadata = install_source_metadata(project)

    assert metadata.editable_available is False
    assert metadata.local_version is None
    assert metadata.source_dir is None


def test_install_source_metadata_accepts_generic_skill_checkout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    checkout = make_skill_checkout(tmp_path / "checkout")
    monkeypatch.chdir(checkout / "skill" / "scripts")

    metadata = install_source_metadata(project)

    assert metadata.editable_available is True
    assert metadata.local_version == "7.8.9"
    assert metadata.source_dir == checkout / "skill"
    assert metadata.repo_root == checkout
    assert metadata.vcs == "git"


def test_install_source_metadata_accepts_root_skill_checkout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    checkout = make_root_skill_checkout(tmp_path / "checkout")
    monkeypatch.chdir(checkout / "scripts")

    metadata = install_source_metadata(project)

    assert metadata.editable_available is True
    assert metadata.local_version == project.version
    assert metadata.source_dir == checkout
    assert metadata.repo_root == checkout
    assert metadata.vcs == "git"


def test_published_pypi_versions_filters_wheel_releases(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    monkeypatch.setattr(
        "agent_skill_installer.installer.fetch_json_url",
        lambda _url, **_kwargs: {
            "releases": {
                "1.0.0": [{"packagetype": "bdist_wheel"}],
                "1.10.0": [{"packagetype": "bdist_wheel"}],
                "2.0.0": [{"packagetype": "sdist"}],
            }
        },
    )

    assert published_pypi_versions(project, limit=3) == ["1.10.0", "1.0.0"]


def test_fetch_json_url_uses_metadata_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok": true}'

    def fake_urlopen(url, *, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(
        "agent_skill_installer.installer.urllib.request.urlopen",
        fake_urlopen,
    )

    assert fetch_json_url("https://example.invalid/project.json") == {"ok": True}
    assert captured["timeout"] == PYPI_METADATA_TIMEOUT_SECONDS


def test_reinstall_reports_upgrade_from_previous_manifest_version(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    installer = Installer(project)
    installer.install(["codex"], "repo", repo=repo)
    skill_dir = repo / ".codex" / "skills" / project.skill_name
    manifest = read_install_manifest(project, skill_dir)
    manifest["package_version"] = "0.0.0"
    write_manifest(project, skill_dir, manifest)

    result = installer.install(["codex"], "repo", repo=repo)[0]

    assert result.version == project.version
    assert result.previous_version == "0.0.0"
    assert result.version_change == "upgrade"
    assert "upgraded from 0.0.0" in format_status_line(result, color=False)


def test_reinstall_reports_downgrade_from_previous_manifest_version(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    installer = Installer(project)
    installer.install(["codex"], "repo", repo=repo)
    skill_dir = repo / ".codex" / "skills" / project.skill_name
    manifest = read_install_manifest(project, skill_dir)
    manifest["package_version"] = "9.0.0"
    write_manifest(project, skill_dir, manifest)

    result = installer.install(["codex"], "repo", repo=repo)[0]

    assert result.version == project.version
    assert result.previous_version == "9.0.0"
    assert result.version_change == "downgrade"
    assert "downgraded from 9.0.0" in format_status_line(result, color=False)


def test_uninstall_preserves_existing_hook_content(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    hook = repo / "AGENTS.md"
    hook.write_text("# Existing Instructions\n\nKeep this.\n")

    Installer(project).install(["codex"], "repo", repo=repo)
    Installer(project).uninstall(["codex"], "repo", repo=repo)

    assert hook.read_text() == "# Existing Instructions\n\nKeep this.\n"


def test_reinstall_replaces_existing_discoverability_block(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    hook = repo / "AGENTS.md"
    hook.write_text(f"{project.marker_start}\nold\n{project.marker_end}\n")

    Installer(project).install(["codex"], "repo", repo=repo)

    hook_text = hook.read_text()
    assert "old" not in hook_text
    assert hook_text.count(project.marker_start) == 1


def test_install_refuses_to_replace_unowned_skill_dir(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    skill_dir = repo / ".codex" / "skills" / project.skill_name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("manual install\n")

    with pytest.raises(InstallerError, match="unowned skill directory"):
        Installer(project).install(["codex"], "repo", repo=repo)


def test_manifest_package_aliases_are_project_specific(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    installer = Installer(project)
    installer.install(["codex"], "repo", repo=repo)
    skill_dir = repo / ".codex" / "skills" / project.skill_name
    manifest = read_raw_manifest(project, skill_dir)
    assert manifest is not None
    manifest["package"] = "old-example-skill"
    write_manifest(project, skill_dir, manifest)

    with pytest.raises(InstallerError, match="not for example-agent-skill"):
        read_raw_manifest(project, skill_dir)

    compatible = SkillProject(
        package_name=project.package_name,
        import_name=project.import_name,
        version=project.version,
        skill_name=project.skill_name,
        description=project.description,
        bundled_skill_source=project.bundled_skill_source,
        manifest_package_aliases=frozenset({"old-example-skill"}),
    )
    assert read_raw_manifest(compatible, skill_dir) is not None


def test_project_can_override_discoverability_marker_slug(tmp_path: Path) -> None:
    project = SkillProject(
        package_name="example-agent-skill",
        import_name="example_agent_skill",
        version="1.2.3",
        skill_name="example-agent-skill",
        description="Example agent skill for installer tests.",
        bundled_skill_source=make_skill(tmp_path / "bundled-skill"),
        marker_slug_override="EXAMPLE",
    )
    repo = make_repo(tmp_path / "repo")

    Installer(project).install(["codex"], "repo", repo=repo)

    hook_text = (repo / "AGENTS.md").read_text()
    assert "<!-- EXAMPLE-DISCOVERABILITY-START -->" in hook_text
    assert "<!-- EXAMPLE-DISCOVERABILITY-END -->" in hook_text


def test_project_uses_installer_config_instructions_when_present(tmp_path: Path) -> None:
    skill = make_skill(tmp_path / "bundled-skill")
    (skill / "agent-skill-installer.yaml").write_text(
        """
installer:
  agents:
    codex:
      instructions:
        title: Configured Instructions
        body: Use this configured Codex text.
"""
    )
    project = SkillProject(
        package_name="example-agent-skill",
        import_name="example_agent_skill",
        version="1.2.3",
        skill_name="example-agent-skill",
        description="Fallback description.",
        bundled_skill_source=skill,
    )
    repo = make_repo(tmp_path / "repo")

    Installer(project).install(["codex"], "repo", repo=repo)

    hook_text = (repo / "AGENTS.md").read_text()
    assert "Configured Instructions" in hook_text
    assert "Use this configured Codex text." in hook_text
    assert "Fallback description." not in hook_text


def test_cli_no_ui_install_and_uninstall(tmp_path: Path, capsys) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")

    install_code = main(
        [
            "--no-ui",
            "install",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--repo",
            str(repo),
        ],
        project=project,
    )
    output = capsys.readouterr()

    assert install_code == 0
    assert "installed: codex/repo version 1.2.3" in output.out

    uninstall_code = main(
        [
            "--no-ui",
            "uninstall",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--repo",
            str(repo),
        ],
        project=project,
    )
    output = capsys.readouterr()

    assert uninstall_code == 0
    assert "removed: codex/repo version 1.2.3" in output.out


def test_cli_no_ui_verbose_lists_paths(tmp_path: Path, capsys) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")

    exit_code = main(
        [
            "--no-ui",
            "install",
            "--verbose",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--repo",
            str(repo),
        ],
        project=project,
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert f"  skill: {repo / '.codex' / 'skills' / project.skill_name}" in output.out
    assert f"  hook:  {repo / 'AGENTS.md'}" in output.out


def test_cli_no_ui_uses_per_agent_home_directories(
    tmp_path: Path,
    capsys,
) -> None:
    project = make_project(tmp_path)
    codex_home = tmp_path / "codex-cli"
    claude_home = tmp_path / "claude-cli"

    exit_code = main(
        [
            "--no-ui",
            "install",
            "--agent",
            "all",
            "--scope",
            "global",
            "--codex-home",
            str(codex_home),
            "--claude-home",
            str(claude_home),
        ],
        project=project,
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert "installed: codex/global version 1.2.3" in output.out
    assert "installed: claude/global version 1.2.3" in output.out
    assert (codex_home / "skills" / project.skill_name / "SKILL.md").exists()
    assert (claude_home / "skills" / project.skill_name / "SKILL.md").exists()


def test_cli_no_ui_accepts_comma_separated_agents(
    tmp_path: Path,
    capsys,
) -> None:
    project = make_project(tmp_path)
    codex_home = tmp_path / "codex-cli"
    claude_home = tmp_path / "claude-cli"

    exit_code = main(
        [
            "--no-ui",
            "install",
            "--agent",
            "codex,claude",
            "--scope",
            "global",
            "--codex-home",
            str(codex_home),
            "--claude-home",
            str(claude_home),
        ],
        project=project,
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert "installed: codex/global version 1.2.3" in output.out
    assert "installed: claude/global version 1.2.3" in output.out


def test_cli_no_ui_editable_install(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    project = make_project(tmp_path)
    checkout = make_skill_checkout(tmp_path / "checkout")
    repo = make_repo(tmp_path / "repo")
    monkeypatch.chdir(checkout)

    exit_code = main(
        [
            "--no-ui",
            "install",
            "--editable",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--repo",
            str(repo),
        ],
        project=project,
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert "installed: codex/repo version 1.2.3 (editable)" in output.out
    assert (repo / ".codex" / "skills" / project.skill_name).is_symlink()


def test_cli_no_ui_pypi_version_install(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    wheel = make_skill_wheel(tmp_path / "example.whl", project)
    monkeypatch.setattr(
        "agent_skill_installer.installer.download_pypi_wheel",
        lambda _project, _version, _download_dir: wheel,
    )

    exit_code = main(
        [
            "--no-ui",
            "install",
            "--pypi-version",
            "2.0.0",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--repo",
            str(repo),
        ],
        project=project,
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert "Installing from PyPI: example-agent-skill==2.0.0" in output.err
    assert "installed: codex/repo version 2.0.0 (PyPI wheel)" in output.out


def test_cli_no_ui_pypi_version_download_error_names_attempted_package(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")

    def fail_download(_project: SkillProject, _version: str, _download_dir: Path) -> Path:
        raise InstallerError("metadata not found")

    monkeypatch.setattr("agent_skill_installer.installer.download_pypi_wheel", fail_download)

    exit_code = main(
        [
            "--no-ui",
            "install",
            "--pypi-version",
            "9.9.9",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--repo",
            str(repo),
        ],
        project=project,
    )
    output = capsys.readouterr()

    assert exit_code == 1
    assert "Installing from PyPI: example-agent-skill==9.9.9" in output.err
    assert "example-agent-skill: error: metadata not found" in output.err


def test_cli_no_ui_rejects_conflicting_install_sources(
    tmp_path: Path,
    capsys,
) -> None:
    project = make_project(tmp_path)

    exit_code = main(
        [
            "--no-ui",
            "install",
            "--editable",
            "--pypi-version",
            "2.0.0",
            "--agent",
            "codex",
            "--scope",
            "repo",
        ],
        project=project,
    )
    output = capsys.readouterr()

    assert exit_code == 2
    assert "--editable and --pypi-version cannot be used together" in output.err


def test_cli_no_ui_command_preview_uses_project_package_name(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    assert build_no_ui_command(
        project,
        "install",
        agent="all",
        scope="global",
        codex_home=tmp_path / "codex",
        claude_home=tmp_path / "claude",
        editable=True,
    ) == (
        "example-agent-skill --no-ui install --editable --agent all --scope global "
        f"--codex-home {shlex.quote(str(tmp_path / 'codex'))} "
        f"--claude-home {shlex.quote(str(tmp_path / 'claude'))}"
    )


def test_cli_ui_keyboard_interrupt_exits_cleanly(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    project = make_project(tmp_path)
    monkeypatch.setattr("agent_skill_installer.cli.running_on_tty", lambda: True)
    monkeypatch.setattr(
        "agent_skill_installer.cli.complete_with_ui",
        lambda _project, _args: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    exit_code = main([], project=project)
    output = capsys.readouterr()

    assert exit_code == 130
    assert output.err == "\nCancelled.\n"
    assert "Traceback" not in output.err
    assert "Traceback" not in output.out


def test_textual_command_radio_arrow_navigation() -> None:
    if importlib.util.find_spec("textual") is None:
        pytest.skip("Textual is not installed")

    from textual.widgets import Button, RadioSet

    app = make_textual_select_app(
        "What would you like to do?",
        [
            {"name": "Install", "value": "install"},
            {"name": "Uninstall", "value": "uninstall"},
        ],
        command_preview="example-agent-skill --no-ui install --agent all --scope repo",
    )

    async def run_scenario() -> None:
        async with app.run_test() as pilot:
            choice = app.query_one("#choice", RadioSet)
            copy_button = app.query_one("#copy-command", Button)
            assert copy_button.can_focus is False
            await pilot.press("up")
            assert choice.has_focus
            assert not copy_button.has_focus
            assert choice.pressed_index == 0
            await pilot.press("down")
            assert choice.has_focus
            assert choice.pressed_index == 0
            await pilot.press("down")
            assert app.query_one("#continue", Button).has_focus
            await pilot.press("up")
            assert choice.has_focus
            assert choice.pressed_index == 0
            await pilot.press("enter")

    asyncio.run(run_scenario())

    assert app.return_value == "uninstall"


def test_textual_checkbox_all_mode_and_empty_selection() -> None:
    if importlib.util.find_spec("textual") is None:
        pytest.skip("Textual is not installed")

    from textual.widgets import Button, SelectionList, Static

    def preview(selected: object) -> str | None:
        selected_values = list(selected) if isinstance(selected, list) else []
        if not selected_values:
            return None
        return "example-agent-skill --no-ui uninstall --agent all --scope repo"

    app = make_textual_checkbox_app(
        "Select target agents",
        target_choices(),
        command_preview_builder=preview,
    )

    async def run_scenario() -> None:
        async with app.run_test() as pilot:
            choices = app.query_one("#choices", SelectionList)
            copy_button = app.query_one("#copy-command", Button)
            command = app.query_one("#command-preview-command", Static)
            assert copy_button.disabled is True
            assert str(command.content) == "Choose at least one target."

            app.action_accept_selected_targets()
            assert str(app.query_one("#error", Static).content) == (
                "Choose at least one target."
            )
            assert app.return_value is None

            await pilot.press("space")
            await pilot.pause()
            assert set(choices.selected) == {"all"}
            assert choices.get_option("codex").disabled is False
            assert choices.get_option("claude").disabled is False
            assert copy_button.disabled is False

            await pilot.press("space")
            await pilot.pause()
            assert copy_button.disabled is True
            assert str(command.content) == "Choose at least one target."

    asyncio.run(run_scenario())


def test_textual_pypi_version_dropdown_updates_input_and_preview() -> None:
    if importlib.util.find_spec("textual") is None:
        pytest.skip("Textual is not installed")

    from textual.widgets import Input, Select, Static

    def preview(version: object) -> str:
        return (
            "example-agent-skill --no-ui install "
            f"--pypi-version {version} --agent all --scope repo"
        )

    app = make_textual_version_app(
        "PyPI package version",
        "2.0.0",
        [
            {"name": "2.0.0 (latest)", "value": "2.0.0"},
            {"name": "1.0.0", "value": "1.0.0"},
        ],
        command_preview_builder=preview,
    )

    async def run_scenario() -> None:
        async with app.run_test() as pilot:
            select = app.query_one("#version-select", Select)
            select.value = "1.0.0"
            await pilot.pause()

            assert app.query_one("#version", Input).value == "1.0.0"
            command = app.query_one("#command-preview-command", Static)
            assert str(command.content) == preview("1.0.0")

            app.action_accept_version()

    asyncio.run(run_scenario())

    assert app.return_value == "1.0.0"


class ScriptedPrompter:
    def __init__(self, *answers) -> None:
        self.answers = iter(answers)
        self.calls: list[tuple[str, str]] = []
        self.previews: list[str | None] = []
        self.summaries: list[str | None] = []
        self.checkbox_defaults: list[list[str] | None] = []
        self.submit_labels: list[str] = []

    def select(
        self,
        message,
        choices,
        *,
        command_preview=None,
        command_preview_builder=None,
        summary=None,
        summary_builder=None,
        submit_label="Continue",
    ):
        self.calls.append(("select", message))
        self.submit_labels.append(submit_label)
        answer = next(self.answers)
        if isinstance(answer, BaseException):
            raise answer
        self.previews.append(
            command_preview_builder(answer)
            if command_preview_builder is not None
            else command_preview
        )
        self.summaries.append(
            summary_builder(answer)
            if summary_builder is not None
            else summary
        )
        return answer

    def checkbox(
        self,
        message,
        choices,
        *,
        command_preview=None,
        command_preview_builder=None,
        summary=None,
        default_values=None,
        submit_label="Continue",
    ):
        self.calls.append(("checkbox", message))
        self.submit_labels.append(submit_label)
        self.checkbox_defaults.append(
            list(default_values) if default_values is not None else None
        )
        answer = next(self.answers)
        if isinstance(answer, BaseException):
            raise answer
        self.previews.append(
            command_preview_builder(answer)
            if command_preview_builder is not None
            else command_preview
        )
        self.summaries.append(summary)
        return answer

    def path(
        self,
        message,
        default,
        *,
        command_preview=None,
        command_preview_builder=None,
        summary=None,
        submit_label="Continue",
    ):
        self.calls.append(("path", message))
        self.submit_labels.append(submit_label)
        answer = next(self.answers)
        if isinstance(answer, BaseException):
            raise answer
        path = answer if isinstance(answer, Path) else Path(answer)
        self.previews.append(
            command_preview_builder(path)
            if command_preview_builder is not None
            else command_preview
        )
        self.summaries.append(summary)
        return path

    def text(
        self,
        message,
        default,
        *,
        command_preview=None,
        command_preview_builder=None,
        submit_label="Continue",
    ):
        self.calls.append(("text", message))
        self.submit_labels.append(submit_label)
        answer = next(self.answers)
        if isinstance(answer, BaseException):
            raise answer
        value = str(answer).strip() or default
        self.previews.append(
            command_preview_builder(value)
            if command_preview_builder is not None
            else command_preview
        )
        return value

    def version(
        self,
        message,
        default,
        choices,
        *,
        command_preview=None,
        command_preview_builder=None,
        submit_label="Continue",
    ):
        self.calls.append(("version", message))
        self.submit_labels.append(submit_label)
        answer = next(self.answers)
        if isinstance(answer, BaseException):
            raise answer
        value = str(answer).strip() or default
        self.previews.append(
            command_preview_builder(value)
            if command_preview_builder is not None
            else command_preview
        )
        return value


def test_installation_option_choices_offer_install_locations(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    repo = tmp_path / "my-repo"
    home = tmp_path / "home"

    choices = installation_option_choices(
        project,
        ["codex", "claude"],
        repo_available=True,
        repo=repo,
        home=home,
    )

    assert choices == [
        {
            "name": "Global",
            "description": "\n".join(
                [
                    "Install in agent home directory",
                    str(home / ".codex"),
                    str(home / ".claude"),
                ]
            ),
            "value": "global",
            "kind": "scope",
        },
        {
            "name": "Repository install",
            "description": "\n".join(["Install in this repository", str(repo)]),
            "value": "repo",
            "kind": "scope",
        },
    ]


def test_installation_summary_text_reports_repo_and_global_versions(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    home = tmp_path / "home"
    Installer(project).install(["codex"], "repo", repo=repo, home=home)
    Installer(project).install(["claude"], "global", home=home)

    summary = installation_summary_text(project, repo=repo, home=home)

    assert summary == "\n".join(
        [
            "Codex in repo: version 1.2.3",
            "Claude Code in home dir: version 1.2.3",
        ]
    )


def test_command_preview_classes_follow_exact_subcommand() -> None:
    assert (
        command_preview_classes(
            "example-agent-skill --no-ui install --agent all --scope repo"
        )
        == "install-preview"
    )
    assert (
        command_preview_classes(
            "example-agent-skill --no-ui uninstall --agent all --scope repo"
        )
        == "uninstall-preview"
    )


def test_update_command_preview_display_changes_mode_class() -> None:
    class FakeCommand:
        text = ""

        def update(self, text: str) -> None:
            self.text = text

    class FakePanel:
        def __init__(self) -> None:
            self.classes = {"install-preview"}

        def add_class(self, class_name: str) -> None:
            self.classes.add(class_name)

        def remove_class(self, class_name: str) -> None:
            self.classes.discard(class_name)

    class FakeCopyButton:
        disabled = False

    class FakeApp:
        def __init__(self) -> None:
            self.command = FakeCommand()
            self.panel = FakePanel()
            self.copy_button = FakeCopyButton()

        def query_one(self, selector, _widget_type):
            assert selector == "#command-preview-command"
            return self.command

        def query(self, selector):
            if selector == "#command-preview":
                return [self.panel]
            if selector == "#copy-command":
                return [self.copy_button]
            raise AssertionError(selector)

    app = FakeApp()

    update_command_preview_display(
        app,
        "example-agent-skill --no-ui uninstall --agent all --scope repo",
        object,
    )

    assert app.command.text == (
        "example-agent-skill --no-ui uninstall --agent all --scope repo"
    )
    assert app.panel.classes == {"uninstall-preview"}


def test_install_source_choices_offer_bundled_pypi_and_editable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    metadata = Namespace(
        packaged_version="1.2.3",
        editable_available=True,
        local_version="1.2.4",
        vcs="git",
        commit="abc1234",
        dirty=True,
    )
    monkeypatch.setattr(
        "agent_skill_installer.cli.install_source_metadata",
        lambda _project: metadata,
    )

    assert install_source_choices(project)[0] == {
        "name": "Editable local checkout (version 1.2.4, git abc1234, dirty)",
        "value": "editable",
    }


def test_pypi_version_choices_include_latest_ten_published_versions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    versions = [f"1.{index}.0" for index in range(12)]
    monkeypatch.setattr(
        "agent_skill_installer.cli.published_pypi_versions",
        lambda _project, *, limit=10: versions[:limit],
    )

    assert pypi_version_choices(project, bundled_version="1.0.0") == [
        {
            "name": "1.0.0 (latest, same as bundled)",
            "value": "1.0.0",
        },
        *[
            {"name": f"1.{index}.0", "value": f"1.{index}.0"}
            for index in range(1, 10)
        ],
    ]


def test_build_no_ui_command_for_mixed_scope_targets(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    args = Namespace(
        command="install",
        editable=False,
        pypi_version=None,
        force=False,
        agent=None,
        scope=None,
        repo=None,
        codex_home=None,
        claude_home=None,
    )
    repo = tmp_path / "repo"
    codex_home = tmp_path / "codex-home"

    command = build_no_ui_command(
        project,
        args,
        targets=[("codex", "global"), ("codex", "repo")],
        repo=repo,
        codex_home=codex_home,
    )

    assert command == (
        "example-agent-skill --no-ui install --agent codex --scope global "
        f"--codex-home {codex_home}\n"
        f"example-agent-skill --no-ui install --agent codex --scope repo --repo {repo}"
    )


def test_complete_with_ui_selects_specific_targets(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    args = Namespace(
        command=None,
        agent=None,
        scope=None,
        repo=None,
        codex_home=None,
        claude_home=None,
        editable=False,
    )
    prompter = ScriptedPrompter("install", ["codex"], "global")

    complete_with_ui(project, args, prompter)

    assert args.command == "install"
    assert args.targets == [("codex", "global")]
    assert prompter.calls == [
        ("select", "What would you like to do with example-agent-skill?"),
        ("checkbox", "Select agents for example-agent-skill"),
        ("select", "Install location for example-agent-skill"),
    ]
    assert prompter.previews == [
        "example-agent-skill --no-ui install --agent all --scope global",
        "example-agent-skill --no-ui install --agent codex --scope global",
        "example-agent-skill --no-ui install --agent codex --scope global",
    ]
    assert prompter.checkbox_defaults == [["all"]]
    assert prompter.submit_labels == ["Continue", "Continue", "Install"]


def test_complete_with_ui_selects_pypi_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    monkeypatch.setattr(
        "agent_skill_installer.cli.install_source_choices",
        lambda _project: [
            {
                "name": "Bundled skill copy (version 1.2.3, no network)",
                "value": "copy",
            },
            {
                "name": (
                    "PyPI wheel version "
                    "(requires network; choose published or manual version)"
                ),
                "value": "pypi",
            },
        ],
    )
    monkeypatch.setattr(
        "agent_skill_installer.cli.pypi_version_choices",
        lambda _project: [
            {"name": "2.0.0", "value": "2.0.0"},
            {"name": "1.0.0", "value": "1.0.0"},
        ],
    )
    args = Namespace(
        command=None,
        agent=None,
        scope=None,
        repo=None,
        codex_home=None,
        claude_home=None,
    )
    repo = tmp_path / "repo"
    monkeypatch.setattr("agent_skill_installer.cli.find_ui_repo_root", lambda args: repo)
    prompter = ScriptedPrompter("install", "pypi", "2.0.0", ["codex"], "repo")

    complete_with_ui(project, args, prompter)

    assert args.command == "install"
    assert args.editable is False
    assert args.pypi_version == "2.0.0"
    assert args.targets == [("codex", "repo")]
    assert args.repo == repo
    assert prompter.previews == [
        "example-agent-skill --no-ui install --agent all --scope global",
        "example-agent-skill --no-ui install --pypi-version 1.2.3 "
        "--agent all --scope global",
        "example-agent-skill --no-ui install --pypi-version 2.0.0 "
        "--agent all --scope global",
        "example-agent-skill --no-ui install --pypi-version 2.0.0 "
        "--agent codex --scope global",
        "example-agent-skill --no-ui install --pypi-version 2.0.0 "
        f"--agent codex --scope repo --repo {repo}",
    ]


def test_complete_with_ui_escape_goes_back_one_screen(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    args = Namespace(
        command=None,
        agent=None,
        scope=None,
        repo=None,
        codex_home=None,
        claude_home=None,
        editable=False,
    )
    monkeypatch.setattr(
        "agent_skill_installer.cli.inspect_installations",
        lambda _project, **_: [
            InstallationStatus(
                agent=agent,
                scope=scope,
                skill_dir=tmp_path / agent / scope,
                status=(
                    "installed"
                    if (agent, scope) == ("codex", "global")
                    else "not-installed"
                ),
                version=project.version if (agent, scope) == ("codex", "global") else None,
            )
            for agent in ("codex", "claude")
            for scope in ("repo", "global")
        ],
    )
    prompter = ScriptedPrompter(
        "install",
        BackRequested(),
        "uninstall",
        ["codex"],
        ["codex:global"],
    )

    complete_with_ui(project, args, prompter)

    assert args.command == "uninstall"
    assert args.targets == [("codex", "global")]
    assert prompter.calls == [
        ("select", "What would you like to do with example-agent-skill?"),
        ("checkbox", "Select agents for example-agent-skill"),
        ("select", "What would you like to do with example-agent-skill?"),
        ("checkbox", "Select agents for example-agent-skill"),
        ("checkbox", "Select example-agent-skill installations"),
    ]


def test_package_metadata_is_generic() -> None:
    pyproject = Path(__file__).resolve().parents[2].joinpath("pyproject.toml").read_text()

    assert 'name = "agent-skill-installer"' in pyproject
    assert f'version = "{__version__}"' in pyproject
    assert "[project.scripts]" not in pyproject


def test_python_module_entry_point_explains_library_usage() -> None:
    env = os.environ.copy()
    src_dir = Path(__file__).resolve().parents[2] / "src"
    env["PYTHONPATH"] = str(src_dir)
    completed = subprocess.run(
        [sys.executable, "-m", "agent_skill_installer"],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert completed.returncode == 2
    assert "agent-skill-installer is a library" in completed.stderr
