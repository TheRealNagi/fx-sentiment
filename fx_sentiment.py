import requests
import time
import os
import json
from datetime import datetime, timezone
from urllib.parse import unquote
import gspread
from google.oauth2.service_account import Credentials

# ══════════════════════════════════════════════════════════════
#  CONFIGURATION — reads from GitHub Secrets (env variables)
# ══════════════════════════════════════════════════════════════
MYFXBOOK_EMAIL    = os.environ["MYFXBOOK_EMAIL"]
MYFXBOOK_PASSWORD = os.environ["MYFXBOOK_PASSWORD"]
GOOGLE_SHEET_ID   = os.environ["GOOGLE_SHEET_ID"]
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_IDS = os.environ["TELEGRAM_CHAT_IDS"].split(",")

TARGET_PAIRS = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
    "USDCHF", "USDCAD", "NZDUSD",
    "EURJPY", "GBPJPY", "EURGBP",
]

BIAS_THRESHOLD = 60.0

SCHEMA_COLS = [
    "timestamp_utc", "source", "pair", "long_pct", "short_pct",
    "long_positions", "short_positions", "long_volume_lots",
    "short_volume_lots", "mid_price", "bid", "ask",
    "spread_pips", "avg_long_price", "avg_short_price",
    "net_bias", "notes",
]

# ══════════════════════════════════════════════════════════════
#  UTILITIES
# ══════════════════════════════════════════════════════════════
def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def classify_bias(long_pct, short_pct, threshold):
    if long_pct  >= threshold: return "LONG_HEAVY"
    if short_pct >= threshold: return "SHORT_HEAVY"
    return "NEUTRAL"

def safe_float(val, default=None):
    try:    return float(val) if val not in (None, "", "null") else default
    except: return default

def safe_int(val, default=None):
    try:    return int(val) if val not in (None, "", "null") else default
    except: return default

# ══════════════════════════════════════════════════════════════
#  GOOGLE SHEETS AUTH — service account (no browser popup)
# ══════════════════════════════════════════════════════════════
def get_gc():
    sa_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
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
#  MYFXBOOK
# ══════════════════════════════════════════════════════════════
class MyfxbookClient:
    BASE    = "https://www.myfxbook.com/api"
    LOGIN   = BASE + "/login.json"
    LOGOUT  = BASE + "/logout.json"
    OUTLOOK = BASE + "/get-community-outlook.json"

    def __init__(self, email, password):
        self.email    = email
        self.password = password
        self.session  = None

    def login(self):
        try:
            r    = requests.get(self.LOGIN,
                                params={"email": self.email, "password": self.password},
                                timeout=15)
            data = r.json()
            if not data.get("error"):
                self.session = unquote(data["session"])
                print(f"✅ Myfxbook logged in (session: {self.session[:8]}…)")
                return True
            print(f"❌ Login failed: {data.get('message')}")
        except Exception as e:
            print(f"❌ Login exception: {e}")
        return False

    def logout(self):
        if self.session:
            try:
                requests.get(self.LOGOUT,
                             params={"session": self.session}, timeout=10)
            except: pass
            self.session = None
            print("✅ Myfxbook logged out")

    def fetch_outlook(self, target_pairs):
        if not self.session:
            return []
        try:
            r    = requests.get(self.OUTLOOK,
                                params={"session": self.session},
                                timeout=20)
            data = r.json()
            if data.get("error"):
                print(f"❌ Outlook error: {data.get('message')}")
                return []

            symbols = data.get("symbols", [])
            if not symbols:
                symbols = data.get("outlook", {}).get("symbols", {}).get("symbol", [])
            if isinstance(symbols, dict):
                symbols = [symbols]

            general    = data.get("outlook", {}).get("general", {})
            notes_base = (f"real={general.get('realAccountsPercentage')}% "
                          f"demo={general.get('demoAccountsPercentage')}%")
            ts      = now_utc()
            records = []

            for s in symbols:
                name = s.get("name", "").replace("/", "").upper()
                if name not in target_pairs:
                    continue
                long_pct  = safe_float(s.get("longPercentage"),  0.0)
                short_pct = safe_float(s.get("shortPercentage"), 0.0)
                records.append({
                    "timestamp_utc":     ts,
                    "source":            "myfxbook",
                    "pair":              name,
                    "long_pct":          round(long_pct,  2),
                    "short_pct":         round(short_pct, 2),
                    "long_positions":    safe_int(s.get("longPositions")),
                    "short_positions":   safe_int(s.get("shortPositions")),
                    "long_volume_lots":  safe_float(s.get("longVolume")),
                    "short_volume_lots": safe_float(s.get("shortVolume")),
                    "mid_price":         None,
                    "bid":               None,
                    "ask":               None,
                    "spread_pips":       None,
                    "avg_long_price":    safe_float(s.get("avgLongPrice")),
                    "avg_short_price":   safe_float(s.get("avgShortPrice")),
                    "net_bias":          classify_bias(long_pct, short_pct, BIAS_THRESHOLD),
                    "notes":             notes_base,
                })

            print(f"✅ Myfxbook: {len(records)} pairs fetched")
            return records
        except Exception as e:
            print(f"❌ Fetch error: {e}")
            return []

