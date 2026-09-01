from pathlib import Path


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
    assert "-f filter=all" in publish
    assert 'select(.name == "Build and verify distributions")' in publish
    assert 'any(.[];' in publish
    assert "completed:success" in publish
    assert "completed:*" in publish


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
