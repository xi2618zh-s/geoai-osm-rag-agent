# M12a 评测冻结 v1：学校覆盖缺口场景（m12a-protocol-v1）

- 冻结日期：2026-10-08（Asia/Shanghai）。冻结时 `src/scenarios/` 不存在，未写任何场景层或场景专属代码；顺序符合用户要求：预检门 v2 通过 → Fixture 构建 → 独立 oracle → 本冻结（案例、参考答案、阈值）→ 测试先行 → 实现 → 正式运行。冻结后不得据系统结果修改本文件、案例、参考或阈值；如需修改须新版本并记录原因。
- 依据：计划 v2.1、ADR-0021（场景层）、ADR-0024（数据与预检门 v2）。哈希见冻结 manifest `benchmarks/results/m12/m12a/freeze/m12a_freeze_manifest_v1.json`。

## 1. 数据与目录（m12a-catalogue-v1）

| 角色 | 文件 | kind/CRS | source / source_version | 许可与署名 |
|---|---|---|---|---|
| `population_grid_raw` | `data/fixtures/population/scb/m12_lund_v1/scb_population_grid_2025_lund_20km_raw.geojson` | vector / EPSG:3006 | `scb:befolkning_1km_2025` / `2025-12-31` | CC0-1.0；Statistics Sweden (SCB) |
| `osm_poi` | `data/fixtures/osm/m12_lund_poi_v1/lund_poi_points_v1.geojson` | vector / EPSG:4326 | `osm:m12-lund-poi` / `pbf-sha256:da92cd12c8ae` | ODbL-1.0；© OpenStreetMap contributors；源 PBF 提供方未核实 |

- 研究区：`Lund`，EPSG:3006 BBOX `378000,6166000,398000,6186000`，分析/输出 CRS EPSG:3006。
- 边缘网格规则（预检门 v2）：网格为 WFS BBOX 过滤返回的全部 453 格（与 BBOX 相交或接触，未裁剪）；人口合计只能称“WFS 返回网格的已报告人口合计”，不得称研究区或 20×20 km 精确人口。
- 目录变体（评测运行器在临时注册表中确定性构造）：`standard`；`poi_crs_mislabeled`（同一 POI 文件字节以 EPSG:3006 登记，目录声明也是 EPSG:3006——元数据错误）；`poi_without_schools`（POI 文件去掉 `amenity == "school"` 的要素后以 EPSG:4326 登记）。`alternate_snapshot=true` 时，policy 的 `alternate_sources["osm_poi"]` 为同一文件以 EPSG:4326 登记的已验证快照。

## 2. 受限命令语法（m12a-grammar-v1；如实称“受限命令语法”，不是开放式自然语言理解）

- 规范化：去首尾空白，内部连续空白折叠为单个空格；关键词大小写不敏感。
- 产生式（子句顺序固定，均可缺省）：`find school coverage gaps` [` farther than ` DIST ` ` UNIT] [` where children aged ` A`-`B ` are at least ` N] [` in ` PLACE]。DIST=`[0-9]+(\.[0-9]+)?`，UNIT=`m|km`，A/B/N=`[0-9]+`，PLACE=`[A-Za-z][A-Za-z -]*`。不匹配 → `unsupported`，原因 `command_not_recognized`。
- 槽位校验顺序（第一个失败者决定唯一原因）：① 距离存在且换算米数 ≤0 或 >2000 → `distance_out_of_range`（missing_fields `[distance]`）；② 年龄段存在且不属 {0-4, 5-9, 10-14, 15-19} → `age_band_unsupported`（`[age_band]`，choices 按此顺序）；③ 阈值存在且 <1 → `threshold_out_of_range`（`[threshold]`）；④ 地点存在且 casefold 后不等于 `lund` → `place_not_covered`（`[place]`，choices `[Lund]`）；⑤ 任一缺失 → `missing_parameters`，missing_fields 按 distance、age_band、threshold、place 的顺序列出缺失项。全部合法 → `plan`。
- 澄清合同 `ScenarioClarification` v1.0（新合同，不复用 GeoDSL 封闭枚举）。回答格式：`ITEM (; ITEM)*`，ITEM ∈ `distance DIST UNIT`、`age band A-B`、`threshold N`、`place PLACE`；每项只能回答本次 missing_fields 中的槽位且最多一次，原命令须与澄清记录一致，否则 `SCENARIO_CLARIFICATION_INVALID`。补全后重新按上条校验。距离以米记录（整数值记为 int）。

