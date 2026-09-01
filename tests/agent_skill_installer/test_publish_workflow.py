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
