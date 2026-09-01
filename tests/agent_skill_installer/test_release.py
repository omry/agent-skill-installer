from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Sequence

import pytest

from tools import release


def test_parse_config_defaults_to_local_dry_run() -> None:
    config = release.parse_config(["version=0.4.0", "date=2026-09-01"])

    assert config == release.ReleaseConfig(
        version="0.4.0",
        publish=False,
        release_date="2026-09-01",
        commit="",
    )


def test_parse_config_accepts_explicit_publish_and_recovery_commit() -> None:
    commit = "a" * 40

    config = release.parse_config(
        ["version=0.4.0", "publish=true", f"commit={commit}"]
    )

    assert config.publish is True
    assert config.commit == commit


@pytest.mark.parametrize(
    "arguments, message",
    [
        (["publish=true"], "version is required"),
        (["version=0.4"], "canonical 1.2.3"),
        (["version=0.4.0foo"], "valid Python package version"),
        (["version=v0.4.0"], "leading v"),
        (["version=0.4.0", "publish=yes"], "publish must be true or false"),
        (["version=0.4.0", "commit=abc"], "full 40-character"),
        (["version=0.4.0", "unknown=value"], "unknown release argument"),
    ],
)
def test_parse_config_rejects_invalid_arguments(
    arguments: list[str],
    message: str,
) -> None:
    with pytest.raises(release.ReleaseError, match=message):
        release.parse_config(arguments)


def write_release_tree(root: Path) -> None:
    (root / "src/agent_skill_installer").mkdir(parents=True)
    (root / "news").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "agent-skill-installer"\nversion = "0.3.0"\n'
    )
    (root / "src/agent_skill_installer/__init__.py").write_text(
        '__version__ = "0.3.0"\n'
    )
    (root / "NEWS.md").write_text(
        "# Agent Skill Installer Release Notes\n\n"
        "<!-- TOWNCRIER -->\n\n"
        "## Agent Skill Installer 0.3.0 (2026-06-12)\n\n"
        "Old release.\n"
    )
    (root / "news/_template.rst").write_text("template\n")
    (root / "news/36.api_change").write_text("Resolve local companions.\n")


def test_prepare_tree_updates_versions_and_renders_notes(tmp_path: Path) -> None:
    write_release_tree(tmp_path)
    commands: list[tuple[str, ...]] = []

    def fake_towncrier(command: Sequence[str], cwd: Path) -> None:
        commands.append(tuple(command))
        news = cwd / "NEWS.md"
        text = news.read_text()
        text = text.replace(
            release.NEWS_MARKER,
            release.NEWS_MARKER
            + "\n## Agent Skill Installer 0.4.0 (2026-09-01)\n\n"
            + "### API changes and deprecations\n\n"
            + "- Resolve local companions. ([#36](https://example.invalid/36))\n\n",
        )
        news.write_text(text)
        (cwd / "news/36.api_change").unlink()

    config = release.ReleaseConfig(
        version="0.4.0",
        release_date="2026-09-01",
    )

    fragments = release.prepare_tree(tmp_path, config, runner=fake_towncrier)

    assert fragments == [Path("news/36.api_change")]
    assert 'version = "0.4.0"' in (tmp_path / "pyproject.toml").read_text()
    assert '__version__ = "0.4.0"' in (
        tmp_path / "src/agent_skill_installer/__init__.py"
    ).read_text()
    notes = release.release_notes((tmp_path / "NEWS.md").read_text(), "0.4.0")
    assert notes.startswith("### API changes and deprecations")
    assert commands[0][-5:] == (
        "--version",
        "0.4.0",
        "--date",
        "2026-09-01",
        "--yes",
    )


def test_prepare_tree_rejects_late_fragment_for_existing_release(
    tmp_path: Path,
) -> None:
    write_release_tree(tmp_path)
    news = tmp_path / "NEWS.md"
    news.write_text(
        news.read_text().replace(
            release.NEWS_MARKER,
            release.NEWS_MARKER
            + "\n## Agent Skill Installer 0.4.0 (2026-09-01)\n\nNotes.\n\n",
        )
    )

    with pytest.raises(release.ReleaseError, match="late fragments"):
        release.prepare_tree(
            tmp_path,
            release.ReleaseConfig(version="0.4.0", release_date="2026-09-01"),
        )


