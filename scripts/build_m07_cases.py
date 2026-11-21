"""Build the frozen M07 synthetic plan cases once; refuse overwrite."""
import json
from pathlib import Path
places = ["Lund","Malmo","Stockholm","Uppsala","Gothenburg","Vasteras","Orebro","Linkoping","Helsingborg","Norrkoping"]
roles = {
 "sites":{"kind":"vector","source":"fixture:sites","crs":"EPSG:3006","required_fields":["category","region_id"]},
 "schools":{"kind":"vector","source":"fixture:schools","crs":"EPSG:3006","required_fields":[]},
 "regions":{"kind":"vector","source":"fixture:regions","crs":"EPSG:3006","required_fields":["region_id","population"]},
}
rows=[]
def add(group,i,query,tools,params,status="plan"):
 rows.append({"case_id":f"m07_{group}_{i+1:02d}","task_class":group,"request":{"query":query,"datasets":roles,"analysis_crs":"EPSG:3006","place_crs":"EPSG:4326","output_crs":"EPSG:3006"},"expected":{"status":status,"tools":tools,"parameters":params}})
for i,p in enumerate(places):
 add("search",i,f"search sites in {p}",["filter_attributes"],{"role":"sites"})
 add("filtered_search",i,f"search sites where category = cafe in {p}",["filter_attributes"],{"role":"sites","field":"category","operator":"eq","value":"cafe"})
 distance=100+50*i
 add("within_distance",i,f"find sites within {distance} m of schools in {p}",["buffer_features","spatial_join"],{"target":"sites","anchor":"schools","distance_m":distance})
 k=i%3+1
 add("nearest",i,f"find {k} nearest schools to sites in {p}",["nearest_features"],{"source":"sites","target":"schools","k":k})
 add("within",i,f"find sites within regions in {p}",["features_within"],{"source":"sites","container":"regions"})
 pred=("intersects","within","contains")[i%3]
 add("spatial_join",i,f"join sites with regions on {pred} in {p}",["spatial_join"],{"left":"sites","right":"regions","predicate":pred})
 add("aggregate_by_region",i,f"aggregate count by region_id from sites into regions using region_id in {p}",["aggregate_by_region"],{"regions":"regions","values":"sites","region_id_field":"region_id","values_region_id_field":"region_id","operation":"count"})
 add("rank",i,f"rank regions by population desc in {p}",[],{"role":"regions","field":"population","direction":"desc"},"unsupported")
out=Path("benchmarks/data/dev/m07_plan_cases_v1.jsonl")
with out.open("x",encoding="utf-8",newline="\n") as stream:
 for row in rows:
  stream.write(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n")
print(len(rows),out)

