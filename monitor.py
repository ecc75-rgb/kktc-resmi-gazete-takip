#!/usr/bin/env python3
"""KKTC Devlet Basımevi'ni izler ve yeni Resmî Gazete için Telegram bildirimi gönderir."""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any

SITE_URL = "https://basimevi.gov.ct.tr/"
STATE_PATH = Path(__file__).with_name("state.json")
USER_AGENT = "Mozilla/5.0 (compatible; KKTC-Resmi-Gazete-Monitor/1.0)"
PDF_LINK_RE = re.compile(
    r"""<a\b[^>]*href=["'](?P<href>[^"']*\.pdf(?:\?[^"']*)?)["'][^>]*>
        \s*(?:<[^>]+>\s*)*(?P<number>\d{1,4})\s*(?:</[^>]+>\s*)*
        </a>(?P<tail>.{0,1500}?)
        (?P<date>\d{1,2}\.\d{1,2}\.\d{4})""",
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def request(url: str, data: bytes | None = None, timeout: int = 40) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"İstek başarısız: {url} ({last_error})")


def clean_text(raw: str) -> str:
    raw = re.sub(r"<(?:script|style)\b.*?</(?:script|style)>", " ", raw, flags=re.I | re.S)
    return SPACE_RE.sub(" ", html.unescape(TAG_RE.sub(" ", raw))).strip()


def extract_summary(page: str, match: re.Match[str], number: str, issue_date: str) -> str:
    row_start = page.rfind("<tr", 0, match.start())
    row_end = page.find("</tr>", match.end())
    if row_start == -1 or row_end == -1 or row_end - row_start > 30000:
        return ""
    text = clean_text(page[row_start:row_end])
    text = re.sub(rf"^\s*{re.escape(number)}\s*", "", text)
    text = text.replace(issue_date, "", 1).strip(" |:-")
    return text[:850].rstrip()


def fetch_issues() -> list[dict[str, str]]:
    page = request(SITE_URL).decode("utf-8", errors="replace")
    issues: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for match in PDF_LINK_RE.finditer(page):
        number = match.group("number")
        issue_date = match.group("date")
        key = (number, issue_date)
        if key in seen:
            continue
        seen.add(key)
        issues.append(
            {
                "number": number,
                "date": issue_date,
                "url": urllib.parse.urljoin(SITE_URL, html.unescape(match.group("href"))),
                "summary": extract_summary(page, match, number, issue_date),
            }
        )

    if not issues:
        raise RuntimeError("Sayfadaki Resmî Gazete kayıtları bulunamadı; site yapısı değişmiş olabilir.")
    return issues


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        raise RuntimeError("state.json bulunamadı.")
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def telegram_send(message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID GitHub Secret olarak ayarlanmamış.")

    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": "true",
        }
    ).encode()
    response = json.loads(request(endpoint, data=payload).decode("utf-8"))
    if not response.get("ok"):
        raise RuntimeError(f"Telegram bildirimi başarısız: {response}")


def issue_message(issue: dict[str, str], test: bool = False) -> str:
    heading = "✅ Test başarılı" if test else "📰 Yeni KKTC Resmî Gazete yayımlandı"
    lines = [
        heading,
        "",
        f"Sayı: {issue['number']}",
        f"Tarih: {issue['date']}",
    ]
    if issue.get("summary"):
        lines.extend(["", f"İçerik: {issue['summary']}"])
    lines.extend(["", f"PDF: {issue['url']}", f"Ana sayfa: {SITE_URL}"])
    return "\n".join(lines)[:4000]


def main() -> int:
    issues = fetch_issues()
    latest = issues[0]
    state = load_state()

    if os.environ.get("SEND_TEST", "").lower() == "true":
        telegram_send(issue_message(latest, test=True))
        print(f"Test bildirimi gönderildi: {latest['number']} — {latest['date']}")
        return 0

    old_key = (str(state.get("last_number", "")), str(state.get("last_date", "")))
    latest_key = (latest["number"], latest["date"])

    if latest_key != old_key:
        old_index = next(
            (
                index
                for index, issue in enumerate(issues)
                if (issue["number"], issue["date"]) == old_key
            ),
            None,
        )
        new_issues = issues[:old_index] if old_index is not None else [latest]
        for issue in reversed(new_issues):
            telegram_send(issue_message(issue))
            print(f"Bildirim gönderildi: {issue['number']} — {issue['date']}")

        state.update(
            {
                "last_number": latest["number"],
                "last_date": latest["date"],
                "last_url": latest["url"],
                "updated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            }
        )
    else:
        print(f"Yeni sayı yok. Son sayı: {latest['number']} — {latest['date']}")

    heartbeat = date.fromisoformat(state.get("heartbeat_at", "1970-01-01"))
    if (date.today() - heartbeat).days >= 45:
        state["heartbeat_at"] = date.today().isoformat()
        print("45 günlük etkinlik kaydı yenilendi.")

    save_state(state)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        raise SystemExit(1)
