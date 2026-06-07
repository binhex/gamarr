import re
import string
from pathlib import Path


def get_project_root() -> Path:
    return Path(__file__).parent.parent.parent


def normalise_for_compare(text: str) -> str:
    """Normalise a title string for case-insensitive fuzzy comparison.

    Lowercases the text, strips ASCII punctuation and Unicode dashes
    (en-dash U+2013, em-dash U+2014), and collapses whitespace.
    """
    text = text.lower().strip()
    remove_chars = string.punctuation + "\u2013\u2014"
    text = text.translate(str.maketrans("", "", remove_chars))
    return re.sub(r"\s+", " ", text).strip()
