## Project Environment
This repository uses Sapling (`sl`) for source control. Prefer `sl` for
status, diffs, commits, and history.

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

Do not reuse, rename, or append to an unrelated fragment just to avoid creating
an issue. Each fragment must describe the issue named by its filename. If an
editor shows a stale fragment path, verify the live files under `news/` before
editing.

Write fragments as release notes for product users:

- Lead with the user-visible behavior, not the implementation detail.
- Prefer one clear change per fragment. If one issue includes a primary change
  and supporting details, make the primary change the first sentence and keep
  supporting details short.
- Do not repeat the project name at the start of a bullet; Towncrier already
  renders the project heading.
- Do not overclaim uniqueness, compatibility, validation, or safety unless the
  behavior is implemented and verified.
- Do not use a representation-only wording fix to imply an underlying behavior
  fix.
- Keep entries concise enough to read as a changelog bullet after Towncrier adds
  the issue link.

Choose the category by the public effect of the change:

- `feature`: new user-visible capability or workflow.
- `bugfix`: corrected broken, invalid, or surprising behavior.
- `api_change`: command-line flags, Python/API surface, compatibility aliases,
  removals, deprecations, or changed public semantics.
- `docs`: documentation-only changes.
- `misc`: user-visible maintenance or packaging changes that do not fit the
  categories above.

When a change spans categories, prefer the category with the highest-impact
public contract. For example, a CLI flag rename belongs in `api_change` even if
the same issue also improves TUI wording.

Release notes are assembled with Towncrier from `NEWS.md` and the
`[tool.towncrier]` configuration in `pyproject.toml`. The user handles release
cuts; do not build or consume fragments unless explicitly asked.
