"""How names are compared."""

import re


def key(name: str) -> str:
    """A name as compared: lowercase words, without punctuation ("Sea-salt." is "sea salt")."""
    return " ".join(re.sub(r"[^a-z0-9 ]", "", name.lower().replace("-", " ")).split())
