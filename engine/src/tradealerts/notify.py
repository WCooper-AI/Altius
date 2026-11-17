"""Where alerts go: ntfy, Discord, Telegram, Slack, a generic webhook, email, or the console."""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

NTFY_PRIORITIES = {"min": 1, "low": 2, "default": 3, "high": 4, "max": 5, "urgent": 5}
MAX_DELIVERY_ATTEMPTS = 5                          # gives up after this many tries in total
RETRY_BACKOFF_SECONDS = (30, 120, 600, 1800)       # before retries 2, 3, 4 and 5


@dataclass
class Message:
    title: str
    body: str
    priority: str = "default"
    tags: tuple[str, ...] = ()
    html: str | None = None  # optional rich version (email)


class Notifier:
    name = "base"

    def send(self, msg: Message) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    name = "console"

    def send(self, msg: Message) -> None:
        print(f"\n[{msg.priority.upper()}] {msg.title}\n{msg.body}\n", flush=True)


# How alerts reach the network. The hosted edition replaces these with versions that refuse
# to connect to private addresses, so a customer can't point a webhook at the server's own network.
_policy: dict = {"urlopen": None, "smtp": None}


def set_network_policy(urlopen=None, smtp=None) -> None:
    """``urlopen(request, timeout)`` for webhooks; ``smtp(host, port, use_ssl, timeout, context)`` for email."""
    _policy.update(urlopen=urlopen, smtp=smtp)


def _post(url: str, data: bytes, headers: dict, timeout: float = 15) -> None:
    if not url.lower().startswith(("https://", "http://")):
        raise ValueError(f"{url!r} isn't a web address")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "TradingAlerts", **headers}, method="POST")
    opener = _policy["urlopen"] or urllib.request.urlopen
    with opener(req, timeout=timeout) as resp:  # noqa: S310 - user-configured URL
        if resp.status >= 300:
            raise RuntimeError(f"{url} returned HTTP {resp.status}")


class NtfyNotifier(Notifier):
    """Push to an ntfy topic (ntfy.sh or your own server)."""

    name = "ntfy"

    def __init__(self, topic: str, server: str = "https://ntfy.sh", token: str | None = None,
                 username: str | None = None, password: str | None = None):
        if not topic:
            raise ValueError("ntfy notifier needs a topic")
        self.server, self.topic = server.rstrip("/"), topic
        self.token, self.username, self.password = token or None, username, password

    def payload(self, msg: Message) -> dict:
        # JSON publishing keeps UTF-8 (£, →) intact in titles, unlike HTTP headers.
        prio = NTFY_PRIORITIES.get(msg.priority, 3)
        data = {"topic": self.topic, "title": msg.title, "message": msg.body, "priority": prio}
        if msg.tags:
            data["tags"] = list(msg.tags)
        return data

    def headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        elif self.username and self.password:
            import base64
            cred = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            h["Authorization"] = f"Basic {cred}"
        return h

    def send(self, msg: Message) -> None:
        _post(self.server, json.dumps(self.payload(msg)).encode("utf-8"), self.headers())


class DiscordNotifier(Notifier):
    name = "discord"

    def __init__(self, webhook_url: str):
        if not webhook_url:
            raise ValueError("discord notifier needs webhook_url")
        self.url = webhook_url

    def send(self, msg: Message) -> None:
        payload = {"content": f"**{msg.title}**\n{msg.body}"[:1990]}
        _post(self.url, json.dumps(payload).encode(), {"Content-Type": "application/json"})


class TelegramNotifier(Notifier):
    """Message from your own Telegram bot (make one with @BotFather) to a chat ID."""

    name = "telegram"
    api = "https://api.telegram.org"

    def __init__(self, token: str, chat_id: str | int):
        if not token or not chat_id:
            raise ValueError("telegram needs a bot token and a chat ID")
        self.token, self.chat_id = token, str(chat_id)

    def payload(self, msg: Message) -> dict:
        return {"chat_id": self.chat_id, "text": f"{msg.title}\n{msg.body}"[:4000],
                "disable_web_page_preview": True, "disable_notification": msg.priority in ("min", "low")}

    def send(self, msg: Message) -> None:
        _post(f"{self.api}/bot{self.token}/sendMessage", json.dumps(self.payload(msg)).encode(),
              {"Content-Type": "application/json"})


class SlackNotifier(Notifier):
    """Slack incoming webhook (also works with Mattermost and other Slack-compatible chats)."""

    name = "slack"

    def __init__(self, webhook_url: str):
        if not webhook_url:
            raise ValueError("slack notifier needs webhook_url")
        self.url = webhook_url

    @staticmethod
    def payload(msg: Message) -> dict:
        text = f"*{msg.title}*\n{msg.body}"[:3900]
        return {"text": text, "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]}

    def send(self, msg: Message) -> None:
        _post(self.url, json.dumps(self.payload(msg)).encode(), {"Content-Type": "application/json"})


class WebhookNotifier(Notifier):
    """POSTs JSON {title, body, priority, tags} to any URL."""

    name = "webhook"

    def __init__(self, url: str, headers: dict | None = None):
        if not url:
            raise ValueError("webhook notifier needs url")
        self.url, self.extra = url, headers or {}

    def send(self, msg: Message) -> None:
        payload = {"title": msg.title, "body": msg.body, "priority": msg.priority, "tags": list(msg.tags)}
        _post(self.url, json.dumps(payload).encode(), {"Content-Type": "application/json", **self.extra})


class EmailNotifier(Notifier):
    """Send by email through your own SMTP server (e.g. Proton Bridge, Gmail app password, Fastmail)."""

    name = "email"

    def __init__(self, host: str, to: str, port: int | str = 587, username: str | None = None,
                 password: str | None = None, sender: str | None = None, security: str = "starttls"):
        if not host or not to:
            raise ValueError("email needs an SMTP server and a 'to' address")
        self.host, self.port, self.to = host, int(port or 587), to
        self.username, self.password = username or None, password or None
        self.sender = sender or username or to
        self.security = security if security in ("starttls", "ssl", "none") else "starttls"

    def send(self, msg: Message) -> None:
        import smtplib
        import ssl
        from email.message import EmailMessage

        m = EmailMessage()
        m["Subject"], m["From"], m["To"] = msg.title, self.sender, self.to
        m.set_content(msg.body)
        if msg.html:
            m.add_alternative(msg.html, subtype="html")
        ctx = ssl.create_default_context()
        if _policy["smtp"]:
            server = _policy["smtp"](self.host, self.port, self.security == "ssl", 20, ctx)
        elif self.security == "ssl":
            server = smtplib.SMTP_SSL(self.host, self.port, timeout=20, context=ctx)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=20)
        with server:
            if self.security == "starttls":
                server.starttls(context=ctx)
            if self.username:
                server.login(self.username, self.password or "")
            server.send_message(m)


