# M11c 三基线比较协议冻结（正式运行前，m11c-protocol-v1）

- 日期：2026-10-07；在任何正式运行与私有集运行之前发布。用户已确认：FW-A 为第三正式配置；FW-B 仅作公开 dev 消融并在看成绩前冻结；strict 为主指标、“除 required_steps 外的严格通过率”为诊断次指标；A4 故障注入不纳入（留给 M13）。
- 机器可读冻结：benchmarks/results/m11c/freeze/m11c_freeze_manifest_v1.json，SHA-256 8c512e4b545d58029017989669688e25a4890f92c1bd06bcef358ae96584a15c；公开 dev 能力矩阵 m11c_dev_capability_matrix_v1.jsonl，SHA-256 66afad17b310ee49513f660229b061dcfead22f3c6b015721d8b77ca2a6140c5；源码指纹（src 全部 .py 与两个 runner 的路径+SHA-256 汇总）sha256:96049292e2e1803eb5c6eb1a99baccb91bea35a1c6e76bb5b7fe2161b1891cb7。正式运行前 runner 会重新计算三者，任一不符即拒绝运行，须发布新协议版本。
- 取代 M11C_PROTOCOL_DRAFT.md（其中“六种固定模板”表述有误）。

## 1. 数据、评分与身份

- M11b v2.0 公开 dev 160（SHA-256 6c7f2ab5d09581642918ccda6f0f7cba90cbcb25ab9b7427d6c68596dc2d94fb）与本地私有 test 40（e0f9d97fc68f22f1c75e3e861b5d8b8370530ae4a634cae21d72a6f8aa2121fd，私有 manifest 1c640e0a…e75f 与公开 receipt 一致）。评分器 m11b-v2.0 与历史结果不改；每个配置每次运行另由 benchmarks.run_m11b_scoring 生成官方报告并与内存评分逐项一致。
- 系统只收到 SystemInput(task_id, query) 与该切分完整数据目录：dev 为 m04b-v1 全部 7 个文件（含 empty、geographic_points、invalid_bow_tie 干扰项）；test 为私有 regions/sources/targets 三文件，CRS 由评估方声明为 EPSG:3857（仅从隐藏构建器提取 EPSG 令牌）。task_type、category、capability、expectation、source_fixtures 均不进入系统。
- 全部离线确定性：model_id、prompt_version 为空，LLM 调用 0，成本 0 USD；不得称在线 LLM 结果。版本：系统 m11c-baselines-v1、前端 m11c-frontend-v1、M07 语法 m07.2（ADR-0018）。

## 2. 配置

| ID | 角色 | 组成 |
|---|---|---|
| fixed_workflow（FW-A） | 正式 | P4 固定流程架构移植到同一 M04 数据：共享前端 → 固定 filter_attributes → 结果编码；仅 search；无 M07 静态门、无澄清回合（缺地点等记为 error）、无结果验证/重试/重规划。 |
| dynamic_agent | 正式 | 共享前端 → M07 plan_request（m07.2）→ 直接执行计划；无结果验证/重试/重规划。 |
| validator_replan | 正式 | 共享前端 → M07 → M08 execute_validated_plan：allow_empty=true、max_features=10000、无 CRS/几何修复、一次以 plan_request 重新落地的重规划。 |
| template_workflow（FW-B） | dev 消融 | 共享前端 → 五个固定模板之一，无 M07 静态门/澄清/验证/重规划；不是原 P4 的移植。 |
| validator_no_frontend（A1） | dev 消融 | 关闭前端：原始 query 与完整目录直接交 M07+M08。 |
| validator_strict_empty（A2） | dev 消融 | 同 validator_replan，但 allow_empty=false。 |
| validator_no_replan（A3） | dev 消融 | 同 validator_replan，但不提供重规划回调。 |

FW-B 五个模板（每个先 filter_attributes 于括号内角色，再执行操作）：①search：filter(role)；②nearest：filter(source)→nearest_features(k)；③within：filter(source)→features_within(container)；④spatial_join：filter(right)→spatial_join(left, predicate)；⑤aggregate_by_region：filter(values)→aggregate_by_region(region_id_field, values_region_id_field, metric)。冻结矩阵核对：公开 dev 每个模板各 24 条可执行，共 120。

