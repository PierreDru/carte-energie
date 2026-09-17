"""
Génère live.json pour les deux cartes. Sans serveur : lancé par GitHub Actions toutes les 15 min.
Sources gratuites : EIA (clé), AGSI+/GIE (clé), Yahoo Finance via yfinance (sans clé, retard ~15 min, fragile).
Les valeurs sans source gratuite (JKM, Oman, diesel, VLCC, stocks) viennent de manual.yml.
Variables d'environnement : EIA_API_KEY, AGSI_API_KEY (facultatives : la ligne est sautée si absente).
"""
import json, os, datetime as dt, urllib.request, urllib.parse
try:
    import yaml
except ImportError:
    yaml = None

OUT = os.environ.get("LIVE_OUT", "live.json")
now = dt.datetime.now(dt.timezone.utc).isoformat()
prices = {}

def get(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "energy-map/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)

# 1) Yahoo Finance (intraday, retard ~15 min). Casse parfois : chaque symbole est isolé.
try:
    import yfinance as yf
    for key, sym, disp in [("brent", "BZ=F", "$/b"), ("wti", "CL=F", "$/b"), ("hh", "NG=F", "$/MMBtu"), ("ttf", "TTF=F", "€/MWh")]:
        try:
            h = yf.Ticker(sym).history(period="1d", interval="5m")
            if len(h):
                v = float(h["Close"].dropna().iloc[-1]); ts = h.index[-1].to_pydatetime().astimezone(dt.timezone.utc).isoformat()
                if key == "ttf":  # ICE TTF coté en €/MWh → $/MMBtu (1 MWh = 3,412 MMBtu)
                    eur = yf.Ticker("EURUSD=X").history(period="1d")["Close"].iloc[-1]
                    v_usd = v * float(eur) / 3.412
                    prices["ttf"] = {"value": round(v_usd, 2), "display": f"{v_usd:,.2f} $/MMBtu".replace(",", " "), "asof": ts, "source": "ICE via Yahoo (~15 min)", "eur_mwh": round(v, 2)}
                else:
                    prices[key] = {"value": round(v, 2), "display": f"{v:,.2f} {disp}".replace(",", " "), "asof": ts, "source": "Yahoo Finance (~15 min)"}
        except Exception as e:
            print("yahoo", sym, "KO:", e)
except ImportError:
    print("yfinance absent : pip install yfinance")

# 2) EIA (officiel, J-1) : sert de secours si Yahoo est tombé, et de référence quotidienne.
k = os.environ.get("EIA_API_KEY")
if k:
    for key, series, disp in [("brent", "RBRTE", "$/b"), ("wti", "RWTC", "$/b"), ("hh", "RNGWHHD", "$/MMBtu")]:
        try:
            u = ("https://api.eia.gov/v2/seriesid/" + series + "?" + urllib.parse.urlencode({"api_key": k, "length": 1}))
            d = get(u)["response"]["data"][0]
            v = float(d["value"])
            prices.setdefault(key, {"value": v, "display": f"{v:.2f} {disp}", "asof": d["period"], "source": "EIA (quotidien)"})
            prices[key]["eia_daily"] = v
        except Exception as e:
            print("EIA", series, "KO:", e)

# 3) Stockage gaz UE — AGSI+ (GIE), API officielle.
k = os.environ.get("AGSI_API_KEY")
if k:
    try:
        d = get("https://agsi.gie.eu/api?country=EU&size=1", headers={"x-key": k})["data"][0]
        v = float(d["full"])
        prices["eu_storage"] = {"value": v, "display": f"{v:.1f} %", "asof": d["gasDayStart"], "source": "AGSI+ (GIE)"}
    except Exception as e:
        print("AGSI KO:", e)

# 4) Dérivés
if "ttf" in prices and "jkm" in prices:
    s = prices["ttf"]["value"] - prices["jkm"]["value"]
    prices["ttf_jkm"] = {"value": round(s, 2), "display": f"{s:+.2f} $", "asof": now, "source": "calcul"}

# 5) Saisies manuelles (JKM, Oman, diesel, VLCC…) : manual.yml, ne remplacent jamais une valeur automatique.
if yaml and os.path.exists("manual.yml"):
    for key, m in (yaml.safe_load(open("manual.yml")) or {}).items():
        prices.setdefault(key, {"value": m.get("value"), "display": m.get("display"), "asof": m.get("asof"), "source": m.get("source", "saisie manuelle")})

# 6) Fusion avec l'AIS si un relais tourne (facultatif)
vessels, transits = [], None
u = os.environ.get("AIS_RELAY_URL")
if u:
    try:
        d = get(u); vessels = d.get("vessels", []); transits = d.get("hormuz_transits_24h")
    except Exception as e:
        print("relais AIS KO:", e)

# 7) Historique (pour un futur curseur temporel) : on garde 1 point / passage, 90 jours
hist = {}
if os.path.exists(OUT):
    try: hist = json.load(open(OUT)).get("history", {})
    except Exception: pass
for key in ("brent", "wti", "hh", "ttf", "eu_storage"):
    if key in prices:
        hist.setdefault(key, []).append([now, prices[key]["value"]])
        hist[key] = hist[key][-90*96:]

json.dump({"updated": now, "prices": prices, "vessels": vessels, "hormuz_transits_24h": transits, "history": hist}, open(OUT, "w"), ensure_ascii=False)
print("écrit", OUT, "·", len(prices), "prix")
