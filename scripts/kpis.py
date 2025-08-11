# scripts/kpis.py
import json, math, datetime
from pathlib import Path
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "public" / "kpis.json"
OUT.parent.mkdir(parents=True, exist_ok=True)

# Yahoo Finance symbols
SYMS = {
    "BTCUSD": "BTC-USD",
    "USDCOP": "COP=X",
    "GOLD":   "GC=F",
    "COFFEE": "KC=F",
    "SP500":  "^GSPC",
    "COLCAP": "^COLCAP",
}

# Tolerancias de cambio mínimo para evitar commits “ruido”
TOL = {
    "BTCUSD": 5.0,      # USD
    "USDCOP": 5.0,      # COP
    "BTCCOP": 5000.0,   # COP
    "GOLD":   0.5,      # USD/oz
    "COFFEE": 0.2,      # cts/lb
    "SP500":  1.0,      # puntos
    "COLCAP": 1.0,      # puntos
}

def last_and_prev_close(sym: str):
    """
    Devuelve (last, prev) de cierres diarios recientes (salta NaN).
    """
    try:
        tk = yf.Ticker(sym)
        df = tk.history(period="10d", interval="1d", actions=False)
        if df is None or "Close" not in df or len(df) == 0:
            return (None, None)
        closes = [float(c) for c in df["Close"].tolist() if c is not None and not (isinstance(c, float) and math.isnan(c))]
        if not closes:
            return (None, None)
        last = closes[-1]
        prev = closes[-2] if len(closes) >= 2 else None
        return (last, prev)
    except Exception:
        return (None, None)

def build_payload():
    quotes = {}
    # 1) valores base con delta/percent
    for key, ysym in SYMS.items():
        last, prev = last_and_prev_close(ysym)
        if last is not None and prev is not None and prev != 0:
            delta = last - prev
            percent = (delta / prev) * 100.0
        else:
            delta = None
            percent = None
        quotes[key] = {"value": last, "delta": delta, "percent": percent}

    # 2) derivado BTC/COP
    btc = quotes.get("BTCUSD", {}).get("value")
    usd_cop = quotes.get("USDCOP", {}).get("value")
    btccop_val = (btc * usd_cop) if (btc is not None and usd_cop is not None) else None

    # (delta/percent para BTCCOP los dejamos en None para simplicidad)
    quotes["BTCCOP"] = {"value": btccop_val, "delta": None, "percent": None}

    # 3) meta
    quotes["_meta"] = {
        "source": "Yahoo Finance via yfinance",
        "updated_at": datetime.datetime.utcnow().isoformat() + "Z"
    }
    return quotes

def close_enough(old, new, tol):
    if old is None or new is None:
        return False
    try:
        return abs(float(new) - float(old)) <= float(tol)
    except Exception:
        return False

def main():
    # lee previo (si existe) para decidir si vale la pena commitear
    prev = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
    new = build_payload()

    # detectar cambios relevantes en 'value'
    changed = False
    for key in ["BTCUSD","BTCCOP","USDCOP","COFFEE","GOLD","COLCAP","SP500"]:
        oldv = ((prev or {}).get(key) or {}).get("value")
        newv = ((new or {}).get(key) or {}).get("value")
        tol = TOL.get(key, 0.0)
        if oldv is None and newv is not None:
            changed = True; break
        if newv is None and oldv is not None:
            changed = True; break
        if newv is not None and oldv is not None and not close_enough(oldv, newv, tol):
            changed = True; break

    if not changed:
        print("Sin cambios relevantes en KPIs (no se actualiza kpis.json).")
        return

    OUT.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    print("KPIs actualizados:", OUT)

if __name__ == "__main__":
    main()
