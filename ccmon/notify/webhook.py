"""Generic webhook notifier -- one config shape, every provider.

The user configures a list of {url, method, headers, body_template}. Placeholders
like {title}, {message}, {project}, {status}, {session_id}, {pid}, {cwd},
{waiting_for}, {timestamp} are substituted before sending. {env.VAR} is
expanded against os.environ at send time so secrets stay out of the config
file (e.g. {env.BARK_KEY}).

Bark, Server酱, Telegram, Discord, 企业微信机器人 all reduce to this -- no
provider-specific code in ccmon.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Literal

import httpx

from ..models import Session

log = logging.getLogger(__name__)

_PLACEHOLDER = re.compile(r"\{([a-z_]+(?:\.[a-z_]+)?|env\.[A-Z0-9_]+)\}")
_MAX_BODY = 1800  # under Discord's 2000; safe everywhere


@dataclass
class WebhookConfig:
    name: str
    url: str
    method: Literal["GET", "POST", "PUT"] = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    body_template: str | None = None
    content_type: str | None = None  # defaults to form for GET, JSON for POST/PUT
    timeout_s: float = 5.0
    retry_max_attempts: int = 3
    retry_base_s: float = 1.0
    trigger_on: list[str] = field(default_factory=lambda: ["NEEDS_APPROVAL"])

    def should_fire(self, state_name: str) -> bool:
        return state_name in self.trigger_on


def _truncate(text: str, limit: int = _MAX_BODY) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _substitute(template: str, values: dict[str, str]) -> str:
    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key.startswith("env."):
            return os.environ.get(key[4:], "")
        return str(values.get(key, match.group(0)))
    return _PLACEHOLDER.sub(replace, template)


def render(
    cfg: WebhookConfig,
    session: Session,
    *,
    title: str,
    message: str,
    icon_url: str = "",
) -> tuple[str, dict[str, str], str | None, str | None]:
    """Produce (url, headers, content_type, body) for one outbound request."""
    values = {
        "title": _truncate(title, 120),
        "message": _truncate(message),
        "status": session.state.label,
        "project": session.project,
        "cwd": session.cwd,
        "session_id": session.session_id or "",
        "pid": str(session.pid),
        "waiting_for": session.waiting_for or "",
        "tool": session.activity or "",
        "timestamp": str(int(time.time())),
        "icon_url": icon_url,
    }
    url = _substitute(cfg.url, values)
    body = _substitute(cfg.body_template, values) if cfg.body_template else None

    headers = {k: _substitute(v, values) for k, v in cfg.headers.items()}
    if cfg.method != "GET" and body is not None:
        headers.setdefault(
            "Content-Type", cfg.content_type or "application/json"
        )
    return url, headers, headers.get("Content-Type"), body


async def send(
    cfg: WebhookConfig,
    session: Session,
    *,
    title: str,
    message: str,
    icon_url: str = "",
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Fire one webhook with exponential backoff. Returns True on any 2xx."""
    url, headers, _ctype, body = render(cfg, session, title=title, message=message, icon_url=icon_url)
    own = client is None
    client = client or httpx.AsyncClient(timeout=cfg.timeout_s)
    try:
        for attempt in range(cfg.retry_max_attempts):
            try:
                if cfg.method == "GET":
                    resp = await client.get(url, headers=headers)
                elif cfg.method == "PUT":
                    resp = await client.put(url, headers=headers, content=body)
                else:
                    resp = await client.post(url, headers=headers, content=body)
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                log.warning("webhook %s attempt %d: %s", cfg.name, attempt + 1, exc)
                if attempt + 1 >= cfg.retry_max_attempts:
                    return False
                time.sleep(cfg.retry_base_s * (2**attempt))
                continue
            if 200 <= resp.status_code < 300:
                return True
            if resp.status_code in (400, 401, 403, 404):
                # Bad config, not a transient failure -- do not retry.
                log.error("webhook %s permanent %d: %s", cfg.name, resp.status_code, resp.text[:200])
                return False
            if attempt + 1 >= cfg.retry_max_attempts:
                log.warning("webhook %s exhausted at %d: %s", cfg.name, resp.status_code, resp.text[:200])
                return False
            time.sleep(cfg.retry_base_s * (2**attempt))
        return False
    finally:
        if own:
            await client.aclose()
