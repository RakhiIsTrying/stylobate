# backend/tests/test_anthropic_client.py
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_call_with_cache_sends_cache_control_blocks() -> None:
    from app.core.anthropic_client import call_with_cache

    fake_resp = MagicMock()
    fake_resp.content = [MagicMock(type="text", text="hi")]
    fake_resp.stop_reason = "end_turn"
    fake_resp.usage = MagicMock(
        input_tokens=10, output_tokens=2,
        cache_creation_input_tokens=8, cache_read_input_tokens=0,
    )

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=fake_resp)

    result = await call_with_cache(
        client=fake_client,
        model="claude-haiku-4-5",
        system_blocks=[
            {"text": "You are a helper."},
            {"text": "Tool defs go here."},
        ],
        tools=[],
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=100,
    )

    assert result.text == "hi"
    call_args = fake_client.messages.create.call_args.kwargs
    assert call_args["model"] == "claude-haiku-4-5"
    assert call_args["max_tokens"] == 100
    sys_blocks = call_args["system"]
    assert len(sys_blocks) == 2
    # Every cached system block must carry cache_control: ephemeral
    for block in sys_blocks:
        assert block["type"] == "text"
        assert block["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_call_with_cache_returns_usage() -> None:
    from app.core.anthropic_client import call_with_cache

    fake_resp = MagicMock()
    fake_resp.content = [MagicMock(type="text", text="ok")]
    fake_resp.stop_reason = "end_turn"
    fake_resp.usage = MagicMock(
        input_tokens=100, output_tokens=10,
        cache_creation_input_tokens=80, cache_read_input_tokens=20,
    )

    fake_client = MagicMock()
    fake_client.messages.create = AsyncMock(return_value=fake_resp)

    result = await call_with_cache(
        client=fake_client,
        model="claude-sonnet-4-6",
        system_blocks=[{"text": "sys"}],
        tools=[],
        messages=[{"role": "user", "content": "go"}],
        max_tokens=10,
    )
    assert result.usage.input_tokens == 100
    assert result.usage.cache_read_input_tokens == 20