单数据集 buffer 不在其中：共享前端在绑定前拒绝 buffer（m07.2 无产生式，GeoDSL v1 within_distance 需两个数据集，用户决定 M11c 不升级 GeoDSL）；FW-B 须与 Agent 操作集相同，FW-A 与 FW-B 的差距才能归因于 search-only 限制；若加 buffer 模板，公开可执行将为 144 并混入 GeoDSL 合同差异。24 条公开、8 条私有 buffer 计入全量 coverage 与失败，不删除。

## 3. 冻结能力矩阵（公开 dev，不执行工具）

| 配置 | 可执行 | 说明 |
|---|---|---|
| fixed_workflow | 24 | 仅 search |
| template_workflow | 120 | 五模板 |
| dynamic_agent / validator_replan / validator_strict_empty / validator_no_replan | 120 | M07 status=plan |
| validator_no_frontend | 0 | 原始 query：136 missing_place、24 unsupported |

三方共同可执行子集（fixed_workflow ∧ dynamic_agent ∧ validator_replan）为公开 24 条 search；私有子集按同一规则在正式运行时计算，只发布数量（目标 8 条 search，不保证句式可解析）。

## 4. 运行与指标

- seed 11071/11072/11073 仅决定任务执行顺序；每个 seed 依次运行全部配置；每任务新建 DatasetRegistry。正式配置运行 dev 160 + 私有 40，消融只运行 dev 160；均 3 次。私有集只在本冻结之后运行，之后不调参；任何修改须新版本并重跑两切分，保留旧结果。
- 主指标：官方 M11b v2.0 strict pass。次指标“除 required_steps 外的严格通过率”（诊断、非官方 M11b 通过率）：对同一预测仅忽略 required_steps 组件的通过判定，其他组件（状态/原因、操作、字段、工具序列、Feature ID、数值、CRS、几何）仍须通过；不得据此称非验证系统完成端到端验证。
- 范围及分母：full（每切分全部任务；dev 160、test 40，合计 200）；common_executable；agent_executable；decision（基准期望非 success）；supported_unexecutable_by_formal（如 buffer）。每项报告任务数分母、strict/次指标、各组件通过数/适用数、coverage、得分均值/标准差/P50/P95、延迟均值/标准差/P50/P95、工具调用、LLM 调用、成本、状态/原因/失败类别；跨 3 次运行报告均值与样本标准差，逐任务结果签名一致率为稳定性。
- tool_sequence：产生最终返回结果那一轮的分析工具顺序；重试和 validate_geometry 只计入 tool_calls。延迟含目录加载、路由、执行、验证与结果编码。
- 报告：benchmarks/results/m11c/formal/<run_id>/ 排他创建 report.json、report.md、dev_task_results.jsonl、dev_predictions、m11b_reports、deterministic_receipt.json。dev 给逐任务结果；私有只给聚合，不写 query、Feature ID、逐条分数或 seed。scripts.audit_m11c_reproducibility 以冻结配置重跑并逐字节比较确定性 receipt（不含时间）。
- 必须披露：干净数据不能证明重试/重规划恢复收益；若 A3 与完整 Validator 相同，解释为该切分未触发重规划；rank 不支持且不声称排序能力；私有集与公开 M04b Fixture 有结构关联，前端识别器来自公开 dev 句式，私有结果仅反映模板级泛化；M03 Phase 2 公开查询为 catalog-informed 内部评测；GTFS 固定站点缓冲不是交通等时圈。

## 5. DONE 口径（不降低原标准）

M11c 仅在以下全部满足时标 DONE：本协议与矩阵先于正式运行冻结并经 runner 校验；三正式配置各 3 次完成 dev 与私有评估、四个 dev 消融各 3 次，均排他输出；三方共同可执行子集非空并给出同口径对照；全量 200 coverage/unsupported/失败完整报告（buffer 计入）；消融、稳定性、错误分析、延迟/调用/成本、代码/工作区指纹齐全；原 448 项不减，M11b 与 M07–M10 审计及受保护哈希不变，M11c receipt 可逐字节复现。不设通过率门槛；若私有共同子集为空或任一条件不满足，M11c 保持 IN_PROGRESS 并提交口径变更供用户决定。
