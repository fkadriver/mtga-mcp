"""Command-line entry point: `mtga-mcp import [...]` and `mtga-mcp serve`."""

from __future__ import annotations

import argparse
import sys

import json

from . import (
    db,
    deck_analysis,
    deck_sources,
    decklist,
    ingest_catalog,
    ingest_collection,
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
        if run_all or args.scryfall:
            print("scryfall:   downloading/parsing bulk data (this can take a while)...")
            n = ingest_scryfall.ingest(conn, force=args.force_scryfall)
            print(f"scryfall:   {n} cards enriched")
    finally:
        conn.close()
    print(f"\ndatabase:   {paths.DB_PATH}")
    return 0


def _cmd_serve(_args: argparse.Namespace) -> int:
    from . import server

    server.main()
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
            conn, best_of=args.best_of, fmt=args.format, max_wildcards=args.max_wildcards
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

    srv = sub.add_parser("serve", help="Run the MCP server over stdio")
    srv.set_defaults(func=_cmd_serve)

    _add_deck_commands(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
