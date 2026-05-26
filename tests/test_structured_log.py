import json
import logging

from deribit_engine.structured_log import LiveContextFilter, LiveJsonFormatter, configure_live_structured_logging


def test_live_json_formatter_includes_scope_fields():
    configure_live_structured_logging(
        {"investor_id": "alice", "slug": "naked", "strategy": "naked_short", "deribit_env": "testnet"},
        verbose=False,
    )
    logger = logging.getLogger("test.structured")
    record = logger.makeRecord(
        name="test.structured",
        level=logging.INFO,
        fn=__file__,
        lno=1,
        msg="cycle complete",
        args=(),
        exc_info=None,
    )
    record.cycle = 2
    record.regime = "normal"
    LiveContextFilter().filter(record)
    formatter = LiveJsonFormatter()
    payload = json.loads(formatter.format(record))
    assert payload["investor_id"] == "alice"
    assert payload["slug"] == "naked"
    assert payload["cycle"] == 2
    assert payload["regime"] == "normal"
    assert payload["message"] == "cycle complete"
