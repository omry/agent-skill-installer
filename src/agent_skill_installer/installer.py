from __future__ import annotations

import json
import platform as platform_module
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
from typing import Callable, Iterable, Mapping

from omegaconf import OmegaConf
from omegaconf.errors import OmegaConfBaseException
from yaml import YAMLError

from .config import CONFIG_FILE_NAME, AgentInstructions, InstallerConfig, load_installer_config_text


MANIFEST_VERSION = 1
PYPI_BASE_URL = "https://pypi.org/pypi"
PYPI_METADATA_TIMEOUT_SECONDS = 2.0
PYPI_DOWNLOAD_TIMEOUT_SECONDS = 10.0
GITHUB_DOWNLOAD_TIMEOUT_SECONDS = 10.0
DEFAULT_GITHUB_REF = "main"
AGENTS = ("codex", "claude")
SCOPES = ("repo", "global")


class InstallerError(Exception):
    pass


def normalize_platform_os(system_platform: str | None = None) -> str:
    value = (system_platform or sys.platform).strip().lower()
    if value.startswith("linux"):
        return "linux"
    if value == "darwin":
        return "darwin"
    if value.startswith(("win32", "cygwin", "msys")):
        return "windows"
    raise InstallerError(f"unsupported platform OS: {system_platform or sys.platform}")


def normalize_platform_arch(machine: str | None = None) -> str:
    value = (machine or platform_module.machine()).strip().lower()
    normalized = value.replace("-", "_")
    if normalized in {"x86_64", "amd64"}:
        return "amd64"
    if normalized in {"aarch64", "arm64"}:
        return "arm64"
    raise InstallerError(f"unsupported platform arch: {machine or platform_module.machine()}")


def local_platform_values(
    *,
    system_platform: str | None = None,
    machine: str | None = None,
) -> dict[str, str]:
    os_name = normalize_platform_os(system_platform)
    arch = normalize_platform_arch(machine)
    return {
        "os": os_name,
        "arch": arch,
        "platform": f"{os_name}-{arch}",
    }


def render_platform_template(template: str) -> str:
    try:
        return template.format(**local_platform_values())
    except KeyError as error:
        name = str(error).strip("'")
        raise InstallerError(
            f"unknown platform_specific template field: {name}"
        ) from error
    except ValueError as error:
        raise InstallerError(f"invalid platform_specific template: {error}") from error


@dataclass(frozen=True)
class SkillProject:
    package_name: str
    import_name: str
    version: str
    skill_name: str
    description: str
    hook_blocks: Mapping[str, str] = field(default_factory=dict)
    installer_config: InstallerConfig | None = None
    bundled_skill_path: str = "_skill"
    bundled_skill_source: Path | None = None
    pypi_project_name: str | None = None
    cli_name: str | None = None
    pypi_base_url: str = PYPI_BASE_URL
    manifest_package_aliases: frozenset[str] = field(default_factory=frozenset)
    marker_slug_override: str | None = None
    source_skill_name: str | None = None
    source_skill_path: str | None = None

    @property
    def marker_slug(self) -> str:
        if self.marker_slug_override is not None:
            return self.marker_slug_override
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
    def command_name(self) -> str:
        return self.cli_name or self.package_name

    @property
    def wheel_skill_prefix(self) -> PurePosixPath:
        return PurePosixPath(self.import_name, self.bundled_skill_path)

    def config_instructions(self, agent: str) -> AgentInstructions | None:
        config = self.installer_config
        if config is None:
            config = load_packaged_installer_config(self)
        if config is None:
            return None
        agents = config.installer.agents
        if agent == "codex" and agents.codex is not None:
            return agents.codex.instructions
        if agent == "claude" and agents.claude is not None:
            return agents.claude.instructions
        return None

    def hook_block(self, agent: str) -> str:
        instructions = self.config_instructions(agent)
        if instructions is not None:
            return (
                f"{self.marker_start}\n"
                f"## {instructions.title}\n\n"
                f"{instructions.body.rstrip()}\n"
                f"{self.marker_end}\n"
            )
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


