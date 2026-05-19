from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from skill_installer.cli import build_no_ui_command, format_status_line, main
from skill_installer.installer import (
    PYPI_METADATA_TIMEOUT_SECONDS,
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
        "skill_installer.installer.download_pypi_wheel",
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
        "skill_installer.installer.find_repo_root",
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
        "skill_installer.installer.fetch_json_url",
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
        "skill_installer.installer.urllib.request.urlopen",
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
        "skill_installer.installer.download_pypi_wheel",
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

    monkeypatch.setattr("skill_installer.installer.download_pypi_wheel", fail_download)

    with pytest.raises(SystemExit) as error:
        main(
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

    assert error.value.code == 1
    assert "Installing from PyPI: example-agent-skill==9.9.9" in output.err
    assert "example-agent-skill: error: metadata not found" in output.err


def test_cli_no_ui_rejects_conflicting_install_sources(
    tmp_path: Path,
    capsys,
) -> None:
    project = make_project(tmp_path)

    with pytest.raises(SystemExit) as error:
        main(
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

    assert error.value.code == 2
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


def test_code_does_not_carry_awd_specific_constants() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "skill_installer"
    source = "\n".join(path.read_text() for path in root.glob("*.py"))

    assert "agent-workflow-dsl" not in source
    assert "agent_workflow_dsl" not in source
    assert "awd-installer" not in source
    assert "AWD" not in source


def test_package_metadata_is_generic() -> None:
    pyproject = Path(__file__).resolve().parents[2].joinpath("pyproject.toml").read_text()

    assert 'name = "skill-installer"' in pyproject
    assert "agent-workflow-dsl" not in pyproject
    assert "agent_workflow_dsl" not in pyproject
    assert "[project.scripts]" not in pyproject


def test_python_module_entry_point_explains_library_usage() -> None:
    env = os.environ.copy()
    src_dir = Path(__file__).resolve().parents[2] / "src"
    env["PYTHONPATH"] = str(src_dir)
    completed = subprocess.run(
        [sys.executable, "-m", "skill_installer"],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert completed.returncode == 2
    assert "skill-installer is a library" in completed.stderr
