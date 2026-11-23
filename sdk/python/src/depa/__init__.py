"""DEPA Training v2 SDK."""

import os

__version__ = "2.0.0"

DEFAULT_SOURCE_REPO = "https://github.com/prescera/depa-training"


def source_repo() -> str:
    """Source repository recorded in provenance (``DEPA_SOURCE_REPO`` overrides the default)."""
    return os.environ.get("DEPA_SOURCE_REPO") or DEFAULT_SOURCE_REPO
