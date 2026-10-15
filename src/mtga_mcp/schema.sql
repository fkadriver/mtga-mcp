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
-- resolve_card (decklist.py) and anchors.resolve_card both look up by `name = ? COLLATE
-- NOCASE`; a binary-collation index can't serve that, forcing a full table scan on every
-- card resolved. This index matches the comparison's collation so it actually gets used.
CREATE INDEX IF NOT EXISTS idx_cards_name_nocase ON cards(name COLLATE NOCASE);

-- Owned quantities, keyed by MTGA GrpId. Either from Player.log GrantedCards deltas
-- (capture) or replaced wholesale by a full memory-scanner export (ingest_export).
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
    imported_at TEXT NOT NULL,
    dup_of      INTEGER REFERENCES decks(id)  -- near-duplicate of this other deck, if any
                                               -- (see deck_dedupe.py); never deleted/merged
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

-- Raw InventoryInfo payloads, accumulated across log rotations. Preserves the `Changes`
-- deltas (GrantedCards etc.) so the card-delta parser (capture._apply_card_changes) can
-- rebuild owned counts as packs are opened / new sets release.
--
-- SeqId is scoped to a single MTGA client session/log file, not globally unique -- it resets
-- to 1 on every session, so two payloads from different sessions commonly share a SeqId.
-- Dedup therefore keys on payload_hash (sha256 of the JSON), not seq_id.
CREATE TABLE IF NOT EXISTS inventory_raw (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    seq_id       INTEGER NOT NULL,
    payload_hash TEXT NOT NULL UNIQUE,
    captured_at  TEXT NOT NULL,
    payload      TEXT NOT NULL          -- full InventoryInfo JSON
);

CREATE INDEX IF NOT EXISTS idx_inventory_raw_seq ON inventory_raw(seq_id);

-- Flattened wildcard/currency snapshot per capture, for trend queries.
CREATE TABLE IF NOT EXISTS inventory_history (
    captured_at TEXT PRIMARY KEY,
    seq_id      INTEGER,
    common      INTEGER,
    uncommon    INTEGER,
    rare        INTEGER,
    mythic      INTEGER,
    gold        INTEGER,
    gems        INTEGER,
    vault       INTEGER
);

-- Your own completed-match results, parsed from Player.log's matchGameRoomStateChangedEvent
-- (see match_capture.py). deck_name/format come from the EventSetDeckV3 request that queued
-- the match, joined against `decks.name` (case-insensitive) at query time -- there's no
-- direct foreign key, since an Arena deck and a stored `decks` row are different entities
-- that only line up when you've named your in-game deck the same as something you imported.
CREATE TABLE IF NOT EXISTS match_history (
    match_id   TEXT PRIMARY KEY,
    deck_name  TEXT,              -- NULL if we couldn't find the queuing EventSetDeckV3
    format     TEXT,
    won        INTEGER NOT NULL,  -- 1/0
    played_at  TEXT NOT NULL
);
