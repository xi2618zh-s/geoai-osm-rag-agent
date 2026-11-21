"""Standalone offline SVG page for an audited ScenarioReport (no external scripts or styles)."""
from __future__ import annotations

import html
import json

from .report import ScenarioReport


def render_scenario_html(report: ScenarioReport) -> str:
    payload = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    page = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ · GeoAI scenario</title>
<style>
:root{font-family:Inter,system-ui,"Segoe UI",sans-serif;color:#182636;background:#edf3f5}
*{box-sizing:border-box}body{margin:0}header{padding:26px 4vw;background:#10384a;color:#fff}
header .eyebrow{letter-spacing:.16em;text-transform:uppercase;color:#7ee0d0;font-size:12px;font-weight:700}
header h1{font-size:clamp(26px,4vw,40px);margin:8px 0}header p{margin:4px 0;color:#c5dde4}
main{padding:22px 4vw 48px;max-width:1500px;margin:auto}
section{background:#fff;border-radius:14px;padding:20px;margin-bottom:18px;box-shadow:0 8px 24px #17354b12;min-width:0;overflow-wrap:anywhere}
h2{font-size:19px;margin:0 0 12px}h3{font-size:15px;margin:14px 0 8px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.metric{border:1px solid #dce7ea;border-radius:12px;padding:12px;background:#f8fbfc}
.metric strong{display:block;font-size:24px;color:#0c6d72}.metric span{font-size:13px;color:#526575}
.layout{display:grid;grid-template-columns:minmax(0,2fr) minmax(260px,1fr);gap:18px}
@media(max-width:900px){.layout{grid-template-columns:1fr}}
#map{width:100%;height:460px;background:#f6fafb;border:1px solid #cfdfe4;border-radius:12px}
#layers{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
label.layer{display:flex;align-items:center;gap:6px;font-size:13px;background:#f1f6f7;padding:6px 9px;border-radius:8px}
.swatch{display:inline-block;width:12px;height:12px;border-radius:3px}
#popup{min-height:62px;background:#f5fafb;border:1px solid #d9e7e9;padding:10px;border-radius:8px;font-size:13px;white-space:pre-wrap;overflow-wrap:anywhere}
.bar-row{display:grid;grid-template-columns:150px 1fr 70px;align-items:center;gap:8px;font-size:13px;margin:10px 0}
.bar-track{height:16px;background:#eaf1f4;border-radius:12px;overflow:hidden}.bar{height:100%;border-radius:12px;background:#19a7a0}
.table-wrap{overflow:auto;max-height:360px}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #e5ecef;white-space:nowrap}th{position:sticky;top:0;background:#f2f7f8}
a.download{display:inline-block;margin:5px 8px 5px 0;padding:8px 12px;border-radius:8px;background:#0f6973;color:#fff;text-decoration:none;font-size:13px}
ul{padding-left:20px;line-height:1.55}small,.muted{color:#5c7180}code{background:#f1f6f7;padding:2px 5px;border-radius:5px}
</style>
</head>
<body>
<header><div class="eyebrow">GeoAI · verified scenario</div><h1 id="title"></h1><p id="command"></p><p id="grammar"></p></header>
<main>
<section><h2>Conclusion</h2><p id="headline"></p><div class="grid" id="metrics"></div></section>
<div class="layout"><section><h2>Map layers</h2><div id="layers"></div><svg id="map" viewBox="0 0 800 460" role="img" aria-label="Verified vector layers"></svg><small id="map-note"></small></section>
<section><h2>Selected feature</h2><div id="popup">Select a feature on the map.</div><h3 id="chart-title"></h3><div id="chart"></div></section></div>
<section><h2 id="table-title"></h2><div class="table-wrap"><table id="table"></table></div><small id="table-note"></small></section>
<section><h2>Business summary</h2><div id="report"></div><h3>Downloads</h3><div id="downloads"></div></section>
<div class="layout"><section><h2>Plan and verified steps</h2><h3>Clarifications</h3><ul id="clarifications"></ul><h3>Plan preview</h3><ul id="plan"></ul><h3>Executed steps</h3><ul id="steps"></ul><h3>Recovery events</h3><ul id="recoveries"></ul></section>
<section><h2>Data, licences and limits</h2><h3>Licences and attribution</h3><ul id="licenses"></ul><h3>Sources</h3><ul id="sources"></ul><h3>Warnings</h3><ul id="warnings"></ul><h3>Assumptions</h3><ul id="assumptions"></ul><h3>Limitations</h3><ul id="limitations"></ul></section></div>
</main>
<script id="scenario-data" type="application/json">__PAYLOAD__</script>
<script>
(function(){
const data=JSON.parse(document.getElementById('scenario-data').textContent);
const $=id=>document.getElementById(id);
const text=(el,value)=>{el.textContent=value;return el;};
const make=(tag,value)=>text(document.createElement(tag),value===undefined?'':String(value));
const list=(id,items)=>{const ul=$(id);if(!items.length){ul.appendChild(make('li','None'));}items.forEach(i=>ul.appendChild(make('li',i)));};
const values={};data.metrics.forEach(m=>{values[m.metric_id]=typeof m.value==='number'&&!Number.isInteger(m.value)?m.value.toPrecision(4):String(m.value);});
const fill=t=>t.replace(/\{([a-z][a-z0-9_]*)\}/g,(_,k)=>values[k]);
text($('title'),data.title+' — '+data.place);
text($('command'),'Command: '+data.command);
text($('grammar'),data.grammar_note);
text($('headline'),fill(data.headline_template));
data.metrics.forEach(m=>{const d=document.createElement('div');d.className='metric';d.appendChild(make('strong',values[m.metric_id]));d.appendChild(make('span',m.label+' ('+m.unit+')'));$('metrics').appendChild(d);});
data.report_templates.forEach(t=>$('report').appendChild(make('p',fill(t))));
const svg=$('map');const NS='http://www.w3.org/2000/svg';
let minx=Infinity,miny=Infinity,maxx=-Infinity,maxy=-Infinity;
const walk=(c,f)=>{if(typeof c[0]==='number'){f(c);}else{c.forEach(x=>walk(x,f));}};
data.layers.forEach(l=>l.geojson.features.forEach(ft=>{if(ft.geometry)walk(ft.geometry.coordinates,p=>{minx=Math.min(minx,p[0]);maxx=Math.max(maxx,p[0]);miny=Math.min(miny,p[1]);maxy=Math.max(maxy,p[1]);});}));
const s=Math.min(780/Math.max(maxx-minx,1),440/Math.max(maxy-miny,1));
const ox=10+(780-(maxx-minx)*s)/2,oy=10+(440-(maxy-miny)*s)/2;
const px=p=>[ox+(p[0]-minx)*s,450-oy+10-(p[1]-miny)*s];
const ring=r=>r.map(p=>px(p).join(',')).join(' ');
const groups={};
data.layers.forEach(l=>{const g=document.createElementNS(NS,'g');groups[l.layer_id]=g;svg.appendChild(g);
 l.geojson.features.forEach(ft=>{const geom=ft.geometry;if(!geom)return;let shapes=[];
  const polys=geom.type==='Polygon'?[geom.coordinates]:geom.type==='MultiPolygon'?geom.coordinates:[];
  polys.forEach(poly=>{const e=document.createElementNS(NS,'polygon');e.setAttribute('points',ring(poly[0]));e.setAttribute('fill',l.color);e.setAttribute('fill-opacity','0.35');e.setAttribute('stroke',l.color);e.setAttribute('stroke-width','0.8');shapes.push(e);});
  if(geom.type==='Point'){const e=document.createElementNS(NS,'circle');const q=px(geom.coordinates);e.setAttribute('cx',q[0]);e.setAttribute('cy',q[1]);e.setAttribute('r','3.2');e.setAttribute('fill',l.color);shapes.push(e);}
  shapes.forEach(e=>{e.style.cursor='pointer';e.addEventListener('click',()=>{const props=ft.properties||{};text($('popup'),l.label+'\n'+l.popup_fields.map(k=>k+': '+(props[k]===undefined||props[k]===null?'':props[k])).join('\n'));});g.appendChild(e);});
 });
 const lab=document.createElement('label');lab.className='layer';const cb=document.createElement('input');cb.type='checkbox';cb.checked=true;cb.addEventListener('change',()=>{g.style.display=cb.checked?'':'none';});
 const sw=document.createElement('span');sw.className='swatch';sw.style.background=l.color;lab.appendChild(cb);lab.appendChild(sw);lab.appendChild(make('span',l.label+' ('+l.preview_count+'/'+l.feature_count+')'));$('layers').appendChild(lab);});
text($('map-note'),'Coordinates in '+data.crs+' (projected metres); no online basemap. Previews show at most the first 500 features per layer; downloads hold the full verified result.');
text($('chart-title'),data.chart.title);
const chartVals=data.chart.metric_ids.map(id=>data.metrics.find(m=>m.metric_id===id));const top=Math.max(...chartVals.map(m=>m.value),1);
chartVals.forEach(m=>{const r=document.createElement('div');r.className='bar-row';r.appendChild(make('span',m.label));const t=document.createElement('div');t.className='bar-track';const b=document.createElement('div');b.className='bar';b.style.width=(100*m.value/top)+'%';t.appendChild(b);r.appendChild(t);r.appendChild(make('span',values[m.metric_id]));$('chart').appendChild(r);});
text($('table-title'),data.table.title);
const tr=document.createElement('tr');data.table.columns.forEach(c=>tr.appendChild(make('th',c)));$('table').appendChild(tr);
data.table.rows.forEach(row=>{const r=document.createElement('tr');row.forEach(c=>r.appendChild(make('td',c)));$('table').appendChild(r);});
text($('table-note'),'Showing '+data.table.rows.length+' of '+data.table.total_rows+' rows.');
data.downloads.forEach(d=>{const a=make('a',d.filename+' ('+d.feature_count+' features)');a.className='download';a.href=d.href;a.download=d.filename;$('downloads').appendChild(a);});
list('clarifications',data.clarifications.map(c=>c.reason_code+': '+c.question+' → '+c.answer));
list('plan',data.plan_preview);
list('steps',data.steps.map(s=>s.step_id+' '+s.tool+' (attempts '+s.attempts+')'));
list('recoveries',data.recoveries.map(r=>r.action+' '+r.status+' ('+r.reason_code+')'));
list('licenses',data.data_licenses.map(l=>l.role+': '+l.license+' — '+l.attribution+(l.provider_verified?'':' (source provider unverified)')+'; '+l.temporal_note));
list('sources',data.sources.map(s=>s.role+': '+s.source+' @ '+s.source_version+' · '+s.row_count+' rows · '+s.content_hash.slice(0,19)+'…'));
list('warnings',data.warnings);list('assumptions',data.assumptions);list('limitations',data.limitations);
})();
</script>
</body>
</html>
"""
    return page.replace("__TITLE__", html.escape(report.title)).replace("__PAYLOAD__", payload)
