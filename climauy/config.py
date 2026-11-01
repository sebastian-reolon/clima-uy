"""Runtime paths, always injected through environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    archive_dir: Path
    db_path: Path
    out_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        """Read paths from the environment; a missing variable raises KeyError naming it."""
        return cls(
            archive_dir=Path(os.environ["CLIMAUY_ARCHIVE_DIR"]),
            db_path=Path(os.environ["CLIMAUY_DB"]),
            out_dir=Path(os.environ["CLIMAUY_OUT"]),
        )
