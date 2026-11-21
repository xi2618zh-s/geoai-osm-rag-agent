"""Standalone, offline SVG view for an audited M10 AnalysisResult."""
from __future__ import annotations

import html
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agent.result_presentation import AnalysisResult, VerifiedRun
    from src.datasets import DatasetRegistry


def render_analysis_html(result: "AnalysisResult", primary: "VerifiedRun", registry: "DatasetRegistry", *, comparison: "VerifiedRun | None" = None) -> str:
    from src.agent.result_presentation import validate_analysis_result
    validate_analysis_result(result, primary, registry, comparison=comparison)
    return _render_audited_payload(result)


def _render_audited_payload(result: "AnalysisResult") -> str:
    payload = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    page = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ · GeoAI</title>
<style>
:root{font-family:Inter,system-ui,"Segoe UI",sans-serif;color:#182636;background:#edf3f5}
*{box-sizing:border-box}body{margin:0}header{padding:28px 4vw;background:#10384a;color:#fff}
header .eyebrow{letter-spacing:.17em;text-transform:uppercase;color:#7ee0d0;font-size:12px;font-weight:700}
header h1{font-size:clamp(28px,4vw,44px);margin:10px 0}header p{margin:0;color:#c5dde4}
main{padding:25px 4vw 50px;max-width:1500px;margin:auto}
section{background:#fff;border-radius:16px;padding:22px;margin-bottom:20px;box-shadow:0 8px 24px #17354b12}
h2{font-size:20px;margin:0 0 14px}h3{font-size:15px;margin:17px 0 8px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}
.metric{border:1px solid #dce7ea;border-radius:12px;padding:14px;background:#f8fbfc}
.metric strong{display:block;font-size:28px;color:#0c6d72}.metric span{font-size:13px;color:#526575}
.layout{display:grid;grid-template-columns:minmax(0,2fr) minmax(270px,1fr);gap:20px}
@media(max-width:900px){.layout{grid-template-columns:1fr}}
#map{width:100%;height:410px;background:#f6fafb;border:1px solid #cfdfe4;border-radius:12px}
#layers{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0}
label.layer{display:flex;align-items:center;gap:6px;font-size:13px;background:#f1f6f7;padding:6px 9px;border-radius:8px}
.swatch{display:inline-block;width:12px;height:12px;border-radius:3px}
#popup{min-height:62px;background:#f5fafb;border:1px solid #d9e7e9;padding:10px;border-radius:8px;font-size:13px;white-space:pre-wrap;overflow-wrap:anywhere}
.bar-row{display:grid;grid-template-columns:125px 1fr 55px;align-items:center;gap:8px;font-size:13px;margin:10px 0}
.bar-track{height:17px;background:#eaf1f4;border-radius:12px;overflow:hidden}.bar{height:100%;border-radius:12px;background:#19a7a0}
.table-wrap{overflow:auto;max-height:360px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:9px 11px;text-align:left;border-bottom:1px solid #e5ecef;white-space:nowrap}th{position:sticky;top:0;background:#f2f7f8}
a.download{display:inline-block;margin:5px 8px 5px 0;padding:9px 12px;border-radius:8px;background:#0f6973;color:#fff;text-decoration:none;font-size:13px}
ul{padding-left:20px;line-height:1.55}small,.muted{color:#5c7180}
</style>
</head>
<body>
<header><div class="eyebrow">GeoAI · verified analysis</div><h1 id="title"></h1><p id="subtitle"></p></header>
<main>
<section><h2>Conclusion</h2><p id="headline"></p><div class="grid" id="metrics"></div></section>
<div class="layout"><section><h2>Map layers</h2><div id="layers"></div><svg id="map" viewBox="0 0 800 410" role="img" aria-label="Verified vector layers"></svg><small id="map-note"></small></section>
<section><h2>Selected feature</h2><div id="popup">Select a feature on the map.</div><h3 id="chart-title"></h3><div id="chart"></div></section></div>
<section><h2>Results table</h2><div class="table-wrap"><table id="table"></table></div><small id="table-note"></small></section>
<section><h2>Business summary</h2><div id="report"></div><h3>Downloads</h3><div id="downloads"></div></section>
<div class="layout"><section><h2>Sources and steps</h2><div id="sources"></div><div id="steps"></div></section>
<section><h2>Assumptions and limits</h2><div id="warnings"></div><div id="assumptions"></div><div id="limitations"></div></section></div>
</main>
<script id="analysis-data" type="application/json">__PAYLOAD__</script>
<script>
(function(){
"use strict";
const result=JSON.parse(document.getElementById("analysis-data").textContent);
const byId=(id)=>document.getElementById(id);
const metricMap=Object.fromEntries(result.metrics.map((m)=>[m.metric_id,m]));
const number=(value)=>Number.isInteger(value)?String(value):Number(value).toLocaleString(undefined,{maximumFractionDigits:4});
const claim=(template)=>template.replace(/\{([a-z][a-z0-9_]*)\}/g,(_,id)=>number(metricMap[id].value));
byId("title").textContent=result.title;
byId("subtitle").textContent=result.place+" · "+result.template+" · "+result.crs;
byId("headline").textContent=claim(result.headline_template);
result.metrics.forEach((m)=>{const card=document.createElement("div");card.className="metric";
const value=document.createElement("strong");value.textContent=number(m.value);
const label=document.createElement("span");label.textContent=m.label+" · "+m.unit;
card.append(value,label);byId("metrics").append(card)});
result.report_templates.forEach((text)=>{const p=document.createElement("p");p.textContent=claim(text);byId("report").append(p)});
result.downloads.forEach((item)=>{const a=document.createElement("a");a.className="download";
a.href=item.href;a.download=item.filename;a.textContent="Download "+item.filename;byId("downloads").append(a)});
const table=byId("table"),head=document.createElement("thead"),hrow=document.createElement("tr");
result.table.columns.forEach((name)=>{const th=document.createElement("th");th.textContent=name;hrow.append(th)});
head.append(hrow);table.append(head);const body=document.createElement("tbody");
result.table.rows.forEach((row)=>{const tr=document.createElement("tr");row.forEach((cell)=>{const td=document.createElement("td");td.textContent=cell;tr.append(td)});body.append(tr)});
table.append(body);byId("table-note").textContent="Showing "+result.table.rows.length+" of "+result.table.total_rows+" verified rows.";
byId("chart-title").textContent=result.chart.title;
const chartMetrics=result.chart.metric_ids.map((id)=>metricMap[id]);const max=Math.max(1,...chartMetrics.map((m)=>Math.max(0,m.value)));
chartMetrics.forEach((m)=>{const row=document.createElement("div");row.className="bar-row";
const label=document.createElement("span");label.textContent=m.label;
const track=document.createElement("div");track.className="bar-track";const bar=document.createElement("div");bar.className="bar";
bar.style.width=(100*Math.max(0,m.value)/max)+"%";track.append(bar);
const value=document.createElement("strong");value.textContent=number(m.value);row.append(label,track,value);byId("chart").append(row)});
function list(id,title,values){const box=byId(id),h=document.createElement("h3");h.textContent=title;box.append(h);
const ul=document.createElement("ul");values.forEach((item)=>{const li=document.createElement("li");li.textContent=item;ul.append(li)});box.append(ul)}
list("sources","Dataset provenance",result.sources.map((s)=>s.source+" · "+s.source_version+" · "+s.dataset_id+" · "+s.content_hash));
list("steps","Verified steps",result.steps.map((s)=>s.step_id+" "+s.tool+" · "+(s.reused?"reused":"executed")+" · "+s.output_id));
list("warnings","Warnings",result.warnings.length?result.warnings:["None recorded."]);
list("assumptions","Assumptions",result.assumptions);
list("limitations","Limitations",result.limitations);
const layers=result.layers,visible=new Set(layers.map((l)=>l.layer_id));
layers.forEach((layer)=>{const label=document.createElement("label");label.className="layer";
const check=document.createElement("input");check.type="checkbox";check.checked=true;check.addEventListener("change",()=>{check.checked?visible.add(layer.layer_id):visible.delete(layer.layer_id);drawMap()});
const swatch=document.createElement("span");swatch.className="swatch";swatch.style.background=layer.color;
const text=document.createElement("span");text.textContent=layer.label+" ("+layer.preview_count+"/"+layer.feature_count+")";
label.append(check,swatch,text);byId("layers").append(label)});
function pairs(coordinates,out){if(!Array.isArray(coordinates))return;
if(coordinates.length>=2&&typeof coordinates[0]==="number"&&typeof coordinates[1]==="number"){if(Number.isFinite(coordinates[0])&&Number.isFinite(coordinates[1]))out.push(coordinates);return}
coordinates.forEach((part)=>pairs(part,out))}
const all=[];layers.forEach((l)=>l.geojson.features.forEach((f)=>{if(f.geometry)pairs(f.geometry.coordinates,all)}));
const xs=all.map((p)=>p[0]),ys=all.map((p)=>p[1]);
const minX=xs.length?Math.min(...xs):0,maxX=xs.length?Math.max(...xs):1,minY=ys.length?Math.min(...ys):0,maxY=ys.length?Math.max(...ys):1;
const dx=Math.max(maxX-minX,0.000001),dy=Math.max(maxY-minY,0.000001);
const project=(p)=>[40+720*(p[0]-minX)/dx,370-330*(p[1]-minY)/dy];
const NS="http://www.w3.org/2000/svg";
function shape(feature,color){const g=feature.geometry;if(!g)return [];
const out=[];function line(coords,close){const pts=coords.map(project);if(!pts.length)return;
const path=document.createElementNS(NS,"path");path.setAttribute("d",pts.map((p,i)=>(i?"L":"M")+p[0]+","+p[1]).join(" ")+(close?" Z":""));
path.setAttribute("fill",close?color:"none");path.setAttribute("fill-opacity",close?"0.18":"0");
path.setAttribute("stroke",color);path.setAttribute("stroke-width","2");out.push(path)}
function point(p){const c=document.createElementNS(NS,"circle"),xy=project(p);c.setAttribute("cx",xy[0]);c.setAttribute("cy",xy[1]);c.setAttribute("r","5");c.setAttribute("fill",color);c.setAttribute("stroke","#fff");c.setAttribute("stroke-width","1.5");out.push(c)}
if(g.type==="Point")point(g.coordinates);
else if(g.type==="MultiPoint")g.coordinates.forEach(point);
else if(g.type==="LineString")line(g.coordinates,false);
else if(g.type==="MultiLineString")g.coordinates.forEach((x)=>line(x,false));
else if(g.type==="Polygon")g.coordinates.forEach((x)=>line(x,true));
else if(g.type==="MultiPolygon")g.coordinates.forEach((poly)=>poly.forEach((x)=>line(x,true)));
return out}
function drawMap(){const svg=byId("map");while(svg.firstChild)svg.removeChild(svg.firstChild);
layers.forEach((layer)=>{if(!visible.has(layer.layer_id))return;
layer.geojson.features.forEach((feature,index)=>shape(feature,layer.color).forEach((node)=>{node.style.cursor="pointer";
node.addEventListener("click",()=>{const props=feature.properties||{};
const entries=layer.popup_fields.map((field)=>field+": "+String(props[field]??""));
byId("popup").textContent=layer.label+" · "+String(feature.id??("row-"+(index+1)))+"\n"+entries.join("\n")});
svg.append(node)}))})}
drawMap();
byId("map-note").textContent="Coordinate preview in "+result.crs+"; no online basemap. Toggle layers or select a feature.";
})();
</script>
</body></html>
"""
    return page.replace("__TITLE__", html.escape(result.title, quote=True)).replace("__PAYLOAD__", payload)


def write_analysis_html(path, result: "AnalysisResult", primary: "VerifiedRun", registry: "DatasetRegistry", *, comparison: "VerifiedRun | None" = None) -> None:
    from pathlib import Path
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(render_analysis_html(result, primary, registry, comparison=comparison))

