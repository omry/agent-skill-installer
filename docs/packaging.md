# Packaging

This guide is for maintainers who publish skills as Python wheels. For local or
GitHub-installable skill directories, start with
[Authoring Skills](authoring-skills.md). For wrapper commands and the Python
API, see [API And Wrapper CLI](api-and-wrapper-cli.md).

## Skill Payload

ASI detects skills by finding `SKILL.md` files. The directory containing each
`SKILL.md` is the skill payload:

```text
skill/
  SKILL.md
  agent-skill-installer.yaml  # Optional install-time metadata, not deployed
```

Files and directories under that payload directory, such as `bin/`, `scripts/`,
agent configs, or references, are installed with the skill. ASI does not copy
`agent-skill-installer.yaml` or Python cache files (`__pycache__/`, `*.pyc`)
from the payload into the installed skill.

If the payload directory also contains package-maintenance files, use
`installer.payload.include` and `installer.payload.exclude` in
`agent-skill-installer.yaml` to select the installed payload files explicitly:

```yaml
installer:
  payload:
    include:
      - SKILL.md
      - agents/**
      - bin/**
      - scripts/**
      - references/**
    exclude:
      - tests/**
      - pyproject.toml
```

Rules are matched against POSIX-style paths relative to the `SKILL.md`
directory. A file is installed when it matches at least one `include` pattern
and no `exclude` pattern. Excludes win over includes. `include` defaults to
`["**"]`, and `exclude` defaults to `[]`, so omitting `payload` preserves the
old recursive-copy behavior.

Pattern matching uses Python `fnmatch` semantics, not `.gitignore` semantics:
`*` can match `/`, `**` is not a special recursive operator, and patterns are
matched against the whole relative path. Prefer explicit directory prefixes like
`bin/**` and `scripts/**` for clarity. `SKILL.md` must remain selected; ASI
rejects a payload selection that excludes it.

Payload filters apply when ASI copies a local skill directory or extracts a
skill from a wheel or GitHub archive. Editable local installs are symlinks and
therefore expose the source tree as-is.

A package can contain multiple skills. Each directory with a `SKILL.md` is
detected as a separate source skill:

```text
skills/
  skill-one/
    SKILL.md
  skill-two/
    SKILL.md
```

The payload directory name is not special. The examples use `skill/` because it
is convenient for local development: the same directory can be symlinked into an
agent.

## Python Wheel Shape

A wheel should place the skill payload inside a distribution-specific Python
package directory, not as a top-level `skill/` package:

```text
your-skill-package/
  pyproject.toml
  MANIFEST.in
  your_skill_package/
    __init__.py
    skill/
      SKILL.md
      agent-skill-installer.yaml
```

Minimal setuptools metadata:

```toml
[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "your-skill-package"
version = "1.2.3"
description = "Agent skill for my project workflows"
requires-python = ">=3.10"

[tool.setuptools]
include-package-data = true
```

Include the payload as package data:

```text
recursive-include your_skill_package/skill *
```

For other build backends, configure the equivalent package-data rule so the
built wheel contains `SKILL.md`, optional `agent-skill-installer.yaml`, and the
payload files.

Verify the built artifact before publishing:

```bash
agent-skill-installer --no-ui install \
  --wheel-file dist/your_skill_package-1.2.3-py3-none-any.whl \
  --agent codex \
  --scope dir \
  --repo
```

After publishing to PyPI, install by package requirement:

```bash
agent-skill-installer --no-ui install \
  --pypi-package your-skill-package==1.2.3 \
  --agent codex \
  --scope dir \
  --repo
```

Omit the version to let pip choose the latest compatible release, or use a range
such as `your-skill-package>=1.2,<2`.

See [`examples/wheel-skill/`](../examples/wheel-skill/) for a minimal package.

## Platform-Specific Files

There are two supported ways to package platform-specific files. The choice is
about where the native or platform-specific files live:

- Put the platform-specific files directly in the skill package when the skill
  package owns those files. Publish one wheel per supported platform for the
  same skill package name and version. Pip installs the wheel for the current
  platform, and ASI installs the skill payload from that wheel. No
  `external_wheels` entry is needed.
- Put the platform-specific files in a companion package when another package
  owns them, such as an `arbiter-client` package that provides a native client
  executable. The skill package stays platform-neutral and declares which files
  to copy from the companion package. The companion package publishes normal
  platform-tagged wheels under one package name, and ASI lets pip choose/build
  the right wheel.

Declare companion package copies in `agent-skill-installer.yaml`:

```yaml
installer:
  external_wheels:
    - package: "arbiter-client>=2.4,<2.5"
      editable: ../client
      copies:
        - wheel_path: arbiter_client/bin/arbiter
          skill_path: bin/arbiter
          executable: true
          replace: true
```

`package` is the pip package spec and version contract. During a PyPI or wheel
install, ASI runs `python -m pip wheel --no-deps --wheel-dir ...` for that spec,
then copies only the declared `wheel_path` files into the installed skill at the
matching `skill_path`.

For companion wheels, every platform wheel for the selected package should
contain the same declared `wheel_path`. Pip resolves which wheel file applies to
the current platform; ASI does not choose between platform-specific package
names.

`editable` is optional and only for local copied installs from a checkout. In
that case, ASI builds a wheel from that relative path instead of resolving
`package` from an index.

`skill_path` is always relative to the installed skill directory. Absolute
paths, empty path parts, `.`, and `..` are rejected. Set `executable: true` when
the copied file should be executable. External wheel copies cannot overwrite
files from the skill payload unless the copy rule sets `replace: true`; copies
cannot overwrite another external wheel copy.

`${package.version}` resolves to the version of the skill package being
installed. Use it only when the companion package intentionally shares the skill
package version, for example `package: companion-client==${package.version}`.

GitHub installs are intended for source skill directories. A GitHub source that
also needs platform-specific companion wheels is not officially supported,
because that workflow usually needs package build artifacts. Publish a
wheel-packaged skill for users, or use a local editable install while
developing.

## Binary Executables

For executable tools, use the same `skill_path` in every install mode:

- Published wheel or PyPI installs extract the skill payload, resolve the
  companion wheel with pip, and copy the platform-specific executable into the
  installed skill. Use `replace: true` when this should replace a bundled
  launcher at the same path.
- Local copied installs behave like published installs, but an optional
  `editable` path on the external wheel lets ASI build the companion package
  from a local checkout.
- Local editable installs symlink the top-level skill directory. ASI does not
  resolve or copy external wheels in this mode. Commit a launcher at the same
  `skill_path`, such as `bin/arbiter`, and have that launcher exec the live
  development binary.

See [`examples/companion-wheel-skill/`](../examples/companion-wheel-skill/) for
this launcher-plus-companion-wheel layout.
