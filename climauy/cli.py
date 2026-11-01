"""Command line entry point: python -m climauy <command>."""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys

from climauy.catalog import load_catalog
from climauy.config import Config
from climauy.history import FAILURES_KEY, backfill, real_fetch
from climauy.ingest import ingest_archive
from climauy.store import Store


def _ingest() -> int:
    cfg = Config.from_env()
    with Store(cfg.db_path) as store:
        report = ingest_archive(cfg.archive_dir, store, load_catalog())
    print(json.dumps(dataclasses.asdict(report)))
    # Fail only when every file seen for the first time in this run failed.
    return 1 if report.files_failed_new and report.files_new == 0 else 0


def _backfill(station: str | None) -> int:
    cfg = Config.from_env()
    catalog = load_catalog()
    if station is not None:
        catalog = [s for s in catalog if s.id == station]
        if not catalog:
            print(f"unknown station: {station}", file=sys.stderr)
            return 2
    with Store(cfg.db_path) as store:
        result = backfill(store, catalog, real_fetch)
    print(json.dumps(result))
    return 1 if result.get(FAILURES_KEY) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="climauy")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ingest", help="ingest the raw archive into the store")
    bf = sub.add_parser("backfill", help="download daily history since 1990")
    bf.add_argument("--station", help="only this station id")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    if args.command == "ingest":
        return _ingest()
    if args.command == "backfill":
        return _backfill(args.station)
    return 2
