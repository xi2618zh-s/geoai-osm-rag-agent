# M11c v2 评测协议冻结（m11c-protocol-v2，阶段 1）

- 日期：2026-10-07。本阶段在生成 private v2、以及 qwen 接触任何 benchmark 文本之前发布，冻结题量、分布、独立性、保管、模型配置、失败计数、门槛和 DONE 规则。阶段 2（提示词/代码指纹）与阶段 3（dev 结果哈希与门槛结论）只能追加，不得修改本文件规则。
- v1 不变：m11c-protocol-v1 的协议、结果（公开 dev strict 8/16/136，私有 v1 0/40）、失败结论与 receipt 全部保留。旧 private v1 已被使用，只能作为“重复使用的历史诊断集”：v2 冻结后可用 v2 正式配置运行一次，仅报告聚合并标注“重复使用、非泛化证据”，不得用于调参、选型或门槛判断。
- 用户已批准：方案 3＋4；子代理出题、用户审阅后封存；48 题组成；公开 dev 进入门槛。

## 1. private v2 规格

- 48 题，8 类各 6：search、buffer（单数据集固定距离缓冲，task_type=within_distance）、nearest、within、spatial_join、aggregate_by_region、rank（不支持）、missing_place（缺地点/数据范围澄清）。task_id m11b_test_201–m11b_test_248，split=test，data_version=m11c-private-test-v2；任务合同为 M11b v2.0 字段与语义（src.evaluation.m11c.M11cPrivateTask，仅放宽数据版本与 ID 范围），评分函数 score_m11b 不改。
- 任务族的结构语义与 M11b 相同：search=对一个数据集的 ≥1 个属性条件过滤；buffer=对一个数据集做固定米数缓冲；nearest=过滤 source 后为其找 k 个最近 target；within=过滤 source 后严格位于 container 内；spatial_join=过滤 right 后与 left 按 intersects/within/contains 连接；aggregate=过滤 values 后按区域键对 regions 做 count/sum/mean/min/max；条件为 FIELD eq/ne/gt/gte/lt/lte 数字或字符串。rank 期望 error/unsupported_rank；missing_place 期望 clarification/missing_place，且 query 不点名任何目录数据集。
- 数据：出题人新建项目合成 CC0 GeoJSON（不得复制或仅变换旧私有/公开 Fixture 的 ID 或坐标），自定数据集名称与字段；catalogue.json 声明每个文件的投影 CRS；文件名去扩展名后最后一个下划线段为角色名且唯一。query 由出题人独立撰写，自然多样、参数完整、无歧义，可按名称或自然改写提及数据集与字段。
- 参考答案：scripts/build_m11c_private_v2.py 读取出题人的 task_specs.json，用现有 M04 AnalysisToolRegistry 实际执行，按 M11b v2.0 编码（并集几何、count、并集面积>0 时 area_m2、distance_m 取最小值、显式 CRS、完整工具序列）生成期望。自动检查：48 题与 8×6 分布、ID/query 唯一、与公开 dev 和私有 v1 query 精确重叠为 0、文件与哈希、CRS 合法且 buffer/nearest 为投影 CRS、字段存在、条件非空、工具调用成功、合同校验、参考预测自评 48/48、非空结果计数（报告不判失败）、角色名唯一。

## 2. 保管与访问

- 出题人为全新上下文子代理，只获本节规格与 M04/M11b 合同；不得读取公开 dev 任务、M11b 构建脚本、M11c 前端/提示词/测试或私有 v1。私有包放在仓库之外的独立工作区（路径仅经环境变量 GEOAI_M11C_PRIVATE_V2_DIR 或命令参数传入，代码中无默认绝对路径）。同一操作系统用户下这是流程隔离而非技术访问控制，须如实披露。
- 用户审阅并可修改；每次修改后重新运行构建，重算参考与 SHA-256。用户确认后执行 seal：排他写入公开 benchmarks/data/m11c/m11c_private_v2_receipt.json（只含数量、类别分布、检查结果与哈希，不含名称、措辞或答案），封存后不得修改，运行器发现任何哈希不符即拒绝。
- 开发代理（M11c 实施者）只接收数量、类别分布、检查结果与哈希。正式私有运行由出题/评测角色执行；向开发代理只返回聚合分数和不含题目内容的错误分类。私有的模型原始响应、预测和逐任务记录只存于私有工作区。

## 3. 模型前端（冻结配置）

