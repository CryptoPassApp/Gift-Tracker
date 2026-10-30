"""Тесты этапа «Надёжность»: persistent dedup, WAL, логи, heartbeat, бэкап."""
from __future__ import annotations

import asyncio
import io
import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from api.main import create_app
from bot.config import Settings
from bot.database import Database
from bot.healthcheck import main as healthcheck_main
from bot.logging_setup import JsonFormatter, configure_logging, resolve_level
from bot.models import SeenLot, SnipeSignal
from bot.services.heartbeat import (
    Heartbeat,
    heartbeat_status,
    read_heartbeat,
    write_heartbeat,
)
from bot.services.maintenance import MaintenanceService
from bot.services.market_api import GIFT_PRESETS, MarketAPI, getgems_collection_url
from bot.services.notifier import Notifier
from bot.services.sniper_engine import SniperEngine
from scripts.backup_db import backup_database, prune_backups
from tests.conftest import make_settings
from tests.test_sniper_engine import RecordingMarket, hot_lot, preset_floors


async def count_rows(db: Database, model) -> int:
    async with db.session() as session:
        return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


class TestPersistentDedup:
    async def test_lot_not_repushed_after_engine_restart(self, db, settings):
        """Новый процесс (пустой L1-кэш) видит отметку в SQLite и молчит."""
        market = RecordingMarket([hot_lot()], preset_floors())
        first = SniperEngine(market=market, db=db, notifier=Notifier(settings), settings=settings)
        assert len(await first.scan_once()) == 1

        restarted = SniperEngine(market=market, db=db, notifier=Notifier(settings), settings=settings)
        assert await restarted.scan_once() == []
        assert await count_rows(db, SnipeSignal) == 1
        assert await count_rows(db, SeenLot) == 1

    async def test_claim_seen_lot_is_idempotent(self, db):
        assert await db.claim_seen_lot("https://getgems.io/nft/0:one") is True
        assert await db.claim_seen_lot("https://getgems.io/nft/0:one") is False

    async def test_prune_removes_only_expired_marks(self, db, settings):
        old = datetime.utcnow() - timedelta(days=settings.SEEN_LOTS_RETENTION_DAYS + 1)
        async with db.session() as session:
            session.add(SeenLot(url="https://getgems.io/nft/0:old", seen_at=old))
        await db.claim_seen_lot("https://getgems.io/nft/0:fresh")

        removed = await db.prune_seen_lots()
        assert removed == 1
        async with db.session() as session:
            urls = {row.url for row in (await session.execute(select(SeenLot))).scalars()}
        assert urls == {"https://getgems.io/nft/0:fresh"}

    async def test_different_mock_lots_of_same_collection_both_pass(self, db, settings):
        """У mock URL коллекции общий; фрагмент модели/цены делает лоты разными."""
        base = getgems_collection_url("Plush Pepe")
        from bot.services.market_api import Lot

        lots = [
            Lot("Plush Pepe", "#1", 10.0, f"{base}#model=1&price=10.00"),
            Lot("Plush Pepe", "#2", 10.0, f"{base}#model=2&price=10.00"),
        ]
        engine = SniperEngine(
            market=RecordingMarket(lots, preset_floors()),
            db=db,
            notifier=Notifier(settings),
            settings=settings,
        )
        assert len(await engine.scan_once()) == 2


class TestSqliteMaintenance:
    async def test_checkpoint_returns_sqlite_counters(self, db):
        result = await db.checkpoint_wal()
        assert result is not None
        assert set(result) == {"busy", "log", "checkpointed"}

    async def test_invalid_checkpoint_mode_rejected(self, db):
        with pytest.raises(ValueError):
            await db.checkpoint_wal("DROP TABLE users")

    async def test_maintenance_run_once_prunes_and_checkpoints(self, db, settings):
        old = datetime.utcnow() - timedelta(days=settings.SEEN_LOTS_RETENTION_DAYS + 1)
        async with db.session() as session:
            session.add(SeenLot(url="https://getgems.io/nft/0:ancient", seen_at=old))

        service = MaintenanceService(db, settings)
        checkpoint, pruned = await service.run_once()
        assert checkpoint is not None
        assert pruned == 1

    async def test_maintenance_starts_and_stops_cleanly(self, db, settings):
        service = MaintenanceService(db, settings, interval=0.01)
        await service.start()
        await asyncio.sleep(0.05)
        await service.stop()
        assert service._task is None


