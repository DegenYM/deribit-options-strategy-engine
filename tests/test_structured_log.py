import json
import logging

from deribit_engine.structured_log import (
    REDACTED,
    LiveContextFilter,
    LiveJsonFormatter,
    configure_live_structured_logging,
    scrub_secrets,
)


def test_live_json_formatter_includes_scope_fields():
    configure_live_structured_logging(
        {"investor_id": "alice", "slug": "naked", "strategy": "naked_short", "deribit_env": "mainnet"},
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


def test_live_json_formatter_redacts_secret_extra_fields():
    formatter = LiveJsonFormatter()
    logger = logging.getLogger("test.structured.redact")
    record = logger.makeRecord(
        name="test.structured.redact",
        level=logging.INFO,
        fn=__file__,
        lno=1,
        msg="auth refreshed",
        args=(),
        exc_info=None,
    )
    record.client_secret = "super-secret-value"
    record.access_token = "abc123"
    record.TELEGRAM_BOT_TOKEN = "8000:zzz"
    record.cycle = 7
    payload = json.loads(formatter.format(record))
    assert payload["client_secret"] == REDACTED
    assert payload["access_token"] == REDACTED
    assert payload["TELEGRAM_BOT_TOKEN"] == REDACTED
    assert payload["cycle"] == 7


def test_live_json_formatter_scrubs_token_in_message_and_exc():
    formatter = LiveJsonFormatter()
    logger = logging.getLogger("test.structured.scrub")
    try:
        raise RuntimeError("HTTP error for url https://api.telegram.org/bot12345:AAEsecretTOKEN/sendMessage")
    except RuntimeError:
        import sys

        record = logger.makeRecord(
            name="test.structured.scrub",
            level=logging.ERROR,
            fn=__file__,
            lno=1,
            msg="telegram send error: %s",
            args=("post to /bot12345:AAEsecretTOKEN/sendMessage failed",),
            exc_info=sys.exc_info(),
        )
    payload = json.loads(formatter.format(record))
    assert "AAEsecretTOKEN" not in payload["message"]
    assert "AAEsecretTOKEN" not in payload["exc_info"]
    assert REDACTED in payload["message"]


def test_scrub_secrets_masks_query_params():
    masked = scrub_secrets("GET /api?client_secret=abcd1234&foo=bar access_token=zzz")
    assert "abcd1234" not in masked
    assert "zzz" not in masked
    assert "foo=bar" in masked
