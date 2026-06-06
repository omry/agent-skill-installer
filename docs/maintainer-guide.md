# Maintainer Guide

This guide covers repository maintenance tasks that affect releases and
publishing. User-facing package and skill authoring docs live in the other
guides under `docs/`.

## Release Model

Releases use a protected-branch-friendly workflow:

1. Run **Prepare Release**. If release files need changes, it opens or updates
   a release preparation PR.
2. Merge the release preparation PR after required checks pass.
3. Rerun **Prepare Release** with the same version to create or refresh the
   draft GitHub Release from the prepared target branch.
4. Publish the prepared draft from GitHub Actions.

The draft GitHub Release is the handoff object. It is safe to edit before the
package is public. The publish workflow builds and publishes the PyPI package
first, then promotes the draft GitHub Release only after PyPI publishing
succeeds.

Do not manually publish a draft GitHub Release before PyPI publishing succeeds.
The repository publish workflow intentionally avoids the `release: published`
trigger so that GitHub Releases do not become public before the package is
available on PyPI.

The repository must allow GitHub Actions to create pull requests. Prepare uses
that permission to open or update the release preparation PR.

## Prepare A Release

Run the **Prepare Release** workflow from GitHub Actions.

Inputs:

- `version`: release version without a leading `v`, for example `0.1.4`.
- `date`: optional `YYYY-MM-DD` release date. If omitted, the workflow uses the
  current UTC date.
- `target_branch`: branch to prepare from. Defaults to `main`.

The workflow:

- checks out the target branch
- stops if the version is already published on PyPI
- updates `pyproject.toml`
- updates `src/agent_skill_installer/__init__.py`
- runs Towncrier to consume `news/` fragments into `NEWS.md`
- commits release preparation changes to `release/prepare-v<version>` and opens
  or updates a PR, if release files changed
- dispatches CI for the release preparation branch
- creates or updates a draft GitHub Release for `v<version>`, if the target
  branch is already prepared
- points the draft release tag at the prepared target branch commit
- writes the latest Towncrier release section into the draft body

If the workflow opens or updates a release preparation PR, edit the generated
`NEWS.md` release section in that PR if needed. Review and merge the PR after
required checks pass. Then rerun **Prepare Release** with the same version on
the prepared target branch.

If the workflow creates or updates a draft GitHub Release, review the draft.
Keep the release as a draft.

You can rerun **Prepare Release** with the same version until that version is
published on PyPI. Before the release preparation PR is merged, reruns update
the same PR. After the prepared changes are on the target branch, reruns move
the release tag to that commit and refresh the GitHub Release as a draft.
After the first prepare run, edit the generated section in `NEWS.md` directly
for release-note changes. Do not add late fragments for the same version after
the section exists. GitHub draft body edits are overwritten by the next prepare
run, so make them only after the final prepare run. If the version is already
published on PyPI, preparation fails before changing release files.

## Publish A Release

Run the **Publish** workflow from GitHub Actions with the draft release tag, for
example `v0.1.4`.

The workflow validates that:

- the GitHub Release exists
- the GitHub Release is still a draft
- the tag looks like `vX.Y.Z`
- `pyproject.toml` matches the tag version
- `src/agent_skill_installer/__init__.py` matches the tag version
- no release fragments remain under `news/`

Then it:

- checks out the draft release tag
- builds the wheel and source distribution remotely
- runs `twine check`
- uploads the build artifacts inside the workflow
- publishes to PyPI using trusted publishing
- promotes the GitHub draft release to public after PyPI publishing succeeds

If PyPI publishing fails, the GitHub Release remains a draft. Fix the issue and
rerun the publish workflow against the same draft tag.

## Local Checks

Local checks are useful before preparing a release, but they are not the publish
path.

Use the project virtual environment:

```bash
source .venv/bin/activate
python -m pytest
towncrier build --draft
```

The publish workflow performs the authoritative build from the draft release tag
on GitHub-hosted runners.
