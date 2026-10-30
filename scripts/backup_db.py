"""Бэкап SQLite GiftTracker с ротацией.

Запуск:
    python scripts/backup_db.py
    python scripts/backup_db.py --keep 14
    python scripts/backup_db.py --db data/gift_tracker.db --out D:/backups

Используется sqlite3 backup API: копия снимается с живой базы (WAL
учитывается) без длительной блокировки записи. Хранятся последние
``--keep`` файлов (по умолчанию 7), более старые удаляются.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PREFIX = "gift_tracker-"
SUFFIX = ".db"


def backup_database(db_path: Path, out_dir: Path, keep: int = 7) -> Path:
    """Снимает копию БД и применяет ротацию; возвращает путь к бэкапу."""
    if not db_path.exists():
        raise FileNotFoundError(f"database not found: {db_path}")
    if keep < 1:
        raise ValueError("--keep must be >= 1")

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"{PREFIX}{stamp}{SUFFIX}"
    counter = 1
    while target.exists():  # два запуска в одну секунду
        target = out_dir / f"{PREFIX}{stamp}-{counter}{SUFFIX}"
        counter += 1

    source = sqlite3.connect(str(db_path))
    destination = sqlite3.connect(str(target))
    try:
        with destination:
            source.backup(destination)
    except Exception:
        destination.close()
        source.close()
        target.unlink(missing_ok=True)
        raise
    else:
        destination.close()
        source.close()

    prune_backups(out_dir, keep)
    return target


def prune_backups(out_dir: Path, keep: int) -> list[Path]:
    """Удаляет старые бэкапы, оставляя ``keep`` самых свежих."""
    files = sorted(
        out_dir.glob(f"{PREFIX}*{SUFFIX}"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed = files[max(keep, 0):]
    for path in removed:
        path.unlink(missing_ok=True)
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backup GiftTracker SQLite database")
    parser.add_argument(
        "--db",
        default=str(PROJECT_ROOT / "data" / "gift_tracker.db"),
        help="path to SQLite database (default: data/gift_tracker.db)",
    )
    parser.add_argument(
        "--out",
        default=str(PROJECT_ROOT / "data" / "backups"),
        help="backup directory (default: ./data/backups)",
    )
    parser.add_argument("--keep", type=int, default=7, help="backups to keep (default: 7)")
    args = parser.parse_args(argv)

    try:
        target = backup_database(Path(args.db), Path(args.out), keep=args.keep)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1

    print(f"backup ok: {target} ({target.stat().st_size} bytes, keep={args.keep})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = ["backup_database", "main", "prune_backups"]
