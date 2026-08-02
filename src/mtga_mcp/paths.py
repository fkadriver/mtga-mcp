"""Filesystem locations for MTGA data and our own database.

MTGA on this machine is the native macOS build (launched via Heroic). Unity writes
its logs under ~/Library and ships its card catalog under Application Support. These
paths are macOS-specific; adjust here if you move to another platform.
"""

from __future__ import annotations

import glob
import os
from pathlib import Path

HOME = Path.home()

# MTGA's own SQLite card catalog. The filename embeds a content hash that changes on
# game updates, so we glob and pick the newest.
_RAW_DIR = HOME / "Library" / "Application Support" / "com.wizards.mtga" / "Downloads" / "Raw"
_CARD_DB_GLOB = "Raw_CardDatabase_*.mtga"

# MTGA writes owned-card and inventory payloads here (only when "Detailed Logs" is on).
PLAYER_LOG = HOME / "Library" / "Logs" / "Wizards Of The Coast" / "MTGA" / "Player.log"
PLAYER_LOG_PREV = HOME / "Library" / "Logs" / "Wizards Of The Coast" / "MTGA" / "Player-prev.log"

# Where we keep our own database and any cached downloads (Scryfall bulk).
DATA_DIR = Path(os.environ.get("MTGA_MCP_DATA_DIR", HOME / ".local" / "share" / "mtga-mcp"))
DB_PATH = DATA_DIR / "mtga.db"
SCRYFALL_CACHE = DATA_DIR / "scryfall-default-cards.jsonl.gz"


def find_card_database() -> Path:
    """Return the newest MTGA Raw_CardDatabase file, or raise if none is found."""
    matches = glob.glob(str(_RAW_DIR / _CARD_DB_GLOB))
    if not matches:
        raise FileNotFoundError(
            f"No MTGA card database found under {_RAW_DIR}. "
            "Is MTGA installed and has it finished downloading assets?"
        )
    return Path(max(matches, key=lambda p: os.path.getmtime(p)))


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
