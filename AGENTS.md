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

A new fragment cannot be created without a corresponding issue. If a
corresponding issue is not found, pause and offer to create one, mentioning
that it is needed for the new fragment id.

Release notes are assembled with Towncrier from `NEWS.md` and the
`[tool.towncrier]` configuration in `pyproject.toml`. The user handles release
cuts; do not build or consume fragments unless explicitly asked.
