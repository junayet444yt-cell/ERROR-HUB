/* ERROR HUB - usage stats + admin panel (Cloudflare Worker + D1 database).
   Receives anonymous counts from the apps (POST /t) and shows them on /admin (password protected).
   Needed settings:  binding DB (D1) | secret ADMIN_PASSWORD | secret INGEST_KEY | optional TZ_OFFSET_HOURS (default 6) */

const EVENTS = new Set(["hb", "open", "fetch_ok", "fetch_fail", "download"]);
const PLATS = new Set(["exe", "apk", "web"]);
let ready = false;

async function setup(db) {
  if (ready) return;
  await db.batch([
    db.prepare("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, first_seen INTEGER, last_seen INTEGER, plat TEXT, ver TEXT)"),
    db.prepare("CREATE TABLE IF NOT EXISTS active (day TEXT, id TEXT, PRIMARY KEY (day, id))"),
    db.prepare("CREATE TABLE IF NOT EXISTS events (ts INTEGER, day TEXT, id TEXT, ev TEXT, plat TEXT, host TEXT, err TEXT)"),
    db.prepare("CREATE INDEX IF NOT EXISTS events_day ON events (day, ev)"),
  ]);
  ready = true;
}

const clean = (s, max, re) => String(s == null ? "" : s).toLowerCase().replace(re, "").slice(0, max);
const dayOf = (ts, env) => new Date(ts + (Number(env.TZ_OFFSET_HOURS ?? 6) || 0) * 3600e3).toISOString().slice(0, 10);

function same(a, b) {            // constant-time string compare
  a = String(a); b = String(b);
  let d = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i++) d |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return d === 0;
}

async function ingest(request, env) {
  const raw = await request.text();
  if (raw.length > 1024) return new Response("too big", { status: 413 });
  let b; try { b = JSON.parse(raw); } catch { return new Response("bad", { status: 400 }); }
  if (!env.INGEST_KEY || !same(b.k || "", env.INGEST_KEY)) return new Response("no", { status: 403 });
  const id = clean(b.id, 64, /[^a-f0-9]/g), ev = String(b.ev || ""), plat = String(b.p || "");
  if (id.length < 8 || !EVENTS.has(ev) || !PLATS.has(plat)) return new Response("bad", { status: 400 });
  const ver = clean(b.v, 12, /[^a-z0-9._-]/g), host = clean(b.h, 80, /[^a-z0-9.-]/g), err = clean(b.e, 30, /[^a-z0-9_ -]/g);
  await setup(env.DB);
  const now = Date.now(), day = dayOf(now, env), db = env.DB;
  const st = [
    db.prepare("INSERT INTO users (id, first_seen, last_seen, plat, ver) VALUES (?1, ?2, ?2, ?3, ?4) ON CONFLICT(id) DO UPDATE SET last_seen = ?2, plat = ?3, ver = ?4").bind(id, now, plat, ver),
    db.prepare("INSERT OR IGNORE INTO active (day, id) VALUES (?1, ?2)").bind(day, id),
  ];
  if (ev !== "hb") st.push(db.prepare("INSERT INTO events (ts, day, id, ev, plat, host, err) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)").bind(now, day, id, ev, plat, host, err));
  await db.batch(st);
  return new Response(null, { status: 204 });
}

