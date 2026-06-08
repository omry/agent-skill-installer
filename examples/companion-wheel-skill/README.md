# Companion Wheel Skill

This example shows the final shape for a skill that needs an executable from a
separate Python distribution.

The skill package contains a portable launcher at `bin/demo-tool`. Editable
installs symlink the top-level skill directory, so that launcher stays live in
the checkout. Real wheel or PyPI installs can replace the launcher with the file
from the companion package by using `external_wheels` with `replace: true`.

```text
companion-wheel-skill/
  skill-package/
    companion_wheel_skill/skill/
      SKILL.md
      agent-skill-installer.yaml
      bin/demo-tool              # portable development launcher
  native-client/
    example_native_client/
      bin/demo-tool              # file copied into real installs
```

`native-client/` is the source for one companion Python distribution:
`example-native-client`. In a real release, build and upload one wheel per
supported platform under that same PyPI project name and version, for example
Linux, macOS, and Windows wheels for `example-native-client==0.1.0`. Do not use
different package names per platform.

Each platform wheel should contain the same internal path,
`example_native_client/bin/demo-tool`. The skill declares that path once in
`agent-skill-installer.yaml`; ASI asks pip for `example-native-client==0.1.0`,
pip selects the wheel matching the current platform, and ASI copies the declared
file into `bin/demo-tool`.

For a local copied install from the repository root:

```bash
agent-skill-installer --no-ui install \
  --skill-path examples/companion-wheel-skill/skill-package/companion_wheel_skill/skill \
  --copy \
  --agent codex \
  --scope repo \
  --target-dir /path/to/repo
```

For a published install, publish the skill package and publish platform-tagged
wheels for the companion package named by `package` in
`agent-skill-installer.yaml`.
