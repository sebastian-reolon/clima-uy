"""Command line entry point: python -m climauy <command>."""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sys
from datetime import datetime, timezone

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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _build(base_url: str) -> int:
    from climauy.site.build import build_site  # jinja2 is only needed for this command

    cfg = Config.from_env()
    now = _now()
    with Store(cfg.db_path) as store:
        pages = build_site(store, load_catalog(), cfg.out_dir, now, base_url=base_url)
    print(json.dumps({"pages": [str(p.relative_to(cfg.out_dir)) for p in pages], "built_utc": now.isoformat()}))
    return 0


def _all(base_url: str) -> int:
    """ingest, build, export, snapshot and (only with CLIMAUY_PAGES_PUSH=1) commit and push."""
    from climauy import publish
    from climauy.site.build import build_site
    from climauy.timeutil import local_day

    log = logging.getLogger("climauy.all")
    ingest_rc = _ingest()
    cfg = Config.from_env()
    now = _now()  # one instant for build, export and snapshot, so they agree on the day
    catalog = load_catalog()
    try:
        with Store(cfg.db_path) as store:
            build_site(store, catalog, cfg.out_dir, now, base_url=base_url)
            full = publish.full_export_due(store, now)
            files = publish.export_data(store, catalog, cfg.out_dir, now, full=full)
            if full:
                publish.mark_full_export(store, now)
        snapshot = publish.write_snapshot(cfg.out_dir, local_day(now), base_url=base_url)
    except Exception:
        log.exception("build or export failed")
        return 1
    try:
        hits = publish.scan_tree(cfg.out_dir)
    except Exception:
        log.exception("refusing to publish: the infrastructure scan of the pages output failed")
        return 1
    if hits:
        for h in hits:
            log.error("infrastructure string in the pages output: %s", h)
        log.error("refusing to publish: %d hit(s), nothing committed", len(hits))
        return 1
    pushed = None
    if os.environ.get("CLIMAUY_PAGES_PUSH") == "1":
        try:
            pushed = publish.commit_and_push(cfg.out_dir, f"Build {now.strftime('%Y-%m-%dT%H:%MZ')}")
        except Exception:
            log.exception("commit or push failed")
            return 1
    print(json.dumps({"built_utc": now.isoformat(), "full_export": full, "files": len(files),
                      "snapshot": str(snapshot.relative_to(cfg.out_dir)), "pushed": pushed}))
    return ingest_rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="climauy")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ingest", help="ingest the raw archive into the store")
    bf = sub.add_parser("backfill", help="download daily history since 1990")
    bf.add_argument("--station", help="only this station id")
    b = sub.add_parser("build", help="build the static site into CLIMAUY_OUT")
    b.add_argument("--base-url", default="/clima-uy", help='path prefix of the site ("" for the root)')
    a = sub.add_parser("all", help="ingest, build, export and snapshot; push only with CLIMAUY_PAGES_PUSH=1")
    a.add_argument("--base-url", default="/clima-uy", help='path prefix of the site ("" for the root)')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    if args.command == "ingest":
        return _ingest()
    if args.command == "backfill":
        return _backfill(args.station)
    if args.command == "build":
        return _build(args.base_url)
    if args.command == "all":
        return _all(args.base_url)
    return 2