def build_notifiers(specs: list[dict]) -> list[Notifier]:
    out: list[Notifier] = []
    for spec in specs or [{"type": "console"}]:
        spec = dict(spec)
        kind = str(spec.pop("type", "")).lower()
        if spec.pop("enabled", True) is False:
            continue
        if kind == "console":
            out.append(ConsoleNotifier())
        elif kind == "ntfy":
            out.append(NtfyNotifier(**spec))
        elif kind == "discord":
            out.append(DiscordNotifier(**spec))
        elif kind == "telegram":
            out.append(TelegramNotifier(**spec))
        elif kind == "slack":
            out.append(SlackNotifier(**spec))
        elif kind == "webhook":
            out.append(WebhookNotifier(**spec))
        elif kind == "email":
            out.append(EmailNotifier(**spec))
        else:
            raise ValueError(f"Unknown notifier type {kind!r}. Use ntfy, discord, telegram, slack, webhook, email or console")
    return out


def send_each(notifiers: list[Notifier], msg: Message) -> list[tuple[str, str | None]]:
    """Send to every notifier; (channel name, error) for each one (error is None on success)."""
    out = []
    for n in notifiers:
        try:
            n.send(msg)
            out.append((n.name, None))
        except Exception as exc:  # keep going if one channel is down
            log.warning("Notifier %s failed: %s", n.name, exc)
            out.append((n.name, str(exc)))
    return out


def broadcast(notifiers: list[Notifier], msg: Message) -> list[str]:
    """Send to every notifier; returns a list of error strings (empty on success)."""
    return [f"{name}: {error}" for name, error in send_each(notifiers, msg) if error]


def _parse(ts) -> datetime:
    dt = datetime.fromisoformat(str(ts))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def _retry_delay(attempts: int) -> int:
    """Seconds to wait before trying again, after this many attempts so far."""
    return RETRY_BACKOFF_SECONDS[min(attempts, len(RETRY_BACKOFF_SECONDS)) - 1]


def record_deliveries(store, outcomes: list[tuple[str, str | None]], *, strategy_id: str | None = None,
                      run_id: int | None = None, kind: str = "", title: str = "", body: str = "",
                      priority: str = "default", now=None) -> None:
    """Save what just happened on each channel from :func:`send_each`, so a channel that
    failed can be tried again with a backoff (see :func:`retry_pending`) up to
    ``MAX_DELIVERY_ATTEMPTS`` times, instead of needing attention for one bad moment.

    Called after the triggering trade/event is safely saved, never as part of the same
    database transaction: losing a delivery record to a crash only means one retry isn't
    tracked, where losing the trade record would mean it gets replayed.
    """
    when = _parse(now) if now is not None else datetime.now(timezone.utc)
    for name, error in outcomes:
        if error is None:
            store.add_delivery(strategy_id=strategy_id, run_id=run_id, channel=name, kind=kind,
                               title=title, body=body, priority=priority, status="sent", attempts=1,
                               error=None, next_retry_at=None)
        else:
            next_at = _iso(when + timedelta(seconds=_retry_delay(1)))
            store.add_delivery(strategy_id=strategy_id, run_id=run_id, channel=name, kind=kind,
                               title=title, body=body, priority=priority, status="retrying", attempts=1,
                               error=error, next_retry_at=next_at)


def retry_pending(store, notifiers: list[Notifier], now=None) -> list[dict]:
    """Try again every delivery that's due a retry. Returns the ones that have just run out
    of attempts (now ``failed``), so the caller can raise one ``alert_failed`` problem each."""
    when = _parse(now) if now is not None else datetime.now(timezone.utc)
    by_name = {n.name: n for n in notifiers}
    exhausted = []
    for row in store.due_deliveries(_iso(when)):
        n = by_name.get(row["channel"])
        if n is None:
            continue  # that channel isn't configured any more
        msg = Message(row["title"] or "", row["body"] or "", row["priority"] or "default")
        try:
            n.send(msg)
            store.update_delivery(row["id"], status="sent", attempts=row["attempts"], error=None, next_retry_at=None)
        except Exception as exc:
            attempts = row["attempts"] + 1
            if attempts >= MAX_DELIVERY_ATTEMPTS:
                store.update_delivery(row["id"], status="failed", attempts=attempts, error=str(exc), next_retry_at=None)
                exhausted.append({**row, "attempts": attempts, "error": str(exc)})
            else:
                next_at = _iso(when + timedelta(seconds=_retry_delay(attempts)))
                store.update_delivery(row["id"], status="retrying", attempts=attempts, error=str(exc), next_retry_at=next_at)
    return exhausted
