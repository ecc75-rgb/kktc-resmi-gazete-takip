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


class SiteUnavailable(RuntimeError):
    """İzlenen site geçici olarak cevap vermediğinde kullanılır."""


def request(
    url: str,
    data: bytes | None = None,
    timeout: int = 40,
    attempts: int = 3,
) -> bytes:
    headers = {
        "User-Agent": USER_AGENT,
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(3 * (attempt + 1))
    raise SiteUnavailable(f"İstek başarısız: {url} ({last_error})")


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
    cache_buster = int(time.time())
    page_url = f"{SITE_URL}?_monitor={cache_buster}"
    page = request(page_url, timeout=20, attempts=1).decode("utf-8", errors="replace")
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
    chat_ids: list[str] = []

    for env_name in ("TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_ID_2"):
        chat_id = os.environ.get(env_name, "").strip()
        if chat_id and chat_id not in chat_ids:
            chat_ids.append(chat_id)

    if not token or not chat_ids:
        raise RuntimeError("Telegram bot tokeni veya alıcı Chat ID bilgisi ayarlanmamış.")

    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    for chat_id in chat_ids:
        payload = urllib.parse.urlencode(
            {
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": "true",
            }
        ).encode()
        response = json.loads(request(endpoint, data=payload).decode("utf-8"))
        if not response.get("ok"):
            raise RuntimeError(f"{chat_id} alıcısına Telegram bildirimi başarısız: {response}")


def issue_order(issue: dict[str, str]) -> tuple[int, int]:
    """Yayınları yıl ve sayı ile sıralar; sitenin eski liste göstermesine karşı kullanılır."""
    date_match = re.search(r"(\d{4})$", issue.get("date", ""))
    if not date_match:
        raise RuntimeError(f"Geçersiz Resmî Gazete tarihi: {issue.get('date')}")
    return int(date_match.group(1)), int(issue["number"])


def state_order(state: dict[str, Any]) -> tuple[int, int]:
    date_match = re.search(r"(\d{4})$", str(state.get("last_date", "")))
    if not date_match:
        raise RuntimeError(f"Geçersiz kayıt tarihi: {state.get('last_date')}")
    return int(date_match.group(1)), int(state["last_number"])


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
    state = load_state()

    if os.environ.get("SEND_TEST", "").lower() == "true":
        saved_issue = {
            "number": str(state.get("last_number", "—")),
            "date": str(state.get("last_date", "—")),
            "url": str(state.get("last_url", SITE_URL)),
            "summary": "",
        }
        telegram_send(issue_message(saved_issue, test=True))
        print("Test bildirimi tüm kayıtlı Telegram alıcılarına gönderildi.")
        return 0

    issues: list[dict[str, str]] | None = None
    last_site_error: SiteUnavailable | None = None

    for burst_attempt in range(6):
        try:
            issues = fetch_issues()
            if burst_attempt:
                print(f"Basımevi sitesi {burst_attempt + 1}. hızlı denemede yeniden erişilebilir oldu.")
            break
        except SiteUnavailable as exc:
            last_site_error = exc
            if burst_attempt < 5:
                print(
                    f"UYARI: Site cevap vermedi. 45 saniye sonra hızlı tekrar "
                    f"denenecek ({burst_attempt + 1}/6)."
                )
                time.sleep(45)

    if issues is None:
        print(
            "UYARI: Basımevi sitesi 5 dakikalık hızlı takip boyunca cevap vermedi. "
            f"Sonraki planlı kontrolde yeniden denenecek. ({last_site_error})"
        )
        return 0

    latest = issues[0]
    old_order = state_order(state)
    latest_order = issue_order(latest)

    if latest_order < old_order:
        print(
            "UYARI: Basımevi sitesi eski bir liste gösteriyor; kayıt geriye alınmadı. "
            f"Kayıtlı son sayı: {state['last_number']}, sitede görünen: {latest['number']}."
        )
    elif latest_order == old_order:
        if (
            latest["date"] != str(state.get("last_date", ""))
            or latest["url"] != str(state.get("last_url", ""))
        ):
            state.update(
                {
                    "last_date": latest["date"],
                    "last_url": latest["url"],
                    "updated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                }
            )
            print(f"Sayı {latest['number']} için tarih/bağlantı bilgisi sessizce güncellendi.")
        else:
            print(f"Yeni sayı yok. Son sayı: {latest['number']} — {latest['date']}")
    else:
        new_issues = [
            issue
            for issue in issues
            if old_order < issue_order(issue) <= latest_order
        ]
        if not new_issues:
            new_issues = [latest]

        for issue in sorted(new_issues, key=issue_order):
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
