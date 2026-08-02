-- Our own SQLite database (separate from MTGA's read-only card DB).
-- Populated by the ingest_* modules.

CREATE TABLE IF NOT EXISTS cards (
    grp_id           INTEGER PRIMARY KEY,   -- MTGA GrpId == Scryfall arena_id
    name             TEXT NOT NULL,
    set_code         TEXT,
    collector_number TEXT,
    rarity           TEXT,                  -- basic/common/uncommon/rare/mythic
    colors           TEXT,                  -- e.g. "WU" (from MTGA catalog)
    color_identity   TEXT,                  -- e.g. "WUBRG" (from Scryfall)
    type_line        TEXT,
    mana_cost        TEXT,
    cmc              REAL,
    power            TEXT,
    toughness        TEXT,
    oracle_text      TEXT,
    keywords         TEXT,                  -- comma-separated (from Scryfall)
    prices_usd       REAL,
    legal_standard   TEXT,
    legal_pioneer    TEXT,
    legal_explorer   TEXT,
    legal_historic   TEXT,
    image_uri        TEXT,
    scryfall_id      TEXT
);

CREATE INDEX IF NOT EXISTS idx_cards_name ON cards(name);
CREATE INDEX IF NOT EXISTS idx_cards_set ON cards(set_code);

-- Owned quantities, keyed by MTGA GrpId. From Player.log.
CREATE TABLE IF NOT EXISTS collection (
    grp_id INTEGER PRIMARY KEY,
    count  INTEGER NOT NULL
);

-- Wildcard / currency balances. From Player.log PlayerInventory payload.
CREATE TABLE IF NOT EXISTS wildcards (
    kind  TEXT PRIMARY KEY,   -- common/uncommon/rare/mythic/gold/gems/vault
    count INTEGER NOT NULL
);

-- Bookkeeping: import timestamps, source file hashes, scryfall bulk date, etc.
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
