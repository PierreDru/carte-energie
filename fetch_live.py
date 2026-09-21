"""
Génère live.json pour les deux cartes. Lancé par GitHub Actions toutes les 15 min (aucun serveur).

Sources gratuites :
  - straits.live (sans clé) : transits d'Ormuz (IMF PortWatch), navires AIS dans le Golfe, tankers
    devenus invisibles, prime d'assurance guerre, oléoducs de contournement, dépêches, marchés prédictifs, SPR.
  - Yahoo Finance via yfinance (sans clé, ~15 min de retard) : Brent, WTI, Henry Hub, TTF.
  - EIA (clé EIA_API_KEY, facultative) : prix officiels quotidiens, en secours.
  - AGSI+ (clé AGSI_API_KEY, facultative) : stockage gaz UE.
  - manual.yml : valeurs sans source gratuite (JKM, Oman, diesel, VLCC…).
Chaque source est isolée : si l'une tombe, les autres continuent et live.json est toujours écrit.
Les valeurs « il y a 5 jours » viennent des historiques des sources quand ils existent,
sinon de l'historique que ce script accumule lui-même (disponible 5 jours après la mise en route).
"""
import csv, io, json, os, re, datetime as dt, urllib.request, urllib.parse
try:
    import yaml
except ImportError:
    yaml = None

OUT = os.environ.get("LIVE_OUT", "live.json")
NOW = dt.datetime.now(dt.timezone.utc)
now = NOW.isoformat()
T5 = NOW - dt.timedelta(days=5)
prices, d5 = {}, {"prices": {}, "hormuz": {}, "pipelines": None}
sources = []
UA = {"User-Agent": "carte-energie/1.0 (+github pages; polite 15-min poll)"}