def load_packaged_installer_config(project: SkillProject) -> InstallerConfig | None:
    if project.bundled_skill_source is not None:
        source = project.bundled_skill_source / CONFIG_FILE_NAME
        if source.is_file():
            return load_installer_config_text(source.read_text(), source=source)
        return None

    config = resources.files(project.import_name).joinpath(
        project.bundled_skill_path,
        CONFIG_FILE_NAME,
    )
    if not config.is_file():
        return None
    return load_installer_config_text(
        config.read_text(),
        source=f"{project.import_name}/{project.bundled_skill_path}/{CONFIG_FILE_NAME}",
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
    source_url: str | None = None
    source_ref: str | None = None
    source_path: str | None = None


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
class GithubSource:
    url: str
    owner: str
    repo: str
    ref: str = DEFAULT_GITHUB_REF
    path: PurePosixPath | None = None

    @property
    def version_label(self) -> str:
        return self.ref


@dataclass(frozen=True)
class InstallationStatus:
    agent: str
    scope: str
    skill_dir: Path | None
    status: str
    version: str | None = None
    install_mode: str | None = None
    skill_name: str | None = None
    package_name: str | None = None
    manifest_path: Path | None = None
    hook_path: Path | None = None
    source_url: str | None = None
    source_ref: str | None = None
    source_path: str | None = None
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
        github_url: str | None = None,
        github_ref: str | None = None,
        github_path: str | None = None,
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
            github_url=github_url,
            github_ref=github_ref,
            github_path=github_path,
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

    def discover_managed_installations(
        self,
        *,
        repo: Path | None = None,
        home: Path | None = None,
        codex_home: Path | None = None,
        claude_home: Path | None = None,
    ) -> list[InstallationStatus]:
        return discover_managed_installations(
            self.project,
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


def skill_frontmatter(skill_text: str, source: Path | PurePosixPath | str) -> str | None:
    lines = skill_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[1:index]) + "\n"
    raise InstallerError(
        f"invalid SKILL.md YAML frontmatter in {source}: missing closing ---"
    )


def validate_skill_frontmatter_text(
    skill_text: str,
    source: Path | PurePosixPath | str,
) -> None:
    frontmatter = skill_frontmatter(skill_text, source)
    if frontmatter is None:
        return
    try:
        loaded = OmegaConf.create(frontmatter)
        parsed = OmegaConf.to_container(loaded, resolve=False)
    except (OmegaConfBaseException, YAMLError) as error:
        raise InstallerError(
            f"invalid SKILL.md YAML frontmatter in {source}: {error}"
        ) from error
    if not isinstance(parsed, dict):
        raise InstallerError(
            f"invalid SKILL.md YAML frontmatter in {source}: expected a mapping"
        )


def validate_skill_frontmatter_file(skill_file) -> None:
    try:
        skill_text = skill_file.read_text()
    except OSError as error:
        raise InstallerError(f"failed to read SKILL.md: {skill_file}") from error
    validate_skill_frontmatter_text(skill_text, skill_file)


def validate_skill_root(root) -> None:
    validate_skill_frontmatter_file(root.joinpath("SKILL.md"))


def zip_relative_path(filename: str) -> PurePosixPath | None:
    path = PurePosixPath(filename)
    if path.is_absolute() or ".." in path.parts:
        raise InstallerError(f"unsafe path in zip archive: {filename}")
    if not path.parts:
        return None
    return path


def validate_zip_skill_frontmatter(
    archive: zipfile.ZipFile,
    skill_path: PurePosixPath,
    *,
    relative_path_for: Callable[[str], PurePosixPath | None] = zip_relative_path,
    source: Path | PurePosixPath | str,
) -> None:
    for info in archive.infolist():
        if info.is_dir():
            continue
        relative_path = relative_path_for(info.filename)
        if relative_path != skill_path:
            continue
        try:
            skill_text = archive.read(info).decode("utf-8")
        except UnicodeDecodeError as error:
            raise InstallerError(f"SKILL.md is not valid UTF-8 in {source}") from error
        validate_skill_frontmatter_text(skill_text, source)
        return
    raise InstallerError(f"archive did not contain {skill_path.as_posix()}")


def validate_install_skill_source(
    project: SkillProject,
    *,
    source_dir: Path | None = None,
    pypi_wheel_path: Path | None = None,
    github_archive_path: Path | None = None,
    github_source: GithubSource | None = None,
) -> None:
    if pypi_wheel_path is not None:
        try:
            with zipfile.ZipFile(pypi_wheel_path) as wheel:
                skill_path = project.wheel_skill_prefix / "SKILL.md"
                validate_zip_skill_frontmatter(
                    wheel,
                    skill_path,
                    source=f"{pypi_wheel_path}:{skill_path.as_posix()}",
                )
        except zipfile.BadZipFile as error:
            raise InstallerError(
                f"PyPI wheel is not a valid zip file: {pypi_wheel_path}"
            ) from error
        return

    if github_source is not None:
        if github_archive_path is None:
            raise InstallerError("missing GitHub archive for requested install source")
        try:
            with zipfile.ZipFile(github_archive_path) as archive:
                skill_prefix = github_archive_skill_prefix(
                    archive,
                    github_source.path,
                )
                skill_path = prefixed_skill_file(skill_prefix)
                validate_zip_skill_frontmatter(
                    archive,
                    skill_path,
                    relative_path_for=github_archive_relative_path,
                    source=f"{github_source.url}:{skill_path.as_posix()}",
                )
        except zipfile.BadZipFile as error:
            raise InstallerError(
                f"GitHub archive is not a valid zip file: {github_archive_path}"
            ) from error
        return

    validate_skill_root(source_dir or bundled_skill_root(project))


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


def normalize_github_skill_path(value: str | None) -> PurePosixPath | None:
    if value is None:
        return None
    text = value.strip().strip("/")
    if not text or text == ".":
        return None
    path = PurePosixPath(urllib.parse.unquote(text))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise InstallerError(f"unsafe GitHub skill path: {value}")
    if path.name == "SKILL.md":
        path = path.parent
    return None if path == PurePosixPath(".") else path


def strip_dot_git(repo: str) -> str:
    return repo[:-4] if repo.endswith(".git") else repo


def parse_github_url(
    url: str,
    *,
    ref: str | None = None,
    path: str | None = None,
) -> GithubSource:
    text = url.strip()
    if not text:
        raise InstallerError("GitHub URL must not be empty")

    owner: str
    repo: str
    url_ref: str | None = None
    url_path: str | None = None

    if text.startswith("git@github.com:"):
        rest = text.removeprefix("git@github.com:").strip("/")
        parts = [part for part in rest.split("/") if part]
        if len(parts) != 2:
            raise InstallerError("GitHub SSH URL must look like git@github.com:OWNER/REPO.git")
        owner, repo = parts
        repo = strip_dot_git(repo)
    else:
        parsed = urllib.parse.urlparse(text)
        host = parsed.netloc.lower()
        if parsed.scheme not in {"http", "https"} or host not in {
            "github.com",
            "www.github.com",
        }:
            raise InstallerError("GitHub URL must use https://github.com/OWNER/REPO")
        parts = [
            urllib.parse.unquote(part)
            for part in parsed.path.split("/")
            if part
        ]
        if len(parts) < 2:
            raise InstallerError("GitHub URL must include OWNER/REPO")
        owner, repo = parts[:2]
        repo = strip_dot_git(repo)
        remaining = parts[2:]
        query = urllib.parse.parse_qs(parsed.query)
        url_ref = query.get("ref", [None])[0]
        url_path = query.get("path", [None])[0]
        if remaining:
            kind = remaining[0]
            if kind not in {"tree", "blob"} or len(remaining) < 2:
                raise InstallerError(
                    "GitHub URL path must be a repository root or "
                    "/tree/REF/SKILL_PATH"
                )
            url_ref = remaining[1]
            if len(remaining) > 2:
                url_path = "/".join(remaining[2:])

    owner = owner.strip()
    repo = repo.strip()
    if not owner or not repo:
        raise InstallerError("GitHub URL must include OWNER/REPO")

    selected_ref = (ref or url_ref or DEFAULT_GITHUB_REF).strip()
    if not selected_ref:
        raise InstallerError("GitHub ref must not be empty")
    selected_path = normalize_github_skill_path(path if path is not None else url_path)
    return GithubSource(
        url=text,
        owner=owner,
        repo=repo,
        ref=selected_ref,
        path=selected_path,
    )


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

    layouts: dict[str, dict[str, str]] = {
        "codex": {
            "default_home": ".codex",
            "repo_dir": ".codex",
            "hook_file": "AGENTS.md",
        },
        "claude": {
            "default_home": ".claude",
            "repo_dir": ".claude",
            "hook_file": "CLAUDE.md",
        },
    }
    layout = layouts[agent]
    override_home = codex_home if agent == "codex" else claude_home

    if scope == "global":
        agent_dir = resolve_agent_home(
            override_home,
            default_user_home=home_path,
            default_name=layout["default_home"],
        )
        skill_dir = agent_dir / "skills" / project.skill_name
        hook_path = agent_dir / layout["hook_file"]
    else:
        assert repo_root is not None
        skill_dir = repo_root / layout["repo_dir"] / "skills" / project.skill_name
        hook_path = repo_root / layout["hook_file"]

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
    data = read_manifest_file(path)
    accepted_packages = {project.package_name, *project.manifest_package_aliases}
    if data.get("package") not in accepted_packages:
        raise InstallerError(
            f"install manifest is not for {project.package_name}: {path}"
        )
    return data


def read_manifest_file(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise InstallerError(f"invalid install manifest: {path}") from error
    if not isinstance(data, dict):
        raise InstallerError(f"install manifest must be a JSON object: {path}")
    if data.get("version") != MANIFEST_VERSION:
        raise InstallerError(f"unsupported install manifest version: {path}")
    for field_name in ("agent", "scope", "skill_dir", "hook_path"):
        if not isinstance(data.get(field_name), str):
            raise InstallerError(f"install manifest missing {field_name}: {path}")
    return data


def manifest_str(manifest: dict[str, object], key: str, path: Path) -> str:
    value = manifest.get(key)
    if not isinstance(value, str) or not value:
        raise InstallerError(f"install manifest missing {key}: {path}")
    return value


def status_from_manifest(
    manifest: dict[str, object],
    manifest_file: Path,
) -> InstallationStatus:
    agent = manifest_str(manifest, "agent", manifest_file)
    scope = manifest_str(manifest, "scope", manifest_file)
    skill_dir = Path(manifest_str(manifest, "skill_dir", manifest_file))
    hook_path = Path(manifest_str(manifest, "hook_path", manifest_file))
    install_mode = manifest.get("install_mode")
    skill_name = manifest.get("skill_name")
    package_name = manifest.get("package")
    source_url = manifest.get("source_url")
    source_ref = manifest.get("source_ref")
    source_path = manifest.get("source_path")
    return InstallationStatus(
        agent=agent,
        scope=scope,
        skill_dir=skill_dir,
        status="installed",
        version=manifest_package_version(manifest),
        install_mode=install_mode if isinstance(install_mode, str) else None,
        skill_name=skill_name if isinstance(skill_name, str) else skill_dir.name,
        package_name=package_name if isinstance(package_name, str) else None,
        manifest_path=manifest_file,
        hook_path=hook_path,
        source_url=source_url if isinstance(source_url, str) else None,
        source_ref=source_ref if isinstance(source_ref, str) else None,
        source_path=source_path if isinstance(source_path, str) else None,
    )


def iter_managed_manifest_paths(skills_dir: Path):
    if not skills_dir.is_dir():
        return
    seen: set[Path] = set()
    patterns = ("*/scripts/.*-install.json", ".*-install.json")
    for pattern in patterns:
        for path in sorted(skills_dir.glob(pattern)):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            yield path


def discover_managed_installations_for_target(spec: TargetSpec) -> list[InstallationStatus]:
    statuses: list[InstallationStatus] = []
    for path in iter_managed_manifest_paths(spec.skill_dir.parent) or ():
        try:
            manifest = read_manifest_file(path)
            status = status_from_manifest(manifest, path)
        except InstallerError:
            continue
        if status.agent != spec.agent or status.scope != spec.scope:
            continue
        statuses.append(status)
    return statuses


def discover_managed_installations(
    project: SkillProject,
    *,
    repo: Path | None = None,
    home: Path | None = None,
    codex_home: Path | None = None,
    claude_home: Path | None = None,
) -> list[InstallationStatus]:
    statuses: list[InstallationStatus] = []
    seen: set[Path] = set()
    for agent in AGENTS:
        for scope in SCOPES:
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
            except InstallerError:
                continue
            for status in discover_managed_installations_for_target(spec):
                if status.manifest_path is None or status.manifest_path in seen:
                    continue
                seen.add(status.manifest_path)
                statuses.append(status)
    return statuses


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

    if not (spec.skill_dir.exists() or spec.skill_dir.is_symlink()):
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


def copy_bundled_skill(project: SkillProject, skill_dir: Path) -> list[str]:
    copied: list[str] = []
    for relative_path, source in iter_bundled_skill_files(project):
        target = skill_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        copied.append(relative_path.as_posix())
    return copied


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


def published_pypi_versions(
    project: SkillProject,
    *,
    limit: int = 20,
    timeout: float = PYPI_METADATA_TIMEOUT_SECONDS,
) -> list[str]:
    metadata = fetch_json_url(
        f"{project.pypi_base_url}/{project.pypi_name}/json",
        timeout=timeout,
    )
    releases = metadata.get("releases")
    if not isinstance(releases, dict):
        raise InstallerError("PyPI metadata did not contain releases")

    versions = [
        version
        for version, files in releases.items()
        if isinstance(version, str)
        and isinstance(files, list)
        and any(
            isinstance(item, dict) and item.get("packagetype") == "bdist_wheel"
            for item in files
        )
    ]
    return sorted(versions, key=version_key, reverse=True)[:limit]


def download_url(
    url: str,
    target: Path,
    *,
    timeout: float = PYPI_DOWNLOAD_TIMEOUT_SECONDS,
    description: str = "PyPI wheel",
) -> Path:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response, target.open(
            "wb"
        ) as output:
            shutil.copyfileobj(response, output)
    except (TimeoutError, urllib.error.URLError) as error:
        raise InstallerError(f"failed to download {description}: {error}") from error
    except OSError as error:
        raise InstallerError(f"failed to write {description}: {target}") from error
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
        fetch_json_url(
            f"{project.pypi_base_url}/{project.pypi_name}/"
            f"{urllib.parse.quote(version, safe='')}/json"
        ),
    )
    return download_url(url, download_dir / filename)


def github_archive_url(source: GithubSource) -> str:
    owner = urllib.parse.quote(source.owner, safe="")
    repo = urllib.parse.quote(source.repo, safe="")
    ref = urllib.parse.quote(source.ref, safe="/")
    return f"https://codeload.github.com/{owner}/{repo}/zip/{ref}"


def download_github_archive(source: GithubSource, download_dir: Path) -> Path:
    return download_url(
        github_archive_url(source),
        download_dir / "github-source.zip",
        timeout=GITHUB_DOWNLOAD_TIMEOUT_SECONDS,
        description="GitHub archive",
    )


def github_archive_relative_path(filename: str) -> PurePosixPath | None:
    path = PurePosixPath(filename)
    if path.is_absolute() or ".." in path.parts:
        raise InstallerError(f"unsafe path in GitHub archive: {filename}")
    if len(path.parts) < 2:
        return None
    relative = PurePosixPath(*path.parts[1:])
    if not relative.parts:
        return None
    if "__pycache__" in relative.parts or relative.suffix == ".pyc":
        return None
    return relative


def prefixed_skill_file(prefix: PurePosixPath) -> PurePosixPath:
    return prefix / "SKILL.md" if prefix.parts else PurePosixPath("SKILL.md")


def github_archive_skill_prefix(
    archive: zipfile.ZipFile,
    source_path: PurePosixPath | None,
) -> PurePosixPath:
    files = {
        relative
        for info in archive.infolist()
        if not info.is_dir()
        for relative in [github_archive_relative_path(info.filename)]
        if relative is not None
    }
    candidates = (
        [source_path]
        if source_path is not None
        else [PurePosixPath("skill"), PurePosixPath(".")]
    )
    for candidate in candidates:
        assert candidate is not None
        if prefixed_skill_file(candidate) in files:
            return candidate
    if source_path is not None:
        raise InstallerError(
            "GitHub archive did not contain "
            f"{prefixed_skill_file(source_path).as_posix()}"
        )
    raise InstallerError("GitHub archive did not contain SKILL.md or skill/SKILL.md")


def github_archive_skill_relative_path(
    project: SkillProject,
    filename: str,
    skill_prefix: PurePosixPath,
) -> Path | None:
    relative = github_archive_relative_path(filename)
    if relative is None:
        return None
    if skill_prefix.parts:
        if relative.parts[: len(skill_prefix.parts)] != skill_prefix.parts:
            return None
        relative = PurePosixPath(*relative.parts[len(skill_prefix.parts) :])
    if not relative.parts:
        return None
    if relative == PurePosixPath(project.manifest_relative_path.as_posix()):
        return None
    return Path(*relative.parts)


def copy_zip_skill_files(
    archive: zipfile.ZipFile,
    skill_dir: Path,
    relative_path_for: Callable[[str], Path | None],
) -> list[str]:
    copied: list[str] = []
    for info in sorted(archive.infolist(), key=lambda item: item.filename):
        if info.is_dir():
            continue
        relative_path = relative_path_for(info.filename)
        if relative_path is None:
            continue
        target = skill_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(info))
        copied.append(relative_path.as_posix())
    return copied


