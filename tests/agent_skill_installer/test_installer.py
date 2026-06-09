from __future__ import annotations

import json
import asyncio
import hashlib
import importlib.util
import os
import shutil
import shlex
import subprocess
import sys
import zipfile
from argparse import Namespace
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

from agent_skill_installer import __version__
from agent_skill_installer.config import CONFIG_FILE_NAME, load_installer_config_text
from agent_skill_installer.__main__ import (
    build_parser as build_generic_parser,
    complete_with_ui as complete_generic_with_ui,
    load_recent_github_urls,
    install_source_choices as generic_install_source_choices,
    load_recent_pypi_packages,
    main as generic_main,
    recent_installations_path,
    remember_recent_github_url,
    remember_recent_pypi_package,
    run_install as run_generic_install,
    run_uninstall as run_generic_uninstall,
)
from agent_skill_installer.cli import (
    BackRequested,
    DEFAULT_EMPTY_COMMAND_PREVIEW_MESSAGE,
    PROMPT_BACK,
    build_parser,
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
    target_choices,
    update_command_preview_display,
)
from agent_skill_installer.installer import (
    PYPI_METADATA_TIMEOUT_SECONDS,
    InstallationStatus,
    Installer,
    InstallerError,
    SkillProject,
    copy_github_archive_skill,
    copy_pypi_wheel_skill,
    fetch_json_url,
    install_source_metadata,
    manifest_path,
    parse_github_url,
    published_pypi_versions,
    read_manifest as read_raw_manifest,
    run_pip_wheel,
)


def assert_posix_mode(path: Path, mode: int) -> None:
    if os.name == "nt":
        return
    assert path.stat().st_mode & 0o777 == mode


def make_skill(path: Path, text: str = "example skill\n") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "agents").mkdir()
    (path / "scripts").mkdir()
    (path / "SKILL.md").write_text(text)
    (path / "agents" / "openai.yaml").write_text("agent: openai\n")
    (path / "scripts" / "tool.py").write_text("print('tool')\n")
    return path


def invalid_skill_frontmatter() -> str:
    return (
        "---\n"
        "name: broken-skill\n"
        "description: Follow `(fix -> tests) *3 until pass: tests`.\n"
        "---\n\n"
        "# Broken Skill\n"
    )