def get_json(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def get_text(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def frn(v, d=2):
    """Nombre au format français : 1 234,56"""
    return f"{v:,.{d}f}".replace(",", " ").replace(".", ",")


def parse_ts(s):
    s = str(s)
    if len(s) <= 10:
        return dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc)
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---------- historique accumulé par ce script (horaire, 45 jours) ----------
hist = {}
if os.path.exists(OUT):
    try:
        hist = json.load(open(OUT)).get("history", {}) or {}
    except Exception:
        hist = {}


def push(key, val):
    if val is None:
        return
    arr = hist.setdefault(key, [])
    if arr:
        try:
            if (NOW - parse_ts(arr[-1][0])).total_seconds() < 3300:
                arr[-1] = [now, val]
                return
        except Exception:
            pass
    arr.append([now, val])
    cutoff = NOW - dt.timedelta(days=45)
    hist[key] = [x for x in arr if parse_ts(x[0]) >= cutoff]


def hist_at(key, days=5):
    """Valeur de l'historique propre il y a `days` jours (None si l'historique est trop court)."""
    target = NOW - dt.timedelta(days=days)
    arr = hist.get(key) or []
    older = [x for x in arr if parse_ts(x[0]) <= target]
    if not older or (target - parse_ts(older[-1][0])).total_seconds() > 30 * 3600:
        return None
    return older[-1][1]


# ---------- 1) Yahoo Finance : prix du moment + clôture d'il y a 5 jours ----------
def close_on_or_before(h, when):
    c = h["Close"].dropna()
    best = None
    for i, v in c.items():
        t = i.to_pydatetime().astimezone(dt.timezone.utc)
        if t <= when:
            best = (float(v), t.date().isoformat())
    return best


try:
    import yfinance as yf
    eurusd = None
    try:
        eurusd = float(yf.Ticker("EURUSD=X").history(period="5d")["Close"].dropna().iloc[-1])
    except Exception as e:
        print("EURUSD KO:", e)
    for key, sym, unit in [("brent", "BZ=F", "$/b"), ("wti", "CL=F", "$/b"), ("hh", "NG=F", "$/MMBtu"), ("ttf", "TTF=F", "€/MWh")]:
        try:
            tk = yf.Ticker(sym)
            h = tk.history(period="1d", interval="5m")
            if not len(h) or h["Close"].dropna().empty:
                h = tk.history(period="5d")
            daily = tk.history(period="1mo")
            if len(h) and not h["Close"].dropna().empty:
                v = float(h["Close"].dropna().iloc[-1])
                ts = h.index[-1].to_pydatetime().astimezone(dt.timezone.utc).isoformat()
                old = close_on_or_before(daily, T5) if len(daily) else None
                if key == "ttf":
                    if not eurusd:
                        raise ValueError("pas de taux EUR/USD pour convertir le TTF")
                    conv = lambda x: x * eurusd / 3.412   # €/MWh -> $/MMBtu (1 MWh = 3,412 MMBtu)
                    vu = conv(v)
                    prices["ttf"] = {"value": round(vu, 2), "display": f"{frn(vu)} $/MMBtu", "asof": ts,
                                     "source": f"ICE via Yahoo (~15 min) · {frn(v)} €/MWh"}
                    if old:
                        d5["prices"]["ttf"] = {"value": round(conv(old[0]), 2), "display": f"{frn(conv(old[0]))} $/MMBtu", "asof": old[1]}
                else:
                    prices[key] = {"value": round(v, 2), "display": f"{frn(v)} {unit}", "asof": ts, "source": "Yahoo Finance (~15 min)"}
                    if old:
                        d5["prices"][key] = {"value": round(old[0], 2), "display": f"{frn(old[0])} {unit}", "asof": old[1]}
        except Exception as e:
            print("yahoo", sym, "KO:", e)
    if prices:
        sources.append("Yahoo Finance")
except ImportError:
    print("yfinance absent : pip install yfinance")

# ---------- 2) EIA (officiel, J-1), secours ----------
k = os.environ.get("EIA_API_KEY")
if k:
    for key, series, unit in [("brent", "RBRTE", "$/b"), ("wti", "RWTC", "$/b"), ("hh", "RNGWHHD", "$/MMBtu")]:
        try:
            u = "https://api.eia.gov/v2/seriesid/" + series + "?" + urllib.parse.urlencode({"api_key": k, "length": 10})
            rows = get_json(u)["response"]["data"]
            v = float(rows[0]["value"])
            prices.setdefault(key, {"value": v, "display": f"{frn(v)} {unit}", "asof": rows[0]["period"], "source": "EIA (quotidien)"})
            prices[key]["eia_daily"] = v
            if key not in d5["prices"]:
                old = next((r for r in rows if parse_ts(r["period"]) <= T5), None)
                if old:
                    d5["prices"][key] = {"value": float(old["value"]), "display": f"{frn(float(old['value']))} {unit}", "asof": old["period"]}
        except Exception as e:
            print("EIA", series, "KO:", e)
    sources.append("EIA")

# ---------- 3) Stockage gaz UE (AGSI+) ----------
k = os.environ.get("AGSI_API_KEY")
if k:
    try:
        rows = get_json("https://agsi.gie.eu/api?country=EU&size=10", headers={"x-key": k})["data"]
        v = float(rows[0]["full"])
        prices["eu_storage"] = {"value": v, "display": f"{frn(v, 1)} %", "asof": rows[0]["gasDayStart"], "source": "AGSI+ (GIE)"}
        old = next((r for r in rows if parse_ts(r["gasDayStart"]) <= T5), None)
        if old:
            d5["prices"]["eu_storage"] = {"value": float(old["full"]), "display": f"{frn(float(old['full']), 1)} %", "asof": old["gasDayStart"]}
        sources.append("AGSI+")
    except Exception as e:
        print("AGSI KO:", e)

# ---------- 4) straits.live : trafic, risque, oléoducs, dépêches ----------
hormuz, chokepoints, pipelines, events, markets = {}, [], {}, [], []
MONTHS = {"January": "janv.", "February": "févr.", "March": "mars", "April": "avr.", "May": "mai", "June": "juin", "July": "juil.",
          "August": "août", "September": "sept.", "October": "oct.", "November": "nov.", "December": "déc."}


def fr_market(q):
    q = re.sub(r"\s*\(Before [^)]*\)", "", q or "")
    q = re.sub(r"^Strait of Hormuz traffic returns to normal by (.+)\?$", r"Trafic normal à Ormuz d'ici le \1", q)
    q = re.sub(r"^US announces end of Iranian blockade by (.+?)(, \d{4})?\?$", r"Fin du blocus US annoncée d'ici le \1", q)
    q = re.sub(r"^Will the 7-day moving average of transit calls through the Strait of Hormuz as reported by the IMF PortWatch be above 60 before (.+?)(, \d{4})?\?$",
               r"Transits Ormuz > 60/j (moy. 7 j) avant le \1", q)
    for en, fr in MONTHS.items():
        q = re.sub(en + r" (\d{1,2})\b", lambda m, fr=fr: f"{m.group(1)} {fr}", q).replace(en, fr)
    return q


try:
    S = get_json("https://straits.live/status")
    dtr = S.get("dailyTransits") or {}
    tr = S.get("transits") or {}
    ins = S.get("insurance") or {}
    gaps = S.get("aisGaps") or {}
    hidx = S.get("hormuzIndex") or {}
    hormuz = {
        "date": dtr.get("date") or tr.get("asOfDate"),
        "n": dtr.get("nTotal", tr.get("count")),
        "tankers": dtr.get("nTanker"),
        "baseline": dtr.get("preCrisisBaselineMedian") or tr.get("baseline"),
        "moving": S.get("aisConcurrentInZone"),
        "stranded_offshore": S.get("strandedOffshore"),
        "dark": gaps.get("count"),
        "dark_base7d": gaps.get("baseline7d"),
        "insurance_x": ins.get("multiple"),
        "clubs_out": len(ins.get("withdrawnClubs") or []),
        "crisis": (hidx.get("crisisPressure") or {}).get("value"),
        "escalation": (hidx.get("escalationProbability") or {}).get("value"),
        "carriers_stopped": sum(1 for c in (S.get("carrierSuspensions") or []) if c.get("hormuzPosture") == "stopped"),
        "asof": S.get("asOf"),
    }
    chokepoints = [{k2: c.get(k2) for k2 in ("key", "label", "date", "nTotal", "preCrisisBaselineMedian")} for c in (S.get("chokepoints") or [])]
    pipelines = {p["id"]: {"util": p.get("currentUtilizationPct"), "cap_bpd": p.get("capacityBpd"), "status": p.get("status"), "asof": p.get("authoredAt")}
                 for p in (S.get("pipelineBypass") or []) if p.get("id")}
    events = [{"t": e.get("occurredAt"), "title": e.get("title"), "type": e.get("type"), "sev": e.get("severity")} for e in (S.get("events") or [])[:10]]
    mk = [m for m in (S.get("predictionMarkets") or []) if re.search(r"hormuz|blockade", (m.get("question") or m.get("title") or ""), re.I)]
    mk.sort(key=lambda m: -(m.get("volumeUsd") or 0))
    markets = [{"q": fr_market(m.get("question") or m.get("title")), "p": m.get("probability"), "venue": m.get("venue")} for m in mk[:4]]
    if S.get("brent") and "brent" not in prices:
        prices["brent"] = {"value": S["brent"], "display": f"{frn(S['brent'])} $/b", "asof": S.get("oilAsOf"), "source": "straits.live"}
    if S.get("wti") and "wti" not in prices:
        prices["wti"] = {"value": S["wti"], "display": f"{frn(S['wti'])} $/b", "asof": S.get("oilAsOf"), "source": "straits.live"}
    spr = S.get("spr") or {}
    if spr.get("currentMbbl"):
        prices["spr"] = {"value": spr["currentMbbl"], "display": f"{frn(spr['currentMbbl'], 0)} Mb ({'+' if (spr.get('change4wMbbl') or 0) >= 0 else '−'}{frn(abs(spr.get('change4wMbbl') or 0), 1)} sur 4 sem.)",
                         "asof": spr.get("asOfDate"), "source": "EIA via straits.live"}
    sources.append("straits.live / IMF PortWatch")
except Exception as e:
    print("straits.live KO:", e)

# Série quotidienne des transits d'Ormuz (IMF PortWatch) pour la courbe et la valeur J-5
series = []
try:
    rows = list(csv.DictReader(io.StringIO(get_text("https://straits.live/data/transits.csv"))))
    rows = [{(k2 or "").strip().lower(): (v or "").strip() for k2, v in r.items()} for r in rows]
    if rows:
        cols = list(rows[0].keys())
        dk = next(c for c in cols if "date" in c)
        tk = next(c for c in cols if c in ("total", "ntotal", "n_total", "transits"))
        kk = next((c for c in cols if "tanker" in c), None)
        for r in rows:
            if r.get(tk):
                series.append([r[dk][:10], int(float(r[tk])), int(float(r[kk])) if kk and r.get(kk) else None])
        series.sort(key=lambda x: x[0])
except Exception as e:
    print("transits.csv KO:", e)
if series:
    if not hormuz.get("n"):
        hormuz.update({"date": series[-1][0], "n": series[-1][1], "tankers": series[-1][2]})
    last = parse_ts(hormuz.get("date") or series[-1][0])
    old = [r for r in series if parse_ts(r[0]) <= last - dt.timedelta(days=5)]
    if old:
        d5["hormuz"].update({"date": old[-1][0], "n": old[-1][1], "tankers": old[-1][2]})

# ---------- 5) Saisies manuelles ----------
if yaml and os.path.exists("manual.yml"):
    try:
        for key, m in (yaml.safe_load(open("manual.yml")) or {}).items():
            prices.setdefault(key, {"value": m.get("value"), "display": m.get("display"), "asof": m.get("asof"), "source": m.get("source", "saisie manuelle")})
    except Exception as e:
        print("manual.yml illisible :", e)

if "ttf" in prices and "jkm" in prices and prices["jkm"].get("value") is not None:
    sp = prices["ttf"]["value"] - prices["jkm"]["value"]
    prices["ttf_jkm"] = {"value": round(sp, 2), "display": f"{'+' if sp >= 0 else ''}{frn(sp)} $", "asof": now, "source": "calcul (JKM manuel)"}

# ---------- 6) Relais AIS (facultatif) ----------
vessels, transits24 = [], None
u = os.environ.get("AIS_RELAY_URL")
if u:
    try:
        d = get_json(u)
        vessels = d.get("vessels", [])
        transits24 = d.get("hormuz_transits_24h")
    except Exception as e:
        print("relais AIS KO:", e)

# ---------- 7) Historique propre + valeurs J-5 qui en dépendent ----------
for key in ("brent", "wti", "hh", "ttf", "eu_storage"):
    if key in prices and prices[key].get("value") is not None:
        push(key, prices[key]["value"])
for key in ("moving", "stranded_offshore", "dark", "insurance_x", "crisis"):
    push(key, hormuz.get(key))
if pipelines:
    push("pipelines", pipelines)

for key in ("brent", "wti", "hh", "ttf", "eu_storage"):
    if key not in d5["prices"] and key in prices:
        v = hist_at(key)
        if v is not None:
            disp = prices[key].get("display") or ""
            unit = disp.split(" ")[-1] if " " in disp else ""
            d5["prices"][key] = {"value": v, "display": f"{frn(v, 1 if key == 'eu_storage' else 2)} {unit}".strip(), "asof": T5.isoformat()}
for key in ("moving", "stranded_offshore", "dark", "insurance_x", "crisis"):
    v = hist_at(key)
    if v is not None:
        d5["hormuz"][key] = v
d5["pipelines"] = hist_at("pipelines")

json.dump({"updated": now, "sources": sources, "prices": prices, "d5": d5,
           "hormuz": hormuz, "hormuz_series": series[-60:], "chokepoints": chokepoints, "pipelines": pipelines,
           "events": events, "markets": markets,
           "vessels": vessels, "hormuz_transits_24h": transits24, "history": hist},
          open(OUT, "w"), ensure_ascii=False)
print("écrit", OUT, "·", len(prices), "prix :", ", ".join(sorted(prices)), "· ormuz :", hormuz.get("n"), "navires le", hormuz.get("date"),
      "· J-5 :", sorted(d5["prices"]), d5["hormuz"])
