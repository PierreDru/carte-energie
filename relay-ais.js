// Relais AIS local pour les deux cartes (pétrole / gaz).
// aisstream.io est gratuit mais interdit les connexions directes depuis un navigateur :
// ce script se connecte côté serveur et sert un instantané JSON que les cartes interrogent.
//
//   1. Clé gratuite : https://aisstream.io/account
//   2. npm install ws
//   3. AISSTREAM_API_KEY=xxxx node relay-ais.js
//   4. Ouvrir carte-petrole-ormuz.html ou carte-gaz-gnl.html (le panneau « Direct — AIS » se remplit)
//      ou passer l'URL du relais dans le panneau si le port change.

const http = require('http');
const WebSocket = require('ws');

const KEY = process.env.AISSTREAM_API_KEY;
const PORT = +(process.env.PORT || 8787);
const TTL_MS = 30 * 60 * 1000;               // on oublie un navire muet depuis 30 min
if (!KEY) { console.error('AISSTREAM_API_KEY manquante'); process.exit(1); }

// Zones suivies : Ormuz + Golfe d'Oman, Golfe (Qatar → Ras Tanura), Bab el-Mandeb
const BOXES = [
  [[27.5, 55.0], [23.5, 59.5]],
  [[27.5, 48.5], [23.5, 55.0]],
  [[15.0, 41.0], [11.0, 45.0]],
];

const vessels = new Map(); // mmsi -> {mmsi,name,lat,lon,sog,cog,nav,type,t}
// Compteur de transits d'Ormuz : franchissements d'une ligne virtuelle (lon 56.5°E entre 26.0°N et 27.0°N)
const GATE = { lon: 56.5, lat0: 26.0, lat1: 27.0 };
const transits = [];              // {t, mmsi, dir: 'out'|'in'}
const lastSide = new Map();       // mmsi -> 'W' | 'E'
function gate(v) {
  if (v.lat < GATE.lat0 || v.lat > GATE.lat1) return;
  const side = v.lon < GATE.lon ? 'W' : 'E';
  const prev = lastSide.get(v.mmsi);
  if (prev && prev !== side) transits.push({ t: Date.now(), mmsi: v.mmsi, dir: side === 'E' ? 'out' : 'in', type: v.type });
  lastSide.set(v.mmsi, side);
}
let updated = null, backoff = 1000;

function connect() {
  const ws = new WebSocket('wss://stream.aisstream.io/v0/stream', { perMessageDeflate: true });
  ws.on('open', () => {
    backoff = 1000;
    ws.send(JSON.stringify({ APIKey: KEY, BoundingBoxes: BOXES, FilterMessageTypes: ['PositionReport', 'ShipStaticData'] }));
    console.log('aisstream connecté');
  });
  ws.on('message', (buf) => {
    let m; try { m = JSON.parse(buf.toString()); } catch { return; }
    const meta = m.MetaData; if (!meta) return;
    const v = vessels.get(meta.MMSI) || { mmsi: meta.MMSI };
    v.name = (meta.ShipName || v.name || '').trim();
    if (m.MessageType === 'PositionReport') {
      const p = m.Message.PositionReport;
      v.lat = p.Latitude; v.lon = p.Longitude; v.sog = p.Sog; v.cog = p.Cog; v.nav = p.NavigationalStatus;
      gate(v);
    } else if (m.MessageType === 'ShipStaticData') {
      const s = m.Message.ShipStaticData;
      v.type = s.Type; if (s.Destination) v.dest = s.Destination.trim();
      if (v.lat == null && meta.latitude != null) { v.lat = meta.latitude; v.lon = meta.longitude; }
    }
    v.t = Date.now(); updated = new Date().toISOString();
    vessels.set(meta.MMSI, v);
  });
  ws.on('close', () => { console.log('déconnecté, reconnexion dans', backoff, 'ms'); setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 60000); });
  ws.on('error', (e) => console.error('ws:', e.message));
}
connect();

http.createServer((req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  if (req.url.startsWith('/live.json')) {
    const now = Date.now();
    const list = [...vessels.values()].filter(v => v.lat != null && now - v.t < TTL_MS)
      .map(({ t, ...v }) => v);
    res.setHeader('Content-Type', 'application/json');
    const day = now - 24 * 3600 * 1000;
    while (transits.length && transits[0].t < day) transits.shift();
    const tank = transits.filter(x => x.type >= 80 && x.type <= 89);
    res.end(JSON.stringify({ updated, vessels: list, source: 'aisstream.io',
      hormuz_transits_24h: tank.length, hormuz_transits_24h_all: transits.length,
      hormuz_out_24h: tank.filter(x => x.dir === 'out').length }));
  } else { res.statusCode = 404; res.end(); }
}).listen(PORT, () => console.log(`live.json sur http://localhost:${PORT}/live.json`));