def make_project(tmp_path: Path) -> SkillProject:
    return SkillProject(
        package_name="example-agent-skill",
        import_name="example_agent_skill",
        version="1.2.3",
        skill_name="example-agent-skill",
        description="Example agent skill for installer tests.",
        bundled_skill_path="skill",
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


def make_skill_collection(path: Path) -> Path:
    path.mkdir()
    make_skill(
        path / "skill-one",
        text="---\nname: skill-one\ndescription: First skill.\n---\n\none\n",
    )
    make_skill(
        path / "skill-two",
        text="---\nname: skill-two\ndescription: Second skill.\n---\n\ntwo\n",
    )
    return path


def make_skill_wheel(
    path: Path,
    project: SkillProject,
    *,
    skill_text: str = "wheel skill\n",
    config_text: str | None = None,
    extra_skill_files: dict[str, str] | None = None,
) -> Path:
    with zipfile.ZipFile(path, "w") as wheel:
        bundled_skill_path = project.bundled_skill_path or "skill"
        prefix = PurePosixPath(project.import_name, bundled_skill_path).as_posix()
        wheel.writestr(f"{prefix}/SKILL.md", skill_text)
        wheel.writestr(f"{prefix}/agents/openai.yaml", "agent: wheel\n")
        wheel.writestr(f"{prefix}/scripts/tool.py", "print('wheel')\n")
        for relative_path, data in (extra_skill_files or {}).items():
            wheel.writestr(f"{prefix}/{relative_path}", data)
        if config_text is not None:
            wheel.writestr(f"{prefix}/agent-skill-installer.yaml", config_text)
        wheel.writestr(f"{project.import_name}/__init__.py", "__version__ = '9.9.9'\n")
        wheel.writestr(
            f"{project.import_name}-{project.version}.dist-info/METADATA",
            "Metadata-Version: 2.1\n"
            f"Name: {project.package_name}\n"
            f"Version: {project.version}\n",
        )
    return path


def patch_generic_pypi_build(
    monkeypatch,
    wheel: Path,
    version: str,
    requests: list[str] | None = None,
) -> None:
    def fake_build_pypi_wheel(
        *,
        package: str,
        wheel_dir: Path,
    ) -> tuple[Path, str]:
        if requests is not None:
            requests.append(package)
        target = wheel_dir / wheel.name
        shutil.copy2(wheel, target)
        return target, version

    monkeypatch.setattr(
        "agent_skill_installer.__main__.build_pypi_wheel",
        fake_build_pypi_wheel,
    )


def make_external_wheel(path: Path, *, data: str = "#!/bin/sh\n") -> Path:
    with zipfile.ZipFile(path, "w") as wheel:
        write_zip_file(
            wheel,
            "arbiter_client/bin/arbiter",
            data,
            mode=0o755,
        )
        wheel.writestr("arbiter_client/__init__.py", "")
        wheel.writestr(
            "arbiter_client-2.0.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: arbiter-client\nVersion: 2.0.0\n",
        )
    return path


def write_zip_file(
    archive: zipfile.ZipFile,
    filename: str,
    data: str,
    *,
    mode: int | None = None,
) -> None:
    if mode is None:
        archive.writestr(filename, data)
        return
    info = zipfile.ZipInfo(filename)
    info.external_attr = mode << 16
    archive.writestr(info, data)


def make_wheel_skill_collection(
    path: Path,
    *,
    skills: dict[str, str],
    package_name: str = "example-agent-skill",
    import_name: str = "example_agent_skill",
    version: str = "1.2.3",
) -> Path:
    with zipfile.ZipFile(path, "w") as wheel:
        for skill_path, skill_text in skills.items():
            prefix = f"{import_name}/{skill_path}".rstrip("/")
            wheel.writestr(f"{prefix}/SKILL.md", skill_text)
            wheel.writestr(f"{prefix}/agents/openai.yaml", "agent: wheel\n")
            wheel.writestr(f"{prefix}/scripts/tool.py", "print('wheel')\n")
        wheel.writestr(f"{import_name}/__init__.py", "__version__ = '9.9.9'\n")
        wheel.writestr(
            f"{import_name}-{version}.dist-info/METADATA",
            "Metadata-Version: 2.1\n"
            f"Name: {package_name}\n"
            f"Version: {version}\n",
        )
    return path


def make_github_archive(
    path: Path,
    *,
    root: str = "example-agent-skill-main",
    skill_path: str = "skill",
    skill_text: str = "github skill\n",
) -> Path:
    prefix = f"{root}/{skill_path}".rstrip("/")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{prefix}/SKILL.md", skill_text)
        archive.writestr(f"{prefix}/agents/openai.yaml", "agent: github\n")
        archive.writestr(f"{prefix}/scripts/tool.py", "print('github')\n")
        if skill_path:
            archive.writestr(f"{root}/unrelated/SKILL.md", "ignored\n")
    return path


def make_github_skill_collection_archive(
    path: Path,
    *,
    skills: dict[str, str],
    root: str = "example-agent-skill-main",
) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for skill_path, skill_text in skills.items():
            prefix = f"{root}/{skill_path}".rstrip("/")
            archive.writestr(f"{prefix}/SKILL.md", skill_text)
            archive.writestr(f"{prefix}/agents/openai.yaml", "agent: github\n")
            archive.writestr(f"{prefix}/scripts/tool.py", "print('github')\n")
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


def move_manifest_to_legacy_scripts_path(
    project: SkillProject,
    skill_dir: Path,
) -> Path:
    current = manifest_path(project, skill_dir)
    legacy = skill_dir / "scripts" / project.sidecar_manifest_name
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(current.read_text())
    current.unlink()
    return legacy


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

    removed = installer.uninstall(["codex"], "dir", repo_target=True, repo=repo)[0]

    assert removed.status == "removed"
    assert not skill_dir.exists()
    assert not (repo / "AGENTS.md").exists()


def test_installs_codex_dir_target_into_plain_directory(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    installer = Installer(project)
    target = tmp_path / "plain-directory"
    target.mkdir()

    result = installer.install(
        ["codex"],
        "dir",
        repo=target,
    )[0]

    skill_dir = target / ".codex" / "skills" / project.skill_name
    assert result.status == "installed"
    assert result.repo_target is False
    assert (skill_dir / "SKILL.md").read_text() == "example skill\n"
    assert project.marker_start in (target / "AGENTS.md").read_text()


def test_repo_flag_requires_vcs_repo(tmp_path: Path, monkeypatch) -> None:
    project = make_project(tmp_path)
    target = tmp_path / "plain-directory"
    target.mkdir()
    monkeypatch.setattr("agent_skill_installer.installer.find_repo_root", lambda _: None)

    with pytest.raises(InstallerError, match="--repo requires"):
        Installer(project).install(
            ["codex"],
            "dir",
            repo_target=True,
            repo=target,
        )


def test_packaged_install_requires_explicit_bundled_skill_path(tmp_path: Path) -> None:
    project = SkillProject(
        package_name="example-agent-skill",
        import_name="example_agent_skill",
        version="1.2.3",
        skill_name="example-agent-skill",
        description="Example agent skill for installer tests.",
    )
    repo = make_repo(tmp_path / "repo")

    with pytest.raises(
        InstallerError,
        match="bundled_skill_path is required for packaged skill resources",
    ):
        Installer(project).install(["codex"], "repo", repo=repo)


def test_inspect_accepts_legacy_scripts_manifest(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    installer = Installer(project)
    repo = make_repo(tmp_path / "repo")

    installer.install(["codex"], "dir", repo_target=True, repo=repo)
    skill_dir = repo / ".codex" / "skills" / project.skill_name
    legacy = move_manifest_to_legacy_scripts_path(project, skill_dir)

    status = installer.inspect_installations(repo=repo)

    codex_repo = [
        item
        for item in status
        if item.agent == "codex" and item.scope == "dir" and item.repo_target
    ][0]
    assert legacy.exists()
    assert codex_repo.status == "installed"
    assert codex_repo.version == project.version


def test_reinstall_accepts_legacy_scripts_manifest(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    installer = Installer(project)
    repo = make_repo(tmp_path / "repo")

    installer.install(["codex"], "repo", repo=repo)
    skill_dir = repo / ".codex" / "skills" / project.skill_name
    legacy = move_manifest_to_legacy_scripts_path(project, skill_dir)
    (project.bundled_skill_source / "SKILL.md").write_text("updated skill\n")

    result = installer.install(["codex"], "repo", repo=repo)[0]

    assert result.status == "installed"
    assert result.version_change == "same"
    assert (skill_dir / "SKILL.md").read_text() == "updated skill\n"
    assert not legacy.exists()
    assert manifest_path(project, skill_dir).exists()


def test_uninstall_accepts_legacy_scripts_manifest(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    installer = Installer(project)
    repo = make_repo(tmp_path / "repo")

    installer.install(["codex"], "repo", repo=repo)
    skill_dir = repo / ".codex" / "skills" / project.skill_name
    move_manifest_to_legacy_scripts_path(project, skill_dir)

    result = installer.uninstall(["codex"], "repo", repo=repo)[0]

    assert result.status == "removed"
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


def test_install_rejects_invalid_skill_frontmatter_before_replacing_existing(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    installer = Installer(project)
    repo = make_repo(tmp_path / "repo")
    skill_dir = repo / ".codex" / "skills" / project.skill_name

    installer.install(["codex"], "repo", repo=repo)
    (project.bundled_skill_source / "SKILL.md").write_text(invalid_skill_frontmatter())

    with pytest.raises(InstallerError, match="invalid SKILL.md YAML frontmatter"):
        installer.install(["codex"], "repo", repo=repo)

    assert (skill_dir / "SKILL.md").read_text() == "example skill\n"


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


def test_editable_install_manifest_lists_unfiltered_symlink_source_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    project = replace(
        project,
        installer_config=load_installer_config_text(
            """
installer:
  payload:
    include:
      - SKILL.md
"""
        ),
    )
    checkout = make_skill_checkout(tmp_path / "checkout")
    (checkout / "skill" / "scripts" / "tool.py").write_text("print('hello')\n")
    (checkout / "skill" / "notes.txt").write_text("editable source note\n")
    repo = make_repo(tmp_path / "repo")
    monkeypatch.chdir(checkout)

    Installer(project).install(["codex"], "repo", repo=repo, editable=True)

    skill_dir = repo / ".codex" / "skills" / project.skill_name
    assert skill_dir.is_symlink()
    manifest = read_install_manifest(project, skill_dir)
    assert manifest["files"] == [
        "SKILL.md",
        "agents/openai.yaml",
        "notes.txt",
        "scripts/tool.py",
    ]


def test_editable_install_skips_external_wheels_and_uses_checked_in_launcher(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    project = replace(
        project,
        installer_config=load_installer_config_text(
            """
installer:
  external_wheels:
    - package: "arbiter-client>=2.4,<2.5"
      editable: ../client
      copies:
        - wheel_path: arbiter_client/bin/arbiter
          skill_path: bin/arbiter
          executable: true
          replace: true
"""
        ),
    )
    checkout = make_skill_checkout(tmp_path / "checkout")
    (checkout / "skill" / "bin").mkdir()
    (checkout / "skill" / "bin" / "arbiter").write_text(
        "#!/bin/sh\nexec ../client/bin/arbiter \"$@\"\n"
    )
    repo = make_repo(tmp_path / "repo")
    pip_requests: list[tuple[str, str | None, Path | None]] = []
    monkeypatch.chdir(checkout)

    def fake_build_external_wheel(
        *,
        package: str,
        wheel_dir: Path,
        editable: str | None = None,
        cwd: Path | None = None,
    ) -> tuple[Path, str, str, str]:
        pip_requests.append((package, editable, cwd))
        raise AssertionError("editable skill installs should not resolve external wheels")

    monkeypatch.setattr(
        "agent_skill_installer.installer.build_external_wheel",
        fake_build_external_wheel,
    )

    Installer(project).install(["codex"], "repo", repo=repo, editable=True)

    skill_dir = repo / ".codex" / "skills" / project.skill_name
    assert skill_dir.is_symlink()
    assert pip_requests == []
    assert (checkout / "skill" / "bin" / "arbiter").read_text() == (
        "#!/bin/sh\nexec ../client/bin/arbiter \"$@\"\n"
    )
    manifest = read_install_manifest(project, skill_dir)
    assert "external_wheels" not in manifest


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


def test_copy_local_skill_honors_payload_file_selection(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    source = project.bundled_skill_source
    assert source is not None
    (source / "pyproject.toml").write_text("[project]\nname = 'packaging'\n")
    (source / "MANIFEST.in").write_text("recursive-include demo *\n")
    (source / "bin").mkdir()
    (source / "bin" / "tool").write_text("#!/bin/sh\n")
    project = replace(
        project,
        installer_config=load_installer_config_text(
            """
installer:
  payload:
    include:
      - SKILL.md
      - bin/**
"""
        ),
    )
    repo = make_repo(tmp_path / "repo")

    Installer(project).install(["codex"], "repo", repo=repo)

    skill_dir = repo / ".codex" / "skills" / project.skill_name
    assert sorted(
        path.relative_to(skill_dir).as_posix()
        for path in skill_dir.rglob("*")
        if path.is_file() and not path.name.startswith(".")
    ) == ["SKILL.md", "bin/tool"]
    manifest = read_raw_manifest(project, skill_dir)
    assert manifest["files"] == [
        ".example-agent-skill-install.json",
        "SKILL.md",
        "bin/tool",
    ]


def test_copy_local_skill_default_includes_adjacent_files_recursively(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    source = project.bundled_skill_source
    assert source is not None
    (source / "pyproject.toml").write_text("[project]\nname = 'packaging'\n")
    (source / "references").mkdir()
    (source / "references" / "guide.md").write_text("guide\n")
    repo = make_repo(tmp_path / "repo")

    Installer(project).install(["codex"], "repo", repo=repo)

    skill_dir = repo / ".codex" / "skills" / project.skill_name
    assert (skill_dir / "SKILL.md").read_text() == "example skill\n"
    assert (skill_dir / "pyproject.toml").read_text() == (
        "[project]\nname = 'packaging'\n"
    )
    assert (skill_dir / "references" / "guide.md").read_text() == "guide\n"


def test_copy_local_skill_payload_exclude_wins_over_include(
    tmp_path: Path,
) -> None:
    project = replace(
        make_project(tmp_path),
        installer_config=load_installer_config_text(
            """
installer:
  payload:
    include:
      - "**"
    exclude:
      - scripts/**
"""
        ),
    )
    repo = make_repo(tmp_path / "repo")

    Installer(project).install(["codex"], "repo", repo=repo)

    skill_dir = repo / ".codex" / "skills" / project.skill_name
    assert (skill_dir / "SKILL.md").exists()
    assert (skill_dir / "agents" / "openai.yaml").exists()
    assert not (skill_dir / "scripts" / "tool.py").exists()


def test_copy_local_skill_payload_requires_selected_skill_file(
    tmp_path: Path,
) -> None:
    project = replace(
        make_project(tmp_path),
        installer_config=load_installer_config_text(
            """
installer:
  payload:
    include:
      - scripts/**
"""
        ),
    )
    repo = make_repo(tmp_path / "repo")

    with pytest.raises(InstallerError, match="did not include SKILL.md"):
        Installer(project).install(["codex"], "repo", repo=repo)

    assert not (repo / ".codex" / "skills" / project.skill_name).exists()


def test_payload_patterns_use_fnmatch_path_semantics(tmp_path: Path) -> None:
    project = replace(
        make_project(tmp_path),
        installer_config=load_installer_config_text(
            """
installer:
  payload:
    include:
      - SKILL.md
      - "*.py"
"""
        ),
    )
    repo = make_repo(tmp_path / "repo")

    Installer(project).install(["codex"], "repo", repo=repo)

    skill_dir = repo / ".codex" / "skills" / project.skill_name
    assert (skill_dir / "scripts" / "tool.py").read_text() == "print('tool')\n"
    assert not (skill_dir / "agents" / "openai.yaml").exists()


def test_pypi_wheel_install_copies_declared_external_wheel_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    config_text = """
installer:
  external_wheels:
    - package: "arbiter-client>=2.4,<2.5"
      copies:
        - wheel_path: arbiter_client/bin/arbiter
          skill_path: bin/arbiter
          executable: true
          replace: true
"""
    project = replace(
        project,
        installer_config=load_installer_config_text(
            config_text,
            package_version="2.0.0",
        ),
    )
    skill_wheel = make_skill_wheel(
        tmp_path / "arbiter_skill-2.0.0-py3-none-any.whl",
        project,
        skill_text="arbiter skill\n",
        extra_skill_files={
            "bin/arbiter": "#!/bin/sh\necho development launcher\n",
        },
    )
    external_wheel = make_external_wheel(
        tmp_path / "arbiter_client-2.4.7-py3-none-manylinux_2_17_x86_64.whl",
        data="#!/bin/sh\necho arbiter\n",
    )
    digest = hashlib.sha256(external_wheel.read_bytes()).hexdigest()
    pip_requests: list[tuple[str, str | None, Path | None]] = []

    monkeypatch.setattr(
        "agent_skill_installer.installer.download_pypi_wheel",
        lambda _project, _version, _download_dir: skill_wheel,
    )
    def fake_build_external_wheel(
        *,
        package: str,
        wheel_dir: Path,
        editable: str | None = None,
        cwd: Path | None = None,
    ) -> tuple[Path, str, str, str]:
        pip_requests.append((package, editable, cwd))
        target = wheel_dir / external_wheel.name
        shutil.copy2(external_wheel, target)
        return target, digest, "arbiter-client", "2.4.7"

    monkeypatch.setattr(
        "agent_skill_installer.installer.build_external_wheel",
        fake_build_external_wheel,
    )

    Installer(project).install(
        ["codex"],
        "repo",
        repo=repo,
        pypi_version="2.0.0",
    )

    skill_dir = repo / ".codex" / "skills" / project.skill_name
    tool = skill_dir / "bin" / "arbiter"
    assert pip_requests == [("arbiter-client>=2.4,<2.5", None, None)]
    assert tool.read_text() == "#!/bin/sh\necho arbiter\n"
    assert_posix_mode(tool, 0o755)
    assert not (skill_dir / "arbiter_client" / "__init__.py").exists()
    manifest = read_raw_manifest(project, skill_dir)
    assert manifest["files"] == [
        ".example-agent-skill-install.json",
        "SKILL.md",
        "agents/openai.yaml",
        "bin/arbiter",
        "scripts/tool.py",
    ]
    assert manifest["external_wheels"] == [
        {
            "source_type": "pip_wheel",
            "package": "arbiter-client>=2.4,<2.5",
            "distribution": "arbiter-client",
            "version": "2.4.7",
            "resolution": "python -m pip wheel",
            "wheel": {
                "filename": external_wheel.name,
                "sha256": digest,
            },
            "copies": [
                {
                    "wheel_path": "arbiter_client/bin/arbiter",
                    "skill_path": "bin/arbiter",
                    "executable": True,
                    "replace": True,
                }
            ],
        }
    ]


def test_pypi_external_wheel_install_rejects_missing_declared_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    config_text = """
installer:
  external_wheels:
    - package: arbiter-client==2.4.7
      copies:
        - wheel_path: arbiter_client/bin/missing
          skill_path: bin/arbiter
"""
    project = replace(
        project,
        installer_config=load_installer_config_text(config_text),
    )
    skill_wheel = make_skill_wheel(
        tmp_path / "arbiter_skill-2.0.0-py3-none-any.whl",
        project,
    )
    external_wheel = make_external_wheel(
        tmp_path / "arbiter_client-2.4.7-py3-none-any.whl"
    )
    monkeypatch.setattr(
        "agent_skill_installer.installer.download_pypi_wheel",
        lambda _project, _version, _download_dir: skill_wheel,
    )
    def fake_build_external_wheel(
        *,
        package: str,
        wheel_dir: Path,
        editable: str | None = None,
        cwd: Path | None = None,
    ) -> tuple[Path, str, str, str]:
        target = wheel_dir / external_wheel.name
        shutil.copy2(external_wheel, target)
        return target, hashlib.sha256(target.read_bytes()).hexdigest(), "arbiter-client", "2.4.7"

    monkeypatch.setattr(
        "agent_skill_installer.installer.build_external_wheel",
        fake_build_external_wheel,
    )

    with pytest.raises(InstallerError, match="external wheel did not contain declared file"):
        Installer(project).install(
            ["codex"],
            "repo",
            repo=repo,
            pypi_version="2.0.0",
        )

    assert not (repo / ".codex" / "skills" / project.skill_name).exists()


def test_pypi_external_wheel_install_rejects_skill_payload_overwrite(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    project = replace(
        project,
        installer_config=load_installer_config_text(
            """
installer:
  external_wheels:
    - package: arbiter-client==2.4.7
      copies:
        - wheel_path: arbiter_client/bin/arbiter
          skill_path: SKILL.md
"""
        ),
    )
    skill_wheel = make_skill_wheel(
        tmp_path / "arbiter_skill-2.0.0-py3-none-any.whl",
        project,
        skill_text="original skill\n",
    )
    external_wheel = make_external_wheel(
        tmp_path / "arbiter_client-2.4.7-py3-none-any.whl",
        data="replacement skill\n",
    )
    monkeypatch.setattr(
        "agent_skill_installer.installer.download_pypi_wheel",
        lambda _project, _version, _download_dir: skill_wheel,
    )

    def fake_build_external_wheel(
        *,
        package: str,
        wheel_dir: Path,
        editable: str | None = None,
        cwd: Path | None = None,
    ) -> tuple[Path, str, str, str]:
        target = wheel_dir / external_wheel.name
        shutil.copy2(external_wheel, target)
        return (
            target,
            hashlib.sha256(target.read_bytes()).hexdigest(),
            "arbiter-client",
            "2.4.7",
        )

    monkeypatch.setattr(
        "agent_skill_installer.installer.build_external_wheel",
        fake_build_external_wheel,
    )

    with pytest.raises(
        InstallerError,
        match="external wheel copy would overwrite installed skill file: SKILL.md",
    ):
        Installer(project).install(
            ["codex"],
            "repo",
            repo=repo,
            pypi_version="2.0.0",
        )


def test_pypi_external_wheel_install_rejects_duplicate_copy_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    project = replace(
        project,
        installer_config=load_installer_config_text(
            """
installer:
  external_wheels:
    - package: arbiter-client==2.4.7
      copies:
        - wheel_path: arbiter_client/bin/arbiter
          skill_path: bin/arbiter
        - wheel_path: arbiter_client/bin/arbiter
          skill_path: bin/arbiter
"""
        ),
    )
    skill_wheel = make_skill_wheel(
        tmp_path / "arbiter_skill-2.0.0-py3-none-any.whl",
        project,
    )
    external_wheel = make_external_wheel(
        tmp_path / "arbiter_client-2.4.7-py3-none-any.whl",
        data="#!/bin/sh\n",
    )
    monkeypatch.setattr(
        "agent_skill_installer.installer.download_pypi_wheel",
        lambda _project, _version, _download_dir: skill_wheel,
    )

    def fake_build_external_wheel(
        *,
        package: str,
        wheel_dir: Path,
        editable: str | None = None,
        cwd: Path | None = None,
    ) -> tuple[Path, str, str, str]:
        target = wheel_dir / external_wheel.name
        shutil.copy2(external_wheel, target)
        return (
            target,
            hashlib.sha256(target.read_bytes()).hexdigest(),
            "arbiter-client",
            "2.4.7",
        )

    monkeypatch.setattr(
        "agent_skill_installer.installer.build_external_wheel",
        fake_build_external_wheel,
    )

    with pytest.raises(
        InstallerError,
        match="external wheel copy would overwrite installed skill file: bin/arbiter",
    ):
        Installer(project).install(
            ["codex"],
            "repo",
            repo=repo,
            pypi_version="2.0.0",
        )


def test_pypi_external_wheel_failure_keeps_existing_install(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    skill_dir = repo / ".codex" / "skills" / project.skill_name
    old_wheel = make_skill_wheel(
        tmp_path / "old_skill-1.0.0-py3-none-any.whl",
        project,
        skill_text="old skill\n",
    )
    monkeypatch.setattr(
        "agent_skill_installer.installer.download_pypi_wheel",
        lambda _project, _version, _download_dir: old_wheel,
    )
    Installer(project).install(["codex"], "repo", repo=repo, pypi_version="1.0.0")
    hook_text = (repo / "AGENTS.md").read_text()

    failing_project = replace(
        project,
        installer_config=load_installer_config_text(
            """
installer:
  external_wheels:
    - package: arbiter-client==2.4.7
      copies:
        - wheel_path: arbiter_client/bin/arbiter
          skill_path: SKILL.md
"""
        ),
    )
    new_wheel = make_skill_wheel(
        tmp_path / "new_skill-2.0.0-py3-none-any.whl",
        failing_project,
        skill_text="new skill\n",
    )
    external_wheel = make_external_wheel(
        tmp_path / "arbiter_client-2.4.7-py3-none-any.whl",
        data="replacement skill\n",
    )
    monkeypatch.setattr(
        "agent_skill_installer.installer.download_pypi_wheel",
        lambda _project, _version, _download_dir: new_wheel,
    )

    def fake_build_external_wheel(
        *,
        package: str,
        wheel_dir: Path,
        editable: str | None = None,
        cwd: Path | None = None,
    ) -> tuple[Path, str, str, str]:
        target = wheel_dir / external_wheel.name
        shutil.copy2(external_wheel, target)
        return (
            target,
            hashlib.sha256(target.read_bytes()).hexdigest(),
            "arbiter-client",
            "2.4.7",
        )

    monkeypatch.setattr(
        "agent_skill_installer.installer.build_external_wheel",
        fake_build_external_wheel,
    )

    with pytest.raises(
        InstallerError,
        match="external wheel copy would overwrite installed skill file: SKILL.md",
    ):
        Installer(failing_project).install(
            ["codex"],
            "repo",
            repo=repo,
            pypi_version="2.0.0",
        )

    assert (skill_dir / "SKILL.md").read_text() == "old skill\n"
    assert read_raw_manifest(project, skill_dir)["package_version"] == "1.0.0"
    assert (repo / "AGENTS.md").read_text() == hook_text


def test_pypi_external_wheel_reinstall_swaps_prepared_skill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    skill_dir = repo / ".codex" / "skills" / project.skill_name
    old_wheel = make_skill_wheel(
        tmp_path / "old_skill-1.0.0-py3-none-any.whl",
        project,
        skill_text="old skill\n",
    )
    monkeypatch.setattr(
        "agent_skill_installer.installer.download_pypi_wheel",
        lambda _project, _version, _download_dir: old_wheel,
    )
    Installer(project).install(["codex"], "repo", repo=repo, pypi_version="1.0.0")

    new_project = replace(
        project,
        installer_config=load_installer_config_text(
            """
installer:
  external_wheels:
    - package: arbiter-client==2.4.7
      copies:
        - wheel_path: arbiter_client/bin/arbiter
          skill_path: bin/arbiter
"""
        ),
    )
    new_wheel = make_skill_wheel(
        tmp_path / "new_skill-2.0.0-py3-none-any.whl",
        new_project,
        skill_text="new skill\n",
    )
    external_wheel = make_external_wheel(
        tmp_path / "arbiter_client-2.4.7-py3-none-any.whl",
        data="#!/bin/sh\necho arbiter\n",
    )
    monkeypatch.setattr(
        "agent_skill_installer.installer.download_pypi_wheel",
        lambda _project, _version, _download_dir: new_wheel,
    )

    def fake_build_external_wheel(
        *,
        package: str,
        wheel_dir: Path,
        editable: str | None = None,
        cwd: Path | None = None,
    ) -> tuple[Path, str, str, str]:
        target = wheel_dir / external_wheel.name
        shutil.copy2(external_wheel, target)
        return (
            target,
            hashlib.sha256(target.read_bytes()).hexdigest(),
            "arbiter-client",
            "2.4.7",
        )

    monkeypatch.setattr(
        "agent_skill_installer.installer.build_external_wheel",
        fake_build_external_wheel,
    )

    Installer(new_project).install(["codex"], "repo", repo=repo, pypi_version="2.0.0")

    assert (skill_dir / "SKILL.md").read_text() == "new skill\n"
    assert (skill_dir / "bin" / "arbiter").read_text() == "#!/bin/sh\necho arbiter\n"
    manifest = read_raw_manifest(project, skill_dir)
    assert manifest["package_version"] == "2.0.0"
    assert manifest["external_wheels"][0]["package"] == "arbiter-client==2.4.7"


def test_pypi_reinstall_reports_live_skill_when_cleanup_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    skill_dir = repo / ".codex" / "skills" / project.skill_name
    old_wheel = make_skill_wheel(
        tmp_path / "old_skill-1.0.0-py3-none-any.whl",
        project,
        skill_text="old skill\n",
    )
    monkeypatch.setattr(
        "agent_skill_installer.installer.download_pypi_wheel",
        lambda _project, _version, _download_dir: old_wheel,
    )
    Installer(project).install(["codex"], "repo", repo=repo, pypi_version="1.0.0")

    new_wheel = make_skill_wheel(
        tmp_path / "new_skill-2.0.0-py3-none-any.whl",
        project,
        skill_text="new skill\n",
    )
    monkeypatch.setattr(
        "agent_skill_installer.installer.download_pypi_wheel",
        lambda _project, _version, _download_dir: new_wheel,
    )
    from agent_skill_installer import installer as installer_module

    original_remove_existing_path = installer_module.remove_existing_path

    def fail_backup_cleanup(path: Path) -> None:
        if f".{project.skill_name}.previous-" in path.name:
            raise OSError("backup directory is busy")
        original_remove_existing_path(path)

    monkeypatch.setattr(
        "agent_skill_installer.installer.remove_existing_path",
        fail_backup_cleanup,
    )

    with pytest.raises(InstallerError) as exc_info:
        Installer(project).install(["codex"], "repo", repo=repo, pypi_version="2.0.0")

    message = str(exc_info.value)
    assert "skill is live, but post-install cleanup failed" in message
    assert project.skill_name in message
    assert "remove previous skill backup" in message
    assert "backup directory is busy" in message
    assert (skill_dir / "SKILL.md").read_text() == "new skill\n"
    assert read_raw_manifest(project, skill_dir)["package_version"] == "2.0.0"


def test_install_reports_live_skill_when_staging_cleanup_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    skill_dir = repo / ".codex" / "skills" / project.skill_name
    from agent_skill_installer import installer as installer_module

    original_remove_existing_path = installer_module.remove_existing_path

    def fail_staging_cleanup(path: Path) -> None:
        if f".{project.skill_name}.install-" in path.name:
            raise OSError("staging directory is busy")
        original_remove_existing_path(path)

    monkeypatch.setattr(
        "agent_skill_installer.installer.remove_existing_path",
        fail_staging_cleanup,
    )

    with pytest.raises(InstallerError) as exc_info:
        Installer(project).install(["codex"], "repo", repo=repo)

    message = str(exc_info.value)
    assert "skill is live, but post-install cleanup failed" in message
    assert project.skill_name in message
    assert "remove staging directory" in message
    assert "staging directory is busy" in message
    assert (skill_dir / "SKILL.md").read_text() == "example skill\n"


def test_run_pip_wheel_uses_current_python_module(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[list[str], Path | None]] = []

    def fake_run(
        command: list[str],
        *,
        cwd: Path | None,
        text: bool,
        capture_output: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, cwd))
        wheel_dir = Path(command[command.index("--wheel-dir") + 1])
        make_external_wheel(wheel_dir / "arbiter_client-2.4.7-py3-none-any.whl")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("agent_skill_installer.installer.subprocess.run", fake_run)

    wheel = run_pip_wheel(
        package="arbiter-client>=2.4,<2.5",
        wheel_dir=tmp_path / "wheels",
        editable="../client",
        cwd=tmp_path / "skill",
    )

    assert wheel.name == "arbiter_client-2.4.7-py3-none-any.whl"
    command, cwd = calls[0]
    assert command[:4] == [sys.executable, "-m", "pip", "wheel"]
    assert "--no-deps" in command
    assert command[-1:] == ["../client"]
    assert "--editable" not in command
    assert "--no-build-isolation" not in command
    assert cwd == tmp_path / "skill"


def test_pypi_wheel_install_rejects_invalid_skill_frontmatter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    wheel = make_skill_wheel(
        tmp_path / "example.whl",
        project,
        skill_text=invalid_skill_frontmatter(),
    )

    monkeypatch.setattr(
        "agent_skill_installer.installer.download_pypi_wheel",
        lambda _project, _version, _download_dir: wheel,
    )

    with pytest.raises(InstallerError, match="invalid SKILL.md YAML frontmatter"):
        Installer(project).install(
            ["codex"],
            "repo",
            repo=repo,
            pypi_version="2.0.0",
        )

    assert not (repo / ".codex" / "skills" / project.skill_name).exists()


def test_github_install_extracts_skill_from_repository_archive(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    archive = make_github_archive(tmp_path / "github.zip", skill_text="github\n")

    monkeypatch.setattr(
        "agent_skill_installer.installer.download_github_archive",
        lambda _source, _download_dir: archive,
    )

    result = Installer(project).install(
        ["codex"],
        "repo",
        repo=repo,
        github_url="https://github.com/example/example-agent-skill",
    )[0]

    skill_dir = repo / ".codex" / "skills" / project.skill_name
    assert result.install_mode == "github"
    assert result.version == "main"
    assert result.source_url == "https://github.com/example/example-agent-skill"
    assert result.source_ref == "main"
    assert (skill_dir / "SKILL.md").read_text() == "github\n"
    assert (skill_dir / "agents" / "openai.yaml").read_text() == "agent: github\n"
    assert not (skill_dir / "unrelated" / "SKILL.md").exists()
    manifest = read_install_manifest(project, skill_dir)
    assert manifest["install_mode"] == "github"
    assert manifest["source_url"] == "https://github.com/example/example-agent-skill"
    assert manifest["source_ref"] == "main"


def test_github_install_rejects_invalid_skill_frontmatter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    archive = make_github_archive(
        tmp_path / "github.zip",
        skill_text=invalid_skill_frontmatter(),
    )

    monkeypatch.setattr(
        "agent_skill_installer.installer.download_github_archive",
        lambda _source, _download_dir: archive,
    )

    with pytest.raises(InstallerError, match="invalid SKILL.md YAML frontmatter"):
        Installer(project).install(
            ["codex"],
            "repo",
            repo=repo,
            github_url="https://github.com/example/example-agent-skill",
        )

    assert not (repo / ".codex" / "skills" / project.skill_name).exists()


def test_github_install_accepts_tree_url_for_nested_skill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    archive = make_github_archive(
        tmp_path / "github.zip",
        skill_path="packages/demo/skill",
        skill_text="nested github\n",
    )
    captured = {}

    def fake_download(source, _download_dir):
        captured["source"] = source
        return archive

    monkeypatch.setattr(
        "agent_skill_installer.installer.download_github_archive",
        fake_download,
    )

    result = Installer(project).install(
        ["codex"],
        "repo",
        repo=repo,
        github_url=(
            "https://github.com/example/example-agent-skill/"
            "tree/v2/packages/demo/skill"
        ),
    )[0]

    skill_dir = repo / ".codex" / "skills" / project.skill_name
    assert result.version == "v2"
    assert captured["source"].path.as_posix() == "packages/demo/skill"
    assert (skill_dir / "SKILL.md").read_text() == "nested github\n"


def test_copy_github_archive_skill_extracts_root_skill(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    archive = make_github_archive(
        tmp_path / "github.zip",
        skill_path="",
        skill_text="root github\n",
    )
    with zipfile.ZipFile(archive, "a") as zip_archive:
        root = "example-agent-skill-main"
        zip_archive.writestr(f"{root}/{CONFIG_FILE_NAME}", "installer:\n")
        write_zip_file(
            zip_archive,
            f"{root}/bin/arbiter",
            "#!/bin/sh\n",
            mode=0o755,
        )
    skill_dir = tmp_path / "skill"

    copied = copy_github_archive_skill(project, archive, skill_dir)

    assert copied == [
        "SKILL.md",
        "agents/openai.yaml",
        "bin/arbiter",
        "scripts/tool.py",
    ]
    assert (skill_dir / "SKILL.md").read_text() == "root github\n"
    assert_posix_mode(skill_dir / "bin" / "arbiter", 0o755)
    assert not (skill_dir / CONFIG_FILE_NAME).exists()


def test_copy_github_archive_skill_honors_payload_file_selection(
    tmp_path: Path,
) -> None:
    project = replace(
        make_project(tmp_path),
        installer_config=load_installer_config_text(
            """
installer:
  payload:
    include:
      - SKILL.md
      - bin/**
"""
        ),
    )
    archive = make_github_archive(
        tmp_path / "github.zip",
        skill_path="",
        skill_text="root github\n",
    )
    with zipfile.ZipFile(archive, "a") as zip_archive:
        root = "example-agent-skill-main"
        zip_archive.writestr(f"{root}/pyproject.toml", "[project]\n")
        zip_archive.writestr(f"{root}/bin/arbiter", "#!/bin/sh\n")
    skill_dir = tmp_path / "skill"

    copied = copy_github_archive_skill(project, archive, skill_dir)

    assert copied == ["SKILL.md", "bin/arbiter"]
    assert not (skill_dir / "pyproject.toml").exists()


def test_parse_github_url_supports_overrides_and_blob_path() -> None:
    source = parse_github_url(
        "https://github.com/example/demo/blob/main/skill/SKILL.md",
        ref="release/v1",
        path="packages/demo/skill",
    )

    assert source.owner == "example"
    assert source.repo == "demo"
    assert source.ref == "release/v1"
    assert source.path is not None
    assert source.path.as_posix() == "packages/demo/skill"


def test_copy_pypi_wheel_skill_extracts_only_bundled_skill(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    wheel = make_skill_wheel(
        tmp_path / "example.whl",
        project,
        config_text="installer:\n  version: 1\n",
    )
    prefix = project.wheel_skill_prefix.as_posix()
    with zipfile.ZipFile(wheel, "a") as archive:
        write_zip_file(
            archive,
            f"{prefix}/bin/arbiter",
            "#!/bin/sh\n",
            mode=0o755,
        )
    skill_dir = tmp_path / "skill"

    old_umask = os.umask(0o022)
    try:
        copied = copy_pypi_wheel_skill(project, wheel, skill_dir)
    finally:
        os.umask(old_umask)

    assert copied == [
        "SKILL.md",
        "agents/openai.yaml",
        "bin/arbiter",
        "scripts/tool.py",
    ]
    assert (skill_dir / "SKILL.md").read_text() == "wheel skill\n"
    assert_posix_mode(skill_dir / "SKILL.md", 0o644)
    assert (skill_dir / "agents" / "openai.yaml").read_text() == "agent: wheel\n"
    assert_posix_mode(skill_dir / "bin" / "arbiter", 0o755)
    assert not (skill_dir / CONFIG_FILE_NAME).exists()
    assert not (skill_dir / project.import_name / "__init__.py").exists()
    assert not (skill_dir / f"{project.import_name}-1.2.3.dist-info").exists()


def test_copy_pypi_wheel_skill_honors_payload_file_selection(tmp_path: Path) -> None:
    project = replace(
        make_project(tmp_path),
        installer_config=load_installer_config_text(
            """
installer:
  payload:
    include:
      - SKILL.md
      - bin/**
"""
        ),
    )
    wheel = make_skill_wheel(
        tmp_path / "example.whl",
        project,
        config_text="installer:\n  version: 1\n",
        extra_skill_files={
            "pyproject.toml": "[project]\n",
            "bin/arbiter": "#!/bin/sh\n",
        },
    )
    skill_dir = tmp_path / "skill"

    copied = copy_pypi_wheel_skill(project, wheel, skill_dir)

    assert copied == ["SKILL.md", "bin/arbiter"]
    assert not (skill_dir / "pyproject.toml").exists()


def test_generic_console_wheel_install_uses_embedded_payload_selection(
    tmp_path: Path,
    capsys,
) -> None:
    project = make_project(tmp_path)
    wheel = make_skill_wheel(
        tmp_path / "example.whl",
        project,
        config_text="""
installer:
  payload:
    include:
      - SKILL.md
      - bin/**
""",
        extra_skill_files={
            "pyproject.toml": "[project]\n",
            "bin/arbiter": "#!/bin/sh\n",
        },
    )
    repo = make_repo(tmp_path / "repo")

    exit_code = generic_main(
        [
            "--no-ui",
            "install",
            "--wheel-file",
            str(wheel),
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert "Installed example-agent-skill 1.2.3" in output.out
    skill_dir = repo / ".codex" / "skills" / project.skill_name
    assert sorted(
        path.relative_to(skill_dir).as_posix()
        for path in skill_dir.rglob("*")
        if path.is_file() and not path.name.startswith(".")
    ) == ["SKILL.md", "bin/arbiter"]


def test_copy_pypi_wheel_skill_rejects_missing_skill(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    wheel = tmp_path / "empty.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"{project.import_name}/__init__.py", "")

    with pytest.raises(InstallerError, match="did not contain"):
        copy_pypi_wheel_skill(project, wheel, tmp_path / "skill")


def test_generic_console_installs_wheel_with_root_skill(tmp_path: Path, capsys) -> None:
    repo = make_repo(tmp_path / "repo")
    wheel = tmp_path / "root_skill-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("SKILL.md", "root wheel skill\n")
        archive.writestr("scripts/tool.py", "print('root')\n")
        archive.writestr(
            "root_skill-1.2.3.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: root-skill\nVersion: 1.2.3\n",
        )

    exit_code = generic_main(
        [
            "install",
            "--wheel-file",
            str(wheel),
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert "Installed root-skill 1.2.3 (wheel) to Codex repo:" in output.out
    skill_dir = repo / ".codex" / "skills" / "root-skill"
    assert (skill_dir / "SKILL.md").read_text() == "root wheel skill\n"
    assert (skill_dir / "scripts" / "tool.py").read_text() == "print('root')\n"


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


def test_install_rejects_conflicting_github_source(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")

    with pytest.raises(InstallerError, match="cannot be combined"):
        Installer(project).install(
            ["codex"],
            "repo",
            repo=repo,
            editable=True,
            github_url="https://github.com/example/demo",
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

    Installer(project).install(["codex"], "dir", repo_target=True, repo=repo)
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


def test_generic_uninstall_uses_manifest_hook_markers(tmp_path: Path) -> None:
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

    run_generic_uninstall(
        Namespace(
            skill_name=project.skill_name,
            package_name=project.package_name,
            agent="codex",
            scope="dir",
            repo_target=True,
            repo=repo,
            home=None,
            codex_home=None,
            claude_home=None,
        )
    )

    assert not (repo / "AGENTS.md").exists()
    assert not (repo / ".codex" / "skills" / project.skill_name).exists()


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
            "--target-dir",
            str(repo),
        ],
        project=project,
    )
    output = capsys.readouterr()

    assert install_code == 0
    assert "Installed example-agent-skill 1.2.3 to Codex repo:" in output.out

    uninstall_code = main(
        [
            "--no-ui",
            "uninstall",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ],
        project=project,
    )
    output = capsys.readouterr()

    assert uninstall_code == 0
    assert "Removed example-agent-skill 1.2.3 from Codex repo:" in output.out


def test_cli_install_help_uses_target_dir_path_metavar(
    tmp_path: Path,
    capsys,
) -> None:
    project = make_project(tmp_path)
    parser = build_parser(project)

    with pytest.raises(SystemExit) as error:
        parser.parse_args(["install", "--help"])
    output = capsys.readouterr()

    assert error.value.code == 0
    assert "--target-dir PATH" in output.out
    assert "--target-dir REPO" not in output.out
    assert "--target-type" not in output.out
    assert "--repo" in output.out


def test_cli_no_ui_repo_flag_targets_repository_directory(tmp_path: Path, capsys) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")

    exit_code = main(
        [
            "--no-ui",
            "install",
            "--agent",
            "codex",
            "--scope",
            "dir",
            "--repo",
            "--target-dir",
            str(repo),
        ],
        project=project,
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert "Installed example-agent-skill 1.2.3 to Codex repo:" in output.out


def test_cli_no_ui_dir_scope_installs_plain_directory(tmp_path: Path, capsys) -> None:
    project = make_project(tmp_path)
    target = tmp_path / "plain-directory"
    target.mkdir()

    exit_code = main(
        [
            "--no-ui",
            "install",
            "--agent",
            "codex",
            "--scope",
            "dir",
            "--target-dir",
            str(target),
        ],
        project=project,
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert (
        "Installed example-agent-skill 1.2.3 "
        "to Codex directory (plain-directory):"
    ) in output.out
    assert (target / ".codex" / "skills" / project.skill_name / "SKILL.md").exists()


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
            "--target-dir",
            str(repo),
        ],
        project=project,
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert f"  skill: {repo / '.codex' / 'skills' / project.skill_name}" in output.out
    assert f"  hook:  {repo / 'AGENTS.md'}" in output.out


def test_installing_skills_docs_show_global_verbose_before_subcommand() -> None:
    docs = (Path(__file__).parents[2] / "docs" / "installing-skills.md").read_text()

    assert "agent-skill-installer --no-ui --verbose install" in docs


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
    assert "Installed example-agent-skill 1.2.3 to Codex global:" in output.out
    assert (
        "Installed example-agent-skill 1.2.3 to Claude Code global:"
        in output.out
    )
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
    assert "Installed example-agent-skill 1.2.3 to Codex global:" in output.out
    assert (
        "Installed example-agent-skill 1.2.3 to Claude Code global:"
        in output.out
    )


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
            "--target-dir",
            str(repo),
        ],
        project=project,
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert (
        "Installed example-agent-skill 1.2.3 (editable) to Codex repo:"
        in output.out
    )
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
            "--target-dir",
            str(repo),
        ],
        project=project,
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert "Installing from PyPI: example-agent-skill==2.0.0" in output.err
    assert (
        "Installed example-agent-skill 2.0.0 (PyPI wheel) "
        "to Codex repo:"
        in output.out
    )


def test_cli_no_ui_pypi_install_uses_pip_resolution(
    tmp_path: Path,
    monkeypatch,
    capsys,
    ) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    wheel = make_skill_wheel(tmp_path / "example.whl", project)
    calls: list[tuple[str, Path]] = []

    def fake_build_pypi_wheel(*, package: str, wheel_dir: Path) -> tuple[Path, str]:
        calls.append((package, wheel_dir))
        return wheel, "2.1.0"

    monkeypatch.setattr(
        "agent_skill_installer.installer.build_pypi_wheel",
        fake_build_pypi_wheel,
    )

    exit_code = main(
        [
            "--no-ui",
            "install",
            "--pypi",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ],
        project=project,
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert calls and calls[0][0] == "example-agent-skill"
    assert "Installing from PyPI: example-agent-skill" in output.err
    assert (
        "Installed example-agent-skill 2.1.0 (PyPI wheel) "
        "to Codex repo:"
    ) in output.out


def test_cli_no_ui_github_url_install(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    archive = make_github_archive(tmp_path / "github.zip")
    monkeypatch.setattr(
        "agent_skill_installer.installer.download_github_archive",
        lambda _source, _download_dir: archive,
    )

    exit_code = main(
        [
            "--no-ui",
            "install",
            "--github-url",
            "https://github.com/example/example-agent-skill",
            "--github-ref",
            "v2",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ],
        project=project,
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert "Installing from GitHub: https://github.com/example/example-agent-skill" in (
        output.err
    )
    assert (
        "Installed example-agent-skill v2 (GitHub archive) "
        "to Codex repo:"
        in output.out
    )
    assert (
        repo / ".codex" / "skills" / project.skill_name / "SKILL.md"
    ).read_text() == "github skill\n"


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
            "--target-dir",
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
    assert "--editable, --pypi-version cannot be combined" in output.err


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


def test_cli_no_ui_command_preview_includes_github_source(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    assert build_no_ui_command(
        project,
        "install",
        agent="codex",
        scope="dir",
        repo_target=True,
        repo=tmp_path / "repo",
        github_url="https://github.com/example/demo",
        github_ref="v1",
        github_path="skill",
    ) == (
        "example-agent-skill --no-ui install "
        "--github-url https://github.com/example/demo --github-ref v1 "
        "--github-path skill --agent codex --scope dir --repo --target-dir "
        f"{shlex.quote(str(tmp_path / 'repo'))}"
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

    from textual.widgets import Button, RadioSet, Static

    def preview(command: object) -> str | None:
        if command == "uninstall":
            return None
        return (
            f"agent-skill-installer --no-ui {command} "
            "--agent all --scope global"
        )

    def summary(command: object) -> str:
        return f"{command} summary"

    app = make_textual_select_app(
        "What would you like to do?",
        [
            {"name": "Install", "value": "install"},
            {"name": "Uninstall", "value": "uninstall"},
        ],
        command_preview_builder=preview,
        summary_builder=summary,
    )

    async def run_scenario() -> None:
        async with app.run_test() as pilot:
            choice = app.query_one("#choice", RadioSet)
            copy_button = app.query_one("#copy-command", Button)
            command = app.query_one("#command-preview-command", Static)
            command_panel = app.query_one("#command-preview")
            summary_panel = app.query_one("#installation-summary")
            summary_content = app.query_one("#installation-summary-content", Static)
            command_panel_height = command_panel.region.height
            assert command_panel.has_class("install-preview")
            assert summary_panel.region.y < command.region.y
            assert command.region.height >= 3
            assert str(summary_content.content) == summary("install")
            assert copy_button.can_focus is False
            assert str(command.content) == preview("install")
            await pilot.press("up")
            assert choice.has_focus
            assert not copy_button.has_focus
            assert choice.pressed_index == 0
            await pilot.press("down")
            await pilot.pause()
            assert choice.has_focus
            assert choice.pressed_index == 0
            assert str(command.content) == DEFAULT_EMPTY_COMMAND_PREVIEW_MESSAGE
            assert command_panel.region.height == command_panel_height
            assert command_panel.has_class("uninstall-preview")
            assert not command_panel.has_class("install-preview")
            assert str(summary_content.content) == summary("uninstall")
            assert summary_panel.display is True
            await pilot.press("down")
            assert app.query_one("#continue", Button).has_focus
            await pilot.press("up")
            assert choice.has_focus
            assert choice.pressed_index == 0
            assert str(command.content) == DEFAULT_EMPTY_COMMAND_PREVIEW_MESSAGE
            assert command_panel.has_class("uninstall-preview")
            await pilot.press("enter")

    asyncio.run(run_scenario())

    assert app.return_value == "uninstall"


def test_textual_summary_panel_height_is_reserved_for_multiline_content() -> None:
    if importlib.util.find_spec("textual") is None:
        pytest.skip("Textual is not installed")

    from textual.widgets import Static

    def summary(value: object) -> str:
        if value == "repo":
            return "Line 1\nLine 2\nLine 3"
        return "Line 1"

    app = make_textual_select_app(
        "Install location",
        [
            {"name": "Global", "value": "global"},
            {"name": "Current directory", "value": "repo"},
        ],
        summary_builder=summary,
    )

    async def run_scenario() -> None:
        async with app.run_test() as pilot:
            summary_panel = app.query_one("#installation-summary")
            summary_content = app.query_one("#installation-summary-content", Static)
            initial_height = summary_panel.region.height

            assert str(summary_content.content) == "Line 1"
            app.update_active_choice(1)
            await pilot.pause()

            assert str(summary_content.content) == "Line 1\nLine 2\nLine 3"
            assert summary_panel.region.height == initial_height

    asyncio.run(run_scenario())


def test_textual_prompt_panels_fill_available_screen_width() -> None:
    if importlib.util.find_spec("textual") is None:
        pytest.skip("Textual is not installed")

    app = make_textual_select_app(
        "What would you like to do?",
        [
            {"name": "Install", "value": "install"},
            {"name": "Uninstall", "value": "uninstall"},
        ],
        command_preview_builder=lambda value: (
            f"agent-skill-installer --no-ui {value} --agent all --scope global"
        ),
        summary_builder=lambda value: f"{value} summary",
    )

    async def run_scenario() -> None:
        async with app.run_test(size=(120, 40)):
            expected_width = app.size.width - 4
            for selector in ("#installation-summary", "#command-preview", "#dialog"):
                panel = app.query_one(selector)
                assert panel.region.x == 2
                assert panel.region.width == expected_width
                assert panel.styles.border_top[0] == "solid"

    asyncio.run(run_scenario())


def test_textual_prompt_actions_show_back_and_quit_without_cancel() -> None:
    if importlib.util.find_spec("textual") is None:
        pytest.skip("Textual is not installed")

    from textual.widgets import Button

    app = make_textual_select_app(
        "What would you like to do?",
        [
            {"name": "Install", "value": "install"},
            {"name": "Uninstall", "value": "uninstall"},
        ],
    )

    async def run_scenario() -> None:
        async with app.run_test() as pilot:
            assert not app.query("#cancel")
            assert str(app.query_one("#continue", Button).label) == "Continue"
            assert str(app.query_one("#back", Button).label) == "Back (ESC)"
            assert str(app.query_one("#quit", Button).label) == "Quit Ctrl+Q"
            await pilot.press("ctrl+q")

    asyncio.run(run_scenario())

    assert app.return_value is None


def test_textual_command_preview_keeps_flow_background_without_command() -> None:
    if importlib.util.find_spec("textual") is None:
        pytest.skip("Textual is not installed")

    from textual.widgets import Static

    install_app = make_textual_version_app(
        "GitHub repository URL",
        "",
        [],
        command_preview_builder=lambda value: (
            f"agent-skill-installer --no-ui install --github-url {value}"
            if str(value).strip()
            else None
        ),
        summary="Installing agent-workflow-dsl from GitHub",
    )
    uninstall_app = make_textual_checkbox_app(
        "Select agents",
        target_choices(),
        command_preview_builder=lambda _selected: None,
        summary="Uninstalling agent-workflow-dsl",
    )

    async def run_scenario() -> None:
        async with install_app.run_test():
            panel = install_app.query_one("#command-preview")
            command = install_app.query_one("#command-preview-command", Static)
            assert panel.has_class("install-preview")
            assert not panel.has_class("uninstall-preview")
            assert str(command.content) == DEFAULT_EMPTY_COMMAND_PREVIEW_MESSAGE

        async with uninstall_app.run_test():
            panel = uninstall_app.query_one("#command-preview")
            command = uninstall_app.query_one("#command-preview-command", Static)
            assert panel.has_class("uninstall-preview")
            assert not panel.has_class("install-preview")
            assert str(command.content) == "Choose at least one target."

    asyncio.run(run_scenario())


def test_textual_checkbox_all_mode_and_empty_selection() -> None:
    if importlib.util.find_spec("textual") is None:
        pytest.skip("Textual is not installed")

    from textual.widgets import Button, SelectionList, Static

    def preview(selected: object) -> str | None:
        selected_values = list(selected) if isinstance(selected, list) else []
        if not selected_values:
            return None
        return "example-agent-skill --no-ui uninstall --agent all --scope dir --repo"

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


def test_textual_checkbox_can_require_space_before_accepting_empty_selection() -> None:
    if importlib.util.find_spec("textual") is None:
        pytest.skip("Textual is not installed")

    from textual.widgets import SelectionList, Static

    app = make_textual_checkbox_app(
        "Select source skills",
        [
            {
                "name": "All source skills",
                "value": "__agent_skill_installer_all_source_skills__",
                "kind": "all",
            },
            {"name": "alpha", "value": "alpha", "kind": "skill"},
        ],
        command_preview_builder=lambda selected: (
            "agent-skill-installer --no-ui install --all-src-skills"
            if list(selected)
            else None
        ),
        empty_message="Choose at least one source skill.",
        accept_highlighted_on_empty=False,
    )

    async def run_scenario() -> None:
        async with app.run_test() as pilot:
            choices = app.query_one("#choices", SelectionList)
            command = app.query_one("#command-preview-command", Static)
            assert str(command.content) == "Choose at least one source skill."

            await pilot.press("enter")
            await pilot.pause()
            assert app.return_value is None
            assert list(choices.selected) == []
            assert str(app.query_one("#error", Static).content) == (
                "Choose at least one source skill."
            )

            await pilot.press("space")
            await pilot.press("enter")

    asyncio.run(run_scenario())

    assert app.return_value == ["__agent_skill_installer_all_source_skills__"]


def test_textual_pypi_version_input_suggests_versions_and_updates_preview() -> None:
    if importlib.util.find_spec("textual") is None:
        pytest.skip("Textual is not installed")

    from textual.widgets import Input, OptionList, Static

    def preview(version: object) -> str:
        return (
            "example-agent-skill --no-ui install "
            f"--pypi-version {version} --agent all --scope dir --repo"
        )

    app = make_textual_version_app(
        "PyPI package version",
        "2.0.0",
        [
            {"name": "2.0.0", "value": "2.0.0"},
            {"name": "1.0.0", "value": "1.0.0"},
        ],
        command_preview_builder=preview,
    )

    async def run_scenario() -> None:
        async with app.run_test() as pilot:
            assert not app.query("#version-select")
            version_input = app.query_one("#version", Input)
            options = app.query_one("#version-options", OptionList)
            assert version_input.value == "2.0.0"
            assert tuple(version_input.selection) == (len("2.0.0"), len("2.0.0"))
            assert options.option_count == 2

            await pilot.press("down")
            assert options.has_focus
            await pilot.press("up")
            assert version_input.has_focus
            assert tuple(version_input.selection) == (len("2.0.0"), len("2.0.0"))

            version_input.value = "1"
            await pilot.pause()

            command = app.query_one("#command-preview-command", Static)
            assert str(command.content) == preview("1")
            assert options.option_count == 1
            assert options.display is True
            assert str(options.get_option_at_index(0).prompt) == "1.0.0"

            version_input.value = "missing"
            await pilot.pause()

            assert str(command.content) == preview("missing")
            assert options.option_count == 0
            assert options.display is True

            version_input.value = "1"
            await pilot.pause()

            await pilot.press("down")
            assert options.has_focus
            await pilot.press("enter")

    asyncio.run(run_scenario())

    assert app.return_value == "1.0.0"


def test_textual_version_validator_blocks_invalid_value() -> None:
    if importlib.util.find_spec("textual") is None:
        pytest.skip("Textual is not installed")

    from textual.widgets import Input, Static

    def validate(value: str) -> str | None:
        if value == "missing-package":
            return "PyPI package not found: missing-package"
        return None

    app = make_textual_version_app(
        "PyPI package name",
        "",
        [],
        command_preview_builder=lambda value: (
            f"agent-skill-installer --no-ui install --pypi-package {value}"
            if str(value).strip()
            else None
        ),
        validator=validate,
    )

    async def run_scenario() -> None:
        async with app.run_test() as pilot:
            package_input = app.query_one("#version", Input)
            package_input.value = "missing-package"
            await pilot.press("enter")
            await pilot.pause()

            assert app.return_value is None
            assert str(app.query_one("#error", Static).content) == (
                "PyPI package not found: missing-package"
            )

            package_input.value = "valid-package"
            await pilot.press("enter")

    asyncio.run(run_scenario())

    assert app.return_value == "valid-package"


class ScriptedPrompter:
    def __init__(self, *answers) -> None:
        self.answers = iter(answers)
        self.calls: list[tuple[str, str]] = []
        self.choices: list[list[dict[str, object]]] = []
        self.previews: list[str | None] = []
        self.summaries: list[str | None] = []
        self.checkbox_defaults: list[list[str] | None] = []
        self.checkbox_empty_messages: list[str] = []
        self.checkbox_accept_highlighted_on_empty: list[bool] = []
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
        self.choices.append(list(choices))
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
        summary_builder=None,
        default_values=None,
        empty_message="Choose at least one target.",
        accept_highlighted_on_empty=True,
        submit_label="Continue",
    ):
        self.calls.append(("checkbox", message))
        self.choices.append(list(choices))
        self.submit_labels.append(submit_label)
        self.checkbox_defaults.append(
            list(default_values) if default_values is not None else None
        )
        self.checkbox_empty_messages.append(empty_message)
        self.checkbox_accept_highlighted_on_empty.append(accept_highlighted_on_empty)
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

    def path(
        self,
        message,
        default,
        *,
        command_preview=None,
        command_preview_builder=None,
        summary=None,
        summary_builder=None,
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
        self.summaries.append(
            summary_builder(path)
            if summary_builder is not None
            else summary
        )
        return path

    def text(
        self,
        message,
        default,
        *,
        command_preview=None,
        command_preview_builder=None,
        summary=None,
        summary_builder=None,
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
        self.summaries.append(
            summary_builder(value)
            if summary_builder is not None
            else summary
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
        summary=None,
        summary_builder=None,
        validator=None,
        submit_label="Continue",
    ):
        self.calls.append(("version", message))
        self.choices.append(list(choices))
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
        self.summaries.append(
            summary_builder(value)
            if summary_builder is not None
            else summary
        )
        if validator is not None:
            error = validator(value)
            if error:
                raise InstallerError(error)
        return value


def test_installation_option_choices_offer_install_locations(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "my-repo")
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
            "name": "Agent config directory",
            "description": "\n".join(
                [
                    "Install in agent config directory",
                    str(home / ".codex"),
                    str(home / ".claude"),
                ]
            ),
            "value": "global",
            "kind": "scope",
        },
        {
            "name": "Current directory (repository)",
            "description": f"Directory: {repo} (repository: {repo})",
            "value": "repo",
            "kind": "scope",
        },
        {
            "name": "Directory",
            "description": (
                "Install files into an explicit directory; automatic discovery "
                "is not implied"
            ),
            "value": "specific",
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
            "example-agent-skill --no-ui install --agent all --scope dir --repo"
            )
        == "install-preview"
    )
    assert (
            command_preview_classes(
            "example-agent-skill --no-ui uninstall --agent all --scope dir --repo"
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

        def has_class(self, class_name: str) -> bool:
            return class_name in self.classes

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
        "example-agent-skill --no-ui uninstall --agent all --scope dir --repo",
        object,
    )

    assert app.command.text == (
        "example-agent-skill --no-ui uninstall --agent all --scope dir --repo"
    )
    assert app.panel.classes == {"uninstall-preview"}

    update_command_preview_display(
        app,
        None,
        object,
        empty_message="Choose an install source.",
        preview_class="install-preview",
    )

    assert app.command.text == "Choose an install source."
    assert app.copy_button.disabled is True
    assert app.panel.classes == {"install-preview"}


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
    assert install_source_choices(project)[-1]["value"] == "github"


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
        targets=[("codex", "global", False), ("codex", "dir", True)],
        repo=repo,
        codex_home=codex_home,
    )

    assert command == (
        "example-agent-skill --no-ui install --agent codex --scope global "
        f"--codex-home {shlex.quote(str(codex_home))}\n"
        "example-agent-skill --no-ui install --agent codex --scope dir --repo --target-dir "
        f"{shlex.quote(str(repo))}"
    )


def test_build_no_ui_command_for_mixed_dir_repo_target_targets(
    tmp_path: Path,
) -> None:
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

    command = build_no_ui_command(
        project,
        args,
        targets=[("codex", "dir", True), ("codex", "dir", False)],
        repo=repo,
    )

    assert command == (
        "example-agent-skill --no-ui install --agent codex --scope dir --repo --target-dir "
        f"{shlex.quote(str(repo))}\n"
        "example-agent-skill --no-ui install --agent codex --scope dir --target-dir "
        f"{shlex.quote(str(repo))}"
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
    assert args.targets == [("codex", "global", False)]
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


def test_complete_with_ui_selects_specific_directory(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "chosen-repo")
    args = Namespace(
        command=None,
        agent=None,
        scope=None,
        repo=None,
        codex_home=None,
        claude_home=None,
        editable=False,
    )
    prompter = ScriptedPrompter("install", ["codex"], "specific", repo)

    complete_with_ui(project, args, prompter)

    assert args.command == "install"
    assert args.targets == [("codex", "dir", False)]
    assert args.repo_target is False
    assert args.repo == repo
    assert prompter.calls == [
        ("select", "What would you like to do with example-agent-skill?"),
        ("checkbox", "Select agents for example-agent-skill"),
        ("select", "Install location for example-agent-skill"),
        ("path", "Directory path"),
    ]
    assert prompter.previews == [
        "example-agent-skill --no-ui install --agent all --scope global",
        "example-agent-skill --no-ui install --agent codex --scope global",
        None,
        "example-agent-skill --no-ui install --agent codex --scope dir --target-dir "
        f"{shlex.quote(str(repo))}",
    ]
    assert prompter.summaries[3] == f"Directory: {repo} (repository: {repo})"
    assert prompter.submit_labels == ["Continue", "Continue", "Install", "Install"]


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
                "name": "PyPI wheel (requires network; pip resolves compatible package)",
                "value": "pypi",
            },
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
    prompter = ScriptedPrompter("install", "pypi", ["codex"], "repo")

    complete_with_ui(project, args, prompter)

    assert args.command == "install"
    assert args.editable is False
    assert args.pypi is True
    assert args.pypi_version is None
    assert args.targets == [("codex", "dir", True)]
    assert args.repo == repo
    assert prompter.previews == [
        "example-agent-skill --no-ui install --agent all --scope global",
        "example-agent-skill --no-ui install --pypi --agent all --scope global",
        "example-agent-skill --no-ui install --pypi --agent codex --scope global",
        "example-agent-skill --no-ui install --pypi "
        f"--agent codex --scope dir --repo --target-dir {shlex.quote(str(repo))}",
    ]


def test_complete_with_ui_selects_github_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = make_project(tmp_path)
    monkeypatch.setattr(
        "agent_skill_installer.cli.install_source_choices",
        lambda _project: [
            {"name": "Bundled skill copy", "value": "copy"},
            {"name": "GitHub repository URL", "value": "github"},
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
    prompter = ScriptedPrompter(
        "install",
        "github",
        "https://github.com/example/demo",
        ["codex"],
        "repo",
    )

    complete_with_ui(project, args, prompter)

    assert args.command == "install"
    assert args.editable is False
    assert args.pypi_version is None
    assert args.github_url == "https://github.com/example/demo"
    assert args.targets == [("codex", "dir", True)]
    assert args.repo == repo
    assert prompter.calls == [
        ("select", "What would you like to do with example-agent-skill?"),
        ("select", "Install source for example-agent-skill"),
        ("text", "GitHub repository URL"),
        ("checkbox", "Select agents for example-agent-skill"),
        ("select", "Install location for example-agent-skill"),
    ]
    assert prompter.previews == [
        "example-agent-skill --no-ui install --agent all --scope global",
        "example-agent-skill --no-ui install "
        "--github-url https://github.com/OWNER/example-agent-skill "
        "--agent all --scope global",
        "example-agent-skill --no-ui install "
        "--github-url https://github.com/example/demo --agent all --scope global",
        "example-agent-skill --no-ui install "
        "--github-url https://github.com/example/demo --agent codex --scope global",
        "example-agent-skill --no-ui install "
        "--github-url https://github.com/example/demo --agent codex --scope dir --repo --target-dir "
        f"{shlex.quote(str(repo))}",
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
    assert args.targets == [("codex", "global", False)]
    assert prompter.calls == [
        ("select", "What would you like to do with example-agent-skill?"),
        ("checkbox", "Select agents for example-agent-skill"),
        ("select", "What would you like to do with example-agent-skill?"),
        ("checkbox", "Select agents for example-agent-skill"),
        ("checkbox", "Select example-agent-skill installations"),
    ]


def test_package_metadata_exposes_generic_console_app() -> None:
    pyproject = Path(__file__).resolve().parents[2].joinpath("pyproject.toml").read_text()

    assert 'name = "agent-skill-installer"' in pyproject
    assert f'version = "{__version__}"' in pyproject
    assert "[project.scripts]" in pyproject
    assert 'agent-skill-installer = "agent_skill_installer.__main__:main"' in pyproject


def test_python_module_entry_point_shows_generic_help() -> None:
    env = os.environ.copy()
    src_dir = Path(__file__).resolve().parents[2] / "src"
    env["PYTHONPATH"] = str(src_dir)
    completed = subprocess.run(
        [sys.executable, "-m", "agent_skill_installer", "--help"],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert completed.returncode == 0
    assert "Install or uninstall agent skills from generic sources." in completed.stdout

    install_help = subprocess.run(
        [sys.executable, "-m", "agent_skill_installer", "install", "--help"],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert install_help.returncode == 0
    assert "--target-dir PATH" in install_help.stdout
    assert "--target-dir REPO" not in install_help.stdout
    assert "--target-type" not in install_help.stdout
    assert "--repo" in install_help.stdout


def test_generic_console_bare_command_uses_interactive_ui(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    source = make_skill(tmp_path / "skill-source")
    repo = make_repo(tmp_path / "repo")

    def complete(args: Namespace) -> None:
        args.command = "install"
        args.skill_path = source
        args.skill_name = "example-agent-skill"
        args.agent = "codex"
        args.scope = "dir"
        args.repo_target = True
        args.repo = repo

    monkeypatch.setattr("agent_skill_installer.__main__.running_on_tty", lambda: True)
    monkeypatch.setattr("agent_skill_installer.__main__.complete_with_ui", complete)

    exit_code = generic_main([])
    output = capsys.readouterr()

    assert exit_code == 0
    assert (
        "Installed example-agent-skill local (editable) to Codex repo:"
        in output.out
    )
    skill_dir = repo / ".codex" / "skills" / "example-agent-skill"
    assert skill_dir.is_symlink()
    assert skill_dir.resolve() == source
    assert (skill_dir / "SKILL.md").read_text() == "example skill\n"


def test_generic_install_source_choices_keep_remote_first_without_local_skill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    choices = generic_install_source_choices()

    assert [choice["value"] for choice in choices] == [
        "pypi",
        "wheel",
        "github",
        "local",
    ]


@pytest.mark.parametrize("layout", ["root", "nested"])
def test_generic_install_source_choices_prioritize_local_development_source(
    tmp_path: Path,
    monkeypatch,
    layout: str,
) -> None:
    source = tmp_path if layout == "root" else tmp_path / "skill"
    make_skill(source)
    monkeypatch.chdir(tmp_path)

    choices = generic_install_source_choices()

    assert choices[0] == {
        "name": "Local repo or skill directory (development mode)",
        "value": "local",
    }


def test_generic_complete_with_ui_preview_uses_detected_local_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = make_skill(tmp_path / "skill")
    monkeypatch.chdir(tmp_path)
    repo = make_repo(tmp_path / "repo")
    args = Namespace(
        command=None,
        agent=None,
        scope=None,
        repo=repo,
        codex_home=None,
        claude_home=None,
        home=None,
        no_ui=False,
        verbose=False,
    )
    prompter = ScriptedPrompter(
        "install",
        "local",
        source,
        "editable",
        ["codex"],
        "global",
    )

    complete_generic_with_ui(args, prompter)

    assert prompter.choices[1][0]["value"] == "local"
    assert prompter.previews[0] == (
        "agent-skill-installer --no-ui install "
        "--skill-path skill --agent all --scope global"
    )
    assert prompter.summaries[0] == "Installing a skill"


def test_generic_complete_with_ui_selects_local_install_source(
    tmp_path: Path,
) -> None:
    source = make_skill(tmp_path / "skill-source")
    repo = make_repo(tmp_path / "repo")
    args = Namespace(
        command=None,
        agent=None,
        scope=None,
        repo=repo,
        codex_home=None,
        claude_home=None,
        home=None,
        no_ui=False,
        verbose=False,
    )
    prompter = ScriptedPrompter(
        "install",
        "local",
        source,
        "editable",
        ["codex"],
        "repo",
    )

    complete_generic_with_ui(args, prompter)

    assert args.command == "install"
    assert args.skill_path == source
    assert args.editable is True
    assert args.agent == "codex"
    assert args.scope == "dir"
    assert args.repo_target is True
    assert args.repo == repo
    assert prompter.calls == [
        ("select", "What would you like to do?"),
        ("select", "Install source"),
        ("path", "Local repo or skill directory"),
        ("select", "Local install mode"),
        ("checkbox", "Select agents"),
        ("select", "Install location"),
    ]
    assert prompter.previews[0] is None
    assert prompter.previews[1] == (
        "agent-skill-installer --no-ui install "
        "--skill-path skill --agent all --scope global"
    )
    assert prompter.previews[2] == (
        "agent-skill-installer --no-ui install "
        f"--skill-path {shlex.quote(str(source))} --agent all --scope global"
    )
    assert prompter.previews[3] == (
        "agent-skill-installer --no-ui install "
        f"--skill-path {shlex.quote(str(source))} --editable "
        "--agent all --scope global"
    )
    checkout_name = Path.cwd().name
    assert prompter.summaries[1] == (
        f"Installing {checkout_name} from local path {Path.cwd()}"
    )
    assert prompter.summaries[2] == (
        f"Installing skill-source from local path {source}"
    )
    assert prompter.summaries[3] == (
        f"Installing skill-source as editable symlink from {source}"
    )
    assert prompter.submit_labels[-1] == "Install"


def test_generic_complete_with_ui_selects_local_copy_install_source(
    tmp_path: Path,
) -> None:
    source = make_skill(tmp_path / "skill-source")
    repo = make_repo(tmp_path / "repo")
    args = Namespace(
        command=None,
        agent=None,
        scope=None,
        repo=repo,
        codex_home=None,
        claude_home=None,
        home=None,
        no_ui=False,
        verbose=False,
    )
    prompter = ScriptedPrompter("install", "local", source, "copy", ["codex"], "repo")

    complete_generic_with_ui(args, prompter)

    assert args.editable is False
    assert prompter.previews[3] == (
        "agent-skill-installer --no-ui install "
        f"--skill-path {shlex.quote(str(source))} --copy --agent all --scope global"
    )
    assert prompter.summaries[3] == (
        f"Installing a copy of skill-source from {source}"
    )

    results = run_generic_install(args)
    assert [result.install_mode for result in results] == ["copy"]
    skill_dir = repo / ".codex" / "skills" / "skill-source"
    assert skill_dir.is_dir()
    assert not skill_dir.is_symlink()
    assert (skill_dir / "SKILL.md").read_text() == "example skill\n"


def test_generic_complete_with_ui_selects_specific_directory(
    tmp_path: Path,
) -> None:
    source = make_skill(tmp_path / "skill-source")
    repo = make_repo(tmp_path / "chosen-repo")
    args = Namespace(
        command=None,
        agent=None,
        scope=None,
        repo=None,
        codex_home=None,
        claude_home=None,
        home=None,
        no_ui=False,
        verbose=False,
    )
    prompter = ScriptedPrompter(
        "install",
        "local",
        source,
        "editable",
        ["codex"],
        "specific",
        repo,
    )

    complete_generic_with_ui(args, prompter)

    assert args.command == "install"
    assert args.skill_path == source
    assert args.editable is True
    assert args.agent == "codex"
    assert args.scope == "dir"
    assert args.repo_target is False
    assert args.repo == repo
    assert prompter.calls == [
        ("select", "What would you like to do?"),
        ("select", "Install source"),
        ("path", "Local repo or skill directory"),
        ("select", "Local install mode"),
        ("checkbox", "Select agents"),
        ("select", "Install location"),
        ("path", "Directory path"),
    ]
    assert prompter.previews[5] is None
    assert prompter.previews[6] == (
        "agent-skill-installer --no-ui install "
        f"--skill-path {shlex.quote(str(source))} --editable --agent codex "
        f"--scope dir --target-dir {shlex.quote(str(repo))}"
    )
    assert prompter.summaries[6] == f"Directory: {repo} (repository: {repo})"
    assert prompter.submit_labels[-2:] == ["Install", "Install"]


def test_generic_install_source_selection_summary_excludes_target(
    tmp_path: Path,
) -> None:
    source = make_skill(tmp_path / "skill")
    args = Namespace(
        command="install",
        agent="all",
        scope="global",
        repo=None,
        codex_home=None,
        claude_home=None,
        home=None,
        no_ui=False,
        verbose=False,
    )
    prompter = ScriptedPrompter("local", source, "copy")

    complete_generic_with_ui(args, prompter)

    assert prompter.calls == [
        ("select", "Install source"),
        ("path", "Local repo or skill directory"),
        ("select", "Local install mode"),
    ]
    checkout_name = Path.cwd().name
    assert prompter.summaries[0] == (
        f"Installing {checkout_name} from local path {Path.cwd()}"
    )
    assert "Into" not in prompter.summaries[0]
    assert prompter.summaries[1] == (
        f"Installing skill from local path {source}\n"
        "Into Codex Global, Claude Global"
    )
    assert prompter.summaries[2] == (
        f"Installing a copy of skill from {source}\n"
        "Into Codex Global, Claude Global"
    )


def test_generic_complete_with_ui_escape_goes_back_one_screen(
    tmp_path: Path,
) -> None:
    source = make_skill(tmp_path / "skill-source")
    repo = make_repo(tmp_path / "repo")
    args = Namespace(
        command=None,
        agent=None,
        scope=None,
        repo=repo,
        codex_home=None,
        claude_home=None,
        home=None,
        no_ui=False,
        verbose=False,
    )
    prompter = ScriptedPrompter(
        "install",
        "local",
        source,
        BackRequested(),
        source,
        "editable",
        ["codex"],
        "global",
    )

    complete_generic_with_ui(args, prompter)

    assert args.command == "install"
    assert args.skill_path == source
    assert args.agent == "codex"
    assert args.scope == "global"
    assert prompter.calls == [
        ("select", "What would you like to do?"),
        ("select", "Install source"),
        ("path", "Local repo or skill directory"),
        ("select", "Local install mode"),
        ("path", "Local repo or skill directory"),
        ("select", "Local install mode"),
        ("checkbox", "Select agents"),
        ("select", "Install location"),
    ]


def test_generic_complete_with_ui_escape_on_first_screen_exits() -> None:
    args = Namespace(
        command=None,
        agent=None,
        scope=None,
        repo=None,
        codex_home=None,
        claude_home=None,
        home=None,
        no_ui=False,
        verbose=False,
    )
    prompter = ScriptedPrompter(BackRequested())

    with pytest.raises(KeyboardInterrupt):
        complete_generic_with_ui(args, prompter)


def test_recent_installs_keep_last_ten_and_ignore_load_errors(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    for index in range(12):
        remember_recent_pypi_package(f"skill-{index}", home=home)
    remember_recent_pypi_package("skill-5", home=home)
    for index in range(12):
        remember_recent_github_url(
            f"https://github.com/example/skill-{index}",
            home=home,
        )
    remember_recent_github_url("https://github.com/example/skill-5", home=home)

    assert load_recent_pypi_packages(home) == [
        "skill-5",
        "skill-11",
        "skill-10",
        "skill-9",
        "skill-8",
        "skill-7",
        "skill-6",
        "skill-4",
        "skill-3",
        "skill-2",
    ]
    assert load_recent_github_urls(home) == [
        "https://github.com/example/skill-5",
        "https://github.com/example/skill-11",
        "https://github.com/example/skill-10",
        "https://github.com/example/skill-9",
        "https://github.com/example/skill-8",
        "https://github.com/example/skill-7",
        "https://github.com/example/skill-6",
        "https://github.com/example/skill-4",
        "https://github.com/example/skill-3",
        "https://github.com/example/skill-2",
    ]

    recent_installations_path(home).write_text("{")

    assert load_recent_pypi_packages(home) == []
    assert load_recent_github_urls(home) == []


def test_generic_complete_with_ui_accepts_pypi_requirement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    wheel = make_skill_wheel(tmp_path / "example.whl", make_project(tmp_path))
    requests: list[str] = []
    patch_generic_pypi_build(monkeypatch, wheel, "9.9.9", requests)
    repo = make_repo(tmp_path / "repo")
    home = tmp_path / "home"
    remember_recent_pypi_package("recent-agent-skill", home=home)
    args = Namespace(
        command=None,
        agent=None,
        scope=None,
        repo=repo,
        codex_home=None,
        claude_home=None,
        home=home,
        no_ui=False,
        verbose=False,
    )
    prompter = ScriptedPrompter(
        "install",
        "pypi",
        "example-agent-skill==9.9.9",
        ["codex"],
        "global",
    )

    complete_generic_with_ui(args, prompter)

    assert args.command == "install"
    assert args.pypi_package == "example-agent-skill==9.9.9"
    assert args.pypi_version is None
    assert requests == ["example-agent-skill==9.9.9"]
    assert getattr(args, "_validated_pypi_wheel_path").is_file()
    assert load_recent_pypi_packages(home) == ["recent-agent-skill"]
    results = run_generic_install(args)
    assert [result.status for result in results] == ["installed"]
    assert requests == ["example-agent-skill==9.9.9"]
    assert load_recent_pypi_packages(home) == [
        "example-agent-skill",
        "recent-agent-skill",
    ]
    assert (
        home / ".codex" / "skills" / "example-agent-skill" / "SKILL.md"
    ).read_text() == "wheel skill\n"
    assert prompter.calls == [
        ("select", "What would you like to do?"),
        ("select", "Install source"),
        ("version", "PyPI package name"),
        ("checkbox", "Select agents"),
        ("select", "Install location"),
    ]
    assert prompter.choices[2] == [
        {"name": "recent-agent-skill", "value": "recent-agent-skill"},
    ]
    assert prompter.previews[0] is None
    assert prompter.previews[1] is None
    assert prompter.summaries == [
        "Installing a skill",
        "Installing from PyPI",
        "Installing PyPI package example-agent-skill==9.9.9",
        "Installing PyPI package example-agent-skill==9.9.9\nInto Codex Global",
        "Installing PyPI package example-agent-skill==9.9.9\nInto Codex Global",
    ]


def test_generic_complete_with_ui_selects_wheel_file(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    wheel = make_skill_wheel(
        tmp_path / "example_agent_skill-1.2.3-py3-none-any.whl",
        project,
    )
    repo = make_repo(tmp_path / "repo")
    args = Namespace(
        command=None,
        agent=None,
        scope=None,
        repo=repo,
        codex_home=None,
        claude_home=None,
        home=None,
        no_ui=False,
        verbose=False,
    )
    prompter = ScriptedPrompter("install", "wheel", wheel, ["codex"], "repo")

    complete_generic_with_ui(args, prompter)

    assert args.command == "install"
    assert args.wheel_file == wheel
    assert args.agent == "codex"
    assert args.scope == "dir"
    assert args.repo_target is True
    assert getattr(args, "_validated_wheel_project").skill_name == project.skill_name
    assert prompter.calls == [
        ("select", "What would you like to do?"),
        ("select", "Install source"),
        ("path", "Wheel file"),
        ("checkbox", "Select agents"),
        ("select", "Install location"),
    ]
    assert prompter.previews[1] is None
    assert prompter.previews[2] == (
        "agent-skill-installer --no-ui install "
        f"--wheel-file {shlex.quote(str(wheel))} --agent all --scope global"
    )
    assert prompter.summaries[1] == "Installing from a local wheel file"
    assert prompter.summaries[2] == f"Installing from local wheel file {wheel}"

    results = run_generic_install(args)
    assert [result.install_mode for result in results] == ["wheel"]
    assert (
        repo / ".codex" / "skills" / project.skill_name / "SKILL.md"
    ).read_text() == "wheel skill\n"


def test_generic_complete_with_ui_requires_pypi_package_without_recent_default(
    tmp_path: Path,
) -> None:
    args = Namespace(
        command=None,
        agent=None,
        scope=None,
        repo=None,
        codex_home=None,
        claude_home=None,
        home=tmp_path / "home",
        no_ui=False,
        verbose=False,
    )
    prompter = ScriptedPrompter("install", "pypi", "")

    with pytest.raises(InstallerError, match="PyPI package name must not be empty"):
        complete_generic_with_ui(args, prompter)

    assert not hasattr(args, "pypi_package") or args.pypi_package is None
    assert prompter.previews == [None, None, None]
    assert prompter.summaries == [
        "Installing a skill",
        "Installing from PyPI",
        "Installing from PyPI",
    ]


def test_generic_complete_with_ui_uses_entered_pypi_package(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = SkillProject(
        package_name="agent-workflow-dsl",
        import_name="agent_workflow_dsl",
        version="0.0.1",
        skill_name="agent-workflow-dsl",
        description="AWD skill.",
        bundled_skill_source=make_skill(tmp_path / "awd-skill"),
    )
    wheel = make_skill_wheel(tmp_path / "awd.whl", project)
    requests: list[str] = []
    patch_generic_pypi_build(monkeypatch, wheel, "0.0.1", requests)
    args = Namespace(
        command=None,
        agent=None,
        scope=None,
        repo=None,
        codex_home=None,
        claude_home=None,
        home=tmp_path / "home",
        no_ui=False,
        verbose=False,
    )
    prompter = ScriptedPrompter(
        "install",
        "pypi",
        "agent-workflow-dsl",
        ["codex"],
        "global",
    )

    complete_generic_with_ui(args, prompter)

    assert args.pypi_package == "agent-workflow-dsl"
    assert args.pypi_version is None
    assert requests == ["agent-workflow-dsl"]
    assert prompter.previews[2] == (
        "agent-skill-installer --no-ui install --pypi-package agent-workflow-dsl "
        "--agent all --scope global"
    )


def test_generic_complete_with_ui_selects_github_url_from_dropdown(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = make_repo(tmp_path / "repo")
    home = tmp_path / "home"
    archive = make_github_archive(tmp_path / "github.zip")
    downloads: list[str] = []

    def fake_download(source, _download_dir: Path) -> Path:
        downloads.append(source.url)
        return archive

    monkeypatch.setattr(
        "agent_skill_installer.__main__.download_github_archive",
        fake_download,
    )
    remember_recent_github_url("https://github.com/example/recent-skill", home=home)
    args = Namespace(
        command=None,
        agent=None,
        scope=None,
        repo=repo,
        codex_home=None,
        claude_home=None,
        home=home,
        no_ui=False,
        verbose=False,
    )
    prompter = ScriptedPrompter(
        "install",
        "github",
        "https://github.com/example/recent-skill",
        ["codex"],
        "global",
    )

    complete_generic_with_ui(args, prompter)

    assert args.command == "install"
    assert args.github_url == "https://github.com/example/recent-skill"
    assert downloads == ["https://github.com/example/recent-skill"]
    assert getattr(args, "_validated_github_archive_path").is_file()
    results = run_generic_install(args)
    assert [result.status for result in results] == ["installed"]
    assert downloads == ["https://github.com/example/recent-skill"]
    assert args.agent == "codex"
    assert args.scope == "global"
    assert prompter.calls == [
        ("select", "What would you like to do?"),
        ("select", "Install source"),
        ("version", "GitHub repository URL"),
        ("checkbox", "Select agents"),
        ("select", "Install location"),
    ]
    assert prompter.choices[2] == [
        {
            "name": "https://github.com/example/recent-skill",
            "value": "https://github.com/example/recent-skill",
        },
    ]
    assert prompter.previews[0] is None
    assert prompter.previews[1] is None
    assert prompter.previews[2] == (
        "agent-skill-installer --no-ui install "
        "--github-url https://github.com/example/recent-skill "
        "--agent all --scope global"
    )
    assert prompter.summaries[2] == (
        "Installing recent-skill from GitHub "
        "https://github.com/example/recent-skill at main"
    )


def test_generic_complete_with_ui_validates_pypi_package_contains_skill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    wheel = tmp_path / "plain-package.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("plain_package/__init__.py", "")
    patch_generic_pypi_build(monkeypatch, wheel, "1.0.0")
    args = Namespace(
        command=None,
        agent=None,
        scope=None,
        repo=None,
        codex_home=None,
        claude_home=None,
        home=None,
        no_ui=False,
        verbose=False,
    )
    prompter = ScriptedPrompter(
        "install",
        "pypi",
        "plain-package",
        ["codex"],
        "global",
    )

    with pytest.raises(InstallerError, match="bundled SKILL.md"):
        complete_generic_with_ui(args, prompter)

    assert prompter.calls == [
        ("select", "What would you like to do?"),
        ("select", "Install source"),
        ("version", "PyPI package name"),
    ]


def test_generic_complete_with_ui_validates_github_url_contains_skill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive_path = tmp_path / "github.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("demo-main/README.md", "no skill here\n")
    monkeypatch.setattr(
        "agent_skill_installer.__main__.download_github_archive",
        lambda _source, _download_dir: archive_path,
    )
    args = Namespace(
        command=None,
        agent=None,
        scope=None,
        repo=None,
        codex_home=None,
        claude_home=None,
        home=tmp_path / "home",
        no_ui=False,
        verbose=False,
    )
    prompter = ScriptedPrompter(
        "install",
        "github",
        "https://github.com/example/demo",
        ["codex"],
        "global",
    )

    with pytest.raises(InstallerError, match="SKILL.md"):
        complete_generic_with_ui(args, prompter)

    assert load_recent_github_urls(args.home) == []
    assert prompter.calls == [
        ("select", "What would you like to do?"),
        ("select", "Install source"),
        ("version", "GitHub repository URL"),
    ]


def test_generic_complete_with_ui_stops_when_pypi_package_is_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def missing_package(
        *,
        package: str,
        wheel_dir: Path,
    ) -> tuple[Path, str]:
        assert package == "missing-package"
        raise InstallerError(
            "pip wheel failed for missing-package: no matching distribution found"
        )

    monkeypatch.setattr(
        "agent_skill_installer.__main__.build_pypi_wheel",
        missing_package,
    )
    args = Namespace(
        command=None,
        agent=None,
        scope=None,
        repo=None,
        codex_home=None,
        claude_home=None,
        home=None,
        no_ui=False,
        verbose=False,
    )
    prompter = ScriptedPrompter("install", "pypi", "missing-package")

    with pytest.raises(
        InstallerError,
        match="pip wheel failed for missing-package",
    ):
        complete_generic_with_ui(args, prompter)

    assert prompter.calls == [
        ("select", "What would you like to do?"),
        ("select", "Install source"),
        ("version", "PyPI package name"),
    ]


def test_generic_complete_with_ui_selects_installed_uninstall_target(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    home = tmp_path / "home"
    Installer(project).install(["all"], "global", home=home)
    args = Namespace(
        command=None,
        skill_name=None,
        agent=None,
        scope=None,
        repo=None,
        codex_home=None,
        claude_home=None,
        home=home,
        no_ui=False,
        verbose=False,
    )
    prompter = ScriptedPrompter("uninstall", "0", ["all"])

    complete_generic_with_ui(args, prompter)

    assert args.command == "uninstall"
    assert args.skill_name == project.skill_name
    assert args.package_name == project.package_name
    assert args.agent == "all"
    assert args.scope == "global"
    assert len(args.uninstall_statuses) == 2
    assert prompter.calls == [
        ("select", "What would you like to do?"),
        ("select", "Skills installed by Agent Skill Installer"),
        ("checkbox", f"Select targets to uninstall for {project.skill_name}"),
    ]
    assert prompter.previews[0] is None
    assert prompter.summaries[0] == "Uninstalling a skill"
    assert prompter.previews[1] == (
        "agent-skill-installer --no-ui uninstall "
        f"--skill-name {project.skill_name} --agent all --scope global"
    )
    assert prompter.summaries[1] == f"Uninstalling {project.skill_name}"
    assert prompter.choices[1] == [
        {
            "name": project.skill_name,
            "description": "2 installed targets\nPackage: example-agent-skill",
            "value": "0",
        }
    ]
    assert prompter.choices[2][0] == {
        "name": project.skill_name,
        "value": f"skill:{project.skill_name}",
        "disabled": True,
        "kind": "group",
    }
    assert prompter.choices[2][1] == {
        "name": "  All installed targets",
        "description": "Uninstall this skill from every listed target.",
        "value": "all",
        "kind": "all",
    }
    assert [
        choice["name"].strip()
        for choice in prompter.choices[2][2:]
    ] == [
        "Claude Code User global - version 1.2.3",
        "Codex User global - version 1.2.3",
    ]
    assert prompter.previews[-1] == (
        "agent-skill-installer --no-ui uninstall "
        f"--skill-name {project.skill_name} --agent all --scope global"
    )
    results = run_generic_uninstall(args)
    assert [result.status for result in results] == ["removed", "removed"]
    assert not (home / ".codex" / "skills" / project.skill_name).exists()
    assert not (home / ".claude" / "skills" / project.skill_name).exists()


def test_generic_uninstall_target_labels_github_ref_as_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = make_repo(tmp_path / "repo")
    home = tmp_path / "home"
    archive = make_github_archive(tmp_path / "github.zip")
    monkeypatch.setattr(
        "agent_skill_installer.__main__.download_github_archive",
        lambda _source, _download_dir: archive,
    )
    assert generic_main(
        [
            "install",
            "--github-url",
            "https://github.com/example/demo",
            "--github-ref",
            "main",
            "--skill-name",
            "demo-skill",
            "--agent",
            "all",
            "--scope",
            "global",
            "--home",
            str(home),
        ]
    ) == 0
    args = Namespace(
        command="uninstall",
        skill_name=None,
        agent=None,
        scope=None,
        repo=repo,
        codex_home=None,
        claude_home=None,
        home=home,
        no_ui=False,
        verbose=False,
    )
    prompter = ScriptedPrompter("0", ["0", "1"])

    complete_generic_with_ui(args, prompter)

    assert [
        choice["name"].strip()
        for choice in prompter.choices[1][2:]
    ] == [
        "Claude Code User global - GitHub ref main",
        "Codex User global - GitHub ref main",
    ]


def test_generic_console_installs_and_uninstalls_local_skill(
    tmp_path: Path,
    capsys,
) -> None:
    source = make_skill(tmp_path / "skill-source")
    repo = make_repo(tmp_path / "repo")

    install_code = generic_main(
        [
            "install",
            "--skill-path",
            str(source),
            "--skill-name",
            "example-agent-skill",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    output = capsys.readouterr()

    assert install_code == 0
    assert (
        "Installed example-agent-skill local (editable) to Codex repo:"
        in output.out
    )
    skill_dir = repo / ".codex" / "skills" / "example-agent-skill"
    assert skill_dir.is_symlink()
    assert skill_dir.resolve() == source
    assert (skill_dir / "SKILL.md").read_text() == "example skill\n"

    uninstall_code = generic_main(
        [
            "uninstall",
            "--skill-name",
            "example-agent-skill",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    output = capsys.readouterr()

    assert uninstall_code == 0
    assert "Removed example-agent-skill local from Codex repo:" in output.out
    assert not (repo / ".codex" / "skills" / "example-agent-skill").exists()


def test_generic_console_repo_alias_still_targets_directory(
    tmp_path: Path,
    capsys,
) -> None:
    source = make_skill(tmp_path / "skill-source")
    repo = make_repo(tmp_path / "repo")

    install_code = generic_main(
        [
            "install",
            "--skill-path",
            str(source),
            "--skill-name",
            "example-agent-skill",
            "--agent",
            "codex",
            "--scope",
            "dir",
            "--repo",
            "--target-dir",
            str(repo),
        ]
    )
    output = capsys.readouterr()

    assert install_code == 0
    assert (
        "Installed example-agent-skill local (editable) to Codex repo:"
        in output.out
    )


def test_generic_console_dir_scope_installs_plain_directory(
    tmp_path: Path,
    capsys,
) -> None:
    source = make_skill(tmp_path / "skill-source")
    target = tmp_path / "plain-directory"
    target.mkdir()

    install_code = generic_main(
        [
            "install",
            "--skill-path",
            str(source),
            "--skill-name",
            "example-agent-skill",
            "--agent",
            "codex",
            "--scope",
            "dir",
            "--target-dir",
            str(target),
        ]
    )
    output = capsys.readouterr()

    assert install_code == 0
    assert (
        "Installed example-agent-skill local (editable) "
        "to Codex directory (plain-directory):"
    ) in output.out
    assert (target / ".codex" / "skills" / "example-agent-skill").is_symlink()


def test_generic_console_can_copy_local_skill(
    tmp_path: Path,
    capsys,
) -> None:
    source = make_skill(tmp_path / "skill-source")
    repo = make_repo(tmp_path / "repo")

    install_code = generic_main(
        [
            "install",
            "--skill-path",
            str(source),
            "--copy",
            "--skill-name",
            "example-agent-skill",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    output = capsys.readouterr()

    assert install_code == 0
    assert "Installed example-agent-skill local to Codex repo:" in output.out
    skill_dir = repo / ".codex" / "skills" / "example-agent-skill"
    assert skill_dir.is_dir()
    assert not skill_dir.is_symlink()
    assert (skill_dir / "SKILL.md").read_text() == "example skill\n"

    (source / "SKILL.md").write_text("edited source\n")
    assert (skill_dir / "SKILL.md").read_text() == "example skill\n"


def test_generic_console_requires_explicit_selection_for_multi_skill_source(
    tmp_path: Path,
    capsys,
) -> None:
    source = make_skill_collection(tmp_path / "skills-root")
    repo = make_repo(tmp_path / "repo")

    exit_code = generic_main(
        [
            "install",
            "--skill-path",
            str(source),
            "--copy",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 2
    assert "multiple source skills are available" in output.err
    assert "--src-skill skill-one" in output.err
    assert "--all-src-skills" in output.err
    assert not (repo / ".codex").exists()


def test_generic_console_installs_all_selected_local_source_skills(
    tmp_path: Path,
    capsys,
) -> None:
    source = make_skill_collection(tmp_path / "skills-root")
    repo = make_repo(tmp_path / "repo")

    exit_code = generic_main(
        [
            "install",
            "--skill-path",
            str(source),
            "--copy",
            "--all-src-skills",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert "Installed skill-one local to Codex repo:" in output.out
    assert "Installed skill-two local to Codex repo:" in output.out
    assert (repo / ".codex" / "skills" / "skill-one" / "SKILL.md").exists()
    assert (repo / ".codex" / "skills" / "skill-two" / "SKILL.md").exists()


def test_generic_console_installs_all_selected_local_source_skills_editable(
    tmp_path: Path,
    capsys,
) -> None:
    source = make_skill_collection(tmp_path / "skills-root")
    repo = make_repo(tmp_path / "repo")

    exit_code = generic_main(
        [
            "install",
            "--skill-path",
            str(source),
            "--all-src-skills",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert "Installed skill-one local (editable) to Codex repo:" in output.out
    assert "Installed skill-two local (editable) to Codex repo:" in output.out
    skill_one = repo / ".codex" / "skills" / "skill-one"
    skill_two = repo / ".codex" / "skills" / "skill-two"
    assert skill_one.is_symlink()
    assert skill_two.is_symlink()
    assert skill_one.resolve() == source / "skill-one"
    assert skill_two.resolve() == source / "skill-two"
    assert (skill_one / "SKILL.md").read_text().endswith("one\n")
    assert (skill_two / "SKILL.md").read_text().endswith("two\n")


def test_generic_multi_skill_uninstall_cleans_up_after_creator_removed_first(
    tmp_path: Path,
    capsys,
) -> None:
    source = make_skill_collection(tmp_path / "skills-root")
    repo = make_repo(tmp_path / "repo")

    install_code = generic_main(
        [
            "install",
            "--skill-path",
            str(source),
            "--copy",
            "--all-src-skills",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    capsys.readouterr()

    assert install_code == 0
    assert (repo / ".codex" / "skills" / "skill-one").exists()
    assert (repo / ".codex" / "skills" / "skill-two").exists()

    first_uninstall = generic_main(
        [
            "uninstall",
            "--skill-name",
            "skill-one",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    capsys.readouterr()

    assert first_uninstall == 0
    assert not (repo / ".codex" / "skills" / "skill-one").exists()
    assert (repo / ".codex" / "skills" / "skill-two").exists()
    assert (repo / "AGENTS.md").exists()
    assert "SKILL-TWO-DISCOVERABILITY" in (repo / "AGENTS.md").read_text()

    second_uninstall = generic_main(
        [
            "uninstall",
            "--skill-name",
            "skill-two",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    capsys.readouterr()

    assert second_uninstall == 0
    assert not (repo / "AGENTS.md").exists()
    assert not (repo / ".codex" / "skills").exists()
    assert not (repo / ".codex").exists()


def test_generic_multi_skill_upgrade_propagates_sibling_hook_ownership(
    tmp_path: Path,
    capsys,
) -> None:
    source = make_skill_collection(tmp_path / "skills-root")
    repo = make_repo(tmp_path / "repo")

    install_code = generic_main(
        [
            "install",
            "--skill-path",
            str(source),
            "--copy",
            "--all-src-skills",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    capsys.readouterr()

    assert install_code == 0

    skill_two_manifest_path = (
        repo
        / ".codex"
        / "skills"
        / "skill-two"
        / ".skill-two-install.json"
    )
    skill_two_manifest = json.loads(skill_two_manifest_path.read_text())
    skill_two_manifest["created_hook_file"] = False
    skill_two_manifest_path.write_text(
        json.dumps(skill_two_manifest, indent=2, sort_keys=True) + "\n"
    )

    upgrade_code = generic_main(
        [
            "install",
            "--skill-path",
            str(source),
            "--copy",
            "--src-skill",
            "skill-two",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    capsys.readouterr()

    assert upgrade_code == 0
    skill_two_manifest = json.loads(skill_two_manifest_path.read_text())
    assert skill_two_manifest["created_hook_file"] is True

    for skill_name in ("skill-one", "skill-two"):
        uninstall_code = generic_main(
            [
                "uninstall",
                "--skill-name",
                skill_name,
                "--agent",
                "codex",
                "--scope",
                "repo",
                "--target-dir",
                str(repo),
            ]
        )
        capsys.readouterr()

        assert uninstall_code == 0

    assert not (repo / "AGENTS.md").exists()


def test_generic_multi_skill_uninstall_preserves_preexisting_empty_containers(
    tmp_path: Path,
    capsys,
) -> None:
    source = make_skill_collection(tmp_path / "skills-root")
    repo = make_repo(tmp_path / "repo")
    (repo / ".codex" / "skills").mkdir(parents=True)
    (repo / "AGENTS.md").write_text("")

    install_code = generic_main(
        [
            "install",
            "--skill-path",
            str(source),
            "--copy",
            "--all-src-skills",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    capsys.readouterr()

    assert install_code == 0

    for skill_name in ("skill-one", "skill-two"):
        uninstall_code = generic_main(
            [
                "uninstall",
                "--skill-name",
                skill_name,
                "--agent",
                "codex",
                "--scope",
                "repo",
                "--target-dir",
                str(repo),
            ]
        )
        capsys.readouterr()

        assert uninstall_code == 0

    assert (repo / "AGENTS.md").exists()
    assert (repo / "AGENTS.md").read_text() == ""
    assert (repo / ".codex" / "skills").is_dir()


def test_generic_console_installs_all_selected_github_source_skills(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = make_repo(tmp_path / "repo")
    archive = make_github_skill_collection_archive(
        tmp_path / "github.zip",
        skills={
            "skill-one": "github one\n",
            "skill-two": "github two\n",
        },
    )
    monkeypatch.setattr(
        "agent_skill_installer.__main__.download_github_archive",
        lambda _source, _download_dir: archive,
    )

    exit_code = generic_main(
        [
            "install",
            "--github-url",
            "https://github.com/example/example-agent-skill",
            "--all-src-skills",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert "Installed skill-one main (GitHub archive) to Codex repo:" in output.out
    assert "Installed skill-two main (GitHub archive) to Codex repo:" in output.out
    skill_one = repo / ".codex" / "skills" / "skill-one"
    skill_two = repo / ".codex" / "skills" / "skill-two"
    assert (skill_one / "SKILL.md").read_text() == "github one\n"
    assert (skill_two / "SKILL.md").read_text() == "github two\n"
    assert json.loads((skill_one / ".skill-one-install.json").read_text())[
        "source_path"
    ] == "skill-one"
    assert json.loads((skill_two / ".skill-two-install.json").read_text())[
        "source_path"
    ] == "skill-two"


def test_generic_console_src_skill_matches_single_github_child_directory(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = make_repo(tmp_path / "repo")
    archive = make_github_skill_collection_archive(
        tmp_path / "github.zip",
        skills={"skill-one": "github one\n"},
    )
    monkeypatch.setattr(
        "agent_skill_installer.__main__.download_github_archive",
        lambda _source, _download_dir: archive,
    )

    exit_code = generic_main(
        [
            "install",
            "--github-url",
            "https://github.com/example/example-agent-skill",
            "--src-skill",
            "skill-one",
            "--dst-skill",
            "renamed-skill",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    skill_dir = repo / ".codex" / "skills" / "renamed-skill"
    assert "Installed renamed-skill main (GitHub archive) to Codex repo:" in output.out
    assert (skill_dir / "SKILL.md").read_text() == "github one\n"
    manifest = json.loads((skill_dir / ".renamed-skill-install.json").read_text())
    assert manifest["source_skill_name"] == "skill-one"
    assert manifest["source_skill_path"] == "skill-one"
    assert manifest["source_path"] == "skill-one"


def test_generic_console_src_skill_matches_single_wheel_child_directory(
    tmp_path: Path,
    capsys,
) -> None:
    repo = make_repo(tmp_path / "repo")
    wheel = make_wheel_skill_collection(
        tmp_path / "example_agent_skill-1.2.3-py3-none-any.whl",
        skills={"skills/skill-one": "wheel one\n"},
    )

    exit_code = generic_main(
        [
            "install",
            "--wheel-file",
            str(wheel),
            "--src-skill",
            "skill-one",
            "--dst-skill",
            "renamed-skill",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    skill_dir = repo / ".codex" / "skills" / "renamed-skill"
    assert "Installed renamed-skill 1.2.3 (wheel) to Codex repo:" in output.out
    assert (skill_dir / "SKILL.md").read_text() == "wheel one\n"
    manifest = json.loads((skill_dir / ".renamed-skill-install.json").read_text())
    assert manifest["source_skill_name"] == "skill-one"
    assert manifest["source_skill_path"] == "example_agent_skill/skills/skill-one"


def test_generic_console_renames_single_selected_source_skill(
    tmp_path: Path,
    capsys,
) -> None:
    source = make_skill_collection(tmp_path / "skills-root")
    repo = make_repo(tmp_path / "repo")

    exit_code = generic_main(
        [
            "install",
            "--skill-path",
            str(source),
            "--copy",
            "--src-skill",
            "skill-two",
            "--dst-skill",
            "renamed-skill",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    skill_dir = repo / ".codex" / "skills" / "renamed-skill"
    assert "Installed renamed-skill local to Codex repo:" in output.out
    assert not (repo / ".codex" / "skills" / "skill-one").exists()
    assert not (repo / ".codex" / "skills" / "skill-two").exists()
    assert (skill_dir / "SKILL.md").read_text().endswith("two\n")
    manifest = json.loads((skill_dir / ".renamed-skill-install.json").read_text())
    assert manifest["skill_name"] == "renamed-skill"
    assert manifest["source_skill_name"] == "skill-two"
    assert manifest["source_skill_path"] == "skill-two"


def test_generic_console_rejects_dst_skill_without_explicit_source(
    tmp_path: Path,
    capsys,
) -> None:
    source = make_skill_collection(tmp_path / "skills-root")
    repo = make_repo(tmp_path / "repo")

    exit_code = generic_main(
        [
            "install",
            "--skill-path",
            str(source),
            "--copy",
            "--dst-skill",
            "renamed-skill",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 2
    assert "--dst-skill requires exactly one --src-skill" in output.err
    assert "--rename skill-one:renamed-skill" in output.err


def test_generic_console_rename_implies_source_selection(
    tmp_path: Path,
    capsys,
) -> None:
    source = make_skill_collection(tmp_path / "skills-root")
    repo = make_repo(tmp_path / "repo")

    exit_code = generic_main(
        [
            "install",
            "--skill-path",
            str(source),
            "--copy",
            "--rename",
            "skill-one:first-installed",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )

    assert exit_code == 0
    capsys.readouterr()
    assert (repo / ".codex" / "skills" / "first-installed" / "SKILL.md").exists()
    assert not (repo / ".codex" / "skills" / "skill-two").exists()


def test_generic_complete_with_ui_prompts_for_source_skills(
    tmp_path: Path,
) -> None:
    source = make_skill_collection(tmp_path / "skills-root")
    repo = make_repo(tmp_path / "repo")
    args = Namespace(
        command="install",
        force=False,
        skill_name=None,
        dst_skill=None,
        src_skills=None,
        all_src_skills=False,
        renames=None,
        description=None,
        pypi_package=None,
        pypi_version=None,
        wheel_file=None,
        github_url=None,
        github_ref=None,
        github_path=None,
        skill_path=source,
        editable=False,
        agent="codex",
        scope="dir",
        repo_target=True,
        repo=repo,
        codex_home=None,
        claude_home=None,
        home=None,
        no_ui=False,
        verbose=False,
    )
    prompter = ScriptedPrompter(["skill-two"])

    complete_generic_with_ui(args, prompter)

    assert args.src_skills == ["skill-two"]
    assert args.all_src_skills is False
    assert prompter.calls == [("checkbox", "Select source skills")]
    assert prompter.checkbox_defaults == [None]
    assert prompter.checkbox_empty_messages == ["Choose at least one source skill."]
    assert prompter.checkbox_accept_highlighted_on_empty == [False]
    assert [choice["value"] for choice in prompter.choices[0]] == [
        "__agent_skill_installer_all_source_skills__",
        "skill-one",
        "skill-two",
    ]


def test_generic_multi_skill_install_does_not_commit_on_staging_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = make_skill_collection(tmp_path / "skills-root")
    repo = make_repo(tmp_path / "repo")
    calls: list[str] = []
    original_stage_install_target = __import__(
        "agent_skill_installer.__main__",
        fromlist=["stage_install_target"],
    ).stage_install_target

    def fail_second(project, *args, **kwargs):
        calls.append(project.skill_name)
        if project.skill_name == "skill-two":
            raise InstallerError("boom")
        return original_stage_install_target(project, *args, **kwargs)

    monkeypatch.setattr("agent_skill_installer.__main__.stage_install_target", fail_second)
    args = Namespace(
        command="install",
        force=False,
        skill_name=None,
        dst_skill=None,
        src_skills=None,
        all_src_skills=True,
        renames=None,
        description=None,
        pypi_package=None,
        pypi_version=None,
        wheel_file=None,
        github_url=None,
        github_ref=None,
        github_path=None,
        skill_path=source,
        editable=False,
        agent="codex",
        scope="dir",
        repo_target=True,
        repo=repo,
        codex_home=None,
        claude_home=None,
        home=None,
        verbose=False,
    )

    with pytest.raises(InstallerError, match="boom"):
        run_generic_install(args)

    assert calls == ["skill-one", "skill-two"]
    assert not (repo / ".codex" / "skills" / "skill-one").exists()
    assert not (repo / ".codex" / "skills" / "skill-two").exists()
    assert not (repo / ".codex").exists()
    assert not (repo / "AGENTS.md").exists()


def test_generic_multi_skill_install_reports_all_rollback_cleanup_failures(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = make_skill_collection(tmp_path / "skills-root")
    repo = make_repo(tmp_path / "repo")
    from agent_skill_installer import installer as installer_module

    def fail_hook(_spec) -> None:
        raise InstallerError("hook install failed")

    original_remove_existing_path = installer_module.remove_existing_path

    def fail_skill_cleanup(path: Path) -> None:
        if path.name in {"skill-one", "skill-two"}:
            raise OSError(f"{path.name} cleanup failed")
        original_remove_existing_path(path)

    monkeypatch.setattr("agent_skill_installer.installer.install_hook", fail_hook)
    monkeypatch.setattr(
        "agent_skill_installer.installer.remove_existing_path",
        fail_skill_cleanup,
    )
    args = Namespace(
        command="install",
        force=False,
        skill_name=None,
        dst_skill=None,
        src_skills=None,
        all_src_skills=True,
        renames=None,
        description=None,
        pypi_package=None,
        pypi_version=None,
        wheel_file=None,
        github_url=None,
        github_ref=None,
        github_path=None,
        skill_path=source,
        editable=False,
        agent="codex",
        scope="dir",
        repo_target=True,
        repo=repo,
        codex_home=None,
        claude_home=None,
        home=None,
        verbose=False,
    )

    with pytest.raises(InstallerError) as exc_info:
        run_generic_install(args)

    message = str(exc_info.value)
    assert "install failed and rollback was incomplete" in message
    assert "Original error: hook install failed" in message
    assert "skill-one (codex repo)" in message
    assert "skill-one cleanup failed" in message
    assert "skill-two (codex repo)" in message
    assert "skill-two cleanup failed" in message


def test_generic_console_rejects_copy_without_local_skill_path(capsys) -> None:
    exit_code = generic_main(
        [
            "--no-ui",
            "install",
            "--pypi-package",
            "example-agent-skill",
            "--copy",
            "--agent",
            "codex",
            "--scope",
            "global",
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 2
    assert "--copy requires --skill-path" in output.err


def test_generic_console_installs_local_repo_with_skill_subdir(
    tmp_path: Path,
    capsys,
) -> None:
    source_repo = make_skill_checkout(tmp_path / "local-skill-repo")
    repo = make_repo(tmp_path / "repo")

    exit_code = generic_main(
        [
            "install",
            "--local-repo",
            str(source_repo),
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert (
        "Installed local-skill-repo local (editable) to Codex repo:"
        in output.out
    )
    skill_dir = repo / ".codex" / "skills" / "local-skill-repo"
    assert skill_dir.is_symlink()
    assert skill_dir.resolve() == source_repo / "skill"
    assert (skill_dir / "SKILL.md").read_text() == "editable skill\n"


def test_generic_console_local_copy_uses_external_wheel_editable_path(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    source_repo = make_skill_checkout(tmp_path / "local-skill-repo")
    (source_repo / "skill" / "bin").mkdir()
    (source_repo / "skill" / "bin" / "arbiter").write_text(
        "#!/bin/sh\necho development launcher\n"
    )
    (source_repo / "skill" / CONFIG_FILE_NAME).write_text(
        """
installer:
  external_wheels:
    - package: "arbiter-client>=2.4,<2.5"
      editable: ../client
      copies:
        - wheel_path: arbiter_client/bin/arbiter
          skill_path: bin/arbiter
          executable: true
          replace: true
"""
    )
    repo = make_repo(tmp_path / "repo")
    external_wheel = make_external_wheel(
        tmp_path / "arbiter_client-2.4.7-py3-none-any.whl",
        data="#!/bin/sh\necho copied arbiter\n",
    )
    digest = hashlib.sha256(external_wheel.read_bytes()).hexdigest()
    pip_requests: list[tuple[str, str | None, Path | None]] = []

    def fake_build_external_wheel(
        *,
        package: str,
        wheel_dir: Path,
        editable: str | None = None,
        cwd: Path | None = None,
    ) -> tuple[Path, str, str, str]:
        pip_requests.append((package, editable, cwd))
        target = wheel_dir / external_wheel.name
        shutil.copy2(external_wheel, target)
        return target, digest, "arbiter-client", "2.4.7"

    monkeypatch.setattr(
        "agent_skill_installer.installer.build_external_wheel",
        fake_build_external_wheel,
    )

    exit_code = generic_main(
        [
            "--no-ui",
            "install",
            "--skill-path",
            str(source_repo),
            "--copy",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert "Installed local-skill-repo local to Codex repo:" in output.out
    assert pip_requests == [
        ("arbiter-client>=2.4,<2.5", "../client", source_repo / "skill")
    ]
    skill_dir = repo / ".codex" / "skills" / "local-skill-repo"
    assert not skill_dir.is_symlink()
    assert (skill_dir / "bin" / "arbiter").read_text() == (
        "#!/bin/sh\necho copied arbiter\n"
    )
    assert (source_repo / "skill" / "bin" / "arbiter").read_text() == (
        "#!/bin/sh\necho development launcher\n"
    )


def test_generic_console_installs_github_skill(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = make_repo(tmp_path / "repo")
    home = tmp_path / "home"
    archive = make_github_archive(tmp_path / "github.zip")
    monkeypatch.setattr(
        "agent_skill_installer.__main__.download_github_archive",
        lambda _source, _download_dir: archive,
    )

    exit_code = generic_main(
        [
            "install",
            "--github-url",
            "https://github.com/example/demo",
            "--github-ref",
            "v2",
            "--skill-name",
            "demo-skill",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
            "--home",
            str(home),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert (
        "Installed demo-skill v2 (GitHub archive) to Codex repo:"
        in output.out
    )
    skill_dir = repo / ".codex" / "skills" / "demo-skill"
    assert (skill_dir / "SKILL.md").read_text() == "github skill\n"
    assert read_install_manifest(
        SkillProject(
            package_name="demo-skill",
            import_name="agent_skill_installer",
            version="v2",
            skill_name="demo-skill",
            description="",
        ),
        skill_dir,
    )["source_url"] == "https://github.com/example/demo"
    assert load_recent_github_urls(home) == ["https://github.com/example/demo"]


def test_generic_console_github_install_uses_embedded_payload_selection(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = make_repo(tmp_path / "repo")
    archive = make_github_archive(tmp_path / "github.zip")
    with zipfile.ZipFile(archive, "a") as zip_archive:
        root = "example-agent-skill-main"
        zip_archive.writestr(
            f"{root}/skill/{CONFIG_FILE_NAME}",
            """
installer:
  payload:
    include:
      - SKILL.md
      - bin/**
""",
        )
        zip_archive.writestr(f"{root}/skill/pyproject.toml", "[project]\n")
        zip_archive.writestr(f"{root}/skill/bin/arbiter", "#!/bin/sh\n")
    monkeypatch.setattr(
        "agent_skill_installer.__main__.download_github_archive",
        lambda _source, _download_dir: archive,
    )

    exit_code = generic_main(
        [
            "--no-ui",
            "install",
            "--github-url",
            "https://github.com/example/demo",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert output.err == ""
    skill_dir = repo / ".codex" / "skills" / "demo"
    assert sorted(
        path.relative_to(skill_dir).as_posix()
        for path in skill_dir.rglob("*")
        if path.is_file() and not path.name.startswith(".")
    ) == ["SKILL.md", "bin/arbiter"]


def test_generic_console_does_not_remember_failed_github_install(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = make_repo(tmp_path / "repo")
    home = tmp_path / "home"
    archive_path = tmp_path / "github.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("demo-main/README.md", "no skill here\n")
    monkeypatch.setattr(
        "agent_skill_installer.__main__.download_github_archive",
        lambda _source, _download_dir: archive_path,
    )

    exit_code = generic_main(
        [
            "install",
            "--github-url",
            "https://github.com/example/demo",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
            "--home",
            str(home),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 1
    assert "SKILL.md" in output.err
    assert load_recent_github_urls(home) == []


def test_generic_console_installs_pypi_skill(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    home = tmp_path / "home"
    wheel = make_skill_wheel(tmp_path / "example.whl", project)
    requests: list[str] = []
    patch_generic_pypi_build(monkeypatch, wheel, "2.0.0", requests)

    exit_code = generic_main(
        [
            "install",
            "--pypi-package",
            "example-agent-skill==2.0.0",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
            "--home",
            str(home),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert (
        "Installed example-agent-skill 2.0.0 (PyPI wheel) "
        "to Codex repo:"
        in output.out
    )
    assert (
        repo / ".codex" / "skills" / "example-agent-skill" / "SKILL.md"
    ).read_text() == "wheel skill\n"
    assert requests == ["example-agent-skill==2.0.0"]
    assert load_recent_pypi_packages(home) == ["example-agent-skill"]


def test_generic_console_installs_pypi_skill_with_package_spec(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    home = tmp_path / "home"
    wheel = make_skill_wheel(tmp_path / "example.whl", project)
    requests: list[str] = []
    patch_generic_pypi_build(monkeypatch, wheel, "2.0.0", requests)

    exit_code = generic_main(
        [
            "install",
            "--pypi-package",
            "example-agent-skill==2.0.0",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
            "--home",
            str(home),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert (
        "Installed example-agent-skill 2.0.0 (PyPI wheel) "
        "to Codex repo:"
        in output.out
    )
    assert requests == ["example-agent-skill==2.0.0"]
    assert load_recent_pypi_packages(home) == ["example-agent-skill"]


def test_generic_console_installs_latest_pypi_skill_without_version(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    home = tmp_path / "home"
    wheel = make_skill_wheel(tmp_path / "example.whl", project)
    requests: list[str] = []
    patch_generic_pypi_build(monkeypatch, wheel, "2.1.0", requests)

    exit_code = generic_main(
        [
            "install",
            "--pypi-package",
            "example-agent-skill",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
            "--home",
            str(home),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert "Installed example-agent-skill 2.1.0 (PyPI wheel)" in output.out
    assert requests == ["example-agent-skill"]
    assert load_recent_pypi_packages(home) == ["example-agent-skill"]


def test_generic_console_installs_pypi_skill_with_version_range(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    home = tmp_path / "home"
    wheel = make_skill_wheel(tmp_path / "example.whl", project)
    requests: list[str] = []
    patch_generic_pypi_build(monkeypatch, wheel, "2.5.0", requests)

    exit_code = generic_main(
        [
            "install",
            "--pypi-package",
            "example-agent-skill>=2,<3",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
            "--home",
            str(home),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert (
        "Installed example-agent-skill 2.5.0 (PyPI wheel) "
        "to Codex repo:"
        in output.out
    )
    assert requests == ["example-agent-skill>=2,<3"]
    assert load_recent_pypi_packages(home) == ["example-agent-skill"]


def test_generic_console_installs_pypi_skill_with_wildcard_version(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    wheel = make_skill_wheel(tmp_path / "example.whl", project)
    requests: list[str] = []
    patch_generic_pypi_build(monkeypatch, wheel, "1.8.0", requests)

    exit_code = generic_main(
        [
            "install",
            "--pypi-package",
            "example-agent-skill==1.*",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert "Installed example-agent-skill 1.8.0 (PyPI wheel)" in output.out
    assert requests == ["example-agent-skill==1.*"]


def test_generic_console_reports_pypi_package_version_range_without_wheels(
    monkeypatch,
    capsys,
) -> None:
    def fail_build_pypi_wheel(
        *,
        package: str,
        wheel_dir: Path,
    ) -> tuple[Path, str]:
        assert package == "example-agent-skill>=2,<3"
        raise InstallerError(
            "pip wheel failed for example-agent-skill>=2,<3: "
            "no matching distribution found"
        )

    monkeypatch.setattr(
        "agent_skill_installer.__main__.build_pypi_wheel",
        fail_build_pypi_wheel,
    )

    exit_code = generic_main(
        [
            "--no-ui",
            "install",
            "--pypi-package",
            "example-agent-skill>=2,<3",
            "--agent",
            "codex",
            "--scope",
            "global",
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 1
    assert "pip wheel failed for example-agent-skill>=2,<3" in output.err


def test_generic_console_help_omits_pypi_version() -> None:
    assert "--pypi-version" not in build_generic_parser().format_help()


def test_generic_console_installs_pypi_skill_with_external_wheel(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    source_project = SkillProject(
        package_name="arbiter-skill",
        import_name="arbiter_skill",
        version="2.0.0",
        skill_name="arbiter-skill",
        description="Arbiter skill.",
    )
    repo = make_repo(tmp_path / "repo")
    home = tmp_path / "home"
    skill_wheel = make_skill_wheel(
        tmp_path / "arbiter_skill-2.0.0-py3-none-any.whl",
        source_project,
        skill_text="arbiter skill\n",
        config_text="""
installer:
  external_wheels:
    - package: "arbiter-client>=2.4,<2.5"
      copies:
        - wheel_path: arbiter_client/bin/arbiter
          skill_path: bin/arbiter
          executable: true
""",
    )
    external_wheel = make_external_wheel(
        tmp_path / "arbiter_client-2.4.7-py3-none-any.whl",
        data="#!/bin/sh\necho arbiter\n",
    )
    digest = hashlib.sha256(external_wheel.read_bytes()).hexdigest()
    pypi_requests: list[str] = []
    patch_generic_pypi_build(monkeypatch, skill_wheel, "2.0.0", pypi_requests)

    def fake_build_external_wheel(
        *,
        package: str,
        wheel_dir: Path,
        editable: str | None = None,
        cwd: Path | None = None,
    ) -> tuple[Path, str, str, str]:
        assert package == "arbiter-client>=2.4,<2.5"
        assert editable is None
        target = wheel_dir / external_wheel.name
        shutil.copy2(external_wheel, target)
        return target, digest, "arbiter-client", "2.4.7"

    monkeypatch.setattr(
        "agent_skill_installer.installer.build_external_wheel",
        fake_build_external_wheel,
    )

    exit_code = generic_main(
        [
            "install",
            "--pypi-package",
            "arbiter-skill==2.0.0",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
            "--home",
            str(home),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert "Installed arbiter-skill 2.0.0 (PyPI wheel) to Codex repo:" in output.out
    assert pypi_requests == ["arbiter-skill==2.0.0"]
    skill_dir = repo / ".codex" / "skills" / "arbiter-skill"
    assert (skill_dir / "SKILL.md").read_text() == "arbiter skill\n"
    assert (skill_dir / "bin" / "arbiter").read_text() == "#!/bin/sh\necho arbiter\n"
    assert load_recent_pypi_packages(home) == ["arbiter-skill"]


def test_generic_console_installs_local_wheel_file(
    tmp_path: Path,
    capsys,
) -> None:
    project = make_project(tmp_path)
    repo = make_repo(tmp_path / "repo")
    home = tmp_path / "home"
    wheel = make_skill_wheel(
        tmp_path / "example_agent_skill-1.2.3-py3-none-any.whl",
        project,
    )

    exit_code = generic_main(
        [
            "install",
            "--wheel-file",
            str(wheel),
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
            "--home",
            str(home),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert (
        "Installed example-agent-skill 1.2.3 (wheel) "
        "to Codex repo:"
        in output.out
    )
    skill_dir = repo / ".codex" / "skills" / "example-agent-skill"
    assert (skill_dir / "SKILL.md").read_text() == "wheel skill\n"
    manifest = read_raw_manifest(project, skill_dir)
    assert manifest["install_mode"] == "wheel"
    assert manifest["source_path"] == str(wheel.resolve())
    assert load_recent_pypi_packages(home) == []


def test_generic_console_does_not_remember_failed_pypi_install(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = make_repo(tmp_path / "repo")
    home = tmp_path / "home"
    wheel = tmp_path / "plain-package.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("plain_package/__init__.py", "")
    patch_generic_pypi_build(monkeypatch, wheel, "1.0.0")

    exit_code = generic_main(
        [
            "install",
            "--pypi-package",
            "plain-package==1.0.0",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--target-dir",
            str(repo),
            "--home",
            str(home),
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 1
    assert "SKILL.md" in output.err
    assert load_recent_pypi_packages(home) == []
