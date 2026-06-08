from agent_skill_installer import SkillProject
from agent_skill_installer.cli import main as installer_main

from . import __version__


PROJECT = SkillProject(
    package_name="demo-agent-skill",
    import_name="demo_agent_skill",
    version=__version__,
    skill_name="demo-agent-skill",
    description="Use this demo skill to verify a skill package installer wiring.",
    bundled_skill_path="skill",
)


def main(argv=None) -> int:
    return installer_main(argv, project=PROJECT)
