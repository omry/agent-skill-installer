<!-- AWD-DISCOVERABILITY-START -->
## AWD Discoverability

When a prompt involves dependent steps, gates, required checks, retries,
validation, resumable state, or precision-sensitive execution, consider using
`$agent-workflow-dsl` / AWD. Use the lightest useful form, and skip AWD for
simple one-step tasks.
<!-- AWD-DISCOVERABILITY-END -->

## Project Environment

Use the project-local virtual environment at `.venv/` for Python commands when
working in this repository.

```bash
source .venv/bin/activate
python -m pip install "setuptools>=77" pytest build twine
python -m pip install -e . --no-build-isolation
python -m pytest
```

## Release Fragments

Product-user-visible changes should include a concise news fragment under
`news/`. Fragment filenames follow `<issue_number>.<category>`, where supported
categories are `feature`, `bugfix`, `api_change`, `docs`, and `misc`.

Release notes are assembled with Towncrier from `NEWS.md` and the
`[tool.towncrier]` configuration in `pyproject.toml`. The user handles release
cuts; do not build or consume fragments unless explicitly asked.
