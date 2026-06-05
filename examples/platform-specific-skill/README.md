# Platform-Specific Skill Example

This example shows one installable selector package resolving to different skill
payloads for different platforms.

See [Platform-Specific Skills](../../docs/platform-specific-skills.md) for the
full selector and target package model.

The selector package carries only `agent-skill-selector.yaml`:

```yaml
platform_specific:
  wheel: platform-specific-skill-{os}-{arch}
  local_path: ../../../../targets/platform-specific-skill-{os}-{arch}/src/platform_specific_skill_{os}_{arch}/_skill
```

That template spans a platform matrix. This example ships the Linux rows:

| Machine | `{os}` | `{arch}` | Resolved `wheel` |
| --- | --- | --- | --- |
| Linux x86_64 or amd64 | `linux` | `amd64` | `platform-specific-skill-linux-amd64` |
| Linux aarch64 or arm64 | `linux` | `arm64` | `platform-specific-skill-linux-arm64` |

The same pattern could add rows such as `darwin-arm64` or `windows-amd64` by
publishing matching target packages. If the selector resolves to a row whose
target package or wheel was not published or built, installation fails instead
of falling back to another platform.

Additional selector variables or user-provided constants may be considered when
there is a convincing use case.

For wheel installs, the generic installer reads the selector wheel, renders the
current platform, and installs the matching sibling wheel. For local installs,
`local_path` is resolved relative to the selector YAML file.

The resolved target package has the normal skill layout:

```text
SKILL.md
agent-skill-installer.yaml  # Install-time metadata
bin/demo-tool.txt
```

The installer reads the metadata file for configuration, then installs only the
runtime skill files such as `SKILL.md` and `bin/demo-tool.txt`.

Build the selector and one of the included Linux target packages, then install
through the selector wheel. Substitute `platform-specific-skill-linux-arm64` on
Linux arm64:

```bash
python -m build --wheel --no-isolation --outdir /tmp/platform-specific-dist examples/platform-specific-skill/selector
python -m build --wheel --no-isolation --outdir /tmp/platform-specific-dist examples/platform-specific-skill/targets/platform-specific-skill-linux-amd64
agent-skill-installer --no-ui install \
  --wheel-file /tmp/platform-specific-dist/platform_specific_skill-0.1.0-py3-none-any.whl \
  --agent codex \
  --scope repo \
  --target-dir /path/to/repo
```
