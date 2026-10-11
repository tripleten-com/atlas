"""The ingestion layer: signatures, idempotent storage, the watermark, and deferral."""
import time
import uuid
from datetime import datetime, timezone

import pytest

from app.db import connection, fetch_one
from app.ingest.meridian import sign, verify_signature
from app.ingest.processor import Deferred, apply_event, summarize
from app.ingest.store import store_events


def test_signature_round_trip():
    body = b'{"events": []}'
    header = sign("s3cret", body)
    assert verify_signature("s3cret", body, header)
    assert not verify_signature("other", body, header)
    assert not verify_signature("s3cret", body + b" ", header)


def test_old_signatures_are_refused():
    body = b"{}"
    header = sign("s3cret", body, timestamp=int(time.time()) - 3600)
    assert not verify_signature("s3cret", body, header)


def _event(seq: int, epoch: str, type_: str = "vital_signs.recorded", mrn: str = "ZZZ-1") -> dict:
    return {
        "id": f"evt_test_{uuid.uuid4().hex}", "sequence": seq, "epoch": epoch, "type": type_,
        "occurred_at": datetime.now(timezone.utc).isoformat(), "patient": {"mrn": mrn},
        "encounter": {"visit_id": "VTEST1"},
        "data": {"recorded_at": datetime.now(timezone.utc).isoformat(),
                 "measurements": [{"code": "heart_rate", "value": 80, "unit": "bpm"}]},
    }


@pytest.fixture
def scratch_connection():
    """A real feed connection row, inside a transaction that is always rolled back."""
    with connection() as conn:
        tx = conn.transaction()
        tx.__enter__()
        row = fetch_one(conn, "SELECT * FROM integration_connections ORDER BY id LIMIT 1")
        epoch = "test-" + uuid.uuid4().hex[:6]
        conn.execute("UPDATE integration_connections SET stream_epoch = %s, high_watermark = 0 WHERE id = %s",
                     (epoch, row["id"]))
        row = {**row, "stream_epoch": epoch, "high_watermark": 0}
        try:
            yield conn, row
        finally:
            tx.__exit__(RuntimeError, RuntimeError("rollback"), None)


def test_watermark_stops_at_a_gap_and_duplicates_are_ignored(scratch_connection):
    conn, row = scratch_connection
    first = [_event(1, row["stream_epoch"]), _event(2, row["stream_epoch"])]
    result = store_events(conn, row, first, via="webhook")
    assert result == {"received": 2, "stored": 2, "duplicates": 0, "gap": False, "high_watermark": 2}

    row = {**row, "high_watermark": 2}
    result = store_events(conn, row, [first[1]], via="webhook")  # the same event again
    assert result["stored"] == 0 and result["duplicates"] == 1

    result = store_events(conn, row, [_event(4, row["stream_epoch"])], via="webhook")  # 3 is missing
    assert result["gap"] is True and result["high_watermark"] == 2
    flagged = fetch_one(conn, "SELECT needs_resync FROM integration_connections WHERE id = %s", (row["id"],))
    assert flagged["needs_resync"] is True


def test_event_for_unknown_patient_is_deferred(scratch_connection):
    conn, row = scratch_connection
    with pytest.raises(Deferred):
        apply_event(conn, row, _event(1, row["stream_epoch"], mrn="NOBODY-0"), ingest_id=0)


def test_unknown_event_types_are_skipped(scratch_connection):
    conn, row = scratch_connection
    assert apply_event(conn, row, {**_event(1, row["stream_epoch"]), "type": "imaging.completed"}, ingest_id=0) is False


def test_summaries_are_readable():
    ev = _event(1, "e")
    assert summarize(ev).startswith("Vitals: HR 80")
