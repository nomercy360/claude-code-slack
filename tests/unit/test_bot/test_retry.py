"""Tests for slack_api_call retry wrapper."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from slack_sdk.errors import SlackApiError

from src.bot.utils.retry import slack_api_call


def _make_slack_error(status_code: int) -> SlackApiError:
    resp = MagicMock()
    resp.status_code = status_code
    resp.data = {"ok": False, "error": "test"}
    return SlackApiError("test", response=resp)


async def test_success_no_retry():
    fn = AsyncMock(return_value={"ok": True})
    result = await slack_api_call(fn, channel="C123", text="hi")
    assert result == {"ok": True}
    fn.assert_called_once_with(channel="C123", text="hi")


@patch("src.bot.utils.retry.asyncio.sleep", new_callable=AsyncMock)
async def test_retries_on_429(mock_sleep):
    fn = AsyncMock(side_effect=[_make_slack_error(429), {"ok": True}])
    result = await slack_api_call(fn, retries=2)
    assert result == {"ok": True}
    assert fn.call_count == 2
    mock_sleep.assert_called_once()


@patch("src.bot.utils.retry.asyncio.sleep", new_callable=AsyncMock)
async def test_retries_on_500(mock_sleep):
    fn = AsyncMock(side_effect=[_make_slack_error(500), {"ok": True}])
    result = await slack_api_call(fn, retries=2)
    assert result == {"ok": True}


async def test_no_retry_on_400():
    fn = AsyncMock(side_effect=_make_slack_error(400))
    with pytest.raises(SlackApiError):
        await slack_api_call(fn, retries=2)
    assert fn.call_count == 1


@patch("src.bot.utils.retry.asyncio.sleep", new_callable=AsyncMock)
async def test_exhausts_retries(mock_sleep):
    fn = AsyncMock(
        side_effect=[
            _make_slack_error(429),
            _make_slack_error(429),
            _make_slack_error(429),
        ]
    )
    with pytest.raises(SlackApiError):
        await slack_api_call(fn, retries=2)
    assert fn.call_count == 3


@patch("src.bot.utils.retry.asyncio.sleep", new_callable=AsyncMock)
async def test_backoff_timing(mock_sleep):
    fn = AsyncMock(
        side_effect=[_make_slack_error(502), _make_slack_error(502), {"ok": True}]
    )
    await slack_api_call(fn, retries=2)
    # First retry: 0.5s, second retry: 1.0s
    calls = [c.args[0] for c in mock_sleep.call_args_list]
    assert calls[0] == pytest.approx(0.5)
    assert calls[1] == pytest.approx(1.0)
