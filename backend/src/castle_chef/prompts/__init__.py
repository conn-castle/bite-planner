"""The agent's instructions, one Markdown template per kind of job here, with
`$name` placeholders."""

from pathlib import Path
from string import Template


def fill(name: str, **values: object) -> str:
    """The prompt `name` (its file here, without `.md`), its placeholders filled in."""
    return Template((Path(__file__).parent / f"{name}.md").read_text()).substitute(values)
