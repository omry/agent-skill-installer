from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping


MANIFEST_VERSION = 1
PYPI_BASE_URL = "https://pypi.org/pypi"
PYPI_METADATA_TIMEOUT_SECONDS = 2.0
PYPI_DOWNLOAD_TIMEOUT_SECONDS = 10.0
AGENTS = ("codex", "claude")
SCOPES = ("repo", "global")


class InstallerError(Exception):
    pass


@dataclass(frozen=True)
class SkillProject:
    package_name: str
    import_name: str
    version: str
    skill_name: str
    description: str
    hook_blocks: Mapping[str, str] = field(default_factory=dict)
    bundled_skill_path: str = "_skill"
    bundled_skill_source: Path | None = None
    pypi_project_name: str | None = None
    pypi_base_url: str = PYPI_BASE_URL
    manifest_package_aliases: frozenset[str] = field(default_factory=frozenset)

    @property
    def marker_slug(self) -> str:
        return "".join(
            character.upper() if character.isalnum() else "-"
            for character in self.skill_name
        ).strip("-")

    @property
    def marker_start(self) -> str:
        return f"<!-- {self.marker_slug}-DISCOVERABILITY-START -->"

    @property
    def marker_end(self) -> str:
        return f"<!-- {self.marker_slug}-DISCOVERABILITY-END -->"

    @property
    def manifest_relative_path(self) -> Path:
        return Path("scripts") / f".{self.skill_name}-install.json"

    @property
    def sidecar_manifest_name(self) -> str:
        return f".{self.skill_name}-install.json"

    @property
    def pypi_name(self) -> str:
        return self.pypi_project_name or self.package_name

    @property
    def wheel_skill_prefix(self) -> PurePosixPath:
        return PurePosixPath(self.import_name, self.bundled_skill_path)

    def hook_block(self, agent: str) -> str:
        if agent in self.hook_blocks:
            return self.hook_blocks[agent]
        trigger = f"${self.skill_name}" if agent == "codex" else f"/{self.skill_name}"
        return (
            f"{self.marker_start}\n"
            f"## {self.skill_name} Discoverability\n\n"
            f"{self.description}\n\n"
            f"Use `{trigger}` when a prompt explicitly asks for this skill.\n"
            f"{self.marker_end}\n"
        )


@dataclass(frozen=True)
class TargetSpec:
    agent: str
    scope: str
    skill_dir: Path
    hook_path: Path
    hook_block: str
    marker_start: str
    marker_end: str


@dataclass(frozen=True)
class InstallResult:
    action: str
    agent: str
    scope: str
    skill_dir: Path
    hook_path: Path
    status: str
    version: str | None = None
    previous_version: str | None = None
    version_change: str | None = None
    install_mode: str = "copy"
    source_dir: Path | None = None


@dataclass(frozen=True)
class InstallSourceMetadata:
    packaged_version: str
    editable_available: bool
    local_version: str | None = None
    source_dir: Path | None = None
    repo_root: Path | None = None
    vcs: str | None = None
    commit: str | None = None
    dirty: bool | None = None


@dataclass(frozen=True)
class InstallationStatus:
    agent: str
    scope: str
    skill_dir: Path | None
    status: str
    version: str | None = None
    install_mode: str | None = None
    error: str | None = None