class TestLogging:
    def test_json_formatter_is_structured(self):
        record = logging.LogRecord(
            name="gifttracker.test",
            level=logging.WARNING,
            pathname=__file__,
            lineno=10,
            msg="lot %s",
            args=("x",),
            exc_info=None,
        )
        payload = json.loads(JsonFormatter().format(record))
        assert payload["level"] == "WARNING"
        assert payload["logger"] == "gifttracker.test"
        assert payload["msg"] == "lot x"
        assert payload["ts"].endswith("Z")

    def test_json_formatter_includes_traceback(self):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            record = logging.LogRecord(
                name="gifttracker.test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=11,
                msg="failed",
                args=(),
                exc_info=sys.exc_info(),
            )
        payload = json.loads(JsonFormatter().format(record))
        assert "RuntimeError: boom" in payload["exc"]

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("debug", logging.DEBUG), ("WARNING", logging.WARNING), ("", logging.INFO),
         ("nonsense", logging.INFO), (None, logging.INFO), (logging.ERROR, logging.ERROR)],
    )
    def test_resolve_level(self, raw, expected):
        assert resolve_level(raw) == expected

    def test_configure_logging_emits_json(self):
        settings = Settings(_env_file=None, LOG_LEVEL="DEBUG", LOG_FORMAT="json")
        stream = io.StringIO()
        try:
            configure_logging(settings, stream=stream)
            logging.getLogger("gifttracker.test").debug("hello %s", "json")
            for handler in logging.getLogger().handlers:
                handler.flush()
        finally:
            configure_logging(Settings(_env_file=None, LOG_LEVEL="INFO", LOG_FORMAT="text"))

        payload = json.loads(stream.getvalue().strip().splitlines()[-1])
        assert payload["msg"] == "hello json"
        assert payload["level"] == "DEBUG"


class TestHeartbeat:
    def test_write_read_alive(self, settings):
        write_heartbeat(settings, extra={"scanner_last_scan": "2026-10-27T00:00:00Z"})
        data = read_heartbeat(settings)
        assert data is not None and data["status"] == "ok"
        status = heartbeat_status(settings)
        assert status["alive"] is True
        assert status["age_sec"] is not None and status["age_sec"] < 5
        assert status["scanner_last_scan"] == "2026-10-27T00:00:00Z"

    def test_stale_heartbeat_is_not_alive(self, settings):
        write_heartbeat(settings)
        future = datetime.now(timezone.utc) + timedelta(
            seconds=settings.BOT_HEARTBEAT_STALE_SEC + 1
        )
        assert heartbeat_status(settings, now=future)["alive"] is False

    def test_stopped_heartbeat_is_not_alive(self, settings):
        write_heartbeat(settings, status="stopped")
        assert heartbeat_status(settings)["alive"] is False

    def test_missing_heartbeat(self, settings):
        status = heartbeat_status(settings)
        assert status["alive"] is False
        assert status["status"] == "missing"

    async def test_heartbeat_task_writes_and_stops(self, settings):
        heartbeat = Heartbeat(settings, interval=0.01)
        await heartbeat.start()
        await asyncio.sleep(0.05)
        await heartbeat.stop()
        data = read_heartbeat(settings)
        assert data is not None
        assert data["status"] == "stopped"
        # start_local.ps1 сравнивает это поле с .env, чтобы заметить
        # бота, запущенного со старым URL туннеля.
        assert data["mini_app_url"] == settings.MINI_APP_URL

    def test_healthcheck_cli_exit_codes(self, settings):
        assert healthcheck_main(settings) == 1
        write_heartbeat(settings)
        assert healthcheck_main(settings) == 0

    def test_api_health_reports_bot(self, tmp_path):
        settings = make_settings(tmp_path)
        write_heartbeat(settings)
        app = create_app(settings)
        with TestClient(app) as client:
            body = client.get("/api/health").json()
        assert body["status"] == "ok"
        assert body["bot"]["alive"] is True


