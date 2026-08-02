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

-- Imported decklists (meta decks, brews, ...). Meta-strength columns are optional and
-- feed the "best buildable deck" ranking; best_of distinguishes Bo1 vs Bo3 metagames.
CREATE TABLE IF NOT EXISTS decks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    format      TEXT,
    best_of     INTEGER,          -- 1, 3, or NULL for either/unknown
    tier        TEXT,             -- e.g. "1", "2" or "A"/"B" as provided by the source
    meta_share  REAL,             -- fraction of the metagame, 0..1
    win_rate    REAL,             -- 0..1
    source      TEXT,             -- text / archidekt / moxfield / mtggoldfish
    source_url  TEXT,
    imported_at TEXT NOT NULL
);

-- Cards within a deck. grp_id is the resolved MTGA printing (NULL if the name could not
-- be matched to our catalog). board is 'main' or 'side'.
CREATE TABLE IF NOT EXISTS deck_cards (
    deck_id   INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    card_name TEXT NOT NULL,
    set_code  TEXT,
    quantity  INTEGER NOT NULL,
    board     TEXT NOT NULL DEFAULT 'main',
    grp_id    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_deck_cards_deck ON deck_cards(deck_id);