class Installer:
    def __init__(self, project: SkillProject) -> None:
        self.project = project

    def install(
        self,
        agents: Iterable[str],
        scope: str,
        *,
        repo: Path | None = None,
        home: Path | None = None,
        codex_home: Path | None = None,
        claude_home: Path | None = None,
        force: bool = False,
        editable: bool = False,
        pypi_version: str | None = None,
    ) -> list[InstallResult]:
        return install(
            self.project,
            agents,
            scope,
            repo=repo,
            home=home,
            codex_home=codex_home,
            claude_home=claude_home,
            force=force,
            editable=editable,
            pypi_version=pypi_version,
        )

    def uninstall(
        self,
        agents: Iterable[str],
        scope: str,
        *,
        repo: Path | None = None,
        home: Path | None = None,
        codex_home: Path | None = None,
        claude_home: Path | None = None,
    ) -> list[InstallResult]:
        return uninstall(
            self.project,
            agents,
            scope,
            repo=repo,
            home=home,
            codex_home=codex_home,
            claude_home=claude_home,
        )

    def inspect_installations(
        self,
        *,
        repo: Path | None = None,
        home: Path | None = None,
        codex_home: Path | None = None,
        claude_home: Path | None = None,
    ) -> list[InstallationStatus]:
        return inspect_installations(
            self.project,
            repo=repo,
            home=home,
            codex_home=codex_home,
            claude_home=claude_home,
        )

    def published_pypi_versions(
        self,
        *,
        limit: int = 20,
        timeout: float = PYPI_METADATA_TIMEOUT_SECONDS,
    ) -> list[str]:
        return published_pypi_versions(self.project, limit=limit, timeout=timeout)


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def manifest_package_version(manifest: dict[str, object] | None) -> str | None:
    if manifest is None:
        return None
    version = manifest.get("package_version")
    if isinstance(version, str) and version:
        return version
    legacy_version = manifest.get("installed_version")
    if isinstance(legacy_version, str) and legacy_version:
        return legacy_version
    return None


def version_key(version: str) -> tuple[tuple[tuple[int, int | str], ...], str]:
    parts: list[tuple[int, int | str]] = []
    for raw_part in version.replace("-", ".").split("."):
        if not raw_part:
            continue
        if raw_part.isdigit():
            parts.append((0, int(raw_part)))
        else:
            parts.append((1, raw_part))
    return tuple(parts), version


def compare_versions(left: str, right: str) -> int:
    left_key = version_key(left)
    right_key = version_key(right)
    if left_key == right_key:
        return 0
    return 1 if left_key > right_key else -1


def version_change(previous_version: str | None, new_version: str) -> str | None:
    if previous_version is None:
        return None
    comparison = compare_versions(new_version, previous_version)
    if comparison > 0:
        return "upgrade"
    if comparison < 0:
        return "downgrade"
    return "same"


def normalize_agents(agents: Iterable[str]) -> list[str]:
    selected: list[str] = []
    for raw_agent in agents:
        agent_values = [item.strip() for item in raw_agent.split(",")]
        if any(not item for item in agent_values):
            raise InstallerError(f"unknown agent target: {raw_agent}")
        if "all" in agent_values:
            if len(agent_values) > 1:
                raise InstallerError(
                    "agent target 'all' cannot be combined with explicit agents"
                )
            for item in AGENTS:
                if item not in selected:
                    selected.append(item)
            continue
        for agent in agent_values:
            if agent not in AGENTS:
                raise InstallerError(f"unknown agent target: {agent}")
            if agent not in selected:
                selected.append(agent)
    if not selected:
        raise InstallerError("at least one agent target is required")
    return selected


def validate_scope(scope: str) -> None:
    if scope not in SCOPES:
        joined = ", ".join(SCOPES)
        raise InstallerError(f"scope must be one of: {joined}")


def bundled_skill_root(project: SkillProject):
    if project.bundled_skill_source is not None:
        root = project.bundled_skill_source
        if root.joinpath("SKILL.md").is_file():
            return root
        raise InstallerError(f"bundled skill source does not contain SKILL.md: {root}")

    packaged = resources.files(project.import_name).joinpath(project.bundled_skill_path)
    if packaged.joinpath("SKILL.md").is_file():
        return packaged
    raise InstallerError(
        f"bundled skill files were not found for package {project.import_name}"
    )


def iter_bundled_skill_files(project: SkillProject):
    root = bundled_skill_root(project)

    def walk(node, prefix: Path):
        for child in sorted(node.iterdir(), key=lambda item: item.name):
            relative_path = prefix / child.name
            if child.is_dir():
                if child.name == "__pycache__":
                    continue
                yield from walk(child, relative_path)
            elif child.is_file():
                if child.suffix == ".pyc":
                    continue
                yield relative_path, child

    yield from walk(root, Path())


