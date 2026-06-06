#!/usr/bin/env python3
"""
Autonomous Trading Bot - GitHub Actions Edition
Runs hourly via GitHub Actions cron.
"""
import os, sys, json, sqlite3, requests, time, subprocess
from datetime import datetime, timedelta

BASE_URL = "https://paper-api.alpaca.markets/v2"
DATA_URL = "https://data.alpaca.markets/v1beta3/crypto/us"

# ── Env validation ──
def get_env(name):
    val = os.environ.get(name, "")
    if not val:
        print(f"[FATAL] Environment variable '{name}' is not set!")
        print(f"  -> Set it as a GitHub Secret in repo settings.")
        sys.exit(1)
    return val

ALPACA_KEY = get_env("APCA_API_KEY_ID")
ALPACA_SECRET = get_env("APCA_API_SECRET_KEY")
HEADERS = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

SYMBOLS = [
    "BTC/USD", "SOL/USD", "ETH/USD", "DOGE/USD", "ADA/USD",
    "AVAX/USD", "LINK/USD", "DOT/USD", "MATIC/USD", "ATOM/USD",
    "NEAR/USD", "FTM/USD", "APE/USD", "LDO/USD", "ARB/USD",
    "OP/USD", "SUI/USD", "SEI/USD", "TIA/USD", "JUP/USD",
]
PARAMS = {
    "stop_loss_pct": 0.05,
    "take_profit_pct": 0.15,
    "max_open_positions": 4,
    "min_trade_usd": 5.0,
}
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "trading.db")

