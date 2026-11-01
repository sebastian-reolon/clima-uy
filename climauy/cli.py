"""Command line entry point: python -m climauy <command>."""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys

from climauy.catalog import load_catalog
from climauy.config import Config
from climauy.ingest import ingest_archive
from climauy.store import Store


def _ingest() -> int:
    cfg = Config.from_env()
    with Store(cfg.db_path) as store:
        report = ingest_archive(cfg.archive_dir, store, load_catalog())
    print(json.dumps(dataclasses.asdict(report)))
    # Fail only when every file seen for the first time in this run failed.
    return 1 if report.files_failed_new and report.files_new == 0 else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="climauy")
    parser.add_subparsers(dest="command", required=True).add_parser("ingest", help="ingest the raw archive into the store")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    if args.command == "ingest":
        return _ingest()
    return 2
