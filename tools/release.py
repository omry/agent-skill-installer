#!/usr/bin/env python3
"""Prepare and dispatch Agent Skill Installer releases.

The default mode is a local dry run. Passing ``publish=true`` prepares the
release in the working tree, commits and pushes it as the maintainer, and then
dispatches the exact resulting commit to the protected publish workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from packaging.version import InvalidVersion, Version


REPOSITORY = "omry/agent-skill-installer"
PROJECT_NAME = "agent-skill-installer"
VERSION_PATTERN = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:[.a-zA-Z0-9-]+)?"
)
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
NEWS_MARKER = "<!-- TOWNCRIER -->\n"
RELEASE_PATHS = (
    Path("pyproject.toml"),
    Path("src/agent_skill_installer/__init__.py"),
    Path("NEWS.md"),
)


class ReleaseError(RuntimeError):
    """Raised when a release cannot be prepared safely."""


@dataclass(frozen=True)
class ReleaseConfig:
    version: str
    publish: bool = False
    verbose: bool = False
    release_date: str = ""
    commit: str = ""


Runner = Callable[[Sequence[str], Path], None]


def parse_bool(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ReleaseError(f"{name} must be true or false")


def normalize_version(value: str) -> str:
    version = value.strip()
    if version.startswith("v"):
        raise ReleaseError("version must not include a leading v")
    try:
        parsed = Version(version)
    except InvalidVersion as exc:
        raise ReleaseError("version must be a valid Python package version") from exc
    if (
        VERSION_PATTERN.fullmatch(version) is None
        or parsed.epoch != 0
        or parsed.local is not None
        or len(parsed.release) != 3
        or str(parsed) != version
    ):
        raise ReleaseError(
            "version must use canonical 1.2.3 form without epochs or local labels"
        )
    return version


def normalize_date(value: str) -> str:
    if not value:
        return datetime.now(timezone.utc).date().isoformat()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ReleaseError("date must be a real YYYY-MM-DD date") from exc


def parse_config(arguments: Sequence[str]) -> ReleaseConfig:
    values: dict[str, str] = {}
    for argument in arguments:
        key, separator, value = argument.partition("=")
        if not separator or not key or not value:
            raise ReleaseError(
                "arguments must use key=value form; expected version=1.2.3 "
                "[publish=true] [verbose=true] [date=YYYY-MM-DD] "
                "[commit=<sha>]"
            )
        if key not in {"version", "publish", "verbose", "date", "commit"}:
            raise ReleaseError(f"unknown release argument: {key}")
        if key in values:
            raise ReleaseError(f"duplicate release argument: {key}")
        values[key] = value

    if "version" not in values:
        raise ReleaseError("version is required")

    commit = values.get("commit", "").strip()
    if commit and COMMIT_PATTERN.fullmatch(commit) is None:
        raise ReleaseError("commit must be a full 40-character lowercase SHA")

    return ReleaseConfig(
        version=normalize_version(values["version"]),
        publish=parse_bool(values.get("publish", "false"), "publish"),
        verbose=parse_bool(values.get("verbose", "false"), "verbose"),
        release_date=normalize_date(values.get("date", "")),
        commit=commit,
    )


def capture_run(command: Sequence[str], cwd: Path, *, verbose: bool = False) -> str:
    if verbose:
        print("+", " ".join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output = completed.stdout
    if verbose and output:
        print(output, end="" if output.endswith("\n") else "\n", flush=True)
    if completed.returncode != 0:
        if not verbose and output:
            print(output, end="" if output.endswith("\n") else "\n", file=sys.stderr)
        raise subprocess.CalledProcessError(
            completed.returncode,
            command,
            output=output,
        )
    return output


def run(command: Sequence[str], cwd: Path, *, verbose: bool = False) -> str:
    if not verbose:
        return capture_run(command, cwd)
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)
    return ""


def capture(command: Sequence[str], cwd: Path) -> str:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def replace_one(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text()
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ReleaseError(f"failed to update {path}")
    path.write_text(updated)


def set_versions(root: Path, version: str) -> None:
    replace_one(
        root / "pyproject.toml",
        r'^version = "[^"]+"$',
        f'version = "{version}"',
    )
    replace_one(
        root / "src/agent_skill_installer/__init__.py",
        r'^__version__ = "[^"]+"$',
        f'__version__ = "{version}"',
    )


def news_fragments(root: Path) -> list[Path]:
    news_dir = root / "news"
    return sorted(
        path.relative_to(root)
        for path in news_dir.iterdir()
        if path.is_file() and path.name != "_template.rst"
    )


def release_section(news: str, version: str) -> str | None:
    if NEWS_MARKER not in news:
        raise ReleaseError("NEWS.md does not contain the Towncrier marker")
    rendered = news.split(NEWS_MARKER, 1)[1]
    pattern = re.compile(
        r"(?ms)^## Agent Skill Installer "
        + re.escape(version)
        + r" [(][^)]+[)]\n.*?(?=^## |\Z)"
    )
    match = pattern.search(rendered)
    return match.group(0).strip() if match is not None else None


def release_notes(news: str, version: str) -> str:
    section = release_section(news, version)
    if section is None:
        raise ReleaseError(f"NEWS.md has no release section for {version}")
    lines = section.splitlines()
    body = "\n".join(lines[1:]).lstrip()
    if not body:
        raise ReleaseError(f"NEWS.md release section for {version} has no body")
    return body.rstrip() + "\n"


def prepare_tree(
    root: Path,
    config: ReleaseConfig,
    runner: Runner = run,
) -> list[Path]:
    set_versions(root, config.version)
    fragments = news_fragments(root)
    news_path = root / "NEWS.md"
    existing = release_section(news_path.read_text(), config.version)

    if existing is not None:
        if fragments:
            names = ", ".join(str(path) for path in fragments)
            raise ReleaseError(
                f"NEWS.md already has a {config.version} section; edit it "
                f"directly instead of adding late fragments: {names}"
            )
    else:
        if not fragments:
            raise ReleaseError(
                f"no news fragments or existing NEWS.md section for {config.version}"
            )
        runner(
            (
                sys.executable,
                "-m",
                "towncrier",
                "build",
                "--version",
                config.version,
                "--date",
                config.release_date,
                "--yes",
            ),
            root,
        )

    remaining_fragments = news_fragments(root)
    if remaining_fragments:
        names = ", ".join(str(path) for path in remaining_fragments)
        raise ReleaseError(f"Towncrier did not consume news fragments: {names}")

    release_notes(news_path.read_text(), config.version)
    return fragments


def copy_repository(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(
            ".git",
            ".sl",
            ".venv",
            ".pytest_cache",
            ".mypy_cache",
            "__pycache__",
            "*.egg-info",
            "build",
            "dist",
            "temp",
        ),
    )


def smoke_python(venv: Path) -> Path:
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def validate_tree(root: Path, version: str, runner: Runner = run) -> None:
    runner((sys.executable, "-m", "pytest", "-q"), root)
    runner((sys.executable, "-m", "build", "--no-isolation"), root)

    distributions = sorted((root / "dist").glob("*"))
    if not distributions:
        raise ReleaseError("release build produced no distributions")
    runner(
        (
            sys.executable,
            "-m",
            "twine",
            "check",
            *(str(path) for path in distributions),
        ),
        root,
    )

    wheels = sorted((root / "dist").glob("*.whl"))
    if len(wheels) != 1:
        raise ReleaseError(f"expected one wheel, found {len(wheels)}")
    smoke_venv = root / ".release-smoke"
    runner((sys.executable, "-m", "venv", str(smoke_venv)), root)
    python = smoke_python(smoke_venv)
    runner((str(python), "-m", "pip", "install", "--no-deps", str(wheels[0])), root)
    runner(
        (
            str(python),
            "-c",
            "import importlib.metadata as m; "
            f"assert m.version('{PROJECT_NAME}') == '{version}'",
        ),
        root,
    )


def pypi_published(version: str) -> bool:
    url = f"https://pypi.org/pypi/{PROJECT_NAME}/{version}/json"
    try:
        with urlopen(url, timeout=15) as response:
            if response.status != 200:
                raise ReleaseError(
                    f"could not verify PyPI release status for {version}: "
                    f"HTTP {response.status}"
                )
            return True
    except HTTPError as exc:
        if exc.code == 404:
            return False
        raise ReleaseError(
            f"could not verify PyPI release status for {version}: HTTP {exc.code}"
        ) from exc
    except URLError as exc:
        raise ReleaseError(
            f"could not verify PyPI release status for {version}: {exc.reason}"
        ) from exc


@dataclass
class PreparedRelease:
    workspace: Path
    notes: str
    fragments: list[Path]
    temporary: tempfile.TemporaryDirectory[str]


def local_dry_run(root: Path, config: ReleaseConfig) -> PreparedRelease:
    print(f"Preparing release {config.version} in a temporary workspace...")
    temporary = tempfile.TemporaryDirectory(prefix="asi-release-")
    workspace = Path(temporary.name) / "agent-skill-installer"
    copy_repository(root, workspace)
    subprocess.run(
        ("git", "init", "--quiet"),
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        ("git", "add", "--all"),
        cwd=workspace,
        check=True,
    )
    def runner(command: Sequence[str], cwd: Path) -> None:
        run(command, cwd, verbose=config.verbose)

    fragments = prepare_tree(workspace, config, runner=runner)
    print("Running tests and validating release artifacts...")
    validate_tree(workspace, config.version, runner=runner)
    notes = release_notes((workspace / "NEWS.md").read_text(), config.version)
    print("Local release validation passed.")
    return PreparedRelease(
        workspace=workspace,
        notes=notes,
        fragments=fragments,
        temporary=temporary,
    )


@dataclass(frozen=True)
class Vcs:
    command: str
    root: Path
    verbose: bool = False

    @classmethod
    def detect(cls, root: Path, *, verbose: bool = False) -> Vcs:
        if (root / ".sl").is_dir():
            return cls("sl", root, verbose)
        if (root / ".git").exists():
            return cls("git", root, verbose)
        raise ReleaseError("release publishing requires a Sapling or Git checkout")

    def status(self) -> str:
        if self.command == "sl":
            return capture(("sl", "status"), self.root)
        return capture(("git", "status", "--porcelain"), self.root)

    def require_clean_main(self) -> None:
        status = self.status()
        if status:
            raise ReleaseError("publish=true requires a clean working tree")
        if self.command == "sl":
            remotes = capture(
                ("sl", "log", "-r", ".", "-T", "{remotenames}"),
                self.root,
            )
            if "remote/main" not in remotes.split():
                raise ReleaseError("publish=true must start from remote/main")
        else:
            branch = capture(("git", "branch", "--show-current"), self.root)
            if branch != "main":
                raise ReleaseError("publish=true must start from main")
            head = capture(("git", "rev-parse", "HEAD"), self.root)
            remote_main = capture(("git", "rev-parse", "origin/main"), self.root)
            if head != remote_main:
                raise ReleaseError(
                    "publish=true must start exactly at origin/main"
                )

    def commit(self, message: str) -> None:
        if self.command == "sl":
            run(
                (
                    "sl",
                    "addremove",
                    "pyproject.toml",
                    "src/agent_skill_installer/__init__.py",
                    "NEWS.md",
                    "news",
                ),
                self.root,
                verbose=self.verbose,
            )
            run(("sl", "commit", "-m", message), self.root, verbose=self.verbose)
        else:
            run(
                (
                    "git",
                    "add",
                    "pyproject.toml",
                    "src/agent_skill_installer/__init__.py",
                    "NEWS.md",
                    "news",
                ),
                self.root,
                verbose=self.verbose,
            )
            run(
                ("git", "commit", "-m", message),
                self.root,
                verbose=self.verbose,
            )

    def push_main(self) -> None:
        if self.command == "sl":
            run(
                ("sl", "push", "--to", "main", "--rev", "."),
                self.root,
                verbose=self.verbose,
            )
        else:
            run(
                ("git", "push", "origin", "HEAD:main"),
                self.root,
                verbose=self.verbose,
            )

    def current_commit(self) -> str:
        if self.command == "sl":
            commit = capture(("sl", "log", "-r", ".", "-T", "{node}"), self.root)
        else:
            commit = capture(("git", "rev-parse", "HEAD"), self.root)
        if COMMIT_PATTERN.fullmatch(commit) is None:
            raise ReleaseError(f"could not resolve a full commit SHA: {commit!r}")
        return commit


def apply_prepared_tree(root: Path, workspace: Path, fragments: Sequence[Path]) -> None:
    for relative_path in RELEASE_PATHS:
        source = workspace / relative_path
        destination = root / relative_path
        destination.write_bytes(source.read_bytes())
    for relative_path in fragments:
        path = root / relative_path
        if path.exists():
            path.unlink()


def require_publish_approval(root: Path, *, verbose: bool = False) -> None:
    output = capture_run(
        (
            "gh",
            "api",
            f"repos/{REPOSITORY}/environments/pypi",
        ),
        root,
        verbose=verbose,
    )
    try:
        environment = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ReleaseError(
            "GitHub returned invalid metadata for the pypi environment"
        ) from exc

    reviewer_rules = [
        rule
        for rule in environment.get("protection_rules", [])
        if rule.get("type") == "required_reviewers" and rule.get("reviewers")
    ]
    if reviewer_rules:
        return

    environment_id = environment.get("id")
    if isinstance(environment_id, int):
        settings_url = (
            f"https://github.com/{REPOSITORY}/settings/environments/"
            f"{environment_id}/edit"
        )
    else:
        settings_url = f"https://github.com/{REPOSITORY}/settings/environments"
    raise ReleaseError(
        "the pypi environment must have at least one required reviewer before "
        f"publishing\nConfigure it here: {settings_url}"
    )


def dispatch_publish(root: Path, config: ReleaseConfig, commit: str) -> str:
    publish = "true" if config.publish else "false"
    output = capture_run(
        (
            "gh",
            "workflow",
            "run",
            "Publish",
            "--repo",
            REPOSITORY,
            "--ref",
            "main",
            "-f",
            f"version={config.version}",
            "-f",
            f"commit={commit}",
            "-f",
            f"publish={publish}",
        ),
        root,
        verbose=config.verbose,
    )
    match = re.search(
        rf"https://github[.]com/{re.escape(REPOSITORY)}/actions/runs/[0-9]+",
        output,
    )
    if match is None:
        raise ReleaseError(
            "GitHub dispatched the workflow but did not return its run URL; "
            f"find the exact run at https://github.com/{REPOSITORY}/actions/"
            "workflows/publish.yml"
        )
    return match.group(0)


def print_dispatch_result(config: ReleaseConfig, commit: str, run_url: str) -> None:
    mode = "release" if config.publish else "remote dry run"
    print(f"\nDispatched {mode} {config.version} from {commit}.")
    if config.publish:
        print(
            "Approval page (available after validation passes):\n"
            f"{run_url}\n"
            "Select Review deployments, choose pypi, then approve and deploy."
        )
    else:
        print(f"Workflow run: {run_url}")


def release(root: Path, config: ReleaseConfig) -> None:
    if config.commit:
        if config.publish and not pypi_published(config.version):
            require_publish_approval(root, verbose=config.verbose)
        print(
            f"Dispatching {'release' if config.publish else 'remote dry run'} "
            f"for {config.version} from {config.commit}."
        )
        run_url = dispatch_publish(root, config, config.commit)
        print_dispatch_result(config, config.commit, run_url)
        return

    if pypi_published(config.version):
        raise ReleaseError(
            f"{PROJECT_NAME} {config.version} is already published on PyPI"
        )

    vcs: Vcs | None = None
    if config.publish:
        vcs = Vcs.detect(root, verbose=config.verbose)
        vcs.require_clean_main()
        require_publish_approval(root, verbose=config.verbose)

    prepared = local_dry_run(root, config)
    print("\nRelease notes:\n")
    print(prepared.notes.rstrip())

    if not config.publish:
        print(
            f"\nDry run complete for {config.version}. "
            "No repository or external state was changed."
        )
        return

    assert vcs is not None
    apply_prepared_tree(root, prepared.workspace, prepared.fragments)
    if vcs.status():
        vcs.commit(f"Prepare {config.version} release")
        vcs.push_main()
    commit = vcs.current_commit()
    run_url = dispatch_publish(root, config, commit)
    print_dispatch_result(config, commit, run_url)


def main(arguments: Sequence[str] | None = None) -> int:
    try:
        config = parse_config(sys.argv[1:] if arguments is None else arguments)
        root = Path(__file__).resolve().parents[1]
        release(root, config)
    except (ReleaseError, subprocess.CalledProcessError) as exc:
        print(f"release error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
