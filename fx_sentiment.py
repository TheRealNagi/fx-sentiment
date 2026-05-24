import requests
import time
import os
import json
import base64
from datetime import datetime, timezone
from urllib.parse import unquote
import gspread
from google.oauth2.service_account import Credentials
from time import sleep

# ══════════════════════════════════════════════════════════════
#  CONFIGURATION — reads from GitHub Secrets (env variables)
# ══════════════════════════════════════════════════════════════
MYFXBOOK_EMAIL    = os.environ["MYFXBOOK_EMAIL"]
MYFXBOOK_PASSWORD = os.environ["MYFXBOOK_PASSWORD"]
GOOGLE_SHEET_ID   = os.environ["GOOGLE_SHEET_ID"]
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_IDS = os.environ["TELEGRAM_CHAT_IDS"].split(",")

TARGET_PAIRS = [
    # USD majors
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF",
    "AUDUSD", "USDCAD", "NZDUSD",
    # EUR crosses
    "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
    # GBP crosses
    "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "GBPNZD",
    # JPY crosses
    "AUDJPY", "CADJPY", "CHFJPY", "NZDJPY",
    # AUD crosses
    "AUDCHF", "AUDCAD", "AUDNZD",
    # CAD/NZD/CHF crosses
    "CADCHF", "NZDCAD", "NZDCHF",
    # Metals & indices
    "XAUUSD", "US30", "SP500", "DAX",
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
#  GOOGLE SHEETS AUTH — base64-encoded service account JSON
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
        # USD majors
        "EURUSD": ("EUR","USD"), "GBPUSD": ("GBP","USD"), "USDJPY": ("USD","JPY"),
        "USDCHF": ("USD","CHF"), "AUDUSD": ("AUD","USD"), "USDCAD": ("USD","CAD"),
        "NZDUSD": ("NZD","USD"),
        # EUR crosses
        "EURGBP": ("EUR","GBP"), "EURJPY": ("EUR","JPY"), "EURCHF": ("EUR","CHF"),
        "EURAUD": ("EUR","AUD"), "EURCAD": ("EUR","CAD"), "EURNZD": ("EUR","NZD"),
        # GBP crosses
        "GBPJPY": ("GBP","JPY"), "GBPCHF": ("GBP","CHF"), "GBPAUD": ("GBP","AUD"),
        "GBPCAD": ("GBP","CAD"), "GBPNZD": ("GBP","NZD"),
        # JPY crosses
        "AUDJPY": ("AUD","JPY"), "CADJPY": ("CAD","JPY"), "CHFJPY": ("CHF","JPY"),
        "NZDJPY": ("NZD","JPY"),
        # AUD crosses
        "AUDCHF": ("AUD","CHF"), "AUDCAD": ("AUD","CAD"), "AUDNZD": ("AUD","NZD"),
        # CAD/NZD/CHF crosses
        "CADCHF": ("CAD","CHF"), "NZDCAD": ("NZD","CAD"), "NZDCHF": ("NZD","CHF"),
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
class YahooPrices:
    """
    Fallback price source for non-FX symbols (gold, indices).
    Free, no API key. Updates ~15 min delayed.
    """
    SYMBOL_MAP = {
        "XAUUSD": "GC=F",      # Gold futures
        "US30":   "^DJI",      # Dow Jones
        "SP500":  "^GSPC",     # S&P 500
        "DAX":    "^GDAXI",    # DAX
    }

    def fetch_all(self, symbols):
        prices = {s: None for s in symbols}
        try:
            import yfinance as yf
            for sym in symbols:
                yahoo_sym = self.SYMBOL_MAP.get(sym)
                if not yahoo_sym:
                    continue
                try:
                    data = yf.Ticker(yahoo_sym).fast_info
                    prices[sym] = round(float(data["last_price"]), 4)
                except Exception as e:
                    print(f"⚠️ Yahoo fetch failed for {sym}: {e}")
            found = sum(1 for v in prices.values() if v)
            print(f"✅ Yahoo prices: {found}/{len(symbols)} symbols")
        except ImportError:
            print("⚠️ yfinance not installed — skipping Yahoo prices")
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

        # Build all rows in memory first
        all_rows = [
            [f"FX Sentiment Dashboard — {now_utc()}"],
            [],
            SCHEMA_COLS,
        ]
        for r in sorted(records, key=lambda x: x["pair"]):
            all_rows.append([str(r.get(c, "") or "") for c in SCHEMA_COLS])

        all_rows.append([])
        all_rows.append(["⚠️ Contrarian Watch"])
        extremes = [r for r in records if r["net_bias"] != "NEUTRAL"]
        if extremes:
            for r in extremes:
                d = "SHORT setup?" if r["net_bias"] == "LONG_HEAVY" else "LONG setup?"
                all_rows.append([
                    r["pair"], r["net_bias"],
                    f"L:{r['long_pct']}% S:{r['short_pct']}%", d
                ])
        else:
            all_rows.append(["All pairs neutral"])

        # ONE single batch write instead of 40+ individual append_row calls
        ws.update(values=all_rows, range_name="A1", value_input_option="USER_ENTERED")
        print("✅ Dashboard updated (batch)")

   def append_history(self, records):
        ws   = self.ensure_history_header()
        rows = [[str(r.get(c, "") or "") for c in SCHEMA_COLS] for r in records]
        
        for attempt in range(3):
            try:
                ws.append_rows(rows, value_input_option="USER_ENTERED")
                print(f"✅ History: {len(rows)} rows appended")
                return
            except Exception as e:
                if "429" in str(e) and attempt < 2:
                    print(f"⚠️ Rate limited, retrying in 30s (attempt {attempt+1}/3)")
                    sleep(30)
                else:
                    raise

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

    # Frankfurter for FX
    prices = FrankfurterPrices().fetch_all(TARGET_PAIRS)
    
    # Yahoo for gold + indices
    non_fx_symbols = ["XAUUSD", "US30", "SP500", "DAX"]
    yahoo_prices   = YahooPrices().fetch_all(non_fx_symbols)
    prices.update(yahoo_prices)

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
