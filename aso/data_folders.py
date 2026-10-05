"""The owner's real data folders, which tools that fill or read a scratch folder must never touch.

The Mac app keeps its data in ~/Library/Application Support/RespectASO; a
checkout run without DATA_DIR uses ./data. seed_scratch (Pro) and
export_site_measurements refuse both.
"""

from pathlib import Path

from django.conf import settings

REAL_DATA_DIRS = (
    Path.home() / "Library" / "Application Support" / "RespectASO",
    Path(settings.BASE_DIR) / "data",
)


def is_real_data_dir(path) -> bool:
    """True when ``path`` is one of the owner's real data folders."""
    resolved = Path(path).resolve()
    return any(resolved == real.resolve() for real in REAL_DATA_DIRS)
