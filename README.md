# live-kit — mise à jour automatique des deux cartes

Les cartes lisent un fichier `live.json` (même dossier qu'elles, ou URL saisie dans le panneau « Direct — AIS »).
Ce kit le produit de deux façons complémentaires.

## A. Sans serveur (prix, stockage) — GitHub Actions + GitHub Pages
1. Créer un dépôt GitHub public, y copier : `carte-petrole-ormuz.html`, `carte-gaz-gnl.html`,
   `fetch_live.py`, `manual.yml`, `requirements.txt`, `.github/workflows/live.yml`.
2. Settings → Pages → Source : branche `main`, dossier `/`.
3. Clés gratuites → Settings → Secrets → Actions :
   - `EIA_API_KEY` : https://www.eia.gov/opendata/register.php
   - `AGSI_API_KEY` : https://agsi.gie.eu/account
4. Actions → live-data → Run workflow. Un `live.json` est commité toutes les 15 min.
5. Ouvrir `https://<user>.github.io/<repo>/carte-petrole-ormuz.html` : les prix passent en « direct ».

## B. Avec un petit serveur (navires AIS + compteur de transits d'Ormuz)
1. Clé gratuite : https://aisstream.io/account
2. `npm install ws` puis `AISSTREAM_API_KEY=xxx node relay-ais.js` (port 8787).
3. Pour que GitHub Actions y accède, exposer le relais publiquement (Fly.io, Railway, Cloudflare Tunnel)
   et renseigner le secret `AIS_RELAY_URL` (ex. `https://mon-relais.fly.dev/live.json`).
   Sans exposition publique : ouvrir les cartes en local et saisir `http://localhost:8787/live.json` dans le panneau.

## Format de live.json
{ "updated": ISO, "prices": { "brent": {"value","display","asof","source"}, "wti", "hh", "ttf", "jkm", "eu_storage", … },
  "vessels": [ {mmsi,name,lat,lon,sog,cog,nav,type} ], "hormuz_transits_24h": n, "history": { "brent": [[ISO, v], …] } }
Clés reconnues par les cartes : brent, wti, oman, diesel_eu, vlcc, stocks_exchina, spr, ttf, jkm, hh, ttf_jkm, eu_storage.