## 3. 计划（m12a-plan-v1）与执行

- 工具序列（POI 声明 CRS ≠ EPSG:3006 时）：`step_1 load_population_dataset(population_grid_raw, provider=scb, dataset_year=2025) → population_grid`；`step_2 reproject_dataset(osm_poi, EPSG:3006) → poi_3006`；`step_3 filter_attributes([amenity eq school], all) → schools`；`step_4 buffer_features(schools, D) → school_service_areas`；`step_5 nearest_features(population_grid → schools, k=1) → grid_nearest_school`；`step_6 filter_attributes([distance_m gt D, left_population_age_<band> gte N], all) → coverage_gaps`。POI 声明 CRS 等于 EPSG:3006 时不插入重投影（共 5 步，编号顺延）。空结果策略：步骤 1–5 拒绝（`SCENARIO_EMPTY_UNEXPECTED`），步骤 6 允许。≤12 步；工具调用（含内部 `validate_geometry`）≤64。
- 执行前绑定检查（任何工具调用之前，对每个角色）：DatasetRef 当前且哈希一致、kind 与目录一致、DatasetRef CRS 等于目录声明 CRS（否则 `SCENARIO_CRS_MISMATCH`）、**坐标合理性**：DatasetRef bbox 须在声明 CRS 适用范围（pyproj `area_of_use` 经 transform_bounds 换算到该 CRS，四边各放宽 1% 宽/高）内，且与研究区 BBOX（换算到该 CRS）相交；否则 `SCENARIO_CRS_IMPLAUSIBLE`。bbox 不可用（空数据集）时跳过合理性检查，交由空结果策略。
- **错误 CRS 不得重投影**：`SCENARIO_CRS_IMPLAUSIBLE` 时不调用任何分析工具（含 `reproject_dataset`、`repair_geometry`）。恢复只有两条：(a) policy 声明了该角色的替代快照且其通过同样的当前性/哈希/kind/字段/合理性检查 → 记录 `alternate_source` 成功 → 以替代快照重新规划一次（记录 `replan_once`）→ 执行；(b) 显式调用 `reconfirm_source_crs`：证据为仓库内 `data/fixtures/` 下的版本化 manifest（相对路径、无 `..`）中指定文件条目，其 `sha256` 须等于 DatasetRef 内容哈希、manifest 顶层 `crs` 须为合法 EPSG → 以该 CRS 重新登记为新 DatasetRef（血缘 `crs_reconfirmed`，父 ID 为原引用，参数哈希为证据规范 JSON），目录该角色改为新引用与新声明 CRS；否则 `SCENARIO_CRS_EVIDENCE_INVALID`。
- 每步输出：当前性、vector、CRS=EPSG:3006、`validate_geometry`、空结果策略、要素上限（policy `max_features`=10000）。工具异常 `SCENARIO_TOOL_FAILED`（保留原错误码于 finding）；超时同参数重试一次。全局最多一次重规划。
- 记分用“调用的分析工具序列”（invoked_tools）= 实际调用过的分析工具（不含内部 `validate_geometry`），按调用顺序；“计划工具序列”（planned_tools）= 最终执行那次运行在任何重规划之前的计划。

## 4. 报告（ScenarioReport v1.0）

