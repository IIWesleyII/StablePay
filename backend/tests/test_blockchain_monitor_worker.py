import asyncio

import pytest

from config import settings
from services.blockchain_monitor import BlockchainMonitorResult
from workers import blockchain_monitor


class FakeSession:
    def __init__(self) -> None:
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exception_type, exception, traceback):
        return None

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeBlockchainClient:
    def __init__(self) -> None:
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exception_type, exception, traceback):
        self.closed = True


@pytest.mark.asyncio
async def test_worker_runs_cycle_and_closes_client(monkeypatch):
    stop_event = asyncio.Event()
    fake_client = FakeBlockchainClient()
    fake_session = FakeSession()
    cycle_count = 0

    async def fake_monitor_once(session, blockchain_client):
        nonlocal cycle_count
        assert session is fake_session
        assert blockchain_client is fake_client
        cycle_count += 1
        stop_event.set()
        return BlockchainMonitorResult(
            latest_block=100,
            confirmed_tip=98,
            scanned_from=90,
            scanned_to=98,
            transfers_seen=0,
            payments_matched=0,
            payments_confirmed=0,
            ambiguous_transfers=0,
        )

    monkeypatch.setattr(settings, "blockchain_monitor_enabled", True)
    monkeypatch.setattr(
        blockchain_monitor.BaseSepoliaClient,
        "from_settings",
        lambda: fake_client,
    )
    monkeypatch.setattr(blockchain_monitor, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        blockchain_monitor,
        "monitor_blockchain_once",
        fake_monitor_once,
    )

    await blockchain_monitor.run_blockchain_monitor_worker(stop_event)

    assert cycle_count == 1
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_worker_can_be_disabled_without_opening_rpc(monkeypatch):
    monkeypatch.setattr(settings, "blockchain_monitor_enabled", False)
    monkeypatch.setattr(
        blockchain_monitor.BaseSepoliaClient,
        "from_settings",
        lambda: pytest.fail("RPC client should not be created"),
    )

    await blockchain_monitor.run_blockchain_monitor_worker(asyncio.Event())


@pytest.mark.asyncio
async def test_worker_catches_up_without_waiting_between_batches(monkeypatch):
    stop_event = asyncio.Event()
    fake_client = FakeBlockchainClient()
    fake_session = FakeSession()
    cycle_count = 0

    async def fake_monitor_once(session, blockchain_client):
        nonlocal cycle_count
        cycle_count += 1
        if cycle_count == 1:
            return BlockchainMonitorResult(
                latest_block=110,
                confirmed_tip=108,
                scanned_from=90,
                scanned_to=99,
                transfers_seen=0,
                payments_matched=0,
                payments_confirmed=0,
                ambiguous_transfers=0,
            )
        stop_event.set()
        return BlockchainMonitorResult(
            latest_block=110,
            confirmed_tip=108,
            scanned_from=100,
            scanned_to=108,
            transfers_seen=0,
            payments_matched=0,
            payments_confirmed=0,
            ambiguous_transfers=0,
        )

    monkeypatch.setattr(settings, "blockchain_monitor_enabled", True)
    monkeypatch.setattr(
        blockchain_monitor.BaseSepoliaClient,
        "from_settings",
        lambda: fake_client,
    )
    monkeypatch.setattr(blockchain_monitor, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(
        blockchain_monitor,
        "monitor_blockchain_once",
        fake_monitor_once,
    )

    await asyncio.wait_for(
        blockchain_monitor.run_blockchain_monitor_worker(stop_event),
        timeout=1,
    )

    assert cycle_count == 2
