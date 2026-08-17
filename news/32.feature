Installs now warn when a skill advertises a trigger that does not match the
name it installs under, so a discoverability block cannot silently promise a
`/trigger` that was never registered. A second warning covers renames, where
the install directory and the `SKILL.md` frontmatter name disagree.
