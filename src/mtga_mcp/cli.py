"""Command-line entry point: `mtga-mcp import [...]` and `mtga-mcp serve`."""

from __future__ import annotations

import argparse
import sys

from . import db, ingest_catalog, ingest_collection, ingest_scryfall, paths


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

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
