# scripts/kpis.py
import json, os, math, datetime
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "public" / "kpis.json"
OUT.parent.mkdir(parents=True, exist_ok=True)

# Mapa de símbolos (Yahoo Finance)
SYMS = {
    "BTCUSD": "BTC-USD",   # Bitcoin en USD
    "USDCOP": "COP=X",     # USD/COP
    "GOLD":   "GC=F",      # Oro (futuro COMEX)
    "COFFEE": "KC=F",      # Café (ICE Arabica)
    "SP500":  "^GSPC",     # S&P 500
    "COLCAP": "^COLCAP",   # Índice COLCAP
}

# Tolerancias para evitar “commit ruido” (cambios mínimos)
TOL = {
    "BTCUSD": 5.0,        # USD
    "USDCOP": 5.0,        # COP
    "BTCCOP": 5000.0,     # COP
    "GOLD":   0.5,        # USD/oz
    "COFFEE": 0.2,        # centavos por libra
    "SP500":  1.0,        # puntos índice
    "COLCAP": 1.0,        # puntos índice
}

def get_last_price(sym: str):
    """Regresa último precio de cierre conocido o None."""
    try:
        tk = yf.Ticker(sym)
        # 5 días por si hubo feriados / fines de semana
        df = tk.history(period="5d", interval="1d", actions=False)
        if df is not None and len(df) > 0 and "Close" in df:
            closes = [c for c in df["Close"].tolist() if c is not None and not (isinstance(c, float) and math.isnan(c))]
            if closes:
                return float(closes[-1])
    except Exception:
        pass
    return None

def close_enough(a, b, tol):
    if a is None or b is None:
        return False
    try:
        return abs(a - b) <= tol
    except Exception:
        return False

def main():
    # 1) lee el anterior (si existe)
    prev = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            prev = {}

    prev_vals = (prev or {}).get("values", {})

    # 2) consulta nuevos valores
    data = {}
    for k, s in SYMS.items():
        data[k] = get_last_price(s)

    # 3) derivado BTC/COP
    btc_usd = data.get("BTCUSD")
    usd_cop = data.get("USDCOP")
    data["BTCCOP"] = (btc_usd * usd_cop) if (btc_usd and usd_cop) else None

    # 4) ¿cambiaron de verdad?
    changed = False
    for key, newv in data.items():
        oldv = prev_vals.get(key)
        tol = TOL.get(key, 0.0)
        if oldv is None and newv is not None:
            changed = True
            break
        if newv is None and oldv is not None:
            changed = True
            break
        if newv is not None and oldv is not None and not close_enough(newv, float(oldv), tol):
            changed = True
            break

    if not changed:
        print("Sin cambios relevantes en KPIs (no se actualiza kpis.json).")
        return  # no escribe archivo => el workflow no tendrá nada que commitear

    # 5) escribe si cambió algo
    out = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "values": data
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("KPIs actualizados:", OUT)

if __name__ == "__main__":
    main()
