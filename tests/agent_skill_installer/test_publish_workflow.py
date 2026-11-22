import json
import os
from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest


WORKFLOW = Path(__file__).parents[2] / ".github/workflows/publish.yml"


def test_conflicting_tag_is_checked_before_pypi_upload() -> None:
    workflow = WORKFLOW.read_text()

    tag_preflight = workflow.index('remote_tag="$(git ls-remote')
    upload = workflow.index("uses: pypa/gh-action-pypi-publish")

    assert tag_preflight < upload
    assert 'if [[ "$existing_commit" != "$COMMIT" ]]' in workflow


def test_nonempty_release_notes_are_checked_before_pypi_upload() -> None:
    workflow = WORKFLOW.read_text()

    upload = workflow.index("uses: pypa/gh-action-pypi-publish")
    validation = workflow[:upload]

    assert "match is None or not match.group(1).strip()" in validation


def test_existing_release_is_published_when_repaired() -> None:
    workflow = WORKFLOW.read_text()

    edit_start = workflow.index('gh release edit "$TAG"')
    edit_end = workflow.index("else", edit_start)

    assert "--draft=false" in workflow[edit_start:edit_end]


def test_publish_approval_is_available_while_validation_runs() -> None:
    workflow = WORKFLOW.read_text()
    publish_start = workflow.index("\n  publish:\n")
    release_start = workflow.index("\n  release:\n")
    publish = workflow[publish_start:release_start]

    assert "name: Approve and publish to PyPI" in publish
    assert "needs: publication_status" in publish
    assert "needs.validate" not in publish
    assert "needs.build" not in publish
    assert "environment:\n      name: pypi" in publish
    assert "needs.publication_status.outputs.already_published != 'true'" in publish


def test_publish_waits_for_successful_build_from_the_same_run() -> None:
    workflow = WORKFLOW.read_text()
    publish_start = workflow.index("\n  publish:\n")
    release_start = workflow.index("\n  release:\n")
    publish = workflow[publish_start:release_start]

    wait = publish.index("- name: Wait for release validation")
    upload = publish.index("uses: pypa/gh-action-pypi-publish")

    assert wait < upload
    assert "github.run_id" in publish
    assert "--paginate" in publish
    assert "--slurp" in publish
    assert "-f per_page=100 | jq -r '" in publish
    assert "-f filter=all" in publish
    assert '[.[].jobs[] |' in publish
    assert 'select(.name == "Build and verify distributions")' in publish
    assert 'any(.[];' in publish
    assert "completed:success" in publish
    assert "completed:*" in publish


@pytest.mark.skipif(os.name == "nt", reason="release job uses a POSIX shell")
@pytest.mark.parametrize("conclusion", ["success", "failure"])
def test_release_wait_executes_paginated_job_filter(tmp_path, conclusion) -> None:
    if shutil.which("jq") is None:
        pytest.skip("release runner's jq is unavailable")
    workflow = WORKFLOW.read_text()
    step = workflow.split("      - name: Wait for release validation\n", 1)[1]
    script = textwrap.dedent(step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0])
    script = script.replace("${{ github.repository }}", "omry/agent-skill-installer")
    script = script.replace("${{ github.run_id }}", "123")
    fixture = tmp_path / "jobs.json"
    fixture.write_text(json.dumps([
        {"jobs": [{"name": "Unrelated job", "status": "in_progress"}]},
        {"jobs": [{"name": "Build and verify distributions",
                   "status": "completed", "conclusion": conclusion}]},
    ]))
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        "for arg do\n"
        "  if [ \"$arg\" = --jq ]; then\n"
        "    echo 'gh rejects --slurp combined with --jq' >&2\n"
        "    exit 2\n"
        "  fi\n"
        "done\n"
        "cat \"$ASI_RELEASE_JOBS_FIXTURE\"\n"
    )
    gh.chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", script],
        env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
             "ASI_RELEASE_JOBS_FIXTURE": str(fixture)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == (0 if conclusion == "success" else 1), result.stderr
    if conclusion == "failure":
        assert "release validation did not pass (completed:failure)" in result.stdout


def test_publish_wait_covers_the_workflow_job_lifetime() -> None:
    workflow = WORKFLOW.read_text()
    publish_start = workflow.index("\n  publish:\n")
    release_start = workflow.index("\n  release:\n")
    publish = workflow[publish_start:release_start]

    assert "timeout-minutes: 360" in publish
    assert "deadline=$((SECONDS + 21000))" in publish


def test_failed_validation_cancels_pending_pypi_approval() -> None:
    workflow = WORKFLOW.read_text()
    cancel_start = workflow.index("\n  cancel_failed_publication:\n")
    publish_start = workflow.index("\n  publish:\n")
    cancel = workflow[cancel_start:publish_start]

    assert "always()" in cancel
    assert "needs.publication_status.result == 'success'" in cancel
    assert "needs.publication_status.outputs.already_published != 'true'" in cancel
    assert "needs.validate.result != 'success'" in cancel
    assert "needs.tests.result != 'success'" in cancel
    assert "needs.build.result != 'success'" in cancel
    assert "actions: write" in cancel
    assert 'gh api --method POST "$run_api/cancel"' in cancel
    assert 'gh api --method POST "$run_api/force-cancel"' in cancel


def test_already_published_recovery_skips_pypi_approval() -> None:
    workflow = WORKFLOW.read_text()
    status_start = workflow.index("\n  publication_status:\n")
    validate_start = workflow.index("\n  validate:\n")
    publish_start = workflow.index("\n  publish:\n")
    release_start = workflow.index("\n  release:\n")

    publication_status = workflow[status_start:validate_start]
    publish = workflow[publish_start:release_start]

    assert "already_published" in publication_status
    assert "needs: publication_status" in publish
    assert "needs.publication_status.outputs.already_published != 'true'" in publish