# ══════════════════════════════════════════════════════════════
#  PRICES
# ══════════════════════════════════════════════════════════════
class FrankfurterPrices:
    BASE_URL = "https://api.frankfurter.app/latest"
    PAIR_MAP = {
        "EURUSD": ("EUR","USD"), "GBPUSD": ("GBP","USD"), "USDJPY": ("USD","JPY"),
        "AUDUSD": ("AUD","USD"), "USDCHF": ("USD","CHF"), "USDCAD": ("USD","CAD"),
        "NZDUSD": ("NZD","USD"), "EURJPY": ("EUR","JPY"), "GBPJPY": ("GBP","JPY"),
        "EURGBP": ("EUR","GBP"),
    }

    def fetch_all(self, pairs):
        prices     = {p: None for p in pairs}
        needed     = set(self.PAIR_MAP[p][0] for p in pairs if p in self.PAIR_MAP)
        rate_cache = {}
        for base in needed:
            try:
                time.sleep(0.3)
                r = requests.get(self.BASE_URL, params={"from": base}, timeout=10)
                rate_cache[base] = r.json().get("rates", {})
            except Exception as e:
                print(f"⚠️ Price fetch failed for {base}: {e}")
        for pair in pairs:
            m = self.PAIR_MAP.get(pair)
            if m and m[1] in rate_cache.get(m[0], {}):
                prices[pair] = round(float(rate_cache[m[0]][m[1]]), 6)
        found = sum(1 for v in prices.values() if v)
        print(f"✅ Prices: {found}/{len(pairs)} pairs")
        return prices

# ══════════════════════════════════════════════════════════════
#  GOOGLE SHEETS
# ══════════════════════════════════════════════════════════════
class SheetsManager:
    def __init__(self, gc, sheet_id):
        self.wb = gc.open_by_key(sheet_id)

    def _get_or_create(self, name, rows=5000, cols=20):
        try:
            return self.wb.worksheet(name)
        except:
            ws = self.wb.add_worksheet(name, rows=rows, cols=cols)
            print(f"  Created tab: {name}")
            return ws

    def ensure_history_header(self):
        ws   = self._get_or_create("History", rows=100000, cols=len(SCHEMA_COLS))
        data = ws.get_all_values()
        if not data or data[0] != SCHEMA_COLS:
            ws.insert_row(SCHEMA_COLS, 1)
            print("  ✅ History header written")
        return ws

    def write_dashboard(self, records):
        ws = self._get_or_create("Dashboard", rows=100, cols=20)
        ws.clear()
        ws.append_row([f"FX Sentiment Dashboard — {now_utc()}"])
        ws.append_row([])
        ws.append_row(SCHEMA_COLS)
        for r in sorted(records, key=lambda x: x["pair"]):
            ws.append_row([str(r.get(c, "") or "") for c in SCHEMA_COLS])
        ws.append_row([])
        ws.append_row(["⚠️ Contrarian Watch"])
        extremes = [r for r in records if r["net_bias"] != "NEUTRAL"]
        if extremes:
            for r in extremes:
                d = "SHORT setup?" if r["net_bias"] == "LONG_HEAVY" else "LONG setup?"
                ws.append_row([r["pair"], r["net_bias"],
                               f"L:{r['long_pct']}% S:{r['short_pct']}%", d])
        else:
            ws.append_row(["All pairs neutral"])
        print("✅ Dashboard updated")

    def append_history(self, records):
        ws   = self.ensure_history_header()
        rows = [[str(r.get(c, "") or "") for c in SCHEMA_COLS] for r in records]
        ws.append_rows(rows, value_input_option="USER_ENTERED")
        print(f"✅ History: {len(rows)} rows appended")