# ── Database ──
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
        action TEXT NOT NULL, symbol TEXT NOT NULL,
        price REAL, qty REAL, notional REAL, tp_price REAL, sl_price REAL,
        confidence REAL, reason TEXT, pnl REAL DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
        equity REAL, cash REAL, positions TEXT, market_view TEXT, decisions TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS run_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, status TEXT, message TEXT)""")
    conn.commit()
    return conn

def log_trade(conn, t):
    c = conn.cursor()
    c.execute("INSERT INTO trades (timestamp,action,symbol,price,qty,notional,tp_price,sl_price,confidence,reason,pnl) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (datetime.utcnow().isoformat(), t.get("action"), t.get("symbol"), t.get("price"),
         t.get("qty"), t.get("notional"), t.get("tp_price"), t.get("sl_price"),
         t.get("confidence"), t.get("reason"), t.get("pnl", 0)))
    conn.commit()

def log_snapshot(conn, equity, cash, positions, view, decisions):
    c = conn.cursor()
    c.execute("INSERT INTO snapshots (timestamp,equity,cash,positions,market_view,decisions) VALUES (?,?,?,?,?,?)",
        (datetime.utcnow().isoformat(), equity, cash, json.dumps(positions), view, json.dumps(decisions)))
    conn.commit()

def log_run(conn, status, message):
    c = conn.cursor()
    c.execute("INSERT INTO run_log (timestamp,status,message) VALUES (?,?,?)",
              (datetime.utcnow().isoformat(), status, message))
    conn.commit()

# ── Alpaca API ──
def api_get(path, params=None, base=None):
    url = (base or BASE_URL) + path
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=30)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 5))
                print(f"  [RATE LIMIT] Waiting {wait}s...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            if attempt < 2:
                print(f"  [RETRY {attempt+1}] {path}: {e}")
                time.sleep(2)
            else:
                raise
    return None

def api_post(path, payload):
    for attempt in range(3):
        try:
            r = requests.post(BASE_URL + path, headers=HEADERS, json=payload, timeout=30)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 5))
                print(f"  [RATE LIMIT] Waiting {wait}s...")
                time.sleep(wait)
                continue
            if r.status_code in (200, 201):
                try: return {"success": True, "data": r.json()}
                except: return {"success": True, "data": None}
            return {"success": False, "error": r.text}
        except requests.exceptions.RequestException as e:
            if attempt < 2:
                print(f"  [RETRY {attempt+1}] POST {path}: {e}")
                time.sleep(2)
            else:
                return {"success": False, "error": str(e)}
    return {"success": False, "error": "max retries"}

def api_delete(path):
    for attempt in range(3):
        try:
            r = requests.delete(BASE_URL + path, headers=HEADERS, timeout=30)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 5))
                print(f"  [RATE LIMIT] Waiting {wait}s...")
                time.sleep(wait)
                continue
            if r.status_code in (200, 204):
                try: return {"success": True, "data": r.json()}
                except: return {"success": True, "data": None}
            return {"success": False, "error": r.text}
        except requests.exceptions.RequestException as e:
            if attempt < 2:
                print(f"  [RETRY {attempt+1}] DELETE {path}: {e}")
                time.sleep(2)
            else:
                return {"success": False, "error": str(e)}
    return {"success": False, "error": "max retries"}

def get_account():
    return api_get("/account")

def get_positions():
    data = api_get("/positions")
    if isinstance(data, list):
        return data
    return []

def get_crypto_bars(symbol, timeframe="1Day", limit=50):
    start_dt = datetime.utcnow() - timedelta(days=limit + 10)
    params = {
        "symbols": symbol, "timeframe": timeframe,
        "start": start_dt.strftime("%Y-%m-%dT00:00:00Z"),
        "limit": limit, "sort": "asc",
    }
    data = api_get("/bars", params=params, base=DATA_URL)
    if data and "bars" in data and symbol in data["bars"]:
        return data["bars"][symbol]
    return []

def get_crypto_price(symbol):
    data = api_get("/latest/trades?symbols=" + symbol, base=DATA_URL)
    if data and "trades" in data and symbol in data["trades"]:
        return float(data["trades"][symbol]["p"])
    return None

def place_market_buy(symbol, notional):
    return api_post("/orders", {
        "symbol": symbol, "side": "buy", "type": "market",
        "notional": str(round(notional, 2)), "time_in_force": "gtc",
    })

def place_stop_limit_sell(symbol, qty, stop_price, limit_price):
    return api_post("/orders", {
        "symbol": symbol, "qty": str(qty), "side": "sell",
        "type": "stop_limit", "stop_price": str(stop_price),
        "limit_price": str(limit_price), "time_in_force": "gtc",
    })

def place_limit_sell(symbol, qty, limit_price):
    return api_post("/orders", {
        "symbol": symbol, "qty": str(qty), "side": "sell",
        "type": "limit", "limit_price": str(limit_price), "time_in_force": "gtc",
    })

def close_position(symbol):
    return api_delete("/positions/" + symbol)

# ── Technical Analysis ──
def compute_rsi(closes, period=14):
    if len(closes) < period + 1: return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    if len(gains) < period: return None
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period-1) + gains[i]) / period
        al = (al * (period-1) + losses[i]) / period
    if al == 0: return 100
    return round(100 - (100 / (1 + ag/al)), 2)

def compute_sma(closes, period):
    if len(closes) < period: return None
    return round(sum(closes[-period:]) / period, 4)

def compute_ema(closes, period):
    if len(closes) < period: return None
    m = 2 / (period + 1)
    e = sum(closes[:period]) / period
    for p in closes[period:]:
        e = (p - e) * m + e
    return round(e, 4)

def compute_macd(closes):
    if len(closes) < 26: return None
    e12 = compute_ema(closes, 12)
    e26 = compute_ema(closes, 26)
    return round(e12 - e26, 4) if e12 and e26 else None

def compute_bollinger(closes, period=20, std_mult=2):
    if len(closes) < period: return None, None, None
    sma = sum(closes[-period:]) / period
    var = sum((c - sma)**2 for c in closes[-period:]) / period
    std = var ** 0.5
    return round(sma + std_mult * std, 4), round(sma, 4), round(sma - std_mult * std, 4)

def analyze_symbol(symbol):
    bars = get_crypto_bars(symbol)
    if not bars or len(bars) < 26:
        return None
    closes = [float(b["c"]) for b in bars]
    highs = [float(b["h"]) for b in bars]
    lows = [float(b["l"]) for b in bars]
    volumes = [float(b["v"]) for b in bars]
    price = closes[-1]
    sma20 = compute_sma(closes, 20)
    sma50 = compute_sma(closes, 50) if len(closes) >= 50 else None
    trend = "NEUTRAL"
    if sma20 and sma50:
        if sma20 > sma50 and price > sma20:
            trend = "BULLISH"
        elif sma20 < sma50 and price < sma20:
            trend = "BEARISH"
    avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes) / len(volumes)
    bb = compute_bollinger(closes)
    return {
        "symbol": symbol, "price": price, "rsi": compute_rsi(closes),
        "sma20": sma20, "sma50": sma50, "macd": compute_macd(closes),
        "bb_upper": bb[0], "bb_mid": bb[1], "bb_lower": bb[2],
        "volume_ratio": round(volumes[-1] / avg_vol, 2) if avg_vol > 0 else 1.0,
        "change_1d": round((closes[-1]-closes[-2])/closes[-2]*100, 2) if len(closes)>=2 else 0,
        "change_7d": round((closes[-1]-closes[-7])/closes[-7]*100, 2) if len(closes)>=7 else 0,
        "change_30d": round((closes[-1]-closes[-30])/closes[-30]*100, 2) if len(closes)>=30 else 0,
        "trend": trend,
        "support": round(min(lows[-20:]), 4),
        "resistance": round(max(highs[-20:]), 4),
    }

# ── Scoring / Advisor ──
def score_coin(m):
    score = 0.0
    reasons = []
    rsi = m.get("rsi")
    if rsi is not None:
        if rsi < 30:
            score += 3.0
            reasons.append(f"deeply oversold RSI {rsi}")
        elif rsi < 40:
            score += 2.0
            reasons.append(f"oversold RSI {rsi}")
        elif rsi < 45:
            score += 0.5
        elif rsi > 70:
            score -= 2.0
            reasons.append(f"overbought RSI {rsi}")
        elif rsi > 60:
            score -= 0.5
    if m.get("trend") == "BULLISH":
        score += 2.0
        reasons.append("bullish trend")
    elif m.get("trend") == "BEARISH":
        score -= 1.5
        reasons.append("bearish trend")
    macd = m.get("macd")
    if macd is not None:
        if macd > 0:
            score += 1.0
            reasons.append("positive MACD")
        else:
            score -= 1.0
            reasons.append("negative MACD")
    price = m.get("price", 0)
    bb_lower = m.get("bb_lower")
    bb_upper = m.get("bb_upper")
    if bb_lower and price <= bb_lower * 1.03:
        score += 2.0
        reasons.append("at/below lower BB")
    elif bb_upper and price >= bb_upper * 0.97:
        score -= 1.0
        reasons.append("at/above upper BB")
    vol = m.get("volume_ratio", 1)
    if vol > 2.0:
        score += 1.5
        reasons.append(f"high vol {vol}x")
    elif vol > 1.5:
        score += 0.5
    elif vol < 0.3:
        score -= 1.0
        reasons.append(f"low vol {vol}x")
    c7d = m.get("change_7d", 0)
    if c7d > 10:
        score += 1.5
        reasons.append(f"strong 7d +{c7d:.1f}%")
    elif c7d > 5:
        score += 0.5
    elif c7d < -15:
        score -= 2.0
        reasons.append(f"crashing 7d {c7d:.1f}%")
    elif c7d < -10:
        score -= 1.0
    c30d = m.get("change_30d", 0)
    if c30d > 20:
        score += 1.0
    elif c30d < -30:
        score -= 1.5
    return score, reasons

def make_decisions(analyses, cash, positions):
    bullish = sum(1 for a in analyses if a.get("trend") == "BULLISH")
    bearish = sum(1 for a in analyses if a.get("trend") == "BEARISH")
    avg_7d = sum(a.get("change_7d", 0) for a in analyses) / max(len(analyses), 1)
    if bearish > len(analyses) * 0.5:
        view = f"BEARISH: {bearish}/{len(analyses)} downtrend. Avg 7d: {avg_7d:+.1f}%. Capital preservation."
    elif bullish > len(analyses) * 0.3:
        view = f"BULLISH: {bullish}/{len(analyses)} trending up. Avg 7d: {avg_7d:+.1f}%. Selective buying."
    else:
        view = f"NEUTRAL: {bullish} bull, {bearish} bear. Avg 7d: {avg_7d:+.1f}%. Wait for setups."

    scored = []
    for a in analyses:
        s, r = score_coin(a)
        scored.append({**a, "score": s, "reasons": r})
    scored.sort(key=lambda x: x["score"], reverse=True)

    decisions = []
    open_count = len(positions)
    for coin in scored:
        if coin["score"] < 1.0:
            continue
        if open_count >= PARAMS["max_open_positions"]:
            break
        if coin["score"] >= 4.0:
            alloc, conf = 0.20, 0.80
        elif coin["score"] >= 3.0:
            alloc, conf = 0.15, 0.70
        elif coin["score"] >= 2.0:
            alloc, conf = 0.10, 0.60
        else:
            alloc, conf = 0.05, 0.55
        notional = cash * alloc
        if notional < PARAMS["min_trade_usd"]:
            continue
        decisions.append({
            "symbol": coin["symbol"], "action": "BUY", "confidence": conf,
            "allocation_pct": alloc, "notional": round(notional, 2),
            "reason": ", ".join(coin["reasons"][:3]),
        })
        open_count += 1

    # Check existing positions for overbought
    for pos in positions:
        sym = pos.get("symbol", "")
        matching = [a for a in analyses if a["symbol"] == sym]
        if matching and matching[0].get("rsi", 50) > 75:
            decisions.append({
                "symbol": sym, "action": "SELL", "confidence": 0.7,
                "allocation_pct": 0,
                "reason": f"Overbought RSI {matching[0]['rsi']}, take profit",
            })
    return view, decisions

# ── TP/SL Check ──
def check_tp_sl(positions, conn):
    for pos in positions:
        sym = pos.get("symbol", "")
        current = float(pos.get("current_price", 0))
        entry = float(pos.get("avg_entry_price", 0))
        qty = float(pos.get("qty", 0))
        if entry <= 0:
            continue
        prec = 6 if entry < 1 else 2
        tp_price = round(entry * (1 + PARAMS["take_profit_pct"]), prec)
        sl_price = round(entry * (1 - PARAMS["stop_loss_pct"]), prec)
        if current >= tp_price:
            print(f"  TP HIT {sym}! Closing...")
            r = close_position(sym)
            if r["success"]:
                pnl = (current - entry) * qty
                log_trade(conn, {
                    "action": "sell", "symbol": sym, "price": current, "qty": qty,
                    "notional": None, "tp_price": None, "sl_price": None,
                    "confidence": None, "reason": "take_profit_hit", "pnl": round(pnl, 2),
                })
                print(f"  SOLD {sym} at TP. PnL: ${pnl:.2f}")
        elif current <= sl_price:
            print(f"  SL HIT {sym}! Closing...")
            r = close_position(sym)
            if r["success"]:
                pnl = (current - entry) * qty
                log_trade(conn, {
                    "action": "sell", "symbol": sym, "price": current, "qty": qty,
                    "notional": None, "tp_price": None, "sl_price": None,
                    "confidence": None, "reason": "stop_loss_hit", "pnl": round(pnl, 2),
                })
                print(f"  SOLD {sym} at SL. PnL: ${pnl:.2f}")

# ── Main ──
def main():
    print("=" * 60)
    print(f"  TRADING BOT - {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 60)

    conn = init_db()
    log_run(conn, "started", "Bot run initiated")

    try:
        # [1] Account
        print("\n[1] Account...")
        account = get_account()
        if account is None:
            print("  [ERROR] Could not fetch account. Check API keys.")
            log_run(conn, "error", "Failed to fetch account")
            sys.exit(1)
        cash = float(account.get("cash", 0))
        equity = float(account.get("equity", 0))
        print(f"    Cash: ${cash:.2f}  Equity: ${equity:.2f}")

        # [2] Positions
        print("\n[2] Positions...")
        positions = get_positions()
        print(f"    Open: {len(positions)}")
        for p in positions:
            print(f"    - {p['symbol']}: {p['qty']} @ ${p.get('avg_entry_price',0)} -> ${p.get('current_price',0)}")

        # [3] TP/SL Check
        print("\n[3] TP/SL Check...")
        check_tp_sl(positions, conn)
        positions = get_positions()

        # [4] Market Scan
        print("\n[4] Market Scan...")
        analyses = []
        for i, sym in enumerate(SYMBOLS):
            try:
                a = analyze_symbol(sym)
                if a:
                    analyses.append(a)
                # Rate limit: max ~5 per second to stay safe
                if (i + 1) % 5 == 0:
                    time.sleep(1)
            except Exception as e:
                print(f"    WARN: {sym}: {e}")
        print(f"    Scanned {len(analyses)}/{len(SYMBOLS)} coins")

        top = sorted(analyses, key=lambda x: x.get("change_1d", 0), reverse=True)[:10]
        for a in top:
            rsi_str = str(a.get("rsi", "-"))
            print(f"    {a['symbol']:<12} ${a['price']:>10.4f}  RSI:{rsi_str:>6}  {a['trend']:<9}  24h:{a['change_1d']:>+6.1f}%")

        # [5] Analysis & Decisions
        print("\n[5] Analysis & Decisions...")
        view, decisions = make_decisions(analyses, cash, positions)
        print(f"    View: {view}")
        for d in decisions:
            conf_pct = f"{d.get('confidence',0):.0%}"
            reason = (d.get("reason", "") or "")[:60]
            print(f"    -> {d['action']} {d['symbol']} (conf:{conf_pct}) {reason}")

        # [6] Execute
        print("\n[6] Executing...")
        for d in decisions:
            action = d.get("action")
            if action == "HOLD":
                continue
            sym = d["symbol"]
            conf = d.get("confidence", 0)
            if conf < 0.55:
                print(f"    SKIP {sym}: low confidence")
                continue

            if action == "BUY":
                if len(positions) >= PARAMS["max_open_positions"]:
                    print(f"    SKIP {sym}: max positions")
                    continue
                notional = d.get("notional", 0)
                print(f"    BUY {sym} ${notional:.2f}")
                r = place_market_buy(sym, notional)
                if r["success"]:
                    time.sleep(3)
                    price = get_crypto_price(sym)
                    qty = notional / price if price else 0
                    entry = price or 0
                    prec = 6 if entry < 1 else 2
                    tp = round(entry * (1 + PARAMS["take_profit_pct"]), prec)
                    sl = round(entry * (1 - PARAMS["stop_loss_pct"]), prec)
                    sl_lim = round(sl - (0.001 if sl < 1 else 0.5), prec)
                    sl_ok = place_stop_limit_sell(sym, qty, sl, sl_lim)["success"]
                    tp_ok = place_limit_sell(sym, qty, tp)["success"]
                    tp_mode = "broker" if tp_ok else "bot"
                    sl_mode = "broker" if sl_ok else "bot"
                    print(f"    OK! entry=${entry:.4f} qty={qty:.6f} TP=${tp} ({tp_mode}) SL=${sl} ({sl_mode})")
                    log_trade(conn, {
                        "action": "buy", "symbol": sym, "price": entry, "qty": qty,
                        "notional": notional, "tp_price": tp, "sl_price": sl,
                        "confidence": conf, "reason": d.get("reason", ""),
                    })
                else:
                    err = (r.get("error", "") or "")[:200]
                    print(f"    FAIL: {err}")

            elif action == "SELL":
                print(f"    SELL {sym}")
                r = close_position(sym)
                if r["success"]:
                    price = get_crypto_price(sym)
                    log_trade(conn, {
                        "action": "sell", "symbol": sym, "price": price, "qty": 0,
                        "notional": None, "tp_price": None, "sl_price": None,
                        "confidence": conf, "reason": d.get("reason", ""),
                    })
                    print(f"    SOLD {sym}")
                else:
                    err = (r.get("error", "") or "")[:200]
                    print(f"    FAIL: {err}")

        # Final state
        positions = get_positions()
        log_snapshot(conn, equity, cash, positions, view, decisions)

        # Generate dashboard JSON
        try:
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generate_dashboard_json.py")
            subprocess.run([sys.executable, script], check=True)
            print("  Dashboard JSON updated.")
        except Exception as e:
            print(f"  Dashboard JSON error: {e}")

        log_run(conn, "completed", "Bot run completed successfully")
        print("\n[DONE] Run complete.")

    except Exception as e:
        log_run(conn, "error", str(e))
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
