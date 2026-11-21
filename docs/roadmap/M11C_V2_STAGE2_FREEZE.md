# M11c v2 阶段 2 冻结（m11c-protocol-v2，提示词与代码）

- 日期：2026-10-07。在正式公开 dev 运行与任何私有 v2 运行之前发布。本文件只追加阶段 2 内容，不修改阶段 1 规则（[M11C_V2_EVALUATION_FREEZE.md](M11C_V2_EVALUATION_FREEZE.md)，SHA-256 9f656487ef943bced4a63606b61652116f24c9962a9b65a746615b4d14c7d102）。
- 机器可读冻结：benchmarks/results/m11c_v2/freeze/m11c_v2_stage2_manifest.json，SHA-256 a9b1686ede22c7b6558510d9bf15d9b3f99ec5fdb6a467a64239a8fda8b96e89。run-dev 与 run-private 运行前重新计算源码指纹、提示词 SHA、系统配置、dev 任务与目录 profile，并经 /api/version、/api/tags 核对模型摘要，任一不符即拒绝运行。
- private v2 已由用户封存：公开 receipt benchmarks/data/m11c/m11c_private_v2_receipt.json SHA-256 5e474b3afe14c67362899a2fe5744b2ea0b4fa33818a7abb51fffbaf5c43b13d（48 题 8×6，supported 36，其中非空要素集结果 32）。

## 1. 冻结内容

| 项 | 值 |
|---|---|
| 模型 | qwen2.5:3b，Ollama 0.13.5，摘要 357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b，Q4_K_M，仅 CPU，端点 http://127.0.0.1:11434/api/generate，stream=false |
| 解码 | temperature 0、seed 1107、num_ctx 4096、num_predict 256、num_thread 4、use_mmap true、keep_alive 30m；每次尝试 120 s；每逻辑调用最多 2 次尝试 |
| 提示词 | src/agent/prompts/m11c_frontend_v2_prompt.txt，标签 m11c_frontend_v2_prompt，SHA-256 be9ea50f10e5cb50c3a9c6af001c2b0fe27b86b1e0596688fef4f80ebfcda51f（换行归一为 LF 后计算） |
| 帧 Schema | 结构冻结：op ∈ {search, buffer, nearest, within, join, aggregate, rank, other}、data/other/subject 为目录角色枚举或 null、where[{field 枚举, cmp ∈ {=,!=,>,>=,<,<=}, value 字符串或数字}]、k、relation ∈ {intersects, within, contains}、metric ∈ {count, sum, mean, min, max}、metric_field；必填 op、data；角色与字段枚举由系统收到的声明目录确定性生成（公开 dev 实例 SHA-256 f72182eb4230e9706251bb39d287b01b295adcd61c98384cc93c820d42744fbc） |
| 前端版本 | m11c-frontend-v2（src/agent/model_frontend.py），渲染复用规则前端 render_request；规则前端 m11c-frontend-v1 的 interpret 行为不变 |
| 源码指纹 | sha256:47118196a78b701d4654581dd01576e0dff6c29def485043aae2f3ca4c23ab1a（src 全部 .py、benchmarks/run_m11c_v2.py、run_m11c_baselines.py、run_m11b_scoring.py、scripts/build_m11c_private_v2.py 与提示词文件） |
| 系统 | 正式 fixed_workflow、dynamic_agent、validator_replan；dev 消融 template_workflow（FW-B）、validator_no_frontend（A1，不调用模型）、validator_strict_empty（A2）、validator_no_replan（A3）。除前端身份字段外，各配置与 m11c-protocol-v1 相同 |
| seed / 门槛 | 11071/11072/11073；门槛按阶段 1 第 5 节逐次判定 |

## 2. 模型只做语义帧，其余确定性

模型输入：提示词（说明、公开 dev 目录上的示例，含开发代理自写的 dev 派生改写）、当前声明目录的 profile（角色、几何类型、行数、字段名/类型、≤12 个取值的文本字段取值表）与 query。确定性落地规则（ground_frame）：

1. 帧缺失（2 次尝试后无合法帧）→ unsupported/model_frontend_failed。
2. op=rank → unsupported_rank（注册表无排序工具）；op=other → unsupported_operation。
3. data 与 other 均未绑定目录角色 → clarification/missing_place；被绑定数据集在 query 中均无词面依据（角色名、仅属该数据集的字段名、该数据集 3 字符以上文本值）→ missing_place。
4. 模型判 within 但 query 含 join/pair/match 动词 → spatial_join。op=buffer → unsupported_buffer（不升级 GeoDSL）。
5. 双数据集操作缺一方 → unknown_dataset；条件字段只属于另一数据集时交换被过滤侧（nearest/within 的 source、join 的 right、aggregate 的 values 为被过滤侧）。
6. 被过滤数据集的取值表字段在 query 中恰好出现一个取值且帧无该字段条件 → 补等值条件（与角色同名者除外）。字段须存在；数值字段接受数字或数字文本；文本值按观测值做大小写/单复数归一；含空格或类型不符 → unexpressible_condition。
7. nearest 缺 k → 1；join 缺关系 → intersects；join 关系主语在两侧均有词面位置时取先出现者，否则用模型 subject，主语为被过滤侧时 within/contains 取逆。
8. aggregate：缺指标 → count；非 count 指标字段须为 values 的数值非键字段，否则唯一数值字段，否则 ambiguous_metric_field；连接键先按值重叠（区域键唯一、值重叠 ≥0.5、唯一最优），否则回退 v1 字段名规则，仍无则 ambiguous_join_key。
9. 经 render_request 渲染 M07 命令，再由 M07（m07.2）规划、M08 验证执行；模型不生成空间结果。

## 3. 开发证据（只用公开 dev，已记入工程日志）

- 迭代 1（提示词 81728663…）：dev validator 125/160；迭代 2（be9ea50f…）：136/160（除 buffer 外全部），rank 8/8、missing_place 8/8、调用失败 0/160。
- dev 派生改写探针（开发诊断，不参与门槛）：v1 40 条，迭代 1 提示词 31/40 → 规则调整后 37/40；规则确定后新写的 v2 40 条仅运行一次 38/40（未通过者均为不支持的 buffer）。探针与 dev 同一目录、同源题型，不能代表私有集措辞。

## 4. 后续运行

- 阶段 3：`python -m benchmarks.run_m11c_v2 run-dev` 排他写出 benchmarks/results/m11c_v2/formal/m11c_v2_dev_formal/（report、deterministic_receipt、frontend_calls 原始响应、dev 逐任务结果、21 份官方 M11b 报告、stage3_dev_gate.json）。三次运行逐次判定门槛；任一次不过即停止，不运行 private v2、不换模型、不回退规则前端。
- 门槛通过后，由全新评测子代理执行一次 `run-private`（三正式配置各 3 次）：仅当阶段 3 判定通过、dev 结果哈希未变、private v2 receipt 与本冻结一致时运行，且输出目录已有私有运行即拒绝。公开输出仅聚合；逐任务记录与模型原始响应写入出题工作区。之后 `scan-leaks` 只打印命中数，`replay-private` 由记录重放复核。
- 审计：scripts/audit_m11c_v2_reproducibility.py 以记录的原始响应重放公开 dev 运行并逐字节比较确定性 receipt。
- 本冻结之后提示词、代码或配置的任何改动都须发布新版本；不得据 private v2 结果调整。
