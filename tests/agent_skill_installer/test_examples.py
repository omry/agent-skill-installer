from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def make_repo(path: Path) -> Path:
    path.mkdir()
    (path / ".git").mkdir()
    return path


def run_example(command: list[str], *, pythonpath: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = pythonpath
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        env=env,
        cwd=REPO_ROOT,
    )


def test_demo_installer_example_installs_and_uninstalls(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    pythonpath = os.pathsep.join(
        [
            str(REPO_ROOT / "src"),
            str(REPO_ROOT / "examples" / "demo-installer" / "src"),
        ]
    )

    version = run_example(
        [sys.executable, "-m", "demo_agent_skill", "--version"],
        pythonpath=pythonpath,
    )

    assert version.returncode == 0, version.stderr
    assert version.stdout.strip() == "demo-agent-skill 0.1.0"

    install = run_example(
        [
            sys.executable,
            "-m",
            "demo_agent_skill",
            "--no-ui",
            "install",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--repo",
            str(repo),
        ],
        pythonpath=pythonpath,
    )

    assert install.returncode == 0, install.stderr
    assert "Installed demo-agent-skill 0.1.0 to Codex repo:" in install.stdout
    skill_dir = repo / ".codex" / "skills" / "demo-agent-skill"
    assert "Demo Agent Skill" in skill_dir.joinpath("SKILL.md").read_text()
    hook = repo.joinpath("AGENTS.md").read_text()
    assert "<!-- DEMO-AGENT-SKILL-DISCOVERABILITY-START -->" in hook
    assert "Use this demo skill" in hook

    uninstall = run_example(
        [
            sys.executable,
            "-m",
            "demo_agent_skill",
            "--no-ui",
            "uninstall",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--repo",
            str(repo),
        ],
        pythonpath=pythonpath,
    )

    assert uninstall.returncode == 0, uninstall.stderr
    assert (
        "Removed demo-agent-skill 0.1.0 from Codex repo:"
        in uninstall.stdout
    )
    assert not skill_dir.exists()


def test_wheel_skill_example_builds_and_installs(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    example = tmp_path / "wheel-skill"
    dist = tmp_path / "dist"
    shutil.copytree(REPO_ROOT / "examples" / "wheel-skill", example)
    pythonpath = str(REPO_ROOT / "src")

    build = run_example(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(dist),
            str(example),
        ],
        pythonpath=pythonpath,
    )

    assert build.returncode == 0, build.stderr
    wheel = dist / "wheel_agent_skill-0.1.0-py3-none-any.whl"
    assert wheel.is_file()

    install = run_example(
        [
            sys.executable,
            "-m",
            "agent_skill_installer",
            "--no-ui",
            "install",
            "--wheel-file",
            str(wheel),
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--repo",
            str(repo),
        ],
        pythonpath=pythonpath,
    )

    assert install.returncode == 0, install.stderr
    assert (
        "Installed wheel-agent-skill 0.1.0 (wheel) to Codex repo:"
        in install.stdout
    )
    skill_dir = repo / ".codex" / "skills" / "wheel-agent-skill"
    assert "Wheel Agent Skill" in skill_dir.joinpath("SKILL.md").read_text()

    uninstall = run_example(
        [
            sys.executable,
            "-m",
            "agent_skill_installer",
            "--no-ui",
            "uninstall",
            "--skill-name",
            "wheel-agent-skill",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--repo",
            str(repo),
        ],
        pythonpath=pythonpath,
    )

    assert uninstall.returncode == 0, uninstall.stderr
    assert "Removed wheel-agent-skill 0.1.0 from Codex repo:" in uninstall.stdout
    assert not skill_dir.exists()


def test_api_install_example_installs_and_uninstalls(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    script = REPO_ROOT / "examples" / "api-install" / "install_demo_skill.py"
    pythonpath = str(REPO_ROOT / "src")

    install = run_example(
        [
            sys.executable,
            str(script),
            "install",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--repo",
            str(repo),
        ],
        pythonpath=pythonpath,
    )

    assert install.returncode == 0, install.stderr
    assert (
        "Installed api-demo-agent-skill 0.1.0 to Codex repo:"
        in install.stdout
    )
    skill_dir = repo / ".codex" / "skills" / "api-demo-agent-skill"
    assert "API Demo Agent Skill" in skill_dir.joinpath("SKILL.md").read_text()
    hook = repo.joinpath("AGENTS.md").read_text()
    assert "<!-- API-DEMO-AGENT-SKILL-DISCOVERABILITY-START -->" in hook
    assert "Use this API demo skill" in hook

    uninstall = run_example(
        [
            sys.executable,
            str(script),
            "uninstall",
            "--agent",
            "codex",
            "--scope",
            "repo",
            "--repo",
            str(repo),
        ],
        pythonpath=pythonpath,
    )

    assert uninstall.returncode == 0, uninstall.stderr
    assert (
        "Removed api-demo-agent-skill 0.1.0 from Codex repo:"
        in uninstall.stdout
    )
    assert not skill_dir.exists()
