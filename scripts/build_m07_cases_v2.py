"""Create supplemental M07 v2 cases without overwriting v1."""
import json
from pathlib import Path

source=Path("benchmarks/data/dev/m07_plan_cases_v1.jsonl")
target=Path("benchmarks/data/dev/m07_plan_cases_v2.jsonl")
rows=[json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
places=["Lund","Malmo","Stockholm","Uppsala","Gothenburg","Vasteras","Orebro","Linkoping","Helsingborg","Norrkoping"]
for i, place in enumerate(places,1):
    base=next(x for x in rows if x["case_id"]==f"m07_aggregate_by_region_{i:02d}")
    request=json.loads(json.dumps(base["request"]))
    request["datasets"]["sites"]["required_fields"].append("amount")
    request["query"]=f"aggregate sum amount by region_id from sites into regions using region_id in {place}"
    rows.append({
        "case_id":f"m07_aggregate_sum_{i:02d}",
        "task_class":"aggregate_sum",
        "request":request,
        "expected":{
            "status":"plan",
            "tools":["aggregate_by_region"],
            "parameters":{"regions":"regions","values":"sites","region_id_field":"region_id","values_region_id_field":"region_id","operation":"sum","field":"amount"},
        },
    })
with target.open("x",encoding="utf-8",newline="\n") as stream:
    for row in rows:
        stream.write(json.dumps(row,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n")
print(len(rows),target)
