#!/usr/bin/env python3
"""Generate dashboard.json from SQLite DB for GitHub Pages."""
import json, sqlite3, os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "trading.db")
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dashboard", "data.json")

if not os.path.exists(DB_PATH):
    print("No DB found yet.")
    exit(0)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# Latest snapshot
snap = conn.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
# Recent trades
trades = conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 50").fetchall()
# Run log
logs = conn.execute("SELECT * FROM run_log ORDER BY id DESC LIMIT 20").fetchall()

data = {
    "equity": snap["equity"] if snap else 0,
    "cash": snap["cash"] if snap else 0,
    "positions": json.loads(snap["positions"]) if snap and snap["positions"] else [],
    "market_view": snap["market_view"] if snap else "N/A",
    "last_run": snap["timestamp"] if snap else "N/A",
    "trades": [dict(t) for t in trades],
    "run_log": [dict(r) for r in logs],
}

os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
with open(OUT_PATH, "w") as f:
    json.dump(data, f, indent=2)

print(f"Dashboard data written: {len(trades)} trades, {len(logs)} log entries")
conn.close()