# ══════════════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════════════
def send_telegram(records, token, chat_ids):
    if not token or not chat_ids:
        print("ℹ️  Telegram not configured — skipping")
        return

    icons   = {"LONG_HEAVY": "🔴", "SHORT_HEAVY": "🟢", "NEUTRAL": "⚪"}
    ts      = records[0]["timestamp_utc"] if records else now_utc()
    lines   = [f"<b>📊 FX Sentiment — {ts}</b>", ""]

    for r in sorted(records, key=lambda x: x["pair"]):
        icon  = icons.get(r["net_bias"], "⚪")
        price = f" | <code>{r['mid_price']}</code>" if r.get("mid_price") else ""
        lines.append(
            f"{icon} <b>{r['pair']}</b> — "
            f"L:<code>{r['long_pct']}%</code> S:<code>{r['short_pct']}%</code> "
            f"({r['net_bias']}){price}"
        )

    extremes = [r for r in records if r["net_bias"] != "NEUTRAL"]
    if extremes:
        lines += ["", "⚠️ <b>Contrarian Watch:</b>"]
        for r in extremes:
            d = "SHORT setup?" if r["net_bias"] == "LONG_HEAVY" else "LONG setup?"
            lines.append(f"  → {r['pair']}: {r['net_bias'].replace('_',' ')} — {d}")

    message = "\n".join(lines)

    for chat_id in chat_ids:
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
                timeout=15,
            )
            resp.raise_for_status()
            print(f"✅ Telegram sent → {chat_id}")
        except Exception as e:
            print(f"❌ Telegram failed for {chat_id}: {e}")

# ══════════════════════════════════════════════════════════════
#  MAIN PIPELINE
# ══════════════════════════════════════════════════════════════
def run_pipeline():
    print("=" * 55)
    print(f"Pipeline start — {now_utc()}")
    print("=" * 55)

    gc = get_gc()
    print("✅ Google Sheets authenticated")

    mfx = MyfxbookClient(MYFXBOOK_EMAIL, MYFXBOOK_PASSWORD)
    if not mfx.login():
        return
    records = mfx.fetch_outlook(TARGET_PAIRS)
    mfx.logout()

    if not records:
        print("❌ No records — aborting")
        return

    prices = FrankfurterPrices().fetch_all(TARGET_PAIRS)
    for r in records:
        r["mid_price"] = prices.get(r["pair"])

    sheets = SheetsManager(gc, GOOGLE_SHEET_ID)
    sheets.write_dashboard(records)
    sheets.append_history(records)

    send_telegram(records, TELEGRAM_TOKEN, TELEGRAM_CHAT_IDS)

    print("\n📋 Snapshot:")
    print(f"{'PAIR':<10} {'L%':>6} {'S%':>6} {'BIAS':<14} {'MID':>10}")
    print("─" * 50)
    for r in sorted(records, key=lambda x: x["pair"]):
        mid = f"{r['mid_price']:.5f}" if r["mid_price"] else "N/A"
        print(f"{r['pair']:<10} {r['long_pct']:>6.1f} "
              f"{r['short_pct']:>6.1f} {r['net_bias']:<14} {mid:>10}")

    print("\n✅ Pipeline complete")

if __name__ == "__main__":
    run_pipeline()