- 指标 ID 与证据：`gap_cell_count`（dataset_row_count：coverage_gaps）、`gap_children`（dataset_field_sum：coverage_gaps.`left_population_age_<band>`）、`total_children`（dataset_field_sum：population_grid.`population_age_<band>`）、`gap_children_share`（ratio：gap_children/total_children，total 为 0 时省略）、`school_count`（dataset_row_count：schools）、`grid_cell_count`（dataset_row_count：population_grid）、`band_not_reported_cells`（dataset_field_null_count：population_grid.`population_age_<band>`）、`distance_threshold_m`、`children_threshold`、`age_band_min`、`age_band_max`（plan_parameter）。报告模板不得含字面数字。
- 不报告、不评分 `max_gap_distance_m`：边缘网格最多伸出研究区 1 km，POI 只抽取到研究区外 3 km，故 >2000 m 的最近距离只是上界（真实值 >2000 m 但可能更小）；表格列名为“到抽取范围内最近学校的距离”，限制说明写明此点。
- 图层 4 个：SCB 1 km 网格、学校（OSM）、学校直线缓冲（不是步行范围）、覆盖缺口；表为缺口格（grid_id、该年龄段儿童、总人口、距离、最近学校）；下载缺口 GeoJSON/CSV（空结果也输出合法空文件）。
- 必含：数据许可与署名分列（SCB CC0-1.0 / Statistics Sweden；OSM ODbL-1.0 / © OpenStreetMap contributors，PBF 提供方未核实）；人口表述（合计覆盖 WFS 返回网格、含边缘网格、非精确人口）；“restricted command grammar”字样；限制（欧氏距离非路网、整格保守判定、年龄段代理、OSM 完整性与重复、数据时间不一致、>2000 m 距离为上界）。禁止出现：`isochrone`、`walking time`、`travel time`、`natural language understanding`。
- `validate_scenario_report` 规范重建，任何偏差 `SCENARIO_REPORT_EVIDENCE_MISMATCH`；partial 不生成报告。HTML 为自包含 SVG（无外部脚本/样式），含 4 个图层标签、两条许可署名与受限命令语法说明。

## 5. 案例与参考

- 案例：`benchmarks/data/dev/m12a_scenario_cases_v1.jsonl`，13 条（成功 2、合法空 1、澄清 4（均含补全）、不支持 1、CRS 拒绝 1、替代快照恢复 1、可信重新确认恢复 1、证据无效 1、学校为空 1）。每条的期望字段即评分依据。
- 参考：`benchmarks/data/dev/m12a_references_v1.json`，由 `benchmarks/m12a_oracle.py` 经 `scripts/build_m12a_references.py` 生成，三组参数：`d1000_b5-9_n10`（缺口 11 格、缺口儿童 178、总儿童 11,482）、`d1500_b10-14_n10`（7、97、12,625）、`d2000_b5-9_n20`（0、0、11,482）；学校 132、网格 453、该年龄段未报告 0。距离阈值离最近网格距离的最小间隔 ≥0.48 m（无浮点边界歧义）。
- 披露：阈值参数在查看数据分布（独立几何计算）后、任何系统实现前确定；oracle 与系统同一作者，是独立实现而非独立标注；本集合为公开 dev，不是隐藏测试。

## 6. 评分组件与阈值（全部精确）

逐案例全部组件通过才算通过；阈值 **13/13**：
1. 前端状态、原因、missing_fields、choices；有补全时补全后状态。
2. planned_tools、invoked_tools 精确；forbidden_tools 在全部工具调用（含内部）中出现次数为 0。
3. 结果状态、错误码、失败步骤；恢复事件 (action, status) 列表与 replan_count 精确；C3/C4 的 stage1 错误码、重新确认错误码、重新确认后 CRS 与血缘操作 `crs_reconfirmed`（父 ID 为被拒绝引用）。
4. 有参考时：缺口 grid_id 集合精确；`gap_cell_count`、`gap_children`、`total_children`、`school_count`、`grid_cell_count`、`band_not_reported_cells` 整数精确；`gap_children_share` 相对误差 ≤1e-6（参考为 0 时绝对误差 ≤1e-12）。
5. 期望报告时：`validate_scenario_report` 通过；指标值与参考一致；许可署名、人口表述、受限命令语法字样齐全；禁止短语为 0；4 图层；下载 GeoJSON 要素数 = 缺口数；成功运行分析工具步骤 ≥5。
6. 确定性：同一冻结输入运行两次，去除随机 DatasetRef ID 与时间戳的 deterministic receipt 逐字节一致；审计脚本重跑一致；演示 HTML 由保存的 ScenarioReport JSON 重新渲染逐字节一致。