def test_apply_prepared_tree_copies_release_files_and_consumes_fragments(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    prepared = tmp_path / "prepared"
    write_release_tree(root)
    write_release_tree(prepared)
    release.set_versions(prepared, "0.4.0")
    (prepared / "NEWS.md").write_text("prepared notes\n")

    release.apply_prepared_tree(
        root,
        prepared,
        [Path("news/36.api_change")],
    )

    assert 'version = "0.4.0"' in (root / "pyproject.toml").read_text()
    assert (root / "NEWS.md").read_text() == "prepared notes\n"
    assert not (root / "news/36.api_change").exists()


def test_git_release_accepts_clean_checkout_at_origin_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "a" * 40

    def fake_capture(command: Sequence[str], cwd: Path) -> str:
        assert cwd == tmp_path
        responses = {
            ("git", "status", "--porcelain"): "",
            ("git", "branch", "--show-current"): "main",
            ("git", "rev-parse", "HEAD"): commit,
            ("git", "rev-parse", "origin/main"): commit,
        }
        return responses[tuple(command)]

    monkeypatch.setattr(release, "capture", fake_capture)

    release.Vcs("git", tmp_path).require_clean_main()


def test_git_release_rejects_checkout_ahead_of_origin_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_capture(command: Sequence[str], cwd: Path) -> str:
        assert cwd == tmp_path
        responses = {
            ("git", "status", "--porcelain"): "",
            ("git", "branch", "--show-current"): "main",
            ("git", "rev-parse", "HEAD"): "b" * 40,
            ("git", "rev-parse", "origin/main"): "a" * 40,
        }
        return responses[tuple(command)]

    monkeypatch.setattr(release, "capture", fake_capture)

    with pytest.raises(release.ReleaseError, match="exactly at origin/main"):
        release.Vcs("git", tmp_path).require_clean_main()


def test_recovery_commit_dispatches_without_local_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "b" * 40
    dispatched: list[tuple[release.ReleaseConfig, str]] = []

    def fake_dispatch(
        root: Path,
        config: release.ReleaseConfig,
        exact_commit: str,
    ) -> None:
        assert root == tmp_path
        dispatched.append((config, exact_commit))

    monkeypatch.setattr(release, "dispatch_publish", fake_dispatch)
    config = release.ReleaseConfig(
        version="0.4.0",
        publish=True,
        release_date="2026-09-01",
        commit=commit,
    )

    release.release(tmp_path, config)

    assert dispatched == [(config, commit)]


def test_publish_prepares_commits_pushes_and_dispatches_exact_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "c" * 40
    workspace = tmp_path / "prepared"
    workspace.mkdir()
    events: list[object] = []

    class FakeVcs:
        def require_clean_main(self) -> None:
            events.append("require_clean_main")

        def status(self) -> str:
            events.append("status")
            return "M NEWS.md"

        def commit(self, message: str) -> None:
            events.append(("commit", message))

        def push_main(self) -> None:
            events.append("push_main")

        def current_commit(self) -> str:
            events.append("current_commit")
            return commit

    fake_vcs = FakeVcs()
    config = release.ReleaseConfig(
        version="0.4.0",
        publish=True,
        release_date="2026-09-01",
    )
    prepared = SimpleNamespace(
        workspace=workspace,
        notes="Release notes.\n",
        fragments=[Path("news/36.api_change")],
    )

    monkeypatch.setattr(release, "pypi_published", lambda version: False)
    monkeypatch.setattr(
        release.Vcs,
        "detect",
        staticmethod(lambda root: fake_vcs),
    )
    monkeypatch.setattr(release, "local_dry_run", lambda root, plan: prepared)
    monkeypatch.setattr(
        release,
        "apply_prepared_tree",
        lambda root, prepared_root, fragments: events.append(
            ("apply", prepared_root, fragments)
        ),
    )
    monkeypatch.setattr(
        release,
        "dispatch_publish",
        lambda root, plan, exact_commit: events.append(
            ("dispatch", plan, exact_commit)
        ),
    )

    release.release(tmp_path, config)

    assert events == [
        "require_clean_main",
        ("apply", workspace, [Path("news/36.api_change")]),
        "status",
        ("commit", "Prepare 0.4.0 release"),
        "push_main",
        "current_commit",
        ("dispatch", config, commit),
    ]