class TestMockLotIdentity:
    async def test_mock_urls_are_unique_per_listing(self, settings):
        """Фрагмент #model=...&price=... отличает лоты одной коллекции."""
        market = MarketAPI(settings)
        checked = 0
        for _ in range(30):
            for lot in await market.get_listings():
                base = getgems_collection_url(lot.gift_name)
                assert lot.marketplace_url.startswith(base + "#model=")
                assert f"&price={lot.price:.2f}" in lot.marketplace_url
                checked += 1
        assert checked > 0
        assert all(preset["fragment_slug"] for preset in GIFT_PRESETS.values())


class TestBackup:
    def make_source_db(self, path):
        connection = sqlite3.connect(path)
        with connection:
            connection.execute("CREATE TABLE t (x INTEGER)")
            connection.execute("INSERT INTO t VALUES (1)")
        connection.close()

    def test_backup_copies_data_and_rotates(self, tmp_path):
        source = tmp_path / "source.db"
        self.make_source_db(source)
        out_dir = tmp_path / "backups"

        for _ in range(3):
            backup_database(source, out_dir, keep=2)

        backups = sorted(out_dir.glob("gift_tracker-*.db"))
        assert len(backups) == 2
        copied = sqlite3.connect(backups[-1])
        try:
            assert copied.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
        finally:
            copied.close()

    def test_missing_database_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            backup_database(tmp_path / "nope.db", tmp_path / "backups")

    def test_prune_keeps_newest(self, tmp_path):
        out_dir = tmp_path / "backups"
        out_dir.mkdir()
        files = []
        for index in range(4):
            path = out_dir / f"gift_tracker-2026010{index}-000000.db"
            path.write_bytes(b"x")
            files.append(path)
        removed = prune_backups(out_dir, keep=1)
        assert len(removed) == 3
        assert len(list(out_dir.glob("gift_tracker-*.db"))) == 1


async def test_prune_snipe_signals_keeps_fresh(db, settings):
    """Лента не растет бесконечно: сделки старше retention удаляются, свежие живут."""
    from datetime import datetime, timedelta

    from sqlalchemy import func, select

    from bot.models import SnipeSignal

    old_time = datetime.utcnow() - timedelta(hours=settings.SNIPE_SIGNALS_RETENTION_HOURS + 2)
    rows = []
    for i in range(3):
        rows.append(
            SnipeSignal(
                gift_name="Ion Gem",
                lot_number=str(100 + i),
                floor_price=72.0,
                deal_price=60.0,
                discount_pct=16.0,
                net_profit_ton=1.5,
                marketplace_url=f"https://getgems.io/nft/old-{i}",
                created_at=old_time,
            )
        )
    rows.append(
        SnipeSignal(
            gift_name="Ion Gem",
            lot_number="200",
            floor_price=72.0,
            deal_price=55.0,
            discount_pct=23.0,
            net_profit_ton=3.0,
            marketplace_url="https://getgems.io/nft/fresh-1",
        )
    )
    async with db.session() as session:
        session.add_all(rows)

    deleted = await db.prune_snipe_signals(settings.SNIPE_SIGNALS_RETENTION_HOURS)
    assert deleted == 3

    async with db.session() as session:
        left = (
            await session.execute(select(func.count(SnipeSignal.id)))
        ).scalar_one()
        assert left == 1
        survivors = list((await session.execute(select(SnipeSignal))).scalars())
        assert survivors[0].marketplace_url == "https://getgems.io/nft/fresh-1"

    # Повторный проход ничего не удаляет (идемпотентно).
    assert await db.prune_snipe_signals(settings.SNIPE_SIGNALS_RETENTION_HOURS) == 0
