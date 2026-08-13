"""Webhook rendering and retry tests."""

from __future__ import annotations

import httpx
import pytest
import respx

from ccmon.models import Session, State
from ccmon.notify.webhook import WebhookConfig, render, send


def _session() -> Session:
    return Session(
        pid=28860,
        state=State.NEEDS_APPROVAL,
        cwd="D:\\ComfyUI",
        session_id="979b3ecf-8ea0-49b8-a3bc-01efffb2bc88",
        name="comfyui-e1",
        started_at=1786552008971,
        updated_at=1786594264809,
        status="waiting",
        waiting_for="approve Bash",
        version="2.1.228",
        activity="Bash: pytest -q",
    )


def test_substitutes_every_placeholder():
    cfg = WebhookConfig(
        name="test",
        url="https://example.com/{project}/{status}",
        method="POST",
        body_template='{"chat":"{project}","msg":"{message}","tool":"{tool}","cwd":"{cwd}"}',
    )
    url, headers, _, body = render(cfg, _session(), title="t", message="需要授权")
    assert url == "https://example.com/ComfyUI/等待授权"
    assert "ComfyUI" in body and "需要授权" in body and "Bash: pytest -q" in body
    assert "D:\\\\ComfyUI" in body or "D:\\ComfyUI" in body or "ComfyUI" in body


def test_env_placeholder_expands():
    cfg = WebhookConfig(
        name="bark",
        url="https://api.day.app/{env.BARK_KEY}/{title}",
        method="GET",
    )
    import os
    os.environ["BARK_KEY"] = "testkey"
    url, *_ = render(cfg, _session(), title="ping", message="")
    assert "testkey" in url


def test_missing_env_is_empty_string():
    cfg = WebhookConfig(
        name="bark",
        url="https://example.com/{env.NOT_SET_XYZ}",
        method="GET",
    )
    url, *_ = render(cfg, _session(), title="t", message="m")
    assert url == "https://example.com/"


def test_content_type_defaults_for_post():
    cfg = WebhookConfig(
        name="t",
        url="https://example.com",
        method="POST",
        body_template='{"k":"v"}',
    )
    _, headers, _, _ = render(cfg, _session(), title="t", message="m")
    assert headers.get("Content-Type") == "application/json"


def test_get_request_does_not_force_content_type():
    cfg = WebhookConfig(
        name="bark",
        url="https://example.com",
        method="GET",
    )
    _, headers, _, _ = render(cfg, _session(), title="t", message="m")
    assert "Content-Type" not in headers


@pytest.mark.asyncio
async def test_send_returns_true_on_2xx():
    cfg = WebhookConfig(
        name="ok",
        url="https://example.com/hook",
        method="POST",
        body_template='{"x":1}',
        retry_max_attempts=1,
    )
    with respx.mock(base_url="https://example.com") as respx_mock:
        respx_mock.post("/hook").mock(return_value=httpx.Response(200, json={"ok": True}))
        ok = await send(cfg, _session(), title="t", message="m")
    assert ok is True


@pytest.mark.asyncio
async def test_send_does_not_retry_on_4xx():
    cfg = WebhookConfig(
        name="bad",
        url="https://example.com/hook",
        method="POST",
        body_template='{"x":1}',
        retry_max_attempts=5,
    )
    with respx.mock(base_url="https://example.com") as respx_mock:
        route = respx_mock.post("/hook").mock(return_value=httpx.Response(404))
        ok = await send(cfg, _session(), title="t", message="m")
        assert ok is False
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_send_retries_on_5xx():
    cfg = WebhookConfig(
        name="flaky",
        url="https://example.com/hook",
        method="POST",
        body_template='{"x":1}',
        retry_max_attempts=3,
        retry_base_s=0.01,
    )
    with respx.mock(base_url="https://example.com") as respx_mock:
        route = respx_mock.post("/hook").mock(return_value=httpx.Response(503))
        ok = await send(cfg, _session(), title="t", message="m")
        assert ok is False
        assert route.call_count == 3


def test_should_fire_filters_by_state():
    cfg = WebhookConfig(
        name="only-approval",
        url="https://x",
        trigger_on=["NEEDS_APPROVAL"],
    )
    assert cfg.should_fire("NEEDS_APPROVAL")
    assert not cfg.should_fire("NEEDS_INPUT")


def test_message_truncation():
    long = "x" * 5000
    cfg = WebhookConfig(
        name="t",
        url="https://x",
        body_template='{message}',
    )
    _, _, _, body = render(cfg, _session(), title="t", message=long)
    assert body and len(body) <= 1800
    assert body.endswith("...")
