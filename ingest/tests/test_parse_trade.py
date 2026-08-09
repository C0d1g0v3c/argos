from datetime import timezone
from decimal import Decimal

from argos_ingest.platforms import BybitCopyTrading

# Muestra real capturada de GROWTRADE el 2026-08-09 (endpoint leader-created-record)
SAMPLE = {
    "orderId": "abc123",
    "isOpenOrder": False,
    "symbol": "BTCUSDT",
    "side": "Buy",
    "leverageE2": "10000",
    "sizeX": "400000",
    "entryPriceE8": "6435200000000",
    "closedPnlE8": "-28354859",
    "yieldRateE4": "-992",
    "createdAtE9": "1786068510807607722",
}


def test_parse_maps_price_and_leverage():
    t = BybitCopyTrading.parse_trade_record(SAMPLE, leader_id=7)
    assert t["symbol"] == "BTCUSDT"
    assert t["side"] == "long"
    assert t["leader_id"] == 7
    assert t["trade_uid"] == "abc123"
    assert t["entry_px"] == Decimal("64352")
    assert t["leverage"] == Decimal("100")


def test_parse_timestamp_from_nanos():
    t = BybitCopyTrading.parse_trade_record(SAMPLE, leader_id=1)
    assert t["ts"].tzinfo == timezone.utc
    assert t["ts"].year == 2026 and t["ts"].month == 8 and t["ts"].day == 7


def test_side_short_for_sell():
    t = BybitCopyTrading.parse_trade_record({**SAMPLE, "side": "Sell"}, leader_id=1)
    assert t["side"] == "short"
