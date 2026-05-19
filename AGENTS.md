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