def copy_github_archive_skill(
    project: SkillProject,
    archive_path: Path,
    skill_dir: Path,
    source_path: PurePosixPath | None = None,
) -> list[str]:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            skill_prefix = github_archive_skill_prefix(archive, source_path)
            copied = copy_zip_skill_files(
                archive,
                skill_dir,
                lambda filename: github_archive_skill_relative_path(
                    project,
                    filename,
                    skill_prefix,
                ),
            )
    except zipfile.BadZipFile as error:
        raise InstallerError(
            f"GitHub archive is not a valid zip file: {archive_path}"
        ) from error

    if "SKILL.md" not in copied:
        raise InstallerError("GitHub archive did not contain a usable SKILL.md")
    return copied


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
    try:
        with zipfile.ZipFile(wheel_path) as wheel:
            copied = copy_zip_skill_files(
                wheel,
                skill_dir,
                lambda filename: wheel_skill_relative_path(project, filename),
            )
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


def uninstall_hook(
    spec: TargetSpec,
    *,
    delete_if_empty: bool,
    start_marker: str | None = None,
    end_marker: str | None = None,
) -> bool:
    if not spec.hook_path.exists():
        return False
    updated, changed = remove_marked_block(
        spec.hook_path.read_text(),
        start_marker=start_marker or spec.marker_start,
        end_marker=end_marker or spec.marker_end,
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
    source_url: str | None = None,
    source_ref: str | None = None,
    source_path: str | None = None,
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
    if project.source_skill_name is not None:
        data["source_skill_name"] = project.source_skill_name
    if project.source_skill_path is not None:
        data["source_skill_path"] = project.source_skill_path
    if source_dir is not None:
        data["source_dir"] = str(source_dir)
    if source_url is not None:
        data["source_url"] = source_url
    if source_ref is not None:
        data["source_ref"] = source_ref
    if source_path is not None:
        data["source_path"] = source_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def sibling_manifest_candidates(skill_parent: Path) -> Iterable[Path]:
    yield from skill_parent.glob("*/scripts/.*-install.json")
    yield from skill_parent.glob(".*-install.json")


def sibling_created_ownership(
    spec: TargetSpec,
    *,
    skill_name: str,
) -> tuple[list[Path], bool]:
    created_dirs: list[Path] = []
    created_hook_file = False
    current_manifest = spec.skill_dir / "scripts" / f".{skill_name}-install.json"
    current_sidecar = spec.skill_dir.parent / f".{skill_name}-install.json"
    for candidate in sibling_manifest_candidates(spec.skill_dir.parent):
        if candidate in {current_manifest, current_sidecar}:
            continue
        try:
            data = json.loads(candidate.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("agent") != spec.agent or data.get("scope") != spec.scope:
            continue
        if data.get("hook_path") != str(spec.hook_path):
            continue
        if data.get("created_hook_file") is True:
            created_hook_file = True
        for path in data.get("created_dirs", []):
            if isinstance(path, str):
                directory = Path(path)
                if directory not in created_dirs:
                    created_dirs.append(directory)
    return created_dirs, created_hook_file


def validate_install_source_selection(
    *,
    editable: bool = False,
    pypi_version: str | None = None,
    wheel_path: Path | None = None,
    github_source: GithubSource | None = None,
) -> None:
    selected = [
        name
        for name, enabled in (
            ("--editable", editable),
            ("--pypi-version", pypi_version is not None),
            ("--wheel-file", wheel_path is not None),
            ("--github-url", github_source is not None),
        )
        if enabled
    ]
    if len(selected) > 1:
        raise InstallerError(f"{', '.join(selected)} cannot be combined")


def install_target(
    project: SkillProject,
    spec: TargetSpec,
    *,
    force: bool = False,
    editable: bool = False,
    editable_source_dir: Path | None = None,
    pypi_version: str | None = None,
    pypi_wheel_path: Path | None = None,
    github_source: GithubSource | None = None,
    github_archive_path: Path | None = None,
) -> InstallResult:
    effective_editable = editable or editable_source_dir is not None
    validate_install_source_selection(
        editable=effective_editable,
        pypi_version=pypi_version,
        wheel_path=pypi_wheel_path if pypi_version is None else None,
        github_source=github_source,
    )

    skill_exists = spec.skill_dir.exists() or spec.skill_dir.is_symlink()
    previous_manifest = read_manifest(project, spec.skill_dir) if skill_exists else None
    previous_manifest_file = (
        manifest_path(project, spec.skill_dir)
        if skill_exists and previous_manifest is not None
        else None
    )
    previous_version = manifest_package_version(previous_manifest)
    package_version = (
        pypi_version
        or (github_source.version_label if github_source is not None else None)
        or project.version
    )
    install_mode = (
        "pypi"
        if pypi_version is not None
        else "wheel"
        if pypi_wheel_path is not None
        else "github"
        if github_source is not None
        else "editable"
        if effective_editable
        else "copy"
    )
    source_dir = (
        editable_source_dir
        if editable_source_dir is not None
        else local_checkout_skill_root(project)
        if editable
        else None
    )
    if pypi_version is not None and pypi_wheel_path is None:
        raise InstallerError("missing PyPI wheel for requested install source")
    if github_source is not None and github_archive_path is None:
        raise InstallerError("missing GitHub archive for requested install source")
    validate_install_skill_source(
        project,
        source_dir=source_dir,
        pypi_wheel_path=pypi_wheel_path,
        github_archive_path=github_archive_path,
        github_source=github_source,
    )
    if skill_exists and previous_manifest is None and not force:
        raise InstallerError(
            f"refusing to replace unowned skill directory: {spec.skill_dir}"
        )

    created_dirs = [
        Path(path)
        for path in (previous_manifest or {}).get("created_dirs", [])
        if isinstance(path, str)
    ]
    sibling_created_dirs, sibling_created_hook_file = sibling_created_ownership(
        spec,
        skill_name=project.skill_name,
    )
    for directory in sibling_created_dirs:
        if directory not in created_dirs:
            created_dirs.append(directory)
    previous_created_hook_file = (previous_manifest or {}).get("created_hook_file")
    created_hook_file = bool(
        previous_created_hook_file is True
        or sibling_created_hook_file
        or (previous_created_hook_file is None and not spec.hook_path.exists())
    )

    if skill_exists:
        remove_existing_path(spec.skill_dir)
    if previous_manifest_file is not None:
        previous_manifest_file.unlink(missing_ok=True)

    remember_created_dirs(created_dirs, spec.skill_dir)
    if pypi_version is not None or pypi_wheel_path is not None:
        if pypi_wheel_path is None:
            raise InstallerError("missing PyPI wheel for requested install source")
        spec.skill_dir.mkdir(parents=True, exist_ok=True)
        skill_files = copy_pypi_wheel_skill(project, pypi_wheel_path, spec.skill_dir)
    elif github_source is not None:
        spec.skill_dir.mkdir(parents=True, exist_ok=True)
        if github_archive_path is None:
            raise InstallerError("missing GitHub archive for requested install source")
        skill_files = copy_github_archive_skill(
            project,
            github_archive_path,
            spec.skill_dir,
            github_source.path,
        )
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
        source_url=github_source.url if github_source is not None else None,
        source_ref=github_source.ref if github_source is not None else None,
        source_path=(
            github_source.path.as_posix()
            if github_source is not None and github_source.path is not None
            else str(pypi_wheel_path)
            if install_mode == "wheel" and pypi_wheel_path is not None
            else None
        ),
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
        source_url=github_source.url if github_source is not None else None,
        source_ref=github_source.ref if github_source is not None else None,
        source_path=(
            github_source.path.as_posix()
            if github_source is not None and github_source.path is not None
            else str(pypi_wheel_path)
            if install_mode == "wheel" and pypi_wheel_path is not None
            else None
        ),
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
    skill_exists = spec.skill_dir.exists() or spec.skill_dir.is_symlink()
    manifest = read_manifest(project, spec.skill_dir) if skill_exists else None
    manifest_file = manifest_path(project, spec.skill_dir) if manifest is not None else None
    package_version = manifest_package_version(manifest)
    if skill_exists and manifest is None:
        raise InstallerError(
            f"refusing to remove unowned skill directory: {spec.skill_dir}"
        )

    delete_hook_if_empty = bool((manifest or {}).get("created_hook_file", False))
    marker_start = (manifest or {}).get("hook_marker_start")
    marker_end = (manifest or {}).get("hook_marker_end")
    uninstall_hook(
        spec,
        delete_if_empty=delete_hook_if_empty,
        start_marker=marker_start if isinstance(marker_start, str) else None,
        end_marker=marker_end if isinstance(marker_end, str) else None,
    )

    if manifest_file is not None:
        manifest_file.unlink(missing_ok=True)
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
    github_url: str | None = None,
    github_ref: str | None = None,
    github_path: str | None = None,
) -> list[InstallResult]:
    if github_url is None and (github_ref is not None or github_path is not None):
        raise InstallerError("--github-ref and --github-path require --github-url")
    github_source = (
        parse_github_url(github_url, ref=github_ref, path=github_path)
        if github_url is not None
        else None
    )
    validate_install_source_selection(
        editable=editable,
        pypi_version=pypi_version,
        github_source=github_source,
    )

    selected_agents = normalize_agents(agents)

    def install_targets(
        pypi_wheel_path: Path | None = None,
        github_archive_path: Path | None = None,
    ) -> list[InstallResult]:
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
                github_source=github_source,
                github_archive_path=github_archive_path,
            )
            for agent in selected_agents
        ]

    if pypi_version is None and github_source is None:
        return install_targets()

    if pypi_version is not None:
        with tempfile.TemporaryDirectory(
            prefix="agent-skill-installer-pypi-"
        ) as temp_dir:
            wheel_path = download_pypi_wheel(project, pypi_version, Path(temp_dir))
            return install_targets(pypi_wheel_path=wheel_path)

    assert github_source is not None
    with tempfile.TemporaryDirectory(prefix="agent-skill-installer-github-") as temp_dir:
        archive_path = download_github_archive(github_source, Path(temp_dir))
        return install_targets(github_archive_path=archive_path)


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