- 模型：qwen2.5:3b，Ollama 0.13.5，manifest digest 357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b，模型 blob sha256:5ee4f07cdb9beadbbb293e85803c569b01bd37ed059d2715faa7bb405f31caa6，GGUF Q4_K_M，3.1B。端点 http://127.0.0.1:11434/api/generate，stream=false，format 为冻结 JSON Schema（语义框架）。运行前核对 /api/tags 摘要，不符即拒绝。
- 解码：temperature 0、seed 1107、num_ctx 4096、num_predict 256、num_thread 4、use_mmap true（本机提交内存不足以非 mmap 加载）、keep_alive 30m。仅 CPU。
- 分工：模型只输出语义框架（操作、数据集角色、条件、k、谓词、聚合指标/字段等）；确定性代码校验角色/字段存在、应用 rank/buffer 拒绝规则、推断连接键、渲染 M07 命令，再由 M07/M08 决定计划与执行。模型不得生成空间结果。
- 提示词：只用公开 dev 开发，可含公开 dev 派生示例；阶段 2 冻结文本与 SHA-256。此后提示词、代码或配置任何变化都须新版本。
- 不可用或失败：模型加载/服务不可用即停止并报告；不得自动换模型或回退规则前端。

## 4. 共享前端、调用失败与开销计数

- 每次运行先做前端轮：按该运行 seed 顺序对每个任务调用一次（一次“逻辑调用”），得到的决定由该运行内全部配置共享（三正式配置及 dev 上的 FW-B、A2、A3）；A1 关闭前端、不调用模型。每条预测 latency_ms = 该配置系统耗时 + 该任务前端耗时；llm_calls = 该任务前端的 HTTP 尝试次数（各配置相同，另报告实际唯一调用总数）。
- 超时：单次 HTTP 尝试 120 s。重试：每个逻辑调用最多 2 次尝试；仅在超时、连接/HTTP 错误、非 JSON 或不符合 Schema 时重试一次。
- 模型调用失败：分母为该次运行的逻辑调用数（公开 dev 每次 160）；分子为 2 次尝试后仍无符合 Schema 的框架的逻辑调用。重试成功不算失败但计入尝试数；超时计入失败原因。框架合法但语义错误不算调用失败，由评分体现。失败的逻辑调用以 unsupported/model_frontend_failed 进入系统。报告尝试数、逻辑调用数、失败数、重试数、超时数、提示/生成 token、加载与总耗时。
- 开销：本地 CPU，无 API 费用，estimated_cost_usd=0；另报告模型墙钟时间与 token 计数，不折算电费。

## 5. 运行顺序与门槛

1. 本文件冻结 → 子代理生成 private v2 → 用户审阅/修改 → seal（公开 receipt）。
2. 公开 dev 上开发提示词与适配代码 → 阶段 2 冻结 manifest（模型摘要、提示词 SHA-256、解码配置、源码指纹、本文件 SHA-256、private v2 receipt SHA-256）。
3. 正式公开 dev：七配置（正式三配置 + FW-B、A1、A2、A3）各 3 次，seed 11071/11072/11073；写出排他 dev 报告与结果哈希（阶段 3）。门槛对三次运行逐次判定，全部满足才通过：validator_replan strict ≥120/160；8 条 rank 全部 unsupported_rank；8 条缺地点中 ≥7 条 clarification/missing_place；模型调用失败 ≤2%（每次 ≤3/160）。任何一次不满足即停止并报告，不运行 private v2，不换模型、不回退规则前端。120/160 只是进入门槛，不是性能提升结论。
4. 门槛通过后，由出题/评测角色对 private v2 做一次正式评测：三正式配置各 3 次（FW-B 不在私有运行）；另可对私有 v1 做一次标注“重复使用”的诊断运行。正式私有评测只做一次；之后不得据其结果调整门槛、提示词、代码或题目再称盲测。

## 6. 指标、报告与 DONE

- 主指标：M11b v2.0 原 strict pass。次指标“除 required_steps 外的严格通过率”仅为诊断。另报各组件通过率、coverage、全量/三方共同可执行/两 Agent/决策子集、延迟、工具与 LLM 调用、token、稳定性与不含题目内容的错误分类，分母明确。buffer 留在正式分母且不支持；rank 不支持。
- 报告必须列出 v2 相对 v1（公开 dev validator 136/160 等）的逐配置与逐任务类别变化；私有 v2 只给聚合（含按类别聚合）。
- DONE 规则不变：私有 v2 三方共同可执行子集非空，且 v1 协议列出的其余条件（各 3 次、消融、稳定性、错误分析、指纹、排他报告、审计与测试）全部满足。若再次为零，保留失败结果并报告阻塞原因，不再更换测试集。
- 复核：M11c v1 receipt、M11b、M07–M10 审计，完整离线测试（不少于当前 492 项）与受保护文件哈希；v2 另提供以记录的模型原始响应重放的确定性审计。
