"""
news_t1.py
==========
Fetches Tier-1 (high-impact / "red folder") economic calendar events for G10
currencies from Forex Factory, plus relevant market news from Alpha Vantage.

Writes both to a "T1 News" tab in the same Google Sheet, and sends a Telegram
summary. Designed to run 2-3 times per day via GitHub Actions.

Sources:
  - Forex Factory weekly calendar JSON (free, no key) — scheduled events + impact
  - Alpha Vantage NEWS_SENTIMENT (free, 25 req/day) — news articles + sentiment
"""

import requests
import os
import json
import base64
from datetime import datetime, timezone, timedelta
from time import sleep
import gspread
from google.oauth2.service_account import Credentials

# ══════════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════════
GOOGLE_SHEET_ID      = os.environ["GOOGLE_SHEET_ID"]
TELEGRAM_TOKEN       = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_IDS    = os.environ["TELEGRAM_CHAT_IDS"].split(",")
ALPHAVANTAGE_KEY     = os.environ["ALPHAVANTAGE_KEY"]

# G10 currencies — Forex Factory uses these country codes
G10_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "SEK", "NOK"]

# Only show events at/after this impact level
# Forex Factory uses: "High", "Medium", "Low", "Holiday"
TARGET_IMPACT = ["High"]   # "red folder" only. Add "Medium" for orange too.

# How many hours ahead to look for upcoming events
LOOKAHEAD_HOURS = 24

NEWS_SHEET_TAB = "T1 News"

NEWS_SCHEMA = [
    "fetched_utc", "type", "currency", "impact",
    "event_or_title", "event_time_utc", "forecast", "previous",
    "actual", "source", "url",
]

# ══════════════════════════════════════════════════════════════
#  UTILITIES
# ══════════════════════════════════════════════════════════════
def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

# ══════════════════════════════════════════════════════════════
#  GOOGLE SHEETS AUTH
# ══════════════════════════════════════════════════════════════
def get_gc():
    sa_b64  = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    sa_json = base64.b64decode(sa_b64).decode("utf-8")
    sa_info = json.loads(sa_json)
    creds   = Credentials.from_service_account_info(
        sa_info,
        scopes=[
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
    )
    return gspread.authorize(creds)

# ══════════════════════════════════════════════════════════════
#  FOREX FACTORY CALENDAR
# ══════════════════════════════════════════════════════════════
class ForexFactoryCalendar:
    """
    Free weekly economic calendar with impact ratings.
    URL is rate-limited to 2 downloads per 5 minutes — fine for 2-3x/day.
    """
    URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (FXNewsBot; personal research)"
    }

    def fetch(self):
        events = []
        try:
            r = requests.get(self.URL, headers=self.HEADERS, timeout=20)
            r.raise_for_status()

            # If rate-limited, FF returns an HTML page instead of JSON
            if not r.text.strip().startswith("["):
                print("⚠️ Forex Factory returned non-JSON (likely rate-limited)")
                return []

            raw = r.json()
            now      = datetime.now(timezone.utc)
            cutoff   = now + timedelta(hours=LOOKAHEAD_HOURS)
            fetched  = now_utc()

            for e in raw:
                currency = e.get("country", "")
                impact   = e.get("impact", "")

                # Filter: G10 only, target impact only
                if currency not in G10_CURRENCIES:
                    continue
                if impact not in TARGET_IMPACT:
                    continue

                # Parse event time
                try:
                    event_dt = datetime.fromisoformat(e["date"].replace("Z", "+00:00"))
                except Exception:
                    continue

                # Only upcoming events within lookahead window
                if event_dt < now or event_dt > cutoff:
                    continue

                events.append({
                    "fetched_utc":    fetched,
                    "type":           "calendar",
                    "currency":       currency,
                    "impact":         impact,
                    "event_or_title": e.get("title", ""),
                    "event_time_utc": event_dt.strftime("%Y-%m-%d %H:%M UTC"),
                    "forecast":       e.get("forecast", ""),
                    "previous":       e.get("previous", ""),
                    "actual":         "",  # not known until release
                    "source":         "forexfactory",
                    "url":            "https://www.forexfactory.com/calendar",
                })

            print(f"✅ Forex Factory: {len(events)} high-impact G10 events (next {LOOKAHEAD_HOURS}h)")
            return events

        except Exception as e:
            print(f"❌ Forex Factory error: {e}")
            return []

