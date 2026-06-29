#!/usr/bin/env python3
"""Build a self-contained HTML floor-plan viewer from the DCIM floor-plan JSON.

Reads topologies/dual_dc_enterprise_floorplan.json (the DCIM asset DB) and
inlines it into a single HTML file so the viewer works offline from file://
(no web server, no fetch/CORS). Renders:

  * 2D floor plan  -- per-room canvas: racks at floor_x/floor_y, hot/cold aisle
                      bands, click a rack for its RU elevation (the data shown in
                      docs/dual_dc_enterprise_layout.md).
  * 3D view        -- Three.js (CDN): rack boxes on the room floor, orbit camera.
  * Heatmap toggle -- IDW spatial interpolation (heatmap_design.md sec 7.3) of a
                      per-rack metric, colored with the sec 7.4 bands.
                      Power Density (kW/rack) is REAL data from the asset file.
                      Inlet-thermal is offered as an ESTIMATE only (no live temps
                      exist in a static asset file -- per the design doc thermal
                      must be polled live over Redfish/SNMP).

Usage (canonical output is the copy the web UI serves from webui/public):
    python tools/build_floorplan_viewer.py \
        topologies/dual_dc_enterprise_floorplan.json \
        webui/public/floorplan_viewer.html
"""
from __future__ import annotations

import json
import sys

# ---- HTML/CSS/JS template, split so the JSON is concatenated (no brace escaping) ----

