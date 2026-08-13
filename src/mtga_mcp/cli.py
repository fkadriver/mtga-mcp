"""Command-line entry point: `mtga-mcp import [...]` and `mtga-mcp serve`."""

from __future__ import annotations

import argparse
import sys

import json

from . import (
    capture as capture_mod,
    db,
    deck_analysis,
    deck_sources,
    decklist,
    ingest_catalog,
    ingest_collection,
    ingest_export,
    ingest_scryfall,
    paths,
)


def _cmd_import(args: argparse.Namespace) -> int:
    run_all = args.all or not (args.catalog or args.collection or args.scryfall)
    conn = db.connect()
    try:
        if run_all or args.catalog:
            n = ingest_catalog.ingest(conn)
            print(f"catalog:    {n} cards loaded from MTGA card database")
        if run_all or args.collection:
            res = ingest_collection.ingest(conn)
            if res.source is None:
                print(
                    "collection: no collection payload found in Player.log.\n"
                    "            Enable MTGA Settings -> Account -> 'Detailed Logs "
                    "(Plugin Support)', restart MTGA, open your Collection, then re-run."
                )
            else:
                print(
                    f"collection: {res.cards_written} owned entries, "
                    f"{res.wildcards_written} wildcard/currency balances "
                    f"(from {res.source})"
                )
                if not res.owned_cards_available:
                    print(
                        "            note: this MTGA client does not log the full owned-card "
                        "list, so owned quantities are unavailable (wildcards still imported)."
                    )
        if run_all or args.scryfall:
            print("scryfall:   downloading/parsing bulk data (this can take a while)...")
            n = ingest_scryfall.ingest(conn, force=args.force_scryfall)
            print(f"scryfall:   {n} cards enriched")
    finally:
        conn.close()
    print(f"\ndatabase:   {paths.DB_PATH}")
    return 0


def _cmd_import_collection(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        res = ingest_export.ingest(conn, args.export)
    finally:
        conn.close()
    print(
        f"collection: {res.grp_rows} printings / {res.total_copies} copies "
        f"from {res.entries} cards (memory export {res.export_date or 'n/a'})"
    )
    if res.unknown_grp_ids:
        print(
            f"            note: {res.unknown_grp_ids} grp_ids not in the local catalog; "
            f"run `uv run mtga-mcp import --catalog` to refresh, then re-import."
        )
    print(f"\ndatabase:   {paths.DB_PATH}")
    return 0


def _chown_outputs_to_sudo_user() -> None:
    """When run under sudo, hand our DB (and its WAL sidecars) back to the invoking user so a
    later non-sudo run isn't locked out by root-owned files."""
    import os
    from pathlib import Path

    sudo_uid = os.environ.get("SUDO_UID")
    if not sudo_uid:
        return
    uid = int(sudo_uid)
    gid = int(os.environ.get("SUDO_GID") or -1)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.chown(Path(f"{paths.DB_PATH}{suffix}"), uid, gid)
        except (FileNotFoundError, PermissionError):
            pass


def _cmd_export_collection(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone

    from . import anchors, memory_export

    conn = db.connect()
    try:
        known_ids = {r[0] for r in conn.execute("SELECT grp_id FROM cards")}
        if not known_ids:
            print("No card catalog loaded — run `uv run mtga-mcp import --catalog` first.")
            return 1

        chosen = anchors.choose_anchors(conn, want=args.anchors)
        if len(chosen) < anchors.MIN_ANCHORS:
            print("Aborted: need at least 3 anchor cards.")
            return 1

        print("\nScanning MTGA memory…")
        block = memory_export.scan(known_ids, chosen)
        if not block:
            print(
                "\nScan failed: couldn't locate the collection in memory. Check that MTGA is "
                "running with the Collection screen opened, that your anchor quantities are "
                "exact, and (macOS) that you ran with sudo."
            )
            return 1

        res = ingest_export.ingest_block(
            conn, block, source="memory-scan",
            export_date=datetime.now(timezone.utc).isoformat(),
        )
    finally:
        conn.close()

    _chown_outputs_to_sudo_user()
    print(
        f"\ncollection: {res.grp_rows} printings / {res.total_copies} copies "
        f"scanned from MTGA memory"
    )
    if res.unknown_grp_ids:
        print(
            f"            note: {res.unknown_grp_ids} grp_ids aren't in the local catalog — "
            f"usually filtered token/art printings (harmless). If you just updated MTGA, "
            f"`uv run mtga-mcp import --catalog` may add a newly-released set."
        )
    print(f"\ndatabase:   {paths.DB_PATH}")
    return 0


def _cmd_serve(_args: argparse.Namespace) -> int:
    from . import server

    server.main()
    return 0


def _cmd_capture(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        res = capture_mod.capture(conn, force=args.force)
    finally:
        conn.close()
    if res.skipped:
        print("capture: logs unchanged since last run — skipped (no parsing).")
        return 0
    print(f"capture: +{res.new_raw} new InventoryInfo payloads ({res.total_raw} archived total)")
    if res.snapshot:
        print(f"         wildcards/currency: {res.snapshot}")
    if res.changes_seen:
        print(f"         {res.changes_seen} card copies applied to `collection` "
              f"(from GrantedCards deltas)")
    return 0


def _cmd_history(args: argparse.Namespace) -> int:
    conn = db.connect_readonly()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT captured_at, common, uncommon, rare, mythic, gold, gems, vault "
            "FROM inventory_history ORDER BY captured_at DESC LIMIT ?", (args.limit,)
        )]
    finally:
        conn.close()
    _pretty(rows)
    return 0


def _pretty(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _cmd_deck_import(args: argparse.Namespace) -> int:
    if args.url and "mtggoldfish.com" in args.url:
        fetched = deck_sources.from_scrape(args.url, enable=args.allow_scrape)
    elif args.url:
        fetched = deck_sources.from_url(args.url)
    else:
        text = sys.stdin.read() if args.paste else open(args.file, encoding="utf-8").read()
        fetched = deck_sources.from_text(text, name=args.name)
    conn = db.connect()
    try:
        stored = decklist.store_deck(
            conn, name=args.name or fetched.name, fmt=args.format or fetched.fmt,
            cards=fetched.cards, source=fetched.source, source_url=fetched.source_url,
            best_of=args.best_of if args.best_of is not None else fetched.best_of,
            tier=args.tier, meta_share=args.meta_share,
        )
    finally:
        conn.close()
    print(f"imported deck #{stored.deck_id}: {args.name or fetched.name} "
          f"({stored.resolved} cards resolved)")
    if stored.unresolved:
        print(f"  unresolved ({len(stored.unresolved)}): {', '.join(stored.unresolved)}")
    return 0


def _cmd_deck_list(_args: argparse.Namespace) -> int:
    conn = db.connect_readonly()
    try:
        _pretty([dict(r) for r in conn.execute(
            "SELECT id, name, format, best_of, tier, meta_share, source FROM decks ORDER BY id"
        )])
    finally:
        conn.close()
    return 0


def _cmd_deck_gap(args: argparse.Namespace) -> int:
    conn = db.connect_readonly()
    try:
        _pretty(deck_analysis.deck_gap(conn, args.deck))
    finally:
        conn.close()
    return 0


def _cmd_deck_craft(args: argparse.Namespace) -> int:
    conn = db.connect_readonly()
    try:
        _pretty(deck_analysis.craft_priority(conn, fmt=args.format))
    finally:
        conn.close()
    return 0


def _cmd_deck_best(args: argparse.Namespace) -> int:
    conn = db.connect_readonly()
    try:
        _pretty(deck_analysis.best_buildable_deck(
            conn, best_of=args.best_of, fmt=args.format, max_wildcards=args.max_wildcards,
            include_illegal=args.include_illegal,
        ))
    finally:
        conn.close()
    return 0


def _cmd_deck_delete(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id FROM decks WHERE id = ? OR name = ? COLLATE NOCASE",
            (args.deck if str(args.deck).isdigit() else -1, str(args.deck)),
        ).fetchone()
        if not row:
            print(f"no deck matching {args.deck!r}")
            return 1
        conn.execute("DELETE FROM deck_cards WHERE deck_id = ?", (row["id"],))
        conn.execute("DELETE FROM decks WHERE id = ?", (row["id"],))
        conn.commit()
        print(f"deleted deck #{row['id']}")
    finally:
        conn.close()
    return 0


def _add_deck_commands(sub) -> None:
    deck = sub.add_parser("deck", help="Import and analyze decklists")
    dsub = deck.add_subparsers(dest="deck_command", required=True)

    imp = dsub.add_parser("import", help="Import a decklist from text or a URL")
    src = imp.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="Read decklist text from a file")
    src.add_argument("--paste", action="store_true", help="Read decklist text from stdin")
    src.add_argument("--url", help="Archidekt/Moxfield URL (or MTGGoldfish with --allow-scrape)")
    imp.add_argument("--name", help="Deck name")
    imp.add_argument("--format", help="Format, e.g. Standard/Alchemy/Historic/Explorer")
    imp.add_argument("--best-of", type=int, choices=(1, 3), dest="best_of", help="1 or 3")
    imp.add_argument("--tier", help="Meta tier, e.g. 1 / 2 / A / B")
    imp.add_argument("--meta-share", type=float, dest="meta_share", help="Meta share 0..1")
    imp.add_argument("--allow-scrape", action="store_true", help="Permit MTGGoldfish scraping")
    imp.set_defaults(func=_cmd_deck_import)

    dsub.add_parser("list", help="List stored decks").set_defaults(func=_cmd_deck_list)

    gap = dsub.add_parser("gap", help="Wildcards/cards needed to build a deck")
    gap.add_argument("deck", help="Deck id or name")
    gap.set_defaults(func=_cmd_deck_gap)

    craft = dsub.add_parser("craft-priority", help="Best cards to craft across decks")
    craft.add_argument("--format", help="Restrict to a format")
    craft.set_defaults(func=_cmd_deck_craft)

    best = dsub.add_parser("best", help="Best deck you could build given your cards + meta")
    best.add_argument("--best-of", type=int, choices=(1, 3), dest="best_of")
    best.add_argument("--format", help="Restrict to a format")
    best.add_argument("--max-wildcards", type=int, dest="max_wildcards",
                      help="Hide decks needing more than this many wildcards")
    best.add_argument("--include-illegal", action="store_true", dest="include_illegal",
                      help="Keep decks with cards not legal in their format (flagged, not hidden)")
    best.set_defaults(func=_cmd_deck_best)

    dele = dsub.add_parser("delete", help="Delete a stored deck")
    dele.add_argument("deck", help="Deck id or name")
    dele.set_defaults(func=_cmd_deck_delete)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mtga-mcp", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import", help="Ingest MTGA + Scryfall data into the database")
    imp.add_argument("--catalog", action="store_true", help="Load MTGA card catalog")
    imp.add_argument("--collection", action="store_true", help="Load owned cards from Player.log")
    imp.add_argument("--scryfall", action="store_true", help="Enrich with Scryfall bulk data")
    imp.add_argument("--all", action="store_true", help="Run all import steps (default)")
    imp.add_argument("--force-scryfall", action="store_true", help="Re-download Scryfall bulk")
    imp.set_defaults(func=_cmd_import)

    impc = sub.add_parser(
        "import-collection",
        help="Load the full owned collection from a MTGA-collection-exporter JSON export "
             "(replaces the current collection)",
    )
    impc.add_argument("export", help="Path to mtga_collection.json")
    impc.set_defaults(func=_cmd_import_collection)

    exp = sub.add_parser(
        "export-collection",
        help="Scan the running MTGA client's memory for your full collection and import it "
             "(replaces the current collection). macOS needs sudo.",
    )
    exp.add_argument("--anchors", type=int, default=5,
                     help="How many anchor cards to propose from your collection (default 5)")
    exp.set_defaults(func=_cmd_export_collection)

    srv = sub.add_parser("serve", help="Run the MCP server over stdio")
    srv.set_defaults(func=_cmd_serve)

    cap = sub.add_parser("capture", help="Snapshot MTGA inventory (wildcards/currency) + archive "
                                         "raw payloads; run regularly to preserve deltas")
    cap.add_argument("--force", action="store_true",
                     help="Parse even if the logs haven't changed since the last capture")
    cap.set_defaults(func=_cmd_capture)

    hist = sub.add_parser("history", help="Show recent inventory (wildcard/currency) snapshots")
    hist.add_argument("--limit", type=int, default=20)
    hist.set_defaults(func=_cmd_history)

    _add_deck_commands(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