async function stats(env) {
  await setup(env.DB);
  const db = env.DB, now = Date.now(), today = dayOf(now, env);
  const ago = n => dayOf(now - n * 864e5, env);
  const one = async (sql, ...p) => (await db.prepare(sql).bind(...p).first()) || {};
  const all = async (sql, ...p) => (await db.prepare(sql).bind(...p).all()).results || [];
  const since30 = ago(29);
  const [users, online, dauT, dauY, a7, a30, newToday, plat, series, evSeries, totals, hosts, fails, vers, recent] = await Promise.all([
    one("SELECT COUNT(*) c FROM users"),
    one("SELECT COUNT(*) c FROM users WHERE last_seen > ?", now - 15 * 60e3),
    one("SELECT COUNT(*) c FROM active WHERE day = ?", today),
    one("SELECT COUNT(*) c FROM active WHERE day = ?", ago(1)),
    one("SELECT COUNT(DISTINCT id) c FROM active WHERE day >= ?", ago(6)),
    one("SELECT COUNT(DISTINCT id) c FROM active WHERE day >= ?", since30),
    one("SELECT COUNT(*) c FROM users WHERE first_seen >= ?", Date.parse(today + "T00:00:00Z") - (Number(env.TZ_OFFSET_HOURS ?? 6) || 0) * 3600e3),
    all("SELECT plat, COUNT(*) c FROM users GROUP BY plat"),
    all("SELECT day, COUNT(*) c FROM active WHERE day >= ? GROUP BY day", since30),
    all("SELECT day, ev, COUNT(*) c FROM events WHERE day >= ? AND ev IN ('fetch_ok','fetch_fail','download') GROUP BY day, ev", since30),
    all("SELECT ev, COUNT(*) c FROM events GROUP BY ev"),
    all("SELECT host, SUM(ev='fetch_ok') ok, SUM(ev='fetch_fail') fail, SUM(ev='download') dl FROM events WHERE day >= ? AND host != '' GROUP BY host ORDER BY SUM(ev IN ('fetch_ok','fetch_fail')) DESC LIMIT 15", since30),
    all("SELECT err, COUNT(*) c FROM events WHERE ev = 'fetch_fail' AND day >= ? GROUP BY err ORDER BY c DESC LIMIT 10", since30),
    all("SELECT ver, COUNT(*) c FROM users GROUP BY ver ORDER BY c DESC LIMIT 8"),
    all("SELECT ts, ev, plat, host, err FROM events ORDER BY ts DESC LIMIT 25"),
  ]);
  return { today, tz: Number(env.TZ_OFFSET_HOURS ?? 6) || 0, users: users.c, online: online.c, dauToday: dauT.c, dauYesterday: dauY.c, active7: a7.c, active30: a30.c,
           newToday: newToday.c, plat, series, evSeries, totals, hosts, fails, vers, recent };
}

export default {
  async fetch(request, env) {
    const u = new URL(request.url);
    try {
      if (u.pathname === "/t" && request.method === "POST") return await ingest(request, env);
      if (u.pathname === "/admin") return new Response(PAGE, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store",
          "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'" } });
      if (u.pathname === "/admin/api") {
        const pw = (request.headers.get("authorization") || "").replace(/^Bearer /, "");
        if (!env.ADMIN_PASSWORD || !same(pw, env.ADMIN_PASSWORD)) { await new Promise(r => setTimeout(r, 700)); return new Response("wrong password", { status: 401 }); }
        return new Response(JSON.stringify(await stats(env)), { headers: { "content-type": "application/json", "cache-control": "no-store" } });
      }
      return new Response("ERROR HUB stats - ok", { status: 200 });
    } catch (e) {
      return new Response("server error: " + String(e.message || e).slice(0, 200), { status: 500 });
    }
  },
};