# ══════════════════════════════════════════════════════════════
#  ALPHA VANTAGE NEWS
# ══════════════════════════════════════════════════════════════
class AlphaVantageNews:
    """
    News articles + sentiment. Free tier = 25 requests/day.
    We use forex + monetary/macro topics relevant to G10.
    """
    BASE = "https://www.alphavantage.co/query"

    def fetch(self, limit=15):
        articles = []
        try:
            params = {
                "function": "NEWS_SENTIMENT",
                "topics":   "economy_monetary,economy_macro,economy_fiscal,financial_markets",
                "sort":     "LATEST",
                "limit":    str(limit),
                "apikey":   ALPHAVANTAGE_KEY,
            }
            r = requests.get(self.BASE, params=params, timeout=20)
            r.raise_for_status()
            data = r.json()

            # Alpha Vantage rate-limit / error messages
            if "Note" in data or "Information" in data:
                msg = data.get("Note") or data.get("Information")
                print(f"⚠️ Alpha Vantage limit/notice: {msg[:120]}")
                return []

            feed    = data.get("feed", [])
            fetched = now_utc()

            for item in feed[:limit]:
                # Parse AV time format: 20260524T143000
                t_raw = item.get("time_published", "")
                try:
                    t_dt  = datetime.strptime(t_raw, "%Y%m%dT%H%M%S")
                    t_str = t_dt.strftime("%Y-%m-%d %H:%M UTC")
                except Exception:
                    t_str = t_raw

                # Overall sentiment label
                sentiment = item.get("overall_sentiment_label", "")

                articles.append({
                    "fetched_utc":    fetched,
                    "type":           "news",
                    "currency":       "",  # AV doesn't map cleanly to one currency
                    "impact":         sentiment,   # reuse impact column for sentiment label
                    "event_or_title": item.get("title", "")[:200],
                    "event_time_utc": t_str,
                    "forecast":       "",
                    "previous":       "",
                    "actual":         "",
                    "source":         item.get("source", "alphavantage"),
                    "url":            item.get("url", ""),
                })

            print(f"✅ Alpha Vantage: {len(articles)} news articles")
            return articles

        except Exception as e:
            print(f"❌ Alpha Vantage error: {e}")
            return []

# ══════════════════════════════════════════════════════════════
#  GOOGLE SHEETS WRITER
# ══════════════════════════════════════════════════════════════
class NewsSheetWriter:
    def __init__(self, gc, sheet_id):
        self.wb = gc.open_by_key(sheet_id)

    def _get_or_create(self, name, rows=20000, cols=12):
        try:
            return self.wb.worksheet(name)
        except Exception:
            ws = self.wb.add_worksheet(name, rows=rows, cols=cols)
            print(f"  Created tab: {name}")
            return ws

    def write(self, records):
        ws = self._get_or_create(NEWS_SHEET_TAB)

        # Ensure header
        existing = ws.get_all_values()
        if not existing or existing[0] != NEWS_SCHEMA:
            ws.insert_row(NEWS_SCHEMA, 1)
            print("  ✅ T1 News header written")

        if not records:
            print("ℹ️  No records to append")
            return

        rows = [[str(r.get(c, "") or "") for c in NEWS_SCHEMA] for r in records]

        for attempt in range(3):
            try:
                ws.append_rows(rows, value_input_option="USER_ENTERED")
                print(f"✅ T1 News: {len(rows)} rows appended")
                return
            except Exception as e:
                if "429" in str(e) and attempt < 2:
                    print(f"⚠️ Rate limited, retrying in 30s ({attempt+1}/3)")
                    sleep(30)
                else:
                    raise

# ══════════════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════════════
def send_telegram(calendar_events, news_articles, token, chat_ids):
    if not token or not chat_ids:
        print("ℹ️  Telegram not configured — skipping")
        return

    ts    = now_utc()
    lines = [f"<b>📰 T1 News &amp; Calendar — {ts}</b>", ""]

    # Calendar section
    if calendar_events:
        lines.append(f"<b>🔴 High-Impact Events (next {LOOKAHEAD_HOURS}h):</b>")
        for e in sorted(calendar_events, key=lambda x: x["event_time_utc"]):
            fc = f" | F: {e['forecast']}" if e["forecast"] else ""
            pv = f" | P: {e['previous']}" if e["previous"] else ""
            lines.append(
                f"  <b>{e['currency']}</b> {e['event_time_utc']}\n"
                f"  {e['event_or_title']}{fc}{pv}"
            )
        lines.append("")
    else:
        lines.append("🔴 No high-impact G10 events in the window.")
        lines.append("")

    # News section
    if news_articles:
        lines.append("<b>🗞 Latest Market News:</b>")
        for a in news_articles[:8]:   # cap to keep message short
            senti = f" [{a['impact']}]" if a["impact"] else ""
            lines.append(f"  • {a['event_or_title']}{senti}")

    message = "\n".join(lines)

    # Telegram hard limit is 4096 chars — truncate safely
    if len(message) > 4000:
        message = message[:3990] + "\n…(truncated)"

    for chat_id in chat_ids:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id":                  chat_id,
                    "text":                     message,
                    "parse_mode":               "HTML",
                    "disable_web_page_preview":  True,
                },
                timeout=15,
            )
            resp.raise_for_status()
            print(f"✅ Telegram sent → {chat_id}")
        except Exception as e:
            print(f"❌ Telegram failed for {chat_id}: {e}")

# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════
def run():
    print("=" * 55)
    print(f"T1 News pipeline start — {now_utc()}")
    print("=" * 55)

    gc = get_gc()
    print("✅ Google Sheets authenticated")

    # 1. Calendar events
    calendar_events = ForexFactoryCalendar().fetch()

    # 2. News articles
    news_articles = AlphaVantageNews().fetch(limit=15)

    # 3. Combine and write to sheet
    all_records = calendar_events + news_articles
    writer = NewsSheetWriter(gc, GOOGLE_SHEET_ID)
    writer.write(all_records)

    # 4. Telegram
    send_telegram(calendar_events, news_articles, TELEGRAM_TOKEN, TELEGRAM_CHAT_IDS)

    print(f"\n✅ Done — {len(calendar_events)} events, {len(news_articles)} articles")

if __name__ == "__main__":
    run()