def local_skill_source_for_candidate(path: Path) -> tuple[Path, Path] | None:
    nested_skill = path / "skill" / "SKILL.md"
    if nested_skill.is_file():
        return path, path / "skill"
    root_skill = path / "SKILL.md"
    if root_skill.is_file():
        return path, path
    return None


def find_local_checkout_skill_source(
    start: Path | None = None,
) -> tuple[Path, Path] | None:
    current = (start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    root_skill_source: tuple[Path, Path] | None = None
    for candidate in (current, *current.parents):
        nested_skill = candidate / "skill" / "SKILL.md"
        if nested_skill.is_file():
            return candidate, candidate / "skill"
        if root_skill_source is None and (candidate / "SKILL.md").is_file():
            root_skill_source = candidate, candidate
    return root_skill_source


def run_vcs_command(args: list[str], cwd: Path) -> str | None:
    try:
        completed = subprocess.run(
            args,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def repo_vcs_kind(repo_root: Path) -> str | None:
    if (repo_root / ".sl").exists():
        return "sl"
    if (repo_root / ".git").exists():
        return "git"
    return None


def repo_commit(repo_root: Path, vcs: str) -> str | None:
    if vcs == "sl":
        return run_vcs_command(["sl", "log", "-r", ".", "-T", "{node|short}"], repo_root)
    if vcs == "git":
        return run_vcs_command(["git", "rev-parse", "--short", "HEAD"], repo_root)
    return None


def repo_dirty(repo_root: Path, vcs: str) -> bool | None:
    if vcs == "sl":
        output = run_vcs_command(["sl", "status"], repo_root)
    elif vcs == "git":
        output = run_vcs_command(["git", "status", "--porcelain"], repo_root)
    else:
        return None
    return None if output is None else bool(output)


def local_checkout_version(root: Path, fallback_version: str) -> str:
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return fallback_version
    for line in pyproject.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("version"):
            _, _, value = stripped.partition("=")
            version = value.strip().strip('"')
            if version:
                return version
    return fallback_version


def install_source_metadata(
    project: SkillProject,
    start: Path | None = None,
) -> InstallSourceMetadata:
    source = find_local_checkout_skill_source(start)
    if source is None:
        return InstallSourceMetadata(
            packaged_version=project.version,
            editable_available=False,
        )

    local_root, source_dir = source
    repo_root = find_repo_root(source_dir)
    if repo_root is None:
        return InstallSourceMetadata(
            packaged_version=project.version,
            editable_available=False,
        )

    vcs = repo_vcs_kind(repo_root)
    if vcs is None:
        return InstallSourceMetadata(
            packaged_version=project.version,
            editable_available=False,
        )

    return InstallSourceMetadata(
        packaged_version=project.version,
        editable_available=True,
        local_version=local_checkout_version(local_root, project.version),
        source_dir=source_dir,
        repo_root=repo_root,
        vcs=vcs,
        commit=repo_commit(repo_root, vcs),
        dirty=repo_dirty(repo_root, vcs),
    )


def local_checkout_skill_root(
    project: SkillProject,
    start: Path | None = None,
) -> Path:
    metadata = install_source_metadata(project, start)
    if metadata.source_dir is None or not metadata.editable_available:
        raise InstallerError(
            "--editable requires running from a git or sl checkout with "
            "SKILL.md or skill/SKILL.md"
        )
    return metadata.source_dir


def is_repo_root(path: Path) -> bool:
    return (path / ".sl").exists() or (path / ".git").exists()


def find_repo_root(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if is_repo_root(candidate):
            return candidate
    return None


def resolve_repo_root(repo: Path | None) -> Path:
    root = find_repo_root(repo)
    if root is None:
        location = str((repo or Path.cwd()).resolve())
        raise InstallerError(
            f"repo scope requires a .git or .sl repository above {location}"
        )
    return root


def resolve_home(home: Path | None = None) -> Path:
    return (home or Path.home()).expanduser().resolve()


def resolve_agent_home(
    agent_home: Path | None,
    *,
    default_user_home: Path | None,
    default_name: str,
) -> Path:
    if agent_home is not None:
        return agent_home.expanduser().resolve()
    return resolve_home(default_user_home) / default_name


def target_spec(
    project: SkillProject,
    agent: str,
    scope: str,
    *,
    repo: Path | None = None,
    home: Path | None = None,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
) -> TargetSpec:
    if agent not in AGENTS:
        raise InstallerError(f"unknown agent target: {agent}")
    validate_scope(scope)

    home_path = resolve_home(home)
    repo_root = resolve_repo_root(repo) if scope == "repo" else None

    if agent == "codex":
        if scope == "global":
            codex_dir = resolve_agent_home(
                codex_home,
                default_user_home=home_path,
                default_name=".codex",
            )
            skill_dir = codex_dir / "skills" / project.skill_name
            hook_path = codex_dir / "AGENTS.md"
        else:
            assert repo_root is not None
            skill_dir = repo_root / ".codex" / "skills" / project.skill_name
            hook_path = repo_root / "AGENTS.md"
    else:
        if scope == "global":
            claude_dir = resolve_agent_home(
                claude_home,
                default_user_home=home_path,
                default_name=".claude",
            )
            skill_dir = claude_dir / "skills" / project.skill_name
            hook_path = claude_dir / "CLAUDE.md"
        else:
            assert repo_root is not None
            skill_dir = repo_root / ".claude" / "skills" / project.skill_name
            hook_path = repo_root / "CLAUDE.md"

    return TargetSpec(
        agent=agent,
        scope=scope,
        skill_dir=skill_dir,
        hook_path=hook_path,
        hook_block=project.hook_block(agent),
        marker_start=project.marker_start,
        marker_end=project.marker_end,
    )


def manifest_path(project: SkillProject, skill_dir: Path) -> Path:
    if skill_dir.is_symlink():
        return skill_dir.parent / project.sidecar_manifest_name
    return skill_dir / project.manifest_relative_path


def read_manifest(
    project: SkillProject,
    skill_dir: Path,
) -> dict[str, object] | None:
    path = manifest_path(project, skill_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise InstallerError(f"invalid install manifest: {path}") from error
    if not isinstance(data, dict):
        raise InstallerError(f"install manifest must be a JSON object: {path}")
    accepted_packages = {project.package_name, *project.manifest_package_aliases}
    if data.get("package") not in accepted_packages:
        raise InstallerError(
            f"install manifest is not for {project.package_name}: {path}"
        )
    return data


def inspect_installation(
    project: SkillProject,
    agent: str,
    scope: str,
    *,
    repo: Path | None = None,
    home: Path | None = None,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
) -> InstallationStatus:
    try:
        spec = target_spec(
            project,
            agent,
            scope,
            repo=repo,
            home=home,
            codex_home=codex_home,
            claude_home=claude_home,
        )
    except InstallerError as error:
        return InstallationStatus(
            agent=agent,
            scope=scope,
            skill_dir=None,
            status="unavailable",
            error=str(error),
        )

    if not path_exists(spec.skill_dir):
        return InstallationStatus(
            agent=agent,
            scope=scope,
            skill_dir=spec.skill_dir,
            status="not-installed",
        )

    try:
        manifest = read_manifest(project, spec.skill_dir)
    except InstallerError as error:
        return InstallationStatus(
            agent=agent,
            scope=scope,
            skill_dir=spec.skill_dir,
            status="unowned",
            error=str(error),
        )

    if manifest is None:
        return InstallationStatus(
            agent=agent,
            scope=scope,
            skill_dir=spec.skill_dir,
            status="unowned",
        )

    install_mode = manifest.get("install_mode")
    return InstallationStatus(
        agent=agent,
        scope=scope,
        skill_dir=spec.skill_dir,
        status="installed",
        version=manifest_package_version(manifest),
        install_mode=install_mode if isinstance(install_mode, str) else None,
    )


def inspect_installations(
    project: SkillProject,
    *,
    repo: Path | None = None,
    home: Path | None = None,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
) -> list[InstallationStatus]:
    return [
        inspect_installation(
            project,
            agent,
            scope,
            repo=repo,
            home=home,
            codex_home=codex_home,
            claude_home=claude_home,
        )
        for agent in AGENTS
        for scope in SCOPES
    ]


def missing_parent_dirs(path: Path) -> list[Path]:
    missing: list[Path] = []
    current = path.parent
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    return missing


def remember_created_dirs(created_dirs: list[Path], path: Path) -> None:
    for directory in missing_parent_dirs(path):
        if directory not in created_dirs:
            created_dirs.append(directory)
    path.parent.mkdir(parents=True, exist_ok=True)


def remove_existing_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def remove_manifest_at(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def copy_bundled_skill(project: SkillProject, skill_dir: Path) -> list[str]:
    copied: list[str] = []
    for relative_path, source in iter_bundled_skill_files(project):
        target = skill_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        copied.append(relative_path.as_posix())
    return copied


def pypi_release_url(project: SkillProject, version: str) -> str:
    quoted_version = urllib.parse.quote(version.strip(), safe="")
    return f"{project.pypi_base_url}/{project.pypi_name}/{quoted_version}/json"


def pypi_project_url(project: SkillProject) -> str:
    return f"{project.pypi_base_url}/{project.pypi_name}/json"


def fetch_json_url(
    url: str,
    *,
    timeout: float = PYPI_METADATA_TIMEOUT_SECONDS,
) -> dict[str, object]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = response.read()
    except (TimeoutError, urllib.error.URLError) as error:
        raise InstallerError(f"failed to fetch PyPI metadata: {error}") from error

    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InstallerError("PyPI metadata response was not valid JSON") from error
    if not isinstance(data, dict):
        raise InstallerError("PyPI metadata response was not a JSON object")
    return data


def find_wheel_download_url(
    project: SkillProject,
    release_metadata: dict[str, object],
) -> tuple[str, str]:
    urls = release_metadata.get("urls")
    if not isinstance(urls, list):
        raise InstallerError("PyPI metadata did not contain release files")

    wheels: list[tuple[str, str]] = []
    for item in urls:
        if not isinstance(item, dict):
            continue
        if item.get("packagetype") != "bdist_wheel":
            continue
        filename = item.get("filename")
        url = item.get("url")
        if isinstance(filename, str) and isinstance(url, str):
            wheels.append((filename, url))

    if not wheels:
        raise InstallerError(f"no wheel distribution found on PyPI for {project.pypi_name}")
    return sorted(wheels)[0]


def release_has_wheel(files: object) -> bool:
    if not isinstance(files, list):
        return False
    return any(
        isinstance(item, dict) and item.get("packagetype") == "bdist_wheel"
        for item in files
    )


def published_pypi_versions(
    project: SkillProject,
    *,
    limit: int = 20,
    timeout: float = PYPI_METADATA_TIMEOUT_SECONDS,
) -> list[str]:
    metadata = fetch_json_url(pypi_project_url(project), timeout=timeout)
    releases = metadata.get("releases")
    if not isinstance(releases, dict):
        raise InstallerError("PyPI metadata did not contain releases")

    versions = [
        version
        for version, files in releases.items()
        if isinstance(version, str) and release_has_wheel(files)
    ]
    return sorted(versions, key=version_key, reverse=True)[:limit]


def download_url(
    url: str,
    target: Path,
    *,
    timeout: float = PYPI_DOWNLOAD_TIMEOUT_SECONDS,
) -> Path:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response, target.open(
            "wb"
        ) as output:
            shutil.copyfileobj(response, output)
    except (TimeoutError, urllib.error.URLError) as error:
        raise InstallerError(f"failed to download PyPI wheel: {error}") from error
    except OSError as error:
        raise InstallerError(f"failed to write PyPI wheel: {target}") from error
    return target


def download_pypi_wheel(
    project: SkillProject,
    version: str,
    download_dir: Path,
) -> Path:
    version = version.strip()
    if not version:
        raise InstallerError("PyPI version must not be empty")
    filename, url = find_wheel_download_url(
        project,
        fetch_json_url(pypi_release_url(project, version)),
    )
    return download_url(url, download_dir / filename)


def wheel_skill_relative_path(
    project: SkillProject,
    filename: str,
) -> Path | None:
    path = PurePosixPath(filename)
    if path.is_absolute() or ".." in path.parts:
        raise InstallerError(f"unsafe path in PyPI wheel: {filename}")
    prefix = project.wheel_skill_prefix
    if path.parts[: len(prefix.parts)] != prefix.parts:
        return None

    relative = PurePosixPath(*path.parts[len(prefix.parts) :])
    if not relative.parts:
        return None
    if "__pycache__" in relative.parts or relative.suffix == ".pyc":
        return None
    if relative == PurePosixPath(project.manifest_relative_path.as_posix()):
        return None
    return Path(*relative.parts)


def copy_pypi_wheel_skill(
    project: SkillProject,
    wheel_path: Path,
    skill_dir: Path,
) -> list[str]:
    copied: list[str] = []
    try:
        with zipfile.ZipFile(wheel_path) as wheel:
            for info in sorted(wheel.infolist(), key=lambda item: item.filename):
                if info.is_dir():
                    continue
                relative_path = wheel_skill_relative_path(project, info.filename)
                if relative_path is None:
                    continue
                target = skill_dir / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(wheel.read(info))
                copied.append(relative_path.as_posix())
    except zipfile.BadZipFile as error:
        raise InstallerError(f"PyPI wheel is not a valid zip file: {wheel_path}") from error

    if "SKILL.md" not in copied:
        raise InstallerError(
            f"PyPI wheel did not contain {project.wheel_skill_prefix.as_posix()}/SKILL.md"
        )
    return copied


def iter_local_skill_files(project: SkillProject, root: Path):
    for source in sorted(root.rglob("*")):
        relative_path = source.relative_to(root)
        if "__pycache__" in relative_path.parts:
            continue
        if source.is_dir():
            continue
        if source.suffix == ".pyc":
            continue
        if relative_path == project.manifest_relative_path:
            continue
        yield relative_path, source


def symlink_local_skill(
    project: SkillProject,
    skill_dir: Path,
    source_root: Path,
) -> list[str]:
    skill_dir.symlink_to(source_root, target_is_directory=True)
    return [
        relative_path.as_posix()
        for relative_path, _source in iter_local_skill_files(project, source_root)
    ]


def normalize_block_text(text: str) -> str:
    return text.rstrip() + "\n"


def replace_marked_block(
    text: str,
    block: str,
    *,
    start_marker: str,
    end_marker: str,
) -> str:
    block = normalize_block_text(block)
    start = text.find(start_marker)
    if start == -1:
        prefix = text.rstrip()
        if not prefix:
            return block
        return prefix + "\n\n" + block

    end = text.find(end_marker, start)
    if end == -1:
        raise InstallerError(f"found {start_marker!r} without matching {end_marker!r}")
    end += len(end_marker)
    while end < len(text) and text[end] in " \t":
        end += 1
    if end < len(text) and text[end] == "\n":
        end += 1

    before = text[:start].rstrip()
    after = text[end:].lstrip("\n").rstrip()
    parts = [part for part in (before, block.rstrip(), after) if part]
    return "\n\n".join(parts) + "\n"


def remove_marked_block(
    text: str,
    *,
    start_marker: str,
    end_marker: str,
) -> tuple[str, bool]:
    start = text.find(start_marker)
    if start == -1:
        return text, False
    end = text.find(end_marker, start)
    if end == -1:
        raise InstallerError(f"found {start_marker!r} without matching {end_marker!r}")
    end += len(end_marker)
    while end < len(text) and text[end] in " \t":
        end += 1
    if end < len(text) and text[end] == "\n":
        end += 1

    before = text[:start].rstrip()
    after = text[end:].lstrip("\n").rstrip()
    parts = [part for part in (before, after) if part]
    if not parts:
        return "", True
    return "\n\n".join(parts) + "\n", True


def install_hook(spec: TargetSpec) -> None:
    existing = spec.hook_path.read_text() if spec.hook_path.exists() else ""
    spec.hook_path.write_text(
        replace_marked_block(
            existing,
            spec.hook_block,
            start_marker=spec.marker_start,
            end_marker=spec.marker_end,
        )
    )


def uninstall_hook(spec: TargetSpec, *, delete_if_empty: bool) -> bool:
    if not spec.hook_path.exists():
        return False
    updated, changed = remove_marked_block(
        spec.hook_path.read_text(),
        start_marker=spec.marker_start,
        end_marker=spec.marker_end,
    )
    if not changed:
        return False
    if updated or not delete_if_empty:
        spec.hook_path.write_text(updated)
    else:
        spec.hook_path.unlink()
    return True


def write_manifest(
    project: SkillProject,
    spec: TargetSpec,
    *,
    files: list[str],
    created_dirs: list[Path],
    created_hook_file: bool,
    package_version: str,
    install_mode: str,
    source_dir: Path | None = None,
) -> None:
    path = manifest_path(project, spec.skill_dir)
    manifest_files = [] if spec.skill_dir.is_symlink() else [
        project.manifest_relative_path.as_posix()
    ]
    data = {
        "version": MANIFEST_VERSION,
        "package": project.package_name,
        "package_version": package_version,
        "skill_name": project.skill_name,
        "agent": spec.agent,
        "scope": spec.scope,
        "installed_at": utc_now(),
        "skill_dir": str(spec.skill_dir),
        "hook_path": str(spec.hook_path),
        "hook_marker_start": spec.marker_start,
        "hook_marker_end": spec.marker_end,
        "created_hook_file": created_hook_file,
        "created_dirs": [str(path) for path in created_dirs],
        "files": sorted(files + manifest_files),
        "install_mode": install_mode,
        "manifest_path": str(path),
    }
    if source_dir is not None:
        data["source_dir"] = str(source_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def install_target(
    project: SkillProject,
    spec: TargetSpec,
    *,
    force: bool = False,
    editable: bool = False,
    pypi_version: str | None = None,
    pypi_wheel_path: Path | None = None,
) -> InstallResult:
    if editable and pypi_version is not None:
        raise InstallerError("--editable cannot be combined with --pypi-version")

    skill_exists = path_exists(spec.skill_dir)
    previous_manifest = read_manifest(project, spec.skill_dir) if skill_exists else None
    previous_manifest_file = (
        manifest_path(project, spec.skill_dir)
        if skill_exists and previous_manifest is not None
        else None
    )
    previous_version = manifest_package_version(previous_manifest)
    package_version = pypi_version or project.version
    install_mode = (
        "pypi" if pypi_version is not None else "editable" if editable else "copy"
    )
    source_dir = local_checkout_skill_root(project) if editable else None
    if skill_exists and previous_manifest is None and not force:
        raise InstallerError(
            f"refusing to replace unowned skill directory: {spec.skill_dir}"
        )

    created_dirs = [
        Path(path)
        for path in (previous_manifest or {}).get("created_dirs", [])
        if isinstance(path, str)
    ]
    created_hook_file = bool(
        (previous_manifest or {}).get("created_hook_file", not spec.hook_path.exists())
    )

    if skill_exists:
        remove_existing_path(spec.skill_dir)
    if previous_manifest_file is not None:
        remove_manifest_at(previous_manifest_file)

    remember_created_dirs(created_dirs, spec.skill_dir)
    if pypi_version is not None:
        spec.skill_dir.mkdir(parents=True, exist_ok=True)
        if pypi_wheel_path is None:
            raise InstallerError("missing PyPI wheel for requested install source")
        skill_files = copy_pypi_wheel_skill(project, pypi_wheel_path, spec.skill_dir)
    elif source_dir is not None:
        spec.skill_dir.parent.mkdir(parents=True, exist_ok=True)
        skill_files = symlink_local_skill(project, spec.skill_dir, source_dir)
    else:
        spec.skill_dir.mkdir(parents=True, exist_ok=True)
        skill_files = copy_bundled_skill(project, spec.skill_dir)

    remember_created_dirs(created_dirs, spec.hook_path)
    install_hook(spec)
    write_manifest(
        project,
        spec,
        files=skill_files,
        created_dirs=created_dirs,
        created_hook_file=created_hook_file,
        package_version=package_version,
        install_mode=install_mode,
        source_dir=source_dir,
    )
    return InstallResult(
        action="install",
        agent=spec.agent,
        scope=spec.scope,
        skill_dir=spec.skill_dir,
        hook_path=spec.hook_path,
        status="installed",
        version=package_version,
        previous_version=previous_version,
        version_change=version_change(previous_version, package_version),
        install_mode=install_mode,
        source_dir=source_dir,
    )


def remove_created_dirs(paths: Iterable[object]) -> None:
    directories = [Path(path) for path in paths if isinstance(path, str)]
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        try:
            directory.rmdir()
        except FileNotFoundError:
            continue
        except OSError:
            continue


def uninstall_target(project: SkillProject, spec: TargetSpec) -> InstallResult:
    skill_exists = path_exists(spec.skill_dir)
    manifest = read_manifest(project, spec.skill_dir) if skill_exists else None
    manifest_file = manifest_path(project, spec.skill_dir) if manifest is not None else None
    package_version = manifest_package_version(manifest)
    if skill_exists and manifest is None:
        raise InstallerError(
            f"refusing to remove unowned skill directory: {spec.skill_dir}"
        )

    delete_hook_if_empty = bool((manifest or {}).get("created_hook_file", False))
    uninstall_hook(spec, delete_if_empty=delete_hook_if_empty)

    if manifest_file is not None:
        remove_manifest_at(manifest_file)
    if skill_exists:
        remove_existing_path(spec.skill_dir)
    if manifest is not None:
        remove_created_dirs(manifest.get("created_dirs", []))

    return InstallResult(
        action="uninstall",
        agent=spec.agent,
        scope=spec.scope,
        skill_dir=spec.skill_dir,
        hook_path=spec.hook_path,
        status="removed",
        version=package_version,
    )


def install(
    project: SkillProject,
    agents: Iterable[str],
    scope: str,
    *,
    repo: Path | None = None,
    home: Path | None = None,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
    force: bool = False,
    editable: bool = False,
    pypi_version: str | None = None,
) -> list[InstallResult]:
    if editable and pypi_version is not None:
        raise InstallerError("--editable cannot be combined with --pypi-version")

    selected_agents = normalize_agents(agents)

    def install_targets(pypi_wheel_path: Path | None = None) -> list[InstallResult]:
        return [
            install_target(
                project,
                target_spec(
                    project,
                    agent,
                    scope,
                    repo=repo,
                    home=home,
                    codex_home=codex_home,
                    claude_home=claude_home,
                ),
                force=force,
                editable=editable,
                pypi_version=pypi_version,
                pypi_wheel_path=pypi_wheel_path,
            )
            for agent in selected_agents
        ]

    if pypi_version is None:
        return install_targets()

    with tempfile.TemporaryDirectory(prefix="skill-installer-pypi-") as temp_dir:
        wheel_path = download_pypi_wheel(project, pypi_version, Path(temp_dir))
        return install_targets(wheel_path)


def uninstall(
    project: SkillProject,
    agents: Iterable[str],
    scope: str,
    *,
    repo: Path | None = None,
    home: Path | None = None,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
) -> list[InstallResult]:
    return [
        uninstall_target(
            project,
            target_spec(
                project,
                agent,
                scope,
                repo=repo,
                home=home,
                codex_home=codex_home,
                claude_home=claude_home,
            ),
        )
        for agent in normalize_agents(agents)
    ]


def default_repo_path() -> Path:
    return Path.cwd()


def describe_target(agent: str, scope: str) -> str:
    return f"{agent}/{scope}"


def running_on_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()