HEAD = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DCIM Floor-Plan Viewer</title>
<style>
  :root{ --bg:#0e1116; --panel:#161b22; --panel2:#1c232d; --line:#2a3340;
         --fg:#e6edf3; --muted:#8b97a7; --accent:#4f9dff; }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);
    font:13px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
  #app{display:grid;grid-template-rows:auto 1fr;height:100%}
  header{display:flex;gap:14px;align-items:center;flex-wrap:wrap;
    padding:8px 14px;background:var(--panel);border-bottom:1px solid var(--line)}
  header h1{font-size:15px;margin:0 12px 0 0;font-weight:600}
  header label{color:var(--muted);font-size:11px;text-transform:uppercase;
    letter-spacing:.04em;margin-right:4px}
  select,button{background:var(--panel2);color:var(--fg);border:1px solid var(--line);
    border-radius:6px;padding:5px 9px;font-size:12px;cursor:pointer}
  button.seg{border-radius:0}
  .seg-wrap{display:inline-flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
  .seg-wrap button{border:0;border-right:1px solid var(--line)}
  .seg-wrap button:last-child{border-right:0}
  button.on{background:var(--accent);color:#08121f;font-weight:600}
  button#heat.on{background:#e0683c;color:#1a0a04}
  main{display:grid;grid-template-columns:230px 1fr 0;min-height:0;transition:grid-template-columns .15s}
  main.detail{grid-template-columns:230px 1fr 300px}
  aside{background:var(--panel);border-right:1px solid var(--line);overflow:auto;padding:10px 12px}
  aside h2{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);
    margin:14px 0 6px}
  aside h2:first-child{margin-top:0}
  .legrow{display:flex;align-items:center;gap:7px;padding:2px 0;font-size:12px}
  .sw{width:11px;height:11px;border-radius:2px;flex:0 0 auto}
  .legrow .ct{margin-left:auto;color:var(--muted);font-variant-numeric:tabular-nums}
  #stage{position:relative;min-width:0;overflow:hidden;background:
    radial-gradient(circle at 50% 30%,#11161d,#0a0d12)}
  #c2d{position:absolute;inset:0}
  #c3d{position:absolute;inset:0;display:none}
  #tip{position:absolute;pointer-events:none;background:#000c;border:1px solid var(--line);
    border-radius:6px;padding:6px 8px;font-size:11px;display:none;z-index:5;max-width:240px}
  #hlegend{position:absolute;left:12px;bottom:12px;background:#0b0e13f2;border:1px solid var(--line);
    border-radius:10px;padding:9px 12px 7px;font-size:11px;display:none;z-index:4;
    box-shadow:0 6px 20px #0007;backdrop-filter:blur(3px)}
  #hlegend .ttl{font-weight:600;letter-spacing:.02em;margin-bottom:6px}
  #hlegend .ttl .tag{font-weight:500;font-size:10px;padding:1px 6px;border-radius:10px;margin-left:6px}
  #hlegend .ttl .tag.live{background:#10351f;color:#56d98a;border:1px solid #1d6b3d}
  #hlegend .ttl .tag.est{background:#3a2a0b;color:#ffd98a;border:1px solid #6b5418}
  #hlegend .barwrap{position:relative;width:200px;height:26px}
  #hlegend .bar{height:11px;width:200px;border-radius:3px;
    box-shadow:inset 0 0 0 1px #ffffff14}
  #hlegend .tick{position:absolute;top:12px;transform:translateX(-50%);color:var(--muted);
    font-size:9px;font-variant-numeric:tabular-nums;white-space:nowrap}
  #hlegend .tick::before{content:'';position:absolute;left:50%;top:-12px;width:1px;height:5px;
    background:#ffffff55}
  #hlegend .rng{color:var(--muted);font-size:10px;margin-top:3px}
  #hlegend .rng b{color:var(--fg);font-variant-numeric:tabular-nums}
  #note{position:absolute;top:10px;left:50%;transform:translateX(-50%);background:#3a2a0bdd;
    border:1px solid #6b5418;color:#ffd98a;padding:5px 12px;border-radius:20px;font-size:11px;
    display:none;z-index:6}
  #nav3d{position:absolute;right:14px;bottom:14px;z-index:7;display:none;flex-direction:column;gap:5px;
    align-items:center;background:#0b0e13e6;border:1px solid var(--line);border-radius:10px;padding:8px;
    box-shadow:0 6px 20px #0008}
  #nav3d .nrow{display:flex;gap:4px}
  #nav3d button{min-width:30px;height:26px;padding:0 6px;font-size:12px;line-height:1;
    display:flex;align-items:center;justify-content:center}
  #nav3d button:hover{background:var(--accent);color:#08121f}
  #nav3d .dpad{display:grid;grid-template-columns:repeat(3,30px);grid-template-rows:repeat(3,26px);gap:4px}
  #nav3d .dpad button{width:30px}
  #nav3d .dpad .up{grid-column:2;grid-row:1} #nav3d .dpad .left{grid-column:1;grid-row:2}
  #nav3d .dpad .ctr{grid-column:2;grid-row:2} #nav3d .dpad .right{grid-column:3;grid-row:2}
  #nav3d .dpad .down{grid-column:2;grid-row:3}
  #nav3dhint{position:absolute;top:12px;left:14px;z-index:7;display:none;max-width:46%;
    background:#0b0e13d9;border:1px solid var(--line);border-radius:8px;padding:5px 9px;
    font-size:10px;color:var(--muted)}
  #detail{background:var(--panel);border-left:1px solid var(--line);overflow:auto;padding:0}
  #detail header{position:sticky;top:0;background:var(--panel2);border-bottom:1px solid var(--line)}
  #detail h3{margin:0;font-size:13px}
  #detail .close{margin-left:auto}
  .ru{display:flex;gap:8px;align-items:center;padding:3px 12px;border-bottom:1px solid #1d2531;font-size:12px}
  .ru .u{width:34px;color:var(--muted);text-align:right;font-variant-numeric:tabular-nums;flex:0 0 auto}
  .ru .nm{font-weight:600}
  .ru .mt{color:var(--muted);font-size:11px}
  .ru .pw{margin-left:auto;color:var(--muted);font-variant-numeric:tabular-nums;flex:0 0 auto}
  .meta{padding:10px 12px;color:var(--muted);font-size:11px;border-bottom:1px solid var(--line)}
  .meta b{color:var(--fg)}
  code{background:#0b0e13;padding:1px 5px;border-radius:4px}
</style>
</head>
<body>
<div id="app">
  <header>
    <h1>DCIM Floor-Plan</h1>
    <span><label>DC</label><select id="dc"></select></span>
    <span><label>Room</label><select id="room"></select></span>
    <span class="seg-wrap"><button id="v2d" class="seg on">2D</button><button id="v3d" class="seg">3D</button></span>
    <button id="heat">Heatmap: off</button>
    <button id="cut" title="3D: see into the raised-floor plenum (underfloor sensors)">Cutaway: off</button>
    <span id="metricwrap" style="display:none"><label>Metric</label><select id="metric">
      <option value="power">Power density (kW)</option>
      <option value="capacity">Capacity (RU %)</option>
      <option value="inlet">Inlet thermal (&deg;C)</option>
      <option value="exhaust">Exhaust thermal (&deg;C)</option>
      <option value="humidity">Humidity (%RH)</option>
    </select></span>
    <span><label id="livelbl">Live</label><input id="api" value="http://localhost:8000/api" spellcheck="false"
      style="width:150px;font-size:11px"><button id="live">off</button>
      <span id="lstat" style="color:var(--muted);font-size:11px"></span></span>
  </header>
  <main id="main">
    <aside id="side"></aside>
    <div id="stage">
      <canvas id="c2d"></canvas>
      <div id="c3d"></div>
      <div id="tip"></div>
      <div id="note"></div>
      <div id="hlegend"><div class="ttl"></div><div class="barwrap"><div class="bar"></div><div class="ticks"></div></div><div class="rng"></div></div>
      <div id="nav3d">
        <div class="nrow"><button data-v="top" title="Top view">Top</button><button data-v="iso" title="Isometric">Iso</button><button data-v="front" title="Front view">Front</button></div>
        <div class="dpad">
          <button class="up" data-p="u" title="Pan up (W / Up)">&#9650;</button>
          <button class="left" data-p="l" title="Pan left (A / Left)">&#9664;</button>
          <button class="ctr" data-p="c" title="Reset view (R)">&#8982;</button>
          <button class="right" data-p="r" title="Pan right (D / Right)">&#9654;</button>
          <button class="down" data-p="d" title="Pan down (S / Down)">&#9660;</button>
        </div>
        <div class="nrow"><button data-z="out" title="Zoom out (-)">&#65293;</button><button data-p="fall" title="Lower (E)">&#10515;</button><button data-p="rise" title="Raise (Q)">&#10514;</button><button data-z="in" title="Zoom in (+)">&#65291;</button></div>
      </div>
      <div id="nav3dhint">Drag = orbit &middot; right-drag / buttons = pan &middot; scroll = zoom &middot; WASD / arrows pan &middot; Q/E raise-lower &middot; R reset</div>
    </div>
    <div id="detail"></div>
  </main>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
<script>
const FLOORPLAN_EMBEDDED = """

TAIL = r""";
/* ===================== data indexing (rebuilt on live refresh) ===================== */
let FLOORPLAN = FLOORPLAN_EMBEDDED;
let FP, ROOMS, RACKS, DEVS, FOOT, devByRack = {}, rackById = {}, racksByRoom = {}, DCs = [];
const TYPE = {
  router:['router','#ff66b3'], firewall:['firewall','#ef4444'], load_balancer:['LB','#fb923c'],
  switch:['switch','#3b82f6'], server:['server','#4f9dff'], oob_switch:['OOB','#7aa2ff'],
  sensor:['sensor','#34d399'], generator:['gen','#fbbf24'], ups:['UPS','#fcd34d'],
  rpp:['RPP','#fb923c'], pdu:['PDU','#f59e0b'], energy_monitor:['energy','#a3c644'],
  crah:['CRAH','#38bdf8'], chiller:['chiller','#22d3d3'], pump:['pump','#2dd4bf'],
  cooling_tower:['cool twr','#2aa198'], valve:['valve','#7dd3c8'], cdu:['CDU','#5ead9e']
};
const EMO = {router:'R',firewall:'FW',load_balancer:'LB',switch:'SW',server:'SRV',oob_switch:'OOB',
  sensor:'SEN',generator:'GEN',ups:'UPS',rpp:'RPP',pdu:'PDU',energy_monitor:'EM',crah:'CRAH',
  chiller:'CHL',pump:'PMP',cooling_tower:'CT',valve:'VLV',cdu:'CDU'};
function tcolor(t){ return (TYPE[t]||['?','#888'])[1]; }

function roomKey(r){ return r.datacenter + ' / ' + r.room; }
function roomGeom(dc, room){ return ROOMS[dc + '/' + room] || {width_m:6,depth_m:6,aisles:[]}; }

// (Re)build every data index from a floor-plan document — called once for the
// embedded snapshot and again whenever a live /floorplan refresh arrives.
function applyFloorplan(data){
  FLOORPLAN = data || FLOORPLAN_EMBEDDED;
  FP = FLOORPLAN.floorplan; ROOMS = FP.rooms; RACKS = FLOORPLAN.racks || []; DEVS = FLOORPLAN.devices || [];
  FOOT = FP.rack_footprint || {width:0.6, depth:1.2};
  devByRack = {};
  for(const d of DEVS){ (devByRack[d.rack_id]=devByRack[d.rack_id]||[]).push(d); }
  for(const k in devByRack){ devByRack[k].sort((a,b)=>(b.rack_unit||0)-(a.rack_unit||0)); }
  rackById = {}; for(const r of RACKS) rackById[r.rack_id]=r;
  racksByRoom = {};
  for(const r of RACKS){ (racksByRoom[roomKey(r)]=racksByRoom[roomKey(r)]||[]).push(r); }
  DCs = [...new Set(RACKS.map(r=>r.datacenter))].sort();
}
applyFloorplan(FLOORPLAN_EMBEDDED);

/* ===================== state ===================== */
const S = { dc:null, room:null, view:'2d', heat:false, metric:'power', sel:null, selDev:null, cut:false };
const $ = s=>document.querySelector(s);

function roomsFor(dc){ return [...new Set(RACKS.filter(r=>r.datacenter===dc).map(r=>r.room))]; }

function fillSelect(el, items, val){ el.innerHTML=''; for(const it of items){
  const o=document.createElement('option'); o.value=it; o.textContent=it; el.appendChild(o);} if(val) el.value=val; }

/* ===================== live telemetry (GET /devices) ===================== */
// Real-DCIM pattern: poll device telemetry, JOIN to static placement by identity.
// When embedded in the simulator web UI, the host passes the API base + auth
// token via the URL hash (#api=...&token=...&live=1); hash never hits the server.
const HASH = new URLSearchParams(location.hash.slice(1));
const AUTH_TOKEN = HASH.get('token') || '';
const LIVE = { on:false, ok:false, byId:{}, byName:{}, timer:null, count:0 };
// Re-fill the DC/room pickers after the structure changes, keeping the current
// selection when it still exists (so a live refresh doesn't snap the view away).
function refreshSelectors(){
  if(!DCs.includes(S.dc)) S.dc = DCs[0];
  fillSelect($('#dc'), DCs, S.dc);
  const rs = roomsFor(S.dc);
  if(!rs.includes(S.room)) S.room = rs[0];
  fillSelect($('#room'), rs, S.room);
}
async function pollLive(){
  const base=$('#api').value.replace(/\/+$/,'');
  const headers = AUTH_TOKEN ? {Authorization:'Bearer '+AUTH_TOKEN} : {};
  // 1. Refresh the floor-plan STRUCTURE from the live simulator so racks the
  // fleet engine added after load (new rows, new halls) appear; fall back to the
  // embedded snapshot if the API is unreachable.
  try{
    const fr=await fetch(base+'/floorplan',{cache:'no-store',headers});
    if(fr.ok){ applyFloorplan(await fr.json()); refreshSelectors(); }
  }catch(e){ /* keep the last good structure */ }
  // 2. Overlay live telemetry (joined to placement by device identity).
  try{
    const res=await fetch(base+'/devices',{cache:'no-store',headers});
    if(!res.ok) throw new Error('HTTP '+res.status);
    const j=await res.json(); const list=j.devices||j; LIVE.byId={}; LIVE.byName={};
    for(const d of list){ LIVE.byId[d.id]=d; if(d.name) LIVE.byName[d.name]=d; }
    LIVE.ok=true; LIVE.count=list.length;
    setLstat('connected - '+list.length+' devices', '#34d399');
  }catch(e){ LIVE.ok=false;
    setLstat('offline ('+e.message+') - start the simulator API', '#f59e0b'); }
  render();
}
function setLstat(t,c){ const el=$('#lstat'); el.textContent='● '+t; el.style.color=c; }
function liveOf(d){ return LIVE.byId[d.id] || LIVE.byName[d.name] || null; }
// per-rack aggregation over LIVE readings; excludes null/0 (sec 7.1 "no reading").
// `fields` may be a list: per device, take the first present value (e.g. a PDU
// reports pdu_temperature, a server reports inlet_temp) -- per-rack inlet now
// rides the PDU probe (Option B) plus any reference sensor.
function aggLive(rid, fields, agg){ if(typeof fields==='string') fields=[fields];
  const vals=[];
  for(const d of (devByRack[rid]||[])){ const L=liveOf(d); if(!L) continue;
    let v=null; for(const f of fields){ const x=L[f]; if(x!=null && x!==0){ v=x; break; } }
    if(v==null) continue; vals.push(v); }
  if(!vals.length) return null;
  if(agg==='max') return Math.max(...vals);
  if(agg==='sum') return vals.reduce((a,b)=>a+b,0);
  return vals.reduce((a,b)=>a+b,0)/vals.length;   // mean
}

/* ===================== metrics (heatmap_design sec 7.1 / 7.4) ===================== */
function occupiedU(rid){ return (devByRack[rid]||[]).filter(d=>(d.rack_unit||0)>=1).length; }
function rackKW(r){ return (r.it_power_draw_w||0)/1000; }
// Offline inlet ESTIMATE on an ABSOLUTE scale (not room-relative): a typical
// cold-aisle supply setpoint plus a recirculation rise that grows with the
// rack's ABSOLUTE load against a reference full rack. A lightly loaded hall no
// longer shows a fake-hot rack just for being the densest in the room. Still an
// estimate, not a measurement -- real inlet tracks CRAH supply + containment.
const INLET_SUPPLY_C = 22;   // ASHRAE A1 recommended cold-aisle supply (~22 C)
const RACK_FULL_KW   = 15;   // reference "full" rack load for the estimate
function estTemp(r){ return INLET_SUPPLY_C + Math.min(rackKW(r)/RACK_FULL_KW,1)*10; } // ABSOLUTE 22..32, NOT measured
const live = ()=> LIVE.on && LIVE.ok;
const METRIC = {
  // power: live sum of power_watts when connected, else static nameplate-derived kW.
  power:   { label:'Power density', unit:'kW',
             val:r=> live()? (aggLive(r.rack_id,'power_watts','sum')||0)/1000 : rackKW(r),
             bands:[[0,'#2c6cb0'],[5,'#3fae6b'],[10,'#e6c832'],[15,'#d63c28'],[25,'#8c141e']],
             kind:'either' },
  capacity:{ label:'Capacity', unit:'%RU', val:r=>Math.min(100, occupiedU(r.rack_id)/42*100),
             bands:[[0,'#2c6cb0'],[40,'#3fae6b'],[70,'#e6c832'],[90,'#d63c28'],[100,'#8c141e']],
             kind:'static' },
  // inlet (H1): live max per rack from the PDU probe (pdu_temperature) + any
  // server/reference inlet_temp; falls back to a power-based ESTIMATE offline.
  inlet:   { label:'Inlet thermal', unit:'°C',
             val:r=> live()? aggLive(r.rack_id,['inlet_temp','pdu_temperature'],'max') : estTemp(r),
             bands:[[16,'#2c6cb0'],[18,'#3fae6b'],[27,'#e6c832'],[32,'#e8852f'],[35,'#d63c28']],
             kind:'liveOrEst' },
  // exhaust (H2): live only, servers expose outlet_temp. Bands shifted +~12C (sec 7.4).
  exhaust: { label:'Exhaust thermal', unit:'°C',
             val:r=> live()? aggLive(r.rack_id,'outlet_temp','max') : null,
             bands:[[25,'#2c6cb0'],[35,'#3fae6b'],[42,'#e6c832'],[52,'#e8852f'],[60,'#d63c28']],
             kind:'liveOnly' },
  // humidity (H3): live mean %RH from PDU probe (pdu_humidity) + any sensor. Bimodal band (sec 7.4).
  humidity:{ label:'Humidity', unit:'%RH',
             val:r=> live()? aggLive(r.rack_id,['humidity','pdu_humidity'],'mean') : null,
             bands:[[20,'#d63c28'],[30,'#e6c832'],[40,'#3fae6b'],[60,'#3fae6b'],[70,'#e6c832'],[80,'#d63c28']],
             kind:'liveOnly' },
};
function metricReal(m){ return m.kind==='static' || m.kind==='either'
  || (m.kind==='liveOrEst' && live()) || (m.kind==='liveOnly' && live()); }
// The heatmap overlay only renders with REAL data: 'static' metrics (capacity =
// rack-unit occupancy from topology, no simulator tick needed) are always
// allowed; every telemetry-derived metric (power/inlet/exhaust/humidity)
// requires a live connection to the running simulator. No live = no heat paint,
// so a stopped sim never shows a misleading estimated/nameplate heatmap.
function heatReady(m){ return m.kind==='static' || live(); }

function lerp(a,b,t){ return a+(b-a)*t; }
function hex2rgb(h){ return [parseInt(h.slice(1,3),16),parseInt(h.slice(3,5),16),parseInt(h.slice(5,7),16)]; }
function bandColor(v, bands){
  if(v<=bands[0][0]) return hex2rgb(bands[0][1]);
  for(let i=0;i<bands.length-1;i++){ const [v0,c0]=bands[i],[v1,c1]=bands[i+1];
    if(v<=v1){ const t=(v-v0)/(v1-v0||1),a=hex2rgb(c0),b=hex2rgb(c1);
      return [lerp(a[0],b[0],t),lerp(a[1],b[1],t),lerp(a[2],b[2],t)]; } }
  return hex2rgb(bands[bands.length-1][1]);
}

/* ===================== IDW (heatmap_design sec 7.3) ===================== */
const ROW_PITCH = FP.row_pitch || 2.4, IDW_R = 3*ROW_PITCH, IDW_P = 2, EPS = 0.1;
function samplesFor(racks, m){ const out=[]; for(const r of racks){ const v=m.val(r);
  if(v>0) out.push({x:r.floor_x, y:r.floor_y, v}); } return out; }   // sec 7.1: exclude no-reading
function idw(samples, gx, gy){ let num=0,den=0,hit=false;
  for(const s of samples){ const d=Math.hypot(gx-s.x, gy-s.y); if(d>IDW_R) continue; hit=true;
    const w=1/Math.pow(Math.max(d,EPS),IDW_P); num+=w*s.v; den+=w; }
  return hit? num/den : null; }

/* ===================== 2D render ===================== */
const c2d=$('#c2d'), ctx=c2d.getContext('2d'); let hit2d=[], hitDev=[];
function fitView(g){ const stage=$('#stage'), W=stage.clientWidth, H=stage.clientHeight, pad=40;
  const sc=Math.min((W-2*pad)/g.width_m, (H-2*pad)/g.depth_m);
  const ox=(W-g.width_m*sc)/2, oy=(H-g.depth_m*sc)/2;
  return {sc,ox,oy,W,H}; }

function draw2D(){
  const g=roomGeom(S.dc,S.room), racks=racksByRoom[S.dc+' / '+S.room]||[];
  const m=METRIC[S.metric];
  const dpr=window.devicePixelRatio||1, stage=$('#stage');
  c2d.width=stage.clientWidth*dpr; c2d.height=stage.clientHeight*dpr;
  c2d.style.width=stage.clientWidth+'px'; c2d.style.height=stage.clientHeight+'px';
  ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,stage.clientWidth,stage.clientHeight);
  const {sc,ox,oy}=fitView(g); const X=mx=>ox+mx*sc, Y=my=>oy+my*sc;
  const heatOn = S.heat && heatReady(m);   // no live data -> no heat paint

  // heatmap raster first (under racks)
  if(heatOn){ const samples=samplesFor(racks,m); const step=0.3;
    for(let gx=0;gx<g.width_m;gx+=step) for(let gy=0;gy<g.depth_m;gy+=step){
      const v=idw(samples,gx+step/2,gy+step/2); if(v==null) continue;
      const c=bandColor(v,m.bands); ctx.fillStyle=`rgba(${c[0]|0},${c[1]|0},${c[2]|0},0.78)`;
      ctx.fillRect(X(gx),Y(gy),step*sc+1,step*sc+1);
    } }

  // room outline
  ctx.strokeStyle='#3a4658'; ctx.lineWidth=1.5; ctx.strokeRect(ox,oy,g.width_m*sc,g.depth_m*sc);
  ctx.fillStyle='#6b7686'; ctx.font='11px sans-serif';
  ctx.fillText(`${S.room}  ${g.width_m}m x ${g.depth_m}m`, ox, oy-10);

  // aisles
  for(const a of (g.aisles||[])){ const col=a.type==='cold'?'#3b82f6':'#ef4444';
    ctx.fillStyle=col+'22'; ctx.fillRect(ox, Y(a.y-a.width/2), g.width_m*sc, a.width*sc);
    ctx.fillStyle=col+'cc'; ctx.font='10px sans-serif'; ctx.fillText(a.id, ox+3, Y(a.y)+3); }

  // racks (and facility cells). Gensets get a larger engine-skid footprint with
  // a radiator-grille band + GEN label, not a 0.6x1.2 IT-rack square.
  hit2d=[];
  for(const r of racks){ const ds=devByRack[r.rack_id]||[];
    // chiller plant: spread chillers (rects) + pumps (circles) + valves across
    // the room from the cell origin -- not one IT-rack square.
    if(ds.some(d=>d.device_type==='chiller')){
      const chl=ds.filter(d=>d.device_type==='chiller'), pmp=ds.filter(d=>d.device_type==='pump');
      const vlv=ds.filter(d=>d.device_type==='valve');
      chl.forEach((d,i)=>{ const cw=1.0*sc, ch=2.0*sc;
        const cpx=X(r.floor_x+0.7+i*1.5)-cw/2, cpy=Y(r.floor_y+0.9)-ch/2;
        ctx.fillStyle=heatOn?'#555':tcolor('chiller'); ctx.fillRect(cpx,cpy,cw,ch);
        ctx.strokeStyle='#0b0e13'; ctx.lineWidth=1; ctx.strokeRect(cpx,cpy,cw,ch);
        if(cw>16){ ctx.fillStyle='#052b2b'; ctx.font='bold 9px sans-serif'; ctx.fillText('CH'+(i+1),cpx+3,cpy+12); }
        hit2d.push({x:cpx,y:cpy,w:cw,h:ch,r}); });
      const pr=Math.max(3.5,0.17*sc);
      pmp.forEach((d,i)=>{ const mx=X(r.floor_x+0.55+i*0.55), my=Y(r.floor_y+2.6);
        ctx.fillStyle=tcolor('pump'); ctx.beginPath(); ctx.arc(mx,my,pr,0,7); ctx.fill();
        ctx.strokeStyle='#06231f'; ctx.lineWidth=1; ctx.stroke(); });
      const vs=Math.max(4,0.13*sc);
      vlv.forEach((d,i)=>{ const mx=X(r.floor_x+0.6+i*0.6), my=Y(r.floor_y+3.15);
        ctx.fillStyle=tcolor('valve'); ctx.fillRect(mx-vs/2,my-vs/2,vs,vs); });
      continue;
    }
    const gen=ds.some(d=>d.device_type==='generator');
    const ups=ds.some(d=>d.device_type==='ups');
    const ct=ds.some(d=>d.device_type==='cooling_tower');
    const rpp=ds.some(d=>d.device_type==='rpp');
    const fw=(gen?0.5:ups?0.55:FOOT.width)*sc, fd=(gen?1.7:ups?0.82:FOOT.depth)*sc;
    const px=X(r.floor_x)-fw/2, py=Y(r.floor_y)-fd/2;
    let fill;
    if(heatOn){ const v=m.val(r); const c= v>0? bandColor(v,m.bands):[60,60,60];
      fill=`rgb(${c[0]|0},${c[1]|0},${c[2]|0})`; }
    else { const main=ds[0]; fill = main? tcolor(main.device_type):'#444';
      if(ct) fill=tcolor('cooling_tower'); else if(rpp) fill=tcolor('rpp'); }
    ctx.fillStyle=fill; ctx.fillRect(px,py,fw,fd);
    ctx.strokeStyle = (S.sel===r.rack_id)?'#fff':'#0b0e13'; ctx.lineWidth=(S.sel===r.rack_id)?2:1;
    ctx.strokeRect(px,py,fw,fd);
    if(ct){ const n=ds.filter(d=>d.device_type==='cooling_tower').length;     // fan symbol per tower
      ctx.strokeStyle='#06231f'; ctx.lineWidth=1.5;
      for(let i=0;i<n;i++){ const fx=px+fw/2, fy=py+fd*(i+1)/(n+1), rr=Math.min(fw,fd/n)*0.28;
        ctx.beginPath(); ctx.arc(fx,fy,rr,0,7); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(fx-rr,fy); ctx.lineTo(fx+rr,fy); ctx.moveTo(fx,fy-rr); ctx.lineTo(fx,fy+rr); ctx.stroke(); }
      if(fw>16){ ctx.fillStyle='#06231f'; ctx.font='bold 9px sans-serif'; ctx.fillText('CT', px+3, py+11); }
    } else if(rpp){
      if(fw>16){ ctx.fillStyle='#1a1205'; ctx.font='bold 9px sans-serif'; ctx.fillText('RPP', px+3, py+12); }
    } else if(gen){ const gb=Math.min(11,fd*0.18);                       // radiator grille band at the front (bottom) end
      ctx.fillStyle='#0b0e13aa'; ctx.fillRect(px,py+fd-gb,fw,gb);
      ctx.strokeStyle='#0b0e1366'; ctx.lineWidth=1;
      for(let gx=px+3; gx<px+fw-2; gx+=3){ ctx.beginPath(); ctx.moveTo(gx,py+fd-gb); ctx.lineTo(gx,py+fd); ctx.stroke(); }
      if(fw>16){ ctx.fillStyle='#1a1205'; ctx.font='bold 9px sans-serif'; ctx.fillText('GEN', px+3, py+12); }
    } else if(ups){                                               // power cabinet + battery cabinet split
      const bx=px+fw*0.66; ctx.strokeStyle='#0b0e1399'; ctx.lineWidth=1;
      ctx.beginPath(); ctx.moveTo(bx,py); ctx.lineTo(bx,py+fd); ctx.stroke();         // UPS | battery divider
      ctx.fillStyle='#06212b'; ctx.fillRect(px+4,py+4,fw*0.5,Math.min(7,fd*0.2));     // HMI/LCD
      if(fw>22){ ctx.fillStyle='#1a1205'; ctx.font='bold 9px sans-serif'; ctx.fillText('UPS', px+3, py+fd-4);
        ctx.fillStyle='#0b0e13cc'; ctx.font='7px sans-serif'; ctx.fillText('BAT', bx+2, py+fd-4); }
    } else {
      // facing marker: thin bright edge on the front (cold-aisle) side
      if(r.rack_facing){ ctx.strokeStyle='#cfe3ff'; ctx.lineWidth=2; ctx.beginPath();
        const yEdge = r.rack_facing==='S'? py+fd : py;
        ctx.moveTo(px,yEdge); ctx.lineTo(px+fw,yEdge); ctx.stroke(); }
      if(fw>22){ ctx.fillStyle='#0b0e13cc'; ctx.font='9px sans-serif';
        ctx.fillText('R'+r.rack_num, px+2, py+fd-3); }
    }
    hit2d.push({x:px,y:py,w:fw,h:fd,r});
  }

  // sensor markers — always drawn on top of the rack map. 2D otherwise only
  // paints rack squares, so zero-U sensors would never be visible. Underfloor
  // (plenum) probes get a cyan ring + "UF"; cold-aisle reference probes green.
  hitDev=[];
  for(const r of racks){ const cx=X(r.floor_x), cy=Y(r.floor_y);
    const ds=devByRack[r.rack_id]||[];
    const plant=ds.some(d=>d.device_type==='chiller');           // plant probes line the CHW header
    const sens=ds.filter(d=>d.device_type==='sensor');
    sens.forEach((d,i)=>{ const uf=d.mounting==='underfloor';
      let mx,my;
      if(plant){ mx=X(r.floor_x+0.55+i*0.45); my=Y(r.floor_y+3.35); }
      else { const ang=i*2.39, rad=(sens.length>1? 7:0);
        mx=cx+Math.cos(ang)*rad; my=cy+Math.sin(ang)*rad; }
      ctx.beginPath(); ctx.arc(mx,my,4.5,0,7);
      ctx.fillStyle= uf? '#38e0c8' : tcolor('sensor');
      ctx.fill(); ctx.lineWidth= uf?2:1; ctx.strokeStyle= uf? '#063a33':'#06301f'; ctx.stroke();
      if(uf){ ctx.fillStyle='#cffff6'; ctx.font='bold 7px sans-serif'; ctx.fillText('UF', mx-6, my-7); }
      hitDev.push({x:mx-6,y:my-6,w:12,h:12,d,r}); });
  }
  drawHLegend(m, racks);
}

/* ===================== 3D render (Three.js) — realistic raised-floor hall ===================== */
const U_M=0.0445, RF=0.5, RACK_U=42, RACK_H=RACK_U*U_M;   // 1U, raised-floor/plenum height, 42U cabinet
const DEV_U={server:2,switch:1,router:1,firewall:2,load_balancer:1,oob_switch:1,cdu:4,energy_monitor:1};
let three=null;
function mat3(color,opts){ return new THREE.MeshStandardMaterial(Object.assign({color,metalness:0.35,roughness:0.6},opts||{})); }
function box3(parent,w,h,d,x,y,z,material){ const me=new THREE.Mesh(new THREE.BoxGeometry(w,h,d),material); me.position.set(x,y,z); parent.add(me); return me; }
function emi(col,k){ return new THREE.Color(col).multiplyScalar(k); }
function tileTexture(){ const N=64,cv=document.createElement('canvas');cv.width=cv.height=N;const c=cv.getContext('2d');
  c.fillStyle='#39414f';c.fillRect(0,0,N,N);c.strokeStyle='#232b37';c.lineWidth=3;c.strokeRect(0,0,N,N);
  const t=new THREE.CanvasTexture(cv);t.wrapS=t.wrapT=THREE.RepeatWrapping;return t; }
function perfTexture(){ const N=64,cv=document.createElement('canvas');cv.width=cv.height=N;const c=cv.getContext('2d');
  c.fillStyle='#1f2a38';c.fillRect(0,0,N,N);c.strokeStyle='#36506e';c.lineWidth=2;c.strokeRect(0,0,N,N);
  c.fillStyle='#0a1018';for(let i=0;i<6;i++)for(let j=0;j<6;j++){c.beginPath();c.arc(8+i*9.4,8+j*9.4,2.3,0,7);c.fill();}
  const t=new THREE.CanvasTexture(cv);t.wrapS=t.wrapT=THREE.RepeatWrapping;return t; }

// ---- realistic device front panels (drawn to a canvas, used as map + emissiveMap) ----
const _panelCache={}, _frontCache={}; let _darkMat=null;
function darkMat(){ return _darkMat || (_darkMat=mat3(0x191e26,{metalness:0.55,roughness:0.5}),_darkMat._keep=true,_darkMat); }
function frontMat(t,u){ const k=t+'_'+u; if(_frontCache[k]) return _frontCache[k]; const tex=panelTexture(t,u);
  const mt=new THREE.MeshStandardMaterial({map:tex,emissive:0xffffff,emissiveMap:tex,emissiveIntensity:0.55,metalness:0.3,roughness:0.55}); mt._keep=true;
  return _frontCache[k]=mt; }
function panelTexture(type,u){ const key=type+'_'+u; if(_panelCache[key]) return _panelCache[key];
  const Wp=512, Hp=Math.max(44,Math.round(46*u)); const cv=document.createElement('canvas'); cv.width=Wp; cv.height=Hp;
  const c=cv.getContext('2d'); const A=tcolor(type), R=Math.max(2,Hp*0.05);
  const g=c.createLinearGradient(0,0,0,Hp); g.addColorStop(0,'#2a3340'); g.addColorStop(0.5,'#1a1f28'); g.addColorStop(1,'#11151c');
  c.fillStyle=g; c.fillRect(0,0,Wp,Hp); c.strokeStyle='#080b10'; c.lineWidth=5; c.strokeRect(2,2,Wp-4,Hp-4);
  c.fillStyle='#0c0f14'; c.fillRect(0,0,20,Hp); c.fillRect(Wp-20,0,20,Hp);                          // rack ears
  const led=(x,y,col)=>{ c.fillStyle=col; c.beginPath(); c.arc(x,y,R,0,7); c.fill();
    c.globalAlpha=0.4; c.beginPath(); c.arc(x,y,R*1.9,0,7); c.fill(); c.globalAlpha=1; };
  const portRow=(x0,y0,n,pw,ph,gap,rows)=>{ rows=rows||1; for(let r2=0;r2<rows;r2++) for(let i=0;i<n;i++){
    const x=x0+i*(pw+gap), y=y0+r2*(ph+4); c.fillStyle='#05080c'; c.fillRect(x,y,pw,ph);
    c.strokeStyle='#3a4658'; c.lineWidth=1; c.strokeRect(x,y,pw,ph); led(x+pw*0.5,y-3,(i%3?'#39d353':'#1f6feb')); } };
  if(type==='server'){
    const bays=8, bw=(Wp*0.5)/bays;
    for(let i=0;i<bays;i++){ const x=30+i*bw; c.fillStyle='#0d1117'; c.fillRect(x,Hp*0.16,bw-5,Hp*0.68);
      c.strokeStyle='#2b333f'; c.lineWidth=1; c.strokeRect(x,Hp*0.16,bw-5,Hp*0.68);
      c.fillStyle='#222b36'; c.fillRect(x+3,Hp*0.42,bw-11,Hp*0.16); led(x+bw-9,Hp*0.26,'#39d353'); }
    const vx=Wp*0.56; c.fillStyle='#0c1016'; c.fillRect(vx,Hp*0.16,Wp*0.3,Hp*0.68);                 // vent grille
    c.fillStyle='#1b222c'; for(let yy=Hp*0.2; yy<Hp*0.8; yy+=Math.max(3,Hp*0.1)) c.fillRect(vx+4,yy,Wp*0.3-8,Math.max(1,Hp*0.035));
    led(Wp-42,Hp*0.3,'#39d353'); led(Wp-42,Hp*0.62,'#1f6feb');
    c.strokeStyle='#3a4658'; c.lineWidth=2; c.strokeRect(Wp-58,Hp*0.4,16,16);                       // power button
  } else if(type==='switch'||type==='oob_switch'){
    const n=24, pw=(Wp*0.6)/n; portRow(30,Hp*0.22,n,pw-2,Hp*0.26,2,2);
    for(let i=0;i<4;i++){ const x=Wp*0.7+i*((Wp*0.22)/4); c.fillStyle='#05080c'; c.fillRect(x,Hp*0.3,(Wp*0.22)/4-4,Hp*0.4);
      c.strokeStyle='#4a5566'; c.lineWidth=1; c.strokeRect(x,Hp*0.3,(Wp*0.22)/4-4,Hp*0.4); led(x+5,Hp*0.24,'#f0a020'); }   // SFP cages
    led(Wp-34,Hp*0.3,'#39d353'); led(Wp-34,Hp*0.62,'#39d353');
    c.fillStyle=A; c.fillRect(22,Hp*0.05,Wp-44,3);                                                  // brand accent
  } else if(type==='cdu'){
    c.fillStyle='#3a414d'; c.fillRect(20,4,Wp-40,Hp-8);
    const cpl=(x,col)=>{ c.fillStyle='#11151c'; c.beginPath(); c.arc(x,Hp*0.5,Hp*0.28,0,7); c.fill();
      c.strokeStyle=col; c.lineWidth=5; c.beginPath(); c.arc(x,Hp*0.5,Hp*0.28,0,7); c.stroke(); };
    cpl(Wp*0.2,'#3b82f6'); cpl(Wp*0.36,'#ef4444');                                                  // supply/return couplings
    c.fillStyle='#04222a'; c.fillRect(Wp*0.5,Hp*0.3,Wp*0.28,Hp*0.4); c.strokeStyle='#0d7d8c'; c.lineWidth=2; c.strokeRect(Wp*0.5,Hp*0.3,Wp*0.28,Hp*0.4);
    c.fillStyle='#21d4c4'; c.font=Math.round(Hp*0.15)+'px monospace'; c.fillText('COOLANT OK',Wp*0.52,Hp*0.54);
    led(Wp-44,Hp*0.4,'#39d353'); led(Wp-44,Hp*0.62,'#39d353');
  } else if(type==='energy_monitor'){
    c.fillStyle='#04222a'; c.fillRect(30,Hp*0.25,Wp*0.4,Hp*0.5); c.strokeStyle='#0d7d8c'; c.lineWidth=2; c.strokeRect(30,Hp*0.25,Wp*0.4,Hp*0.5);
    c.fillStyle='#21d4c4'; c.font=Math.round(Hp*0.2)+'px monospace'; c.fillText('kW 12.4',42,Hp*0.56);
    for(let i=0;i<10;i++) led(Wp*0.52+i*((Wp*0.38)/10),Hp*0.5,(i%2?'#39d353':'#1f6feb'));
  } else {                                                                                          // router / firewall / load_balancer
    c.fillStyle=A; c.fillRect(20,Hp*0.08,Wp-40,Math.max(3,Hp*0.1));                                 // brand bar
    portRow(40,Hp*0.45,8,Wp*0.045,Hp*0.3,6,1);
    c.fillStyle='#04222a'; c.fillRect(Wp*0.62,Hp*0.3,Wp*0.2,Hp*0.4); c.strokeStyle='#0d7d8c'; c.lineWidth=2; c.strokeRect(Wp*0.62,Hp*0.3,Wp*0.2,Hp*0.4);
    led(Wp-40,Hp*0.35,'#39d353'); led(Wp-40,Hp*0.6,'#f0a020');
  }
  const tx=new THREE.CanvasTexture(cv); tx.anisotropy=4; tx._keep=true; _panelCache[key]=tx; return tx; }

// rear/exhaust face texture: perforated grille + PSU fans + power inlets (per type+U)
function rearTexture(type,u){ const key='rear_'+type+'_'+u; if(_panelCache[key]) return _panelCache[key];
  const Wp=512,Hp=Math.max(44,Math.round(46*u)); const cv=document.createElement('canvas'); cv.width=Wp; cv.height=Hp;
  const c=cv.getContext('2d');
  c.fillStyle='#13171d'; c.fillRect(0,0,Wp,Hp); c.strokeStyle='#080b10'; c.lineWidth=5; c.strokeRect(2,2,Wp-4,Hp-4);
  c.fillStyle='#090d12'; for(let x=22;x<Wp-22;x+=10) for(let y=8;y<Hp-8;y+=9){ c.beginPath(); c.arc(x,y,2.5,0,7); c.fill(); }   // exhaust grille
  if(type==='cdu'){ const cpl=(x,col)=>{ c.fillStyle='#0e1218'; c.beginPath(); c.arc(x,Hp*0.5,Hp*0.26,0,7); c.fill();
      c.strokeStyle=col; c.lineWidth=6; c.beginPath(); c.arc(x,Hp*0.5,Hp*0.26,0,7); c.stroke(); };
    cpl(Wp*0.3,'#3b82f6'); cpl(Wp*0.62,'#ef4444'); }                                   // CDU rear = hose couplings
  else { const nps=u>=2?2:1, psW=Wp*0.19, psH=Hp*(u>=2?0.4:0.66);
    for(let i=0;i<nps;i++){ const px=Wp-28-psW, py=u>=2?(i?Hp*0.54:Hp*0.06):Hp*0.16;          // PSU module(s)
      c.fillStyle='#1b222b'; c.fillRect(px,py,psW,psH); c.strokeStyle='#39414d'; c.lineWidth=1.5; c.strokeRect(px,py,psW,psH);
      const fx=px+psW*0.5, fy=py+psH*0.5, fr=Math.min(psW,psH)*0.4;
      c.strokeStyle='#4a5566'; c.lineWidth=2; c.beginPath(); c.arc(fx,fy,fr,0,7); c.stroke();
      c.strokeStyle='#2a323d'; c.lineWidth=1.5; for(let a=0;a<8;a++){ const an=a*Math.PI/4; c.beginPath(); c.moveTo(fx,fy); c.lineTo(fx+Math.cos(an)*fr,fy+Math.sin(an)*fr); c.stroke(); }   // fan blades
      c.fillStyle='#05080c'; c.fillRect(px+4,py+psH-13,17,10); c.strokeStyle='#2a323d'; c.strokeRect(px+4,py+psH-13,17,10);    // C14 inlet
      c.fillStyle='#39d353'; c.beginPath(); c.arc(px+psW-9,py+9,3,0,7); c.fill(); }
    for(let i=0;i<2;i++){ c.fillStyle='#05080c'; c.fillRect(26+i*22,Hp*0.42,15,Hp*0.26); c.strokeStyle='#3a4658'; c.lineWidth=1; c.strokeRect(26+i*22,Hp*0.42,15,Hp*0.26); } }   // rear mgmt ports
  const tx=new THREE.CanvasTexture(cv); tx.anisotropy=4; tx._keep=true; _panelCache[key]=tx; return tx; }
function rearMat(t,u){ const k='rearm_'+t+'_'+u; if(_frontCache[k]) return _frontCache[k]; const tex=rearTexture(t,u);
  const mt=new THREE.MeshStandardMaterial({map:tex,emissive:0xffffff,emissiveMap:tex,emissiveIntensity:0.28,metalness:0.4,roughness:0.6}); mt._keep=true;
  return _frontCache[k]=mt; }

// a realistic 0U vertical rack PDU (e.g. APC AP86xx): dark metal strip, top metering
// display, a column of outlets down the inner face, input cord stub at the bottom.
// Built around local origin; caller positions/orients it. All meshes tagged clickable.
function makePDU(d){ const g=new THREE.Group();
  const H=RACK_H*0.9, Wp=0.04, Dp=0.07, face=Dp/2+0.002;       // inner (+z) face carries the outlets
  box3(g,Wp,H,Dp,0,0,0, mat3(0x232a33,{metalness:0.72,roughness:0.42}));       // extruded-aluminium body
  box3(g,Wp,0.05,Dp,0,H/2+0.025,0, mat3(0x12161d,{metalness:0.6,roughness:0.5}));// end cap
  // metering display + status LED near the top
  box3(g,Wp*0.78,0.055,0.012,0,H/2-0.06,face, mat3(0x0a1418,{emissive:emi(tcolor('pdu'),0.85),roughness:0.4}));
  box3(g,0.01,0.01,0.008,Wp*0.22,H/2-0.115,face, mat3(0x0a0d12,{emissive:emi(0x39d353,0.9)}));
  // outlet bank: alternating C13 (blue) / C19 (grey) sockets down the face
  const oc13=mat3(0x0a0d12,{emissive:emi(0x1c4a78,0.35),roughness:0.85});
  const oc19=mat3(0x0a0d12,{emissive:emi(0x4a4f57,0.3),roughness:0.85});
  const top=H/2-0.16, bot=-H/2+0.06, n=Math.max(10,Math.round((top-bot)/0.085));
  for(let i=0;i<n;i++){ const yy=top-(top-bot)*i/(n-1);
    box3(g,Wp*0.6,0.026,0.012,0,yy,face, (i%4===0)?oc19:oc13); }
  box3(g,0.013,0.06,0.013,0,-H/2-0.02,0.01, mat3(0x0a0d12,{roughness:0.95}));  // input power cord stub
  g.traverse(o=>{ o.userData.device=d; });
  return g; }

// a populated IT rack: open cabinet (front panels + rear exhaust faces visible) + zero-U PDUs + rear cabling
function makeRack(r,ds,m){ const grp=new THREE.Group(); const W=FOOT.width,D=FOOT.depth;
  const fr=mat3(0x12161d,{metalness:0.55,roughness:0.5});
  box3(grp,0.022,RACK_H,0.05,-W/2+0.02,RACK_H/2,-D/2+0.02,fr);  // rear door frame (left)
  box3(grp,0.022,RACK_H,0.05, W/2-0.02,RACK_H/2,-D/2+0.02,fr);  // rear door frame (right)
  box3(grp,0.028,RACK_H,D,-W/2+0.014,RACK_H/2,0,fr);            // left post wall
  box3(grp,0.028,RACK_H,D, W/2-0.014,RACK_H/2,0,fr);            // right post wall
  box3(grp,W,0.03,D,0,RACK_H+0.015,0,fr);                      // top
  box3(grp,W,0.06,D,0,0.03,0,fr);                              // base plinth
  let si=0;                                                     // sensor index -> spread so they don't overlap
  for(const d of ds){ const t=d.device_type;
    if(t==='pdu'){ const side=/-B$/.test(d.name)?1:-1;          // A left, B right vertical 0U strip
      const pg=makePDU(d); pg.position.set(side*(W/2-0.045), RACK_H*0.5+0.06, -D/2+0.13);
      grp.add(pg); continue; }
    if(t==='sensor'){ const uf=d.mounting==='underfloor';
      const col=uf?0x38e0c8:tcolor('sensor'); let sb;
      if(uf){                                                    // glowing puck down in the raised-floor plenum void
        sb=new THREE.Mesh(new THREE.CylinderGeometry(0.05,0.05,0.03,16),
          mat3(col,{emissive:emi(col,0.65),roughness:0.4}));
        sb.position.set(-W/2+0.12+(si%2)*0.2, -RF*0.5, D/2-0.14-Math.floor(si/2)*0.22); grp.add(sb);
      } else {                                                   // reference probe clipped to the cold-aisle (front) face
        sb=box3(grp,0.06,0.05,0.04, -W/2+0.1+(si%3)*0.18, 0.25+Math.floor(si/3)*0.2, D/2-0.03,
          mat3(col,{emissive:emi(col,0.45)}));
      }
      sb.userData.device=d; si++; continue; }
    const u=DEV_U[t]||1, base=d.rack_unit||0; if(base<1) continue;
    const hh=u*U_M*0.9, y=0.06+(base-1)*U_M+hh/2;
    const dm=darkMat(), fm=frontMat(t,u), rm=rearMat(t,u);                          // front panel + rear exhaust face
    const chassis=new THREE.Mesh(new THREE.BoxGeometry(W*0.92,hh,D*0.82),[dm,dm,dm,dm,fm,rm]);
    chassis.position.set(0,y,0.02); chassis.userData.device=d; grp.add(chassis);
  }
  for(const sx of [-1,1]){ const cb=new THREE.Mesh(new THREE.CylinderGeometry(0.022,0.028,RACK_H*0.82,6),
    mat3(0x0a0d12,{roughness:0.95})); cb.position.set(sx*(W/2-0.1),RACK_H*0.5+0.06,-D/2+0.06); grp.add(cb); }   // rear cable bundles
  if(S.heat && heatReady(m)){ const v=m.val(r); if(v>0){ const c=bandColor(v,m.bands);
    const sh=new THREE.Mesh(new THREE.BoxGeometry(W+0.05,RACK_H+0.05,D+0.05),
      new THREE.MeshBasicMaterial({color:`rgb(${c[0]|0},${c[1]|0},${c[2]|0})`,transparent:true,opacity:0.42})); sh.position.y=RACK_H/2; grp.add(sh); } }
  return grp; }

// floor-standing perimeter CRAH: brushed-metal cabinet, top intake fan, downflow into plenum
function makeCRAH(r,ds){ const grp=new THREE.Group(); const W=0.62,D=1.0,Hh=2.05;
  box3(grp,W,Hh,D,0,Hh/2,0, mat3(0x99a2af,{metalness:0.6,roughness:0.42}));            // body
  box3(grp,W*0.84,Hh*0.55,0.03,0,Hh*0.4,D/2+0.006, mat3(0x29313c,{roughness:0.75}));   // louvered front
  box3(grp,W*0.9,0.06,D*0.9,0,Hh+0.03,0, mat3(0x171c24));                              // top intake frame
  const fan=new THREE.Mesh(new THREE.RingGeometry(Math.min(W,D)*0.12,Math.min(W,D)*0.34,24), mat3(0x0e1218,{side:THREE.DoubleSide}));
  fan.rotation.x=-Math.PI/2; fan.position.set(0,Hh+0.07,0); grp.add(fan);
  const flow=new THREE.Mesh(new THREE.BoxGeometry(W*0.8,RF,D*0.8), new THREE.MeshBasicMaterial({color:0x35cfe0,transparent:true,opacity:0.14}));
  flow.position.y=-RF/2; grp.add(flow);                                               // cold downflow into plenum
  return grp; }

// end-of-row power: RPP enclosure + breaker face + clamp-on energy monitor
function makePanel(r,ds){ const grp=new THREE.Group(); const D=FOOT.depth;
  box3(grp,0.46,1.45,0.16,0,0.78,-D/2+0.12, mat3(0x6b7280,{metalness:0.5,roughness:0.5}));
  box3(grp,0.36,1.05,0.02,0,0.8,-D/2+0.21, mat3(0x232a35));
  if(ds.some(d=>d.device_type==='energy_monitor'))
    box3(grp,0.2,0.13,0.06,0,1.42,-D/2+0.22, mat3(tcolor('energy_monitor'),{emissive:emi(tcolor('energy_monitor'),0.3)}));
  return grp; }

// standby diesel genset: skid base + weatherproof enclosure, radiator louvers at
// the front end, exhaust silencer + stack on top, side control cubicle (HMI).
// NOT a 42U IT rack -- a packaged engine-alternator set.
function makeGenerator(r,ds){ const grp=new THREE.Group();
  const W=0.5,D=1.7,H=1.45, y0=0.1;
  const gen=ds.find(d=>d.device_type==='generator');
  box3(grp,W+0.08,0.1,D+0.08,0,0.05,0, mat3(0x23272e,{metalness:0.7,roughness:0.5}));          // skid base
  box3(grp,W,H,D,0,y0+H/2,0, mat3(0xb6892a,{metalness:0.45,roughness:0.55}));                   // enclosure (genset yellow)
  box3(grp,W*0.96,H*0.78,0.03,0,y0+H*0.46,D/2+0.006, mat3(0x14181d,{roughness:0.85}));          // radiator grille (front end)
  for(let i=0;i<6;i++) box3(grp,W*0.9,0.012,0.01,0,y0+H*0.2+i*H*0.1,D/2+0.02, mat3(0x2a2f37));  // louver slats
  box3(grp,0.03,H*0.5,D*0.34,W/2+0.006,y0+H*0.42,-D*0.08, mat3(0x1b1f26,{roughness:0.6}));      // side access door
  box3(grp,0.05,0.2,0.16,W/2+0.03,y0+H*0.55,-D*0.08, mat3(0x0a0d12,{emissive:emi(tcolor('generator'),0.5)})); // controller HMI
  const sil=new THREE.Mesh(new THREE.CylinderGeometry(0.1,0.1,D*0.5,12), mat3(0x3a3f47,{metalness:0.7,roughness:0.5}));
  sil.rotation.x=Math.PI/2; sil.position.set(W*0.18,y0+H+0.12,-D*0.16); grp.add(sil);            // exhaust silencer
  const stk=new THREE.Mesh(new THREE.CylinderGeometry(0.045,0.05,0.55,10), mat3(0x2a2e35,{metalness:0.7,roughness:0.5}));
  stk.position.set(W*0.18,y0+H+0.48,-D*0.36); grp.add(stk);                                      // exhaust stack
  grp.traverse(o=>{ o.userData.device=gen; });
  return grp; }

// floor-standing double-conversion UPS (Eaton 93E / APC Symmetra PX): tall metal
// power cabinet with a front HMI/LCD, status LEDs, lower vent louvers + a
// companion battery cabinet beside it. NOT a 42U IT rack.
function makeUPS(r,ds){ const grp=new THREE.Group();
  const ups=ds.find(d=>d.device_type==='ups');
  const W=0.5,D=0.82,H=1.9;
  const cab=mat3(0x3c4048,{metalness:0.55,roughness:0.5});
  box3(grp,W,H,D,-0.14,H/2,0, cab);                                            // UPS cabinet body
  box3(grp,W*0.92,0.04,D*0.92,-0.14,H+0.02,0, mat3(0x1b1f26));                 // top cap
  box3(grp,W*0.52,0.22,0.02,-0.14,H*0.72,D/2+0.012, mat3(0x06212b,{emissive:emi(0x21d4c4,0.7),roughness:0.4})); // HMI/LCD
  for(let i=0;i<3;i++) box3(grp,0.018,0.018,0.012,-0.14+W*0.3,H*0.6-i*0.055,D/2+0.014,
      mat3(0x0a0d12,{emissive:emi(i===1?0xf0a020:0x39d353,0.9)}));            // status LEDs
  for(let i=0;i<8;i++) box3(grp,W*0.74,0.012,0.008,-0.14,H*0.12+i*0.04,D/2+0.012, mat3(0x14181d)); // vent louvers
  box3(grp,W,0.03,0.012,-0.14,H*0.93,D/2+0.012, mat3(tcolor('ups'),{emissive:emi(tcolor('ups'),0.4)})); // brand accent
  // companion battery cabinet (VRLA strings) alongside
  const bw=0.26;
  box3(grp,bw,H*0.92,D,0.18,H*0.46,0, mat3(0x33373d,{metalness:0.5,roughness:0.55}));
  for(let i=0;i<4;i++) box3(grp,bw*0.8,0.02,0.02,0.18,H*0.2+i*0.18,D/2+0.012, mat3(0x14181d)); // battery vent slots
  grp.traverse(o=>{ o.userData.device=ups; });
  return grp; }

// floor/wall RPP (APC Galaxy Remote Power Panel): dead-front distribution
// cabinet with breaker rows + optional clamp-on energy meter. Floor-standing.
function makeRPP(r,ds){ const grp=new THREE.Group();
  const rpp=ds.find(d=>d.device_type==='rpp');
  const W=0.5,D=0.32,H=1.85;
  box3(grp,W,H,D,0,H/2,0, mat3(0x6b7280,{metalness:0.5,roughness:0.5}));            // enclosure
  box3(grp,W*0.86,H*0.82,0.02,0,H*0.5,D/2+0.012, mat3(0x232a35));                   // dead-front door
  for(let row=0;row<9;row++) for(let c=0;c<2;c++)                                    // breaker handles
    box3(grp,W*0.28,0.045,0.02,(c?0.1:-0.1),H*0.2+row*0.14,D/2+0.016, mat3(0x10141a));
  if(ds.some(d=>d.device_type==='energy_monitor'))
    box3(grp,0.16,0.1,0.04,0,H*0.92,D/2+0.02, mat3(tcolor('energy_monitor'),{emissive:emi(tcolor('energy_monitor'),0.45)})); // EV2 meter
  grp.traverse(o=>{ o.userData.device=rpp; });
  return grp; }

// induced-draft evaporative cooling tower (BAC PT2): galvanized box, side air-
// intake louvers, top fan deck with fan, hot-water riser. Rooftop heat rejection.
function makeCoolingTower(d){ const g=new THREE.Group();
  const W=0.46,D=0.56,H=0.95, body=mat3(0x8a9099,{metalness:0.5,roughness:0.55});
  box3(g,W,H,D,0,H/2,0, body);
  for(let i=0;i<5;i++){ const y=H*0.22+i*H*0.13;                                     // intake louvers both sides
    box3(g,0.012,0.05,D*0.8,-W/2-0.006,y,0, mat3(0x2a2f37));
    box3(g,0.012,0.05,D*0.8, W/2+0.006,y,0, mat3(0x2a2f37)); }
  box3(g,W*0.98,0.05,D*0.98,0,H+0.025,0, mat3(0x171c24));                            // fan deck
  const hub=new THREE.Mesh(new THREE.CylinderGeometry(Math.min(W,D)*0.36,Math.min(W,D)*0.36,0.035,20), mat3(0x10141a));
  hub.position.set(0,H+0.06,0); g.add(hub);
  const fan=new THREE.Mesh(new THREE.RingGeometry(Math.min(W,D)*0.1,Math.min(W,D)*0.34,20), mat3(tcolor('cooling_tower'),{emissive:emi(tcolor('cooling_tower'),0.25),side:THREE.DoubleSide}));
  fan.rotation.x=-Math.PI/2; fan.position.set(0,H+0.085,0); g.add(fan);
  const pipe=new THREE.Mesh(new THREE.CylinderGeometry(0.028,0.028,H*0.85,8), mat3(0x3a3f47,{metalness:0.7,roughness:0.5}));
  pipe.position.set(W/2+0.05,H*0.45,-D/2+0.08); g.add(pipe);                         // hot-water riser
  g.traverse(o=>{ o.userData.device=d; });
  return g; }

// rooftop cell: the cooling towers (spread along depth) + small fan-power PDU boxes
function makeRoof(r,ds){ const grp=new THREE.Group();
  const cts=ds.filter(d=>d.device_type==='cooling_tower');
  const pdus=ds.filter(d=>d.device_type==='pdu');
  cts.forEach((d,i)=>{ const t=makeCoolingTower(d);
    t.position.set(0,0,(i-(cts.length-1)/2)*0.78); grp.add(t); });
  pdus.forEach((d,i)=>{ const pb=box3(grp,0.1,0.45,0.1,(i?0.24:-0.24),0.225,0.95,
      mat3(tcolor('pdu'),{emissive:emi(tcolor('pdu'),0.3)})); pb.userData.device=d; });
  return grp; }

// water-cooled centrifugal chiller (Carrier 19DV): horizontal evaporator +
// condenser shells with a centrifugal compressor on top + control panel.
function makeChiller(d){ const g=new THREE.Group(); const W=1.0,D=2.0,H=1.5;
  box3(g,W,H*0.42,D,0,H*0.21,0, mat3(0x5b6573,{metalness:0.5,roughness:0.55}));        // skid base
  const shell=mat3(0x808a97,{metalness:0.55,roughness:0.45});
  for(let i=0;i<2;i++){ const sh=new THREE.Mesh(new THREE.CylinderGeometry(0.3,0.3,D*0.94,16), shell);
    sh.rotation.x=Math.PI/2; sh.position.set(0,H*0.5+i*0.42,0); g.add(sh); }                // evap + condenser shells
  box3(g,W*0.7,0.5,0.62,0,H*0.62,-D/2+0.42, mat3(0x9aa3b0,{metalness:0.6,roughness:0.4})); // compressor
  box3(g,0.05,0.42,0.5,W/2+0.02,H*0.5,D*0.18, mat3(0x06212b,{emissive:emi(0x21d4c4,0.5)}));// control panel
  g.traverse(o=>{ o.userData.device=d; }); return g; }

// vertical inline centrifugal pump (Grundfos): volute + motor stack on a pad.
function makePump(d){ const g=new THREE.Group();
  const vol=new THREE.Mesh(new THREE.CylinderGeometry(0.17,0.17,0.28,14), mat3(tcolor('pump'),{metalness:0.5,roughness:0.5}));
  vol.position.y=0.14; g.add(vol);                                                          // volute (pump teal)
  const mot=new THREE.Mesh(new THREE.CylinderGeometry(0.12,0.12,0.4,12), mat3(0x3a3f47,{metalness:0.6,roughness:0.5}));
  mot.position.y=0.48; g.add(mot);                                                          // motor
  box3(g,0.1,0.06,0.1,0,0.7,0, mat3(0x10141a));                                             // terminal box
  g.traverse(o=>{ o.userData.device=d; }); return g; }

// central chiller plant: chillers in a row, pump skid behind, a CHW header pipe
// with valve actuators + plant temp/flow probes, and PDU/OOB at the edge.
function makePlant(r,ds){ const grp=new THREE.Group();
  const chl=ds.filter(d=>d.device_type==='chiller'), pmp=ds.filter(d=>d.device_type==='pump');
  const vlv=ds.filter(d=>d.device_type==='valve'), sen=ds.filter(d=>d.device_type==='sensor');
  const pdu=ds.filter(d=>d.device_type==='pdu'), oob=ds.filter(d=>d.device_type==='oob_switch');
  chl.forEach((d,i)=>{ const c=makeChiller(d); c.position.set(0.7+i*1.5,0,0.9); grp.add(c); });
  pmp.forEach((d,i)=>{ const p=makePump(d); p.position.set(0.55+i*0.55,0,2.6); grp.add(p); });
  const hdr=new THREE.Mesh(new THREE.CylinderGeometry(0.055,0.055,Math.max(1,chl.length)*1.5,10),
    mat3(0x3a3f47,{metalness:0.6,roughness:0.5})); hdr.rotation.z=Math.PI/2;
  hdr.position.set(0.7+(chl.length-1)*0.75,0.12,3.15); grp.add(hdr);                         // CHW header
  vlv.forEach((d,i)=>{ const vb=box3(grp,0.12,0.2,0.12,0.6+i*0.6,0.22,3.15,
      mat3(tcolor('valve'),{emissive:emi(tcolor('valve'),0.35)})); vb.userData.device=d; });
  sen.forEach((d,i)=>{ const sb=box3(grp,0.06,0.05,0.04,0.55+i*0.45,0.2,3.35,
      mat3(tcolor('sensor'),{emissive:emi(tcolor('sensor'),0.45)})); sb.userData.device=d; });
  pdu.forEach((d,i)=>{ const pb=box3(grp,0.1,0.42,0.1,0.4+i*0.22,0.21,4.0,
      mat3(tcolor('pdu'),{emissive:emi(tcolor('pdu'),0.3)})); pb.userData.device=d; });
  oob.forEach((d,i)=>{ const ob=box3(grp,0.4,0.1,0.45,1.0,0.25,4.0,
      mat3(tcolor('oob_switch'),{emissive:emi(tcolor('oob_switch'),0.3)})); ob.userData.device=d; });
  return grp; }

// Recursively remove + dispose a group's children. Geometries are always fresh
// per build so they are disposed unconditionally; materials/textures tagged
// ._keep (the shared _frontCache/_panelCache/darkMat caches) are left intact.
function disposeGroup(g){
  for(let i=g.children.length-1;i>=0;i--){ const o=g.children[i];
    if(o.children && o.children.length) disposeGroup(o);
    if(o.geometry) o.geometry.dispose();
    if(o.material){ const mats=Array.isArray(o.material)?o.material:[o.material];
      for(const mt of mats){ if(!mt||mt._keep) continue;
        for(const mk of ['map','emissiveMap','normalMap','roughnessMap','metalnessMap','alphaMap']){
          const t=mt[mk]; if(t && !t._keep) t.dispose(); }
        mt.dispose(); } }
    g.remove(o);
  }
}
function draw3D(){
  const host=$('#c3d');
  if(typeof THREE==='undefined'){ host.innerHTML='<div style="color:#ffd98a;padding:30px">3D needs the Three.js CDN (internet). 2D works offline.</div>'; return; }
  const g=roomGeom(S.dc,S.room), racks=racksByRoom[S.dc+' / '+S.room]||[];
  const m=METRIC[S.metric];
  const W=host.clientWidth,H=host.clientHeight;
  if(!three){ const sc=new THREE.Scene(); sc.background=new THREE.Color(0x090c11);
    const cam=new THREE.PerspectiveCamera(50,W/H,0.1,500);
    const rn=new THREE.WebGLRenderer({antialias:true}); rn.setPixelRatio(window.devicePixelRatio);
    host.appendChild(rn.domElement);
    const ctr=new THREE.OrbitControls(cam,rn.domElement); ctr.enableDamping=true; ctr.maxPolarAngle=Math.PI*0.495;
    ctr.screenSpacePanning=true; ctr.enablePan=true; ctr.panSpeed=0.9; ctr.zoomSpeed=0.9; ctr.minDistance=1.2; ctr.maxDistance=220;
    sc.add(new THREE.HemisphereLight(0xb8ccf0,0x0a0d12,0.55));
    sc.add(new THREE.AmbientLight(0xffffff,0.3));
    const dl=new THREE.DirectionalLight(0xffffff,0.9); dl.position.set(8,18,10); sc.add(dl);
    const root=new THREE.Group(); sc.add(root);
    three={sc,cam,rn,ctr,root,ray:new THREE.Raycaster(),mouse:new THREE.Vector2()};
    rn.domElement.addEventListener('click',on3dClick);
    (function loop(){ requestAnimationFrame(loop);
      if(S.view!=='3d') return;   // don't burn GPU rendering the hidden canvas while on 2D
      if(three.anim){ const a=three.anim; a.t++; let k=Math.min(1,a.t/a.dur); k=k<0.5?4*k*k*k:1-Math.pow(-2*k+2,3)/2;
        three.cam.position.lerpVectors(a.fromP,a.toP,k); three.ctr.target.lerpVectors(a.fromT,a.toT,k);
        if(a.t>=a.dur) three.anim=null; }
      three.ctr.update(); three.rn.render(three.sc,three.cam); })();
  }
  const {cam,rn,ctr,root}=three; rn.setSize(W,H); cam.aspect=W/H; cam.updateProjectionMatrix();
  // Free GPU memory from the previous build before rebuilding. Three.js does NOT
  // auto-dispose geometries/materials/textures; without this, a live poll every
  // 5s leaks the whole scene each time -> hundreds of MB after ~20 min -> the
  // browser stalls when the iframe is torn down on 'Back'. Cached, reused
  // resources are tagged ._keep and skipped.
  disposeGroup(root);
  const cx=g.width_m/2, cz=g.depth_m/2;
  // Cutaway fades the plenum perimeter walls too, else they hide the void from the side.
  const wmat = S.cut ? mat3(0x0f141b,{roughness:1,transparent:true,opacity:0.12})
                     : mat3(0x0f141b,{roughness:1});
  three.cx=cx; three.cz=cz;                                   // room offset for focus()

  // Facility rooms (generator/UPS/mechanical/plant/roof) sit on a poured
  // concrete slab on grade -- no raised access floor, no plenum, no vent tiles.
  // White-space halls keep the raised floor + plenum. FLR = height devices sit at.
  const slab = g.class==='facility';
  const FLR = slab ? 0.08 : RF;
  three.flr = FLR;
  if(slab){
    const cmat=mat3(0x44484f,{roughness:0.97,metalness:0.04});                 // concrete slab
    box3(root,g.width_m,FLR,g.depth_m,0,FLR/2,0, cmat);
    const curb=mat3(0x33373d,{roughness:0.95});                                // low perimeter curb
    box3(root,g.width_m,0.06,0.04,0,FLR+0.03,-cz,curb); box3(root,g.width_m,0.06,0.04,0,FLR+0.03,cz,curb);
    box3(root,0.04,0.06,g.depth_m,-cx,FLR+0.03,0,curb); box3(root,0.04,0.06,g.depth_m,cx,FLR+0.03,0,curb);
  } else {
    // plenum base + perimeter walls + raised-floor tile slab
    box3(root,g.width_m,0.04,g.depth_m,0,0.02,0, mat3(0x080b10,{roughness:1}));
    box3(root,g.width_m,RF,0.05,0,RF/2,-cz,wmat); box3(root,g.width_m,RF,0.05,0,RF/2,cz,wmat);
    box3(root,0.05,RF,g.depth_m,-cx,RF/2,0,wmat); box3(root,0.05,RF,g.depth_m,cx,RF/2,0,wmat);
    const tt=tileTexture(); tt.repeat.set(Math.max(1,Math.round(g.width_m/0.6)),Math.max(1,Math.round(g.depth_m/0.6)));
    // Cutaway: fade the tile slab so the plenum void (underfloor sensors) is visible.
    const tileMat=new THREE.MeshStandardMaterial({map:tt,metalness:0.2,roughness:0.85,
      transparent:S.cut, opacity:S.cut?0.16:1});
    box3(root,g.width_m,0.04,g.depth_m,0,RF,0, tileMat);
    // aisles on the raised floor: cold = perforated vent tiles (cyan glow), hot = warm tint
    for(const a of (g.aisles||[])){ const pl=new THREE.PlaneGeometry(g.width_m,a.width); let mt;
      if(a.type==='cold'){ const pt=perfTexture(); pt.repeat.set(Math.max(1,Math.round(g.width_m/0.6)),Math.max(1,Math.round(a.width/0.6)));
        mt=new THREE.MeshStandardMaterial({map:pt,emissive:0x0c4a66,emissiveIntensity:0.6,roughness:0.8}); }
      else mt=new THREE.MeshBasicMaterial({color:0x5a1a1a,transparent:true,opacity:0.16});
      const tile=new THREE.Mesh(pl,mt); tile.rotation.x=-Math.PI/2; tile.position.set(0,RF+0.025,a.y-cz); root.add(tile); }
  }

  // heat field overlay when ON (and live data is available)
  if(S.heat && heatReady(m)){ const hp=new THREE.Mesh(new THREE.PlaneGeometry(g.width_m,g.depth_m),
      new THREE.MeshBasicMaterial({map:heatTexture(g,racks,m),transparent:true,opacity:0.6}));
    hp.rotation.x=-Math.PI/2; hp.position.set(0,FLR+0.05,0); root.add(hp); }

  // cells: CRAH unit / power panel / populated rack
  three.pick=[];
  for(const r of racks){ const ds=devByRack[r.rack_id]||[]; const ts=new Set(ds.map(d=>d.device_type));
    const grp = ts.has('generator') ? makeGenerator(r,ds)
              : ts.has('ups') ? makeUPS(r,ds)
              : ts.has('chiller') ? makePlant(r,ds)
              : ts.has('cooling_tower') ? makeRoof(r,ds)
              : ts.has('crah') ? makeCRAH(r,ds)
              : (!ts.has('server')&&ts.has('rpp')) ? makeRPP(r,ds)
              : (!ts.has('server')&&ts.has('energy_monitor')) ? makePanel(r,ds)
              : makeRack(r,ds,m);
    grp.position.set(r.floor_x-cx, FLR, r.floor_y-cz);
    if(r.rack_facing==='N') grp.rotation.y=Math.PI;             // face intake (front) toward the cold aisle
    grp.traverse(o=>{ o.userData.rack=r; }); root.add(grp); three.pick.push(grp); }

  if(!three.posed){ const span=Math.max(g.width_m,g.depth_m); three.span=span; three.homeY=FLR+0.7;
    cam.position.set(span*0.72, span*0.8+3.5, span*1.05); ctr.target.set(0,FLR+0.7,0); three.posed=true; }
  drawHLegend(m, racks);
}
/* ===================== 3D navigation: pan / dolly / view presets ===================== */
function panCam(dx,dy){ if(!three) return; const cam=three.cam,ctr=three.ctr; cam.updateMatrixWorld();
  const right=new THREE.Vector3().setFromMatrixColumn(cam.matrixWorld,0);
  const up=new THREE.Vector3().setFromMatrixColumn(cam.matrixWorld,1);
  const step=(three.span||10)*0.05, mv=new THREE.Vector3();
  mv.addScaledVector(right,dx*step); mv.addScaledVector(up,dy*step);
  cam.position.add(mv); ctr.target.add(mv); }
function panVert(dir){ if(!three) return; const step=(three.span||10)*0.05;
  three.cam.position.y+=dir*step; three.ctr.target.y+=dir*step; }
function dolly3(f){ if(!three) return; const cam=three.cam,ctr=three.ctr;
  const off=new THREE.Vector3().subVectors(cam.position,ctr.target).multiplyScalar(f);
  const d=off.length(); if(d<(ctr.minDistance||1)||d>(ctr.maxDistance||300)) return;
  cam.position.copy(ctr.target).add(off); }
function setView3(p){ if(!three) return; const cam=three.cam,t=three.ctr.target,s=three.span||10;
  if(p==='top') cam.position.set(t.x,t.y+s*1.7,t.z+0.001);
  else if(p==='front') cam.position.set(t.x,t.y+s*0.25,t.z+s*1.4);
  else cam.position.set(t.x+s*0.72,t.y+s*0.8,t.z+s*1.05);
  cam.lookAt(t); }
function resetView3(){ if(!three) return; three.anim=null; three.ctr.target.set(0,three.homeY||0.7,0); setView3('iso'); }
// fly the camera in to frame one rack/cell head-on. View side = whichever side the
// camera is already on (so clicking the back focuses the back, the front the front).
// Camera looks toward the target, so the opposite row is always BEHIND it, not blocking.
function focusRack(r){ if(!three || three.cx==null) return;
  const FLR=three.flr!=null?three.flr:RF;
  const tgt=new THREE.Vector3(r.floor_x-three.cx, FLR+0.95, r.floor_y-three.cz);
  const sideZ = (three.cam.position.z >= tgt.z) ? 1 : -1;        // front/back: stay on the viewer's side
  const pos=new THREE.Vector3(tgt.x+0.2, tgt.y+0.7, tgt.z + sideZ*1.7);   // z-dominant -> near head-on
  three.anim={fromP:three.cam.position.clone(), toP:pos, fromT:three.ctr.target.clone(), toT:tgt, t:0, dur:30}; }
// local Y (height in the rack group) of a device's mesh -- mirrors makeRack() placement.
function devY(d){ const t=d.device_type;
  if(t==='pdu') return RACK_H*0.5+0.06;                          // full-height 0U strip
  if(t==='sensor') return d.mounting==='underfloor' ? -RF*0.5 : 0.25;
  const u=DEV_U[t]||1, base=d.rack_unit||0;
  if(base<1) return RACK_H*0.5;                                  // zero-U / unplaced -> mid rack
  const hh=u*U_M*0.9; return 0.06+(base-1)*U_M+hh/2; }
// fly the camera in to frame ONE device at its real RU height (tighter than a rack focus).
function focusDevice(d,r){ if(!three || three.cx==null) return;
  const FLR=three.flr!=null?three.flr:RF;
  // Prefer the device's ACTUAL mesh world position: cells like the chiller plant
  // pack many devices into one rack, so r.floor_x/floor_y is just the cell corner.
  let wp=null;
  for(const grp of (three.pick||[])){ grp.traverse(o=>{ if(!wp && o.userData.device===d && o.geometry){
    wp=new THREE.Vector3(); o.getWorldPosition(wp); } }); if(wp) break; }
  const tgt = wp || new THREE.Vector3(r.floor_x-three.cx, FLR+devY(d), r.floor_y-three.cz);
  const sideZ = (three.cam.position.z >= tgt.z) ? 1 : -1;
  const pos=new THREE.Vector3(tgt.x+0.15, tgt.y+0.45, tgt.z + sideZ*1.25);
  three.anim={fromP:three.cam.position.clone(), toP:pos, fromT:three.ctr.target.clone(), toT:tgt, t:0, dur:30}; }
(function wireNav(){ const nav=$('#nav3d'); if(!nav) return; let timer=null;
  function act(b){ const v=b.dataset.v,p=b.dataset.p,z=b.dataset.z;
    if(v) setView3(v); else if(z) dolly3(z==='in'?0.9:1.12);
    else if(p==='c') resetView3(); else if(p==='u') panCam(0,1); else if(p==='d') panCam(0,-1);
    else if(p==='l') panCam(-1,0); else if(p==='r') panCam(1,0); else if(p==='rise') panVert(1); else if(p==='fall') panVert(-1); }
  function stop(){ if(timer){ clearInterval(timer); timer=null; } }
  nav.addEventListener('pointerdown',e=>{ const b=e.target.closest('button'); if(!b) return; act(b);
    if(b.dataset.p!=='c' && !b.dataset.v) timer=setInterval(()=>act(b),70); e.preventDefault(); });
  nav.addEventListener('pointerup',stop); nav.addEventListener('pointerleave',stop); window.addEventListener('pointerup',stop);
})();
window.addEventListener('keydown',e=>{ if(S.view!=='3d'||!three) return;
  if(e.target && (e.target.tagName==='INPUT'||e.target.tagName==='SELECT')) return;
  const k=e.key.toLowerCase(); let used=true;
  if(k==='arrowleft'||k==='a') panCam(-1,0); else if(k==='arrowright'||k==='d') panCam(1,0);
  else if(k==='arrowup'||k==='w') panCam(0,1); else if(k==='arrowdown'||k==='s') panCam(0,-1);
  else if(k==='q') panVert(1); else if(k==='e') panVert(-1);
  else if(k==='+'||k==='=') dolly3(0.9); else if(k==='-'||k==='_') dolly3(1.12);
  else if(k==='r') resetView3(); else used=false;
  if(used) e.preventDefault(); });
function on3dClick(e){ const host=$('#c3d'), b=host.getBoundingClientRect();
  three.mouse.x=((e.clientX-b.left)/b.width)*2-1; three.mouse.y=-((e.clientY-b.top)/b.height)*2+1;
  three.ray.setFromCamera(three.mouse,three.cam); const hits=three.ray.intersectObjects(three.pick,true);
  if(!hits.length) return; let o=hits[0].object, dev=null, rack=null;
  while(o){ if(!dev&&o.userData.device) dev=o.userData.device; if(!rack&&o.userData.rack) rack=o.userData.rack; o=o.parent; }
  if(dev) selectDevice(dev,rack); else if(rack) selectRack(rack); }
function heatTexture(g,racks,m){ const N=128; const cv=document.createElement('canvas'); cv.width=cv.height=N;
  const cc=cv.getContext('2d'); const samples=samplesFor(racks,m);
  for(let i=0;i<N;i++) for(let j=0;j<N;j++){ const gx=(i+0.5)/N*g.width_m, gy=(j+0.5)/N*g.depth_m;
    const v=idw(samples,gx,gy); if(v==null){ cc.clearRect(i,j,1,1); continue; }
    const c=bandColor(v,m.bands); cc.fillStyle=`rgb(${c[0]|0},${c[1]|0},${c[2]|0})`; cc.fillRect(i,N-1-j,1,1); }
  const tx=new THREE.CanvasTexture(cv); tx.minFilter=tx.magFilter=THREE.LinearFilter;
  tx.needsUpdate=true; return tx; }

/* ===================== heatmap legend ===================== */
function drawHLegend(m, racks){ const el=$('#hlegend'); if(!S.heat || !heatReady(m)){ el.style.display='none'; return; }
  el.style.display='block';
  const b0=m.bands[0][0], bN=m.bands[m.bands.length-1][0], span=(bN-b0)||1;
  const pct=v=>Math.round((v-b0)/span*100), dp=m.unit==='%RU'?0:1;
  // title + live/estimated chip
  const isLive = live()&&(m.kind==='liveOrEst'||m.kind==='liveOnly'||m.kind==='either');
  el.querySelector('.ttl').innerHTML = m.label+' <span style="color:var(--muted)">('+m.unit+')</span>'
    + (isLive?'<span class="tag live">live</span>':!metricReal(m)?'<span class="tag est">estimated</span>':'');
  // gradient bar coloured by band thresholds
  const stops=m.bands.map(b=>{ const c=hex2rgb(b[1]); return `rgb(${c[0]},${c[1]},${c[2]}) ${pct(b[0])}%`; }).join(',');
  el.querySelector('.bar').style.background=`linear-gradient(90deg,${stops})`;
  // threshold tick marks: lets the reader map a colour back to a value
  const ticks=el.querySelector('.ticks'); ticks.innerHTML='';
  for(const b of m.bands){ const t=document.createElement('span'); t.className='tick';
    t.style.left=pct(b[0])+'%'; t.textContent=b[0]; ticks.appendChild(t); }
  // actual range over real samples in this room
  const vals=racks.map(m.val).filter(v=>v!=null&&v>0);
  el.querySelector('.rng').innerHTML = vals.length
    ? 'room range <b>'+Math.min(...vals).toFixed(dp)+'</b>–<b>'+Math.max(...vals).toFixed(dp)+'</b> '+m.unit
    : 'no racks reporting';
}

/* ===================== rack detail (layout.md elevation) ===================== */
function selectRack(r){ S.sel=r.rack_id; S.selDev=null; render(); const d=$('#detail'); $('#main').classList.add('detail');
  const ds=devByRack[r.rack_id]||[]; const kw=rackKW(r).toFixed(2);
  let html=`<header><h3>${r.rack_id.split(':').slice(-2).join(' ')}</h3>`+
    `<button class="close" onclick="closeDetail()">x</button></header>`+
    `<div class="meta"><b>${r.datacenter} / ${r.room}</b> - Floor ${r.floor}, Row ${r.row}, Rack ${r.rack_num}<br>`+
    `pos <code>(${r.floor_x}, ${r.floor_y}) m</code> &middot; facing ${r.rack_facing||'-'}<br>`+
    `cold <b>${r.cold_aisle||'-'}</b> / hot <b>${r.hot_aisle||'-'}</b><br>`+
    `${ds.length} devices &middot; IT load <b>${kw} kW</b> &middot; ${occupiedU(r.rack_id)}/42 U`+
    `<br><span style="color:var(--muted)">click a unit for device detail</span></div>`;
  for(const dv of ds){ const u=(dv.rack_unit!=null)?('U'+dv.rack_unit):'';
    html+=`<div class="ru" style="cursor:pointer" onclick="showDev('${r.rack_id}','${dv.id}')"><span class="u">${u}</span>`+
      `<span class="sw" style="background:${tcolor(dv.device_type)}"></span>`+
      `<span><span class="nm">${dv.name||dv.id}</span> <span class="mt">${EMO[dv.device_type]||dv.device_type}`+
      `${dv.model?' &middot; '+dv.model:''}</span></span>`+
      `<span class="pw">${dv.power_draw_w?dv.power_draw_w+' W':''}</span></div>`; }
  d.innerHTML=html;
  if(S.view==='3d') focusRack(r);
}
function selectDevice(d, r){ S.sel=r?r.rack_id:null; S.selDev=d.id; render(); $('#main').classList.add('detail');
  const slot=(d.rack_unit&&d.rack_unit>0)?('U'+d.rack_unit):'zero-U';
  const loc=r?`${r.datacenter} / ${r.room} &middot; Row ${r.row}, Rack ${r.rack_num}`:'';
  let html=`<header><h3>${d.name||d.id}</h3><button class="close" onclick="closeDetail()">x</button></header>`+
    `<div class="meta">`+
    `<span class="sw" style="display:inline-block;vertical-align:middle;background:${tcolor(d.device_type)}"></span> `+
    `<b>${EMO[d.device_type]||d.device_type}</b>${d.model?' &middot; '+d.model:''}<br>`+
    `${d.vendor?d.vendor+'<br>':''}`+
    `${loc?loc+'<br>':''}`+
    `slot <b>${slot}</b>${d.power_draw_w?` &middot; <b>${d.power_draw_w} W</b>`:''}`+
    `${(d.feed_a||d.feed_b)?`<br>feed A <b>${d.feed_a||'-'}</b> &middot; feed B <b>${d.feed_b||'-'}</b>`:''}`+
    `</div>`+
    (r?`<div class="ru" style="cursor:pointer" onclick="showRack('${r.rack_id}')"><span class="nm">&#8617; Rack ${r.rack_num} — full elevation</span></div>`:'');
  $('#detail').innerHTML=html;
  if(S.view==='3d'&&r) focusDevice(d,r);
}
function showRack(id){ const r=rackById[id]; if(r) selectRack(r); }
function showDev(rackId, devId){ const r=rackById[rackId]; const dv=(devByRack[rackId]||[]).find(x=>x.id===devId); if(dv) selectDevice(dv,r); }
function closeDetail(){ S.sel=null; S.selDev=null; $('#main').classList.remove('detail'); $('#detail').innerHTML=''; render(); }

/* ===================== sidebar (inventory, layout.md) ===================== */
function buildSidebar(){ const racks=racksByRoom[S.dc+' / '+S.room]||[];
  const ids=new Set(racks.flatMap(r=>r.device_ids)); const ds=DEVS.filter(d=>ids.has(d.id));
  const cnt={}; for(const d of ds) cnt[d.device_type]=(cnt[d.device_type]||0)+1;
  const totKW=racks.reduce((a,r)=>a+rackKW(r),0).toFixed(1);
  let h=`<h2>Room</h2><div class="legrow"><b>${ds.length}</b>&nbsp;devices in&nbsp;<b>${racks.length}</b>&nbsp;racks</div>`+
    `<div class="legrow">IT load <span class="ct">${totKW} kW</span></div>`;
  h+='<h2>Device types</h2>';
  for(const t of Object.keys(cnt).sort((a,b)=>cnt[b]-cnt[a])){
    h+=`<div class="legrow"><span class="sw" style="background:${tcolor(t)}"></span>${EMO[t]||t}`+
       `<span class="ct">${cnt[t]}</span></div>`; }
  h+='<h2>Tips</h2><div style="color:var(--muted);font-size:11px">'+
     'Click a rack for its RU elevation. Toggle Heatmap; pick a metric. '+
     'Power & capacity are real; inlet thermal is estimated (no live temps in a static asset file).</div>';
  $('#side').innerHTML=h;
}

/* ===================== render dispatch ===================== */
function render(){
  const m=METRIC[S.metric], note=$('#note');
  $('#metricwrap').style.display = S.heat ? '' : 'none';   // metric only matters with the heatmap on
  if(S.heat && !heatReady(m)){ note.style.display='block';
    note.textContent = m.label+' needs live telemetry - start the simulator and enable Live '
      + '(no heatmap is drawn from static/estimated data; heatmap_design sec 3.1).';
  } else note.style.display='none';
  if(S.view==='2d'){ c2d.style.display='block'; $('#c3d').style.display='none'; draw2D(); }
  else { c2d.style.display='none'; $('#c3d').style.display='block'; draw3D(); }
  const n3=S.view==='3d'; $('#nav3d').style.display=n3?'flex':'none'; $('#nav3dhint').style.display=n3?'block':'none';
  $('#cut').style.display=n3?'inline-block':'none';  // Cutaway is a 3D-only control
  buildSidebar();
}

/* ===================== events ===================== */
$('#dc').onchange=e=>{ S.dc=e.target.value; const rs=roomsFor(S.dc); fillSelect($('#room'),rs); S.room=rs[0]; S.sel=null; closeDetailSilent(); render(); };
$('#room').onchange=e=>{ S.room=e.target.value; S.sel=null; closeDetailSilent(); render(); };
$('#v2d').onclick=()=>{ S.view='2d'; $('#v2d').classList.add('on'); $('#v3d').classList.remove('on'); render(); };
$('#v3d').onclick=()=>{ S.view='3d'; three&&(three.posed=false); $('#v3d').classList.add('on'); $('#v2d').classList.remove('on'); render(); };
$('#heat').onclick=()=>{ S.heat=!S.heat; const b=$('#heat'); b.textContent='Heatmap: '+(S.heat?'on':'off'); b.classList.toggle('on',S.heat); render(); };
$('#cut').onclick=()=>{ S.cut=!S.cut; const b=$('#cut'); b.textContent='Cutaway: '+(S.cut?'on':'off'); b.classList.toggle('on',S.cut); render(); };
$('#metric').onchange=e=>{ S.metric=e.target.value; render(); };
$('#live').onclick=()=>{ LIVE.on=!LIVE.on; const b=$('#live');
  b.textContent=LIVE.on?'on':'off'; b.classList.toggle('on',LIVE.on);
  if(LIVE.on){ pollLive(); LIVE.timer=setInterval(pollLive,5000); }   // sec 12: 2-5s
  else { clearInterval(LIVE.timer); LIVE.timer=null; LIVE.ok=false; setLstat('',''); render(); } };
$('#api').onchange=()=>{ if(LIVE.on) pollLive(); };
c2d.addEventListener('click',e=>{ const b=c2d.getBoundingClientRect(),x=e.clientX-b.left,y=e.clientY-b.top;
  for(const h of hitDev){ if(x>=h.x&&x<=h.x+h.w&&y>=h.y&&y<=h.y+h.h){ selectDevice(h.d,h.r); return; } }
  for(const h of hit2d){ if(x>=h.x&&x<=h.x+h.w&&y>=h.y&&y<=h.y+h.h){ selectRack(h.r); return; } } });
c2d.addEventListener('mousemove',e=>{ const b=c2d.getBoundingClientRect(),x=e.clientX-b.left,y=e.clientY-b.top;
  const tip=$('#tip'); for(const h of hit2d){ if(x>=h.x&&x<=h.x+h.w&&y>=h.y&&y<=h.y+h.h){
    const r=h.r,m=METRIC[S.metric]; tip.style.display='block'; tip.style.left=(x+14)+'px'; tip.style.top=(y+12)+'px';
    tip.innerHTML=`<b>Row ${r.row} / Rack ${r.rack_num}</b><br>${r.device_count} dev &middot; ${rackKW(r).toFixed(2)} kW`+
      `<br>${m.label}: ${m.val(r).toFixed(1)} ${m.unit}`; return; } } tip.style.display='none'; });
function closeDetailSilent(){ S.selDev=null; $('#main').classList.remove('detail'); $('#detail').innerHTML=''; }
window.addEventListener('resize',()=>render());

// Proactive teardown: the React host destroys this iframe on 'Back'. Release the
// WebGL context + GPU memory and stop the live poll immediately, rather than
// waiting on GC — that lag was what made the parent app freeze after a long
// session. Idempotent and guarded so a failure here never blocks navigation.
window.addEventListener('pagehide',()=>{ try{
  if(LIVE.timer){ clearInterval(LIVE.timer); LIVE.timer=null; LIVE.on=false; }
  if(three){ disposeGroup(three.root);
    if(three.ctr&&three.ctr.dispose) three.ctr.dispose();
    three.rn.dispose(); if(three.rn.forceContextLoss) three.rn.forceContextLoss();
    const el=three.rn.domElement; if(el&&el.parentNode) el.parentNode.removeChild(el);
    three=null; }
}catch(e){} });

/* ===================== init ===================== */
fillSelect($('#dc'),DCs); S.dc=DCs[0]; const r0=roomsFor(S.dc); fillSelect($('#room'),r0);
S.room=r0.find(x=>/Server Hall/.test(x))||r0[0]; $('#room').value=S.room;
if(HASH.get('api')) $('#api').value=HASH.get('api');           // host-supplied API base
// Embedded in the web UI: host supplies API base + token, so hide the manual
// Live input + toggle (only the status indicator remains). Standalone keeps them.
const EMBED = !!(HASH.get('api')||HASH.get('token'));
if(EMBED){ for(const id of ['livelbl','api','live','lstat']) $('#'+id).style.display='none'; }
render();
if(HASH.get('live')==='1'){ $('#live').click(); }              // auto-connect when embedded
</script>
</body>
</html>
"""


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    src, dst = argv[1], argv[2]
    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Trim embedded payload to what the viewer needs (keeps the HTML smaller).
    slim = {
        "floorplan": data["floorplan"],
        "racks": data["racks"],
        "devices": [
            {k: d.get(k) for k in ("id", "name", "device_type", "model",
                                   "rack_id", "rack_unit", "power_draw_w",
                                   "feed_a", "feed_b", "vendor", "mounting")}
            for d in data["devices"]
        ],
    }
    html = HEAD + json.dumps(slim, separators=(",", ":")) + TAIL
    with open(dst, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {dst}  ({len(html)//1024} KB)  "
          f"{len(slim['racks'])} racks, {len(slim['devices'])} devices, "
          f"{len(slim['floorplan']['rooms'])} rooms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))