"""Filesystem locations for MTGA data and our own database.

Defaults target the **native macOS** MTGA build (Unity logs under ~/Library, card catalog
under Application Support). Other platforms — notably **Linux/NixOS running MTGA via Heroic
(Wine/Proton)** — put these files inside a Wine prefix, so every location can be overridden
with an environment variable:

    MTGA_MCP_PLAYER_LOG    full path to Player.log
    MTGA_MCP_PLAYER_LOG_PREV  (optional) Player-prev.log; defaults to a sibling of PLAYER_LOG
    MTGA_MCP_RAW_DIR       directory holding Raw_CardDatabase_*.mtga
    MTGA_MCP_UTC_LOG_DIR   directory of rotating UTC_Log*.log files
    MTGA_MCP_DATA_DIR      where we keep our own DB / caches (default ~/.local/share/mtga-mcp)

For Heroic on Linux these live under the game's Wine prefix, e.g.
    <prefix>/drive_c/users/<user>/AppData/LocalLow/Wizards Of The Coast/MTGA/Player.log
    .../MTGA/Downloads/Raw
"""

from __future__ import annotations

import glob
import os
from pathlib import Path

HOME = Path.home()

# macOS default roots.
_MAC_LOG_DIR = HOME / "Library" / "Logs" / "Wizards Of The Coast" / "MTGA"
_MAC_APP_SUPPORT = HOME / "Library" / "Application Support" / "com.wizards.mtga"

_CARD_DB_GLOB = "Raw_CardDatabase_*.mtga"


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


# MTGA writes owned-card and inventory payloads here (only when "Detailed Logs" is on).
PLAYER_LOG = _env_path("MTGA_MCP_PLAYER_LOG") or (_MAC_LOG_DIR / "Player.log")
PLAYER_LOG_PREV = _env_path("MTGA_MCP_PLAYER_LOG_PREV") or (PLAYER_LOG.parent / "Player-prev.log")

# MTGA's own SQLite card catalog. The filename embeds a content hash that changes on game
# updates, so we glob and pick the newest.
_RAW_DIR = _env_path("MTGA_MCP_RAW_DIR") or (_MAC_APP_SUPPORT / "Downloads" / "Raw")

# The client also mirrors detailed RPC payloads (incl. InventoryInfo) into rotating UTC logs.
_UTC_LOG_DIR = _env_path("MTGA_MCP_UTC_LOG_DIR") or (_MAC_APP_SUPPORT / "Logs" / "Logs")

# Where we keep our own database and any cached downloads (Scryfall bulk).
DATA_DIR = Path(os.environ.get("MTGA_MCP_DATA_DIR", HOME / ".local" / "share" / "mtga-mcp"))
DB_PATH = DATA_DIR / "mtga.db"
SCRYFALL_CACHE = DATA_DIR / "scryfall-default-cards.jsonl.gz"


def detailed_log_files(max_utc: int = 1) -> list[Path]:
    """Log files that may hold InventoryInfo payloads: Player.log, its prev, and the newest
    `max_utc` UTC logs (bounded to keep capture cheap)."""
    files = [p for p in (PLAYER_LOG, PLAYER_LOG_PREV) if p.exists()]
    if _UTC_LOG_DIR.is_dir():
        utc = sorted(_UTC_LOG_DIR.glob("UTC_Log*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        files.extend(utc[:max_utc])
    return files


def find_card_database() -> Path:
    """Return the newest MTGA Raw_CardDatabase file, or raise if none is found."""
    matches = glob.glob(str(_RAW_DIR / _CARD_DB_GLOB))
    if not matches:
        raise FileNotFoundError(
            f"No MTGA card database found under {_RAW_DIR}. Is MTGA installed and finished "
            "downloading assets? On Linux/Heroic set MTGA_MCP_RAW_DIR to the Wine prefix's "
            "'.../Wizards Of The Coast/MTGA/Downloads/Raw'."
        )
    return Path(max(matches, key=lambda p: os.path.getmtime(p)))


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