const PAGE = `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>ERROR HUB - Admin</title><style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#0d0206;color:#fff1ea;font:15px system-ui,sans-serif;padding:18px;max-width:980px;margin:auto}
h1{font-size:26px;margin:4px 0 14px;background:linear-gradient(90deg,#ff2a4a,#ff9900,#ffd54a);-webkit-background-clip:text;background-clip:text;color:transparent}
h2{font-size:15px;color:#ffd54a;margin:26px 0 8px;letter-spacing:.04em;text-transform:uppercase}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.c{background:#1a0a10;border:1px solid #4a1426;border-radius:12px;padding:12px}.c b{display:block;font-size:28px;color:#fff}.c span{color:#e6a58f;font-size:13px}
table{width:100%;border-collapse:collapse;background:#1a0a10;border:1px solid #4a1426;border-radius:12px;overflow:hidden}
td,th{padding:7px 10px;text-align:left;border-bottom:1px solid #2c0d19;font-size:14px}th{color:#e6a58f;font-weight:600}
.bars{display:flex;align-items:flex-end;gap:3px;height:120px;background:#1a0a10;border:1px solid #4a1426;border-radius:12px;padding:10px 10px 4px}
.bar{flex:1;background:linear-gradient(#ff9900,#ff2a4a);border-radius:3px 3px 0 0;min-height:2px;position:relative}
.lbl{display:flex;gap:3px;padding:2px 10px;color:#a37;font-size:10px}.lbl i{flex:1;text-align:center;font-style:normal;overflow:hidden}
input,button{font:inherit;padding:10px 14px;border-radius:10px;border:2px solid #4a1426;background:#1a0a10;color:#fff}button{background:linear-gradient(90deg,#ff2a4a,#ff9900);border:0;cursor:pointer;font-weight:700}
#login{display:flex;gap:8px;margin:30px 0}#err{color:#ff7a8a}.sm{color:#a37;font-size:12px}.ok{color:#7dff9b}.bad{color:#ff7a8a}.scroll{overflow-x:auto}
</style></head><body><h1>ERROR HUB &middot; Admin</h1>
<div id="login"><input id="pw" type="password" placeholder="Admin password" autocomplete="current-password"><button id="go">Open</button></div><div id="err"></div><div id="out" hidden></div>
<script>
const $=id=>document.getElementById(id);let pw=sessionStorage.getItem('pw')||'',timer;
function el(t,c,x){const e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
function cards(list){const w=el('div','cards');list.forEach(([a,b])=>{const c=el('div','c');c.append(el('b','',a),el('span','',b));w.append(c)});return w}
function table(head,rows){const t=el('table'),h=el('tr');head.forEach(x=>h.append(el('th','',x)));t.append(h);rows.forEach(r=>{const tr=el('tr');r.forEach(x=>{const td=el('td','',x.t!=null?x.t:x);if(x.c)td.className=x.c;tr.append(td)});t.append(tr)});const s=el('div','scroll');s.append(t);return s}
function bars(days,map,title){const mx=Math.max(1,...days.map(d=>map[d]||0)),w=el('div'),b=el('div','bars'),l=el('div','lbl');
  days.forEach(d=>{const v=map[d]||0,x=el('div','bar');x.style.height=(v/mx*100)+'%';x.title=d+': '+v;b.append(x);l.append(el('i','',d.slice(8)))});w.append(el('h2','',title),b,l);return w}
function ago(ts){const s=Math.max(0,(Date.now()-ts)/1000|0);return s<60?s+'s':s<3600?(s/60|0)+'m':s<86400?(s/3600|0)+'h':(s/86400|0)+'d'}
async function load(){
  const r=await fetch('/admin/api',{headers:{authorization:'Bearer '+pw}});
  if(r.status===401){sessionStorage.removeItem('pw');$('err').textContent='Wrong password';$('login').hidden=false;$('out').hidden=true;clearInterval(timer);return}
  if(!r.ok){$('err').textContent='Error: '+await r.text();return}
  const d=await r.json();$('err').textContent='';$('login').hidden=true;$('out').hidden=false;sessionStorage.setItem('pw',pw);
  const tot={};d.totals.forEach(x=>tot[x.ev]=x.c);const o=$('out');o.textContent='';
  o.append(cards([[d.online,'online now (15 min)'],[d.dauToday,'active today'],[d.dauYesterday,'active yesterday'],[d.active7,'active 7 days'],[d.active30,'active 30 days'],[d.users,'total installs'],[d.newToday,'new today'],
    [tot.download||0,'downloads saved (all time)'],[tot.fetch_ok||0,'videos fetched OK'],[tot.fetch_fail||0,'fetch failed']]));
  const days=[];for(let i=29;i>=0;i--){days.push(new Date(Date.parse(d.today+'T12:00:00Z')-i*864e5).toISOString().slice(0,10))}
  const act={},dl={},ok={},fl={};d.series.forEach(x=>act[x.day]=x.c);d.evSeries.forEach(x=>{({download:dl,fetch_ok:ok,fetch_fail:fl})[x.ev][x.day]=x.c});
  o.append(bars(days,act,'Active users per day (30 days)'),bars(days,dl,'Downloads saved per day'),bars(days,ok,'Videos fetched per day'));
  o.append(el('h2','','Users by platform'),table(['Platform','Installs'],d.plat.map(x=>[x.plat||'?',x.c])));
  o.append(el('h2','','App versions'),table(['Version','Installs'],d.vers.map(x=>[x.ver||'?',x.c])));
  o.append(el('h2','','Sites (30 days)'),table(['Site','Fetched OK','Failed','Saved'],d.hosts.map(x=>[x.host,{t:x.ok,c:'ok'},{t:x.fail,c:x.fail?'bad':''},x.dl])));
  o.append(el('h2','','Why fetches fail (30 days)'),table(['Reason','Count'],d.fails.map(x=>[x.err||'other',x.c])));
  o.append(el('h2','','Latest events'),table(['When','Event','Platform','Site','Note'],d.recent.map(x=>[ago(x.ts)+' ago',x.ev,x.plat,x.host,x.err])));
  o.append(el('p','sm','Day boundary: UTC'+(d.tz>=0?'+':'')+d.tz+'. Anonymous data only: random install id, site name, counts. Refreshes every 30 s.'));
}
function start(){if(!pw)return;load();clearInterval(timer);timer=setInterval(load,30000)}
$('go').onclick=()=>{pw=$('pw').value;start()};$('pw').addEventListener('keydown',e=>{if(e.key==='Enter'){pw=$('pw').value;start()}});start();
</script></body></html>`;
