# Platform-Specific Skills

Use a platform-specific skill when the same public package should install a
different skill payload on different operating systems or CPU architectures. For
example, a user can request one package, while Linux amd64 and Linux arm64
machines receive different skill versions with different files under `bin/`.

The package set has two roles:

- A selector package is the package users install.
- A target package contains the real skill payload for one platform.

The selector package carries `agent-skill-selector.yaml`. Target packages carry
the normal skill files, such as `SKILL.md`, `scripts/`, and `bin/`, plus
optional `agent-skill-installer.yaml` install-time metadata.

## Selector Metadata

Put `platform_specific` in `agent-skill-selector.yaml`:

```yaml
platform_specific:
  wheel: arbiter-skill-{os}-{arch}
  local_path: dist/arbiter-skill-{os}-{arch}
```

Supported template fields:

- `{os}`: normalized operating system, such as `linux`, `darwin`, or `windows`.
- `{arch}`: normalized architecture, such as `amd64` or `arm64`.
- `{platform}`: combined value in the form `{os}-{arch}`.

`wheel` names the target package to install for the current platform. This is
what lets one selector package install different target skill versions on
different platforms.
`local_path` points to the target skill directory for local `--skill-path`
installs and is resolved relative to `agent-skill-selector.yaml`.

## Package Layout

A selector package can be very small:

```text
selector-package/
  pyproject.toml
  MANIFEST.in
  src/
    selector_package/
      __init__.py
      _skill/
        agent-skill-selector.yaml
```

Each target package contains the actual skill:

```text
target-package-linux-amd64/
  pyproject.toml
  MANIFEST.in
  src/
    target_package_linux_amd64/
      __init__.py
      _skill/
        SKILL.md
        agent-skill-installer.yaml  # Install-time metadata
        bin/
          tool
```

The target's `agent-skill-installer.yaml` is consumed during install for
configuration and generated hook text, but it is not copied into the installed
skill directory. Do not put selector metadata there unless that target is
intentionally another selector.

## Install Behavior

For PyPI installs, the user asks for the selector package. The installer
downloads the selector wheel first, reads `agent-skill-selector.yaml`, renders
`wheel`, downloads the matching target wheel, and installs that target as the
skill.

For local wheel-file installs, build the selector wheel and target wheel into
the same directory. The installer reads the selector wheel and looks for a
sibling wheel whose distribution name and version match the rendered `wheel`
value.

For local `--skill-path` installs, point at the selector `_skill/` directory or
another local selector source. The installer renders `local_path` and installs
the resolved target skill directory.

Dispatch is intentionally one hop. If the resolved target also contains
`agent-skill-selector.yaml` and points at a different package or directory, the
installer rejects it instead of chaining through multiple selectors.

## Missing Targets

A selector can only install platforms that have a matching target artifact.
If a user runs on a normalized `{os}` and `{arch}` combination that renders to a
target you have not published or built, installation fails instead of falling
back to another platform.

For PyPI installs, publish a wheel for every supported target package and
version. If the rendered target package has no wheel on PyPI, installation fails
with a missing wheel error for that target package.

For local wheel-file installs, build the selector wheel and every supported
target wheel into the same directory. If the matching target wheel is absent,
installation fails with an error naming the missing platform-specific wheel.

For local `--skill-path` installs, `local_path` must resolve to an existing
target skill directory for the current platform.

## Example

See [`examples/platform-specific-skill/`](../examples/platform-specific-skill/)
for a runnable selector package with Linux amd64 and Linux arm64 target
packages.
