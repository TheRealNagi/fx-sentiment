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
