# Maintainer Guide

This guide covers repository maintenance tasks that affect releases and
publishing. User-facing package and skill authoring docs live in the other
guides under `docs/`.

## Release Model

Releases use a maintainer-controlled local command and a protected GitHub
Actions workflow:

1. Run a local dry run for the intended version.
2. Run the same command with `publish=true` when the result is ready.
3. Approve the protected `pypi` environment after remote validation passes.
4. The workflow publishes to PyPI, creates the immutable version tag, and
   creates the GitHub Release from the matching Towncrier section in `NEWS.md`.

The local command makes repository and remote changes only when
`publish=true` is explicit. The workflow pins every job to the exact release
commit, rebuilds and verifies the distributions remotely, and cannot publish
until the `pypi` environment is approved.

GitHub Actions does not create or approve pull requests for releases. Release
commits and pushes use the maintainer's local Sapling or Git identity, while
PyPI uses GitHub Actions Trusted Publishing.

## Prerequisites

Start from a clean `main` checkout at `remote/main` with Sapling or
`origin/main` with Git. Install the local release tools in the project virtual
environment and authenticate GitHub CLI:

```bash
source .venv/bin/activate
python -m pip install "setuptools>=77" pytest build twine towncrier
gh auth status
```

The repository's `pypi` environment must require the intended reviewer and be
configured as the PyPI Trusted Publisher environment. The publish job fails
closed before downloading artifacts if that required-reviewer rule is absent.

## Dry Run

Run the release tool without `publish=true`:

```bash
python tools/release.py version=0.4.0
```

The dry run:

- verifies that the version is not already on PyPI
- copies the current checkout into a temporary release workspace
- updates both package version declarations in that workspace
- consumes `news/` fragments with Towncrier in that workspace
- runs the complete test suite
- builds the wheel and source distribution
- runs `twine check`
- smoke-installs the wheel without dependencies and verifies its metadata
- prints the exact GitHub Release notes

It does not modify the checkout, commit, push, dispatch a workflow, create a
tag, publish to PyPI, or create a GitHub Release.

Use `date=YYYY-MM-DD` only when the Towncrier release date must differ from the
current UTC date:

```bash
python tools/release.py version=0.4.0 date=2026-09-01
```

## Publish

After reviewing a successful dry run, repeat it with `publish=true`:

```bash
python tools/release.py version=0.4.0 publish=true
```

The command repeats the dry-run checks before changing anything. It then:

- updates `pyproject.toml`
- updates `src/agent_skill_installer/__init__.py`
- consumes the news fragments into `NEWS.md`
- commits those release files as `Prepare <version> release`
- pushes the release commit directly to `main` using the maintainer's identity
- dispatches **Publish** with the exact 40-character commit SHA and
  `publish=true`

The remote workflow verifies that the commit is on `main`, that both version
declarations and the Towncrier section match, and that no unconsumed news
fragments remain. It then runs the full Python and operating-system test
matrix, rebuilds the distributions, runs `twine check`, and smoke-installs the
wheel.

The workflow pauses at the protected `pypi` environment immediately before
the Trusted Publishing upload. Review the workflow summary and approve that
environment to publish. After the version is visible on PyPI, the workflow
creates `v<version>` at the exact release commit and creates the GitHub Release
from that version's `NEWS.md` section.

Do not create or publish the GitHub Release manually before PyPI publishing
succeeds.

## Recovery And Remote Dry Runs

Every publish run is pinned to an exact commit. To rerun remote validation for
an existing prepared commit without uploading, pass its full SHA:

```bash
python tools/release.py \
  version=0.4.0 \
  commit=<full-40-character-sha>
```

To resume publication or repair the tag and GitHub Release from that same
commit, add `publish=true`:

```bash
python tools/release.py \
  version=0.4.0 \
  commit=<full-40-character-sha> \
  publish=true
```

Commit recovery does not modify, commit, or push the local checkout. The
workflow requires the commit to be on `main` and validates its prepared release
state before continuing.

If the version already exists on PyPI, the workflow skips the upload and
continues only after the published filenames and SHA-256 hashes exactly match
the distributions rebuilt from the requested commit. It then performs
exact-commit tag verification and GitHub Release creation or repair. It refuses
to move an existing version tag to a different commit.
