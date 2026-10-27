# P1 审查处理与证据

日期：2026-10-27（Asia/Shanghai）。本轮按五个问题分别修改和验证；开发模型仍为用户指定的 DeepSeek，未将结果冒充 gpt-4o-mini 或官方成绩。

SQLite 与真实 PostgreSQL 共用合同/故障场景；最终测试结果及命令记录在文末。没有改动当前运行的正式服务或启动官方评测。

| 问题 | 处理 | 当前结论 |
|---|---|---|
| P1-1 空结果风险 | 原子校验 binding；失败/空组/超预算时独立核验可返回的原文单元；仅在授权快照内回退；缺失证明保持缺失 | 回归覆盖已加入；不保证模型永远能识别证据，核验不确定/故障返回可重试错误 |
| P1-2 时间误分类 | correction/retraction 增加二次核验和明确 target IDs；不核验、不兼容模态或明确不相交的有效日期不撤销历史；旧未核验边在读取时降为冲突 | 控制场景通过；真实 gpt-4o-mini 的误分类率和校准仍未测定 |
| P1-3 长文本故障 | 完整输入 token 预检、有限已知上下文、窗口范围内引用定位、重复 quote 的 occurrence、截断后有界分片、调用/深度/时限上限 | 合成故障和一次真实长文本截断恢复通过；持续模型/引用故障仍原子失败，未静默降级 raw-only |
| P1-4 PG 性能证据 | 真实 PG/pgvector 的三档 1024 维历史测量与 EXPLAIN；来源快照从逐条读取改成有界批量 SQL | 已测至单用户 5 万来源、并发 4 的数据库阶段；没有完成正式端到端容量验收，没有新增未经验证的 HNSW |
| P1-5 机制归因 | 冻结 L1 抽取、时间复核及 query plan；逐个关闭组件；另做固定候选/计划/预算的 selector 对照 | 首轮开发控制消融完成；当前样本没有证实 relations/consolidation/reflection 的独立收益 |

## P1-1：区分绑定失败、证据不足和预算不足

`bind_proofs()` 先验证完整结构再一次性发布，不会在第一个义务成功、第二个失败时留下半成品。无效 ID、错误结构、模型阶段不可用分别记录绑定状态。

当 beam/greedy 未选到证据时，独立 audit 必须检查实际能够返回的原文或 span，才能进入有限的 Top-K 回退。审核只接收当前 user、当前治理快照中的单元；返回未知 ID、uncertain 或发生故障时返回 503，不将故障包装成“没有记忆”。明显不适用的未来计划或晚于 as-of 的纯未来状态不能被回退提升为当前/历史事实。

trace 区分 `verified_fallback`、`no_support_in_candidates`、`no_evidence_fits_budget`。后两者分别表示当前候选中无支持、支持单元无法装入预算，均不宣称整个用户历史中绝对没有相关信息。

回退可以交付部分前提，但不会改写义务的 AND/OR 证明。尤其不能用一个短 span 替换整个来源后，自动把原本需要整段来源的证明记为完成。

## P1-2：时间误分类与历史恢复

新的 `manage/temporal.py` 将抽取的 `change` 当作提议，保存 `change_proposal`、`change_verified` 和 `change_targets`。修订只作用于核验列出的旧断言，不再因为同属性有多个历史值而全部建立 CORRECTS。

CORRECTS/RETRACTS 是状态关系，不直接触发 suppression/erase。无法核验时保留来源与分歧；已有未经新协议核验的修订边，也不能直接降低旧来源的时间可信度。

[故障注入报告](../experiments/reports/p1-temporal-faults.json) 包含 12 个英文、中文、日文控制：把真实迁移故意抽成 correction，并让复核重复该错误或返回 uncertain/change。此处均为明确不相交的 2022/2024 状态：

- 错误失效边：0/12。
- 原始历史来源误抑制：0/12。
- 历史证据恢复：12/12。

这些是 oracle 抽取/合成向量下的故障合同指标，不是模型自然错误率。二次复核仍使用同一允许模型，错误可能相关；同年粗粒度、未知时间、隐含纠正、实体误对齐等仍需要自然数据校准。代码不据此声称时间推理已完全解决。

## P1-3：长文本与模型故障

抽取输入现在把系统提示、消息和有限的已知节点一起计入 token 预检。上下文不会随同次 Add 中的中间节点无限增长。窗口由代码定位，模型可提供重复引文在窗口内的 occurrence；最终 UTF-8 byte span/hash 始终回到完整未改写来源核验。

首轮校验及一次修复仍失败，或模型明确返回 length 截断时，可以拆分多消息组或拆分带重叠的长窗口。默认最大深度 4、最多 128 次抽取阶段尝试、抽取阶段时限 300 秒；这些预算不等于端到端服务 deadline。模型网络故障不被递归放大，持续故障不会发布部分来源。

测试包含重复文本、中文/日文/阿拉伯文、多消息 role/ordinal、超过 20 万字符输入、持续坏 quote、持续截断、SDK length 信号、失败重试与原子性。

[真实 provider 长文本记录](../experiments/reports/p1-real-long-add.json)：DeepSeek + text-embedding-v4，3 条消息、50,706 字符，其中包含大段重复公开背景和末尾过敏约束；故意把第一次真实抽取限制为 48 输出 tokens，实际观察到一次截断，随后恢复：

- 原始文字/role 完整恢复、引用 byte span 全部有效、罕见约束抽取和检索均通过。
- Add 12.62 秒，Search 1.47 秒；8 次结构化调用、9 次 embedding 调用。
- 最终只提交 generation 1。

这是一次开发端到端样本，不是 Add p95、成功率或正式容量证明。持续不可验证的输入仍会返回错误，调用方需沿用相同 request_id/payload 重试。

## P1-4：真实 PostgreSQL 测量

[规模测量](../experiments/reports/p1-postgres-scale.json) 使用独立测试库、1024 维确定性合成稠密向量、单用户 1k/10k/50k 来源，另有 1k 外部用户来源；保留实际 `EXPLAIN (ANALYZE, BUFFERS)`。所有测量都在数据库内执行 Dense 距离排序、user-BM25 和来源读取。

并发 1 的连接与读阶段 p95：1k 为 356ms，10k 为 422ms，50k 为 608ms。样本为每档每并发 12 次、热运行，p95/p99 接近样本最大值，不能当作稳定尾延迟估计。

测量发现旧快照路径中大量逐条 SQL 开销。在同一份 50k 数据、同一查询、同一 100 候选上改成分批 IN 查询后，[复测](../experiments/reports/p1-postgres-batched.json) 的并发 4 结果为：

| 阶段 | 修改前 p95 | 修改后 p95 |
|---|---:|---:|
| Dense | 112ms | 120ms |
| BM25 | 212ms | 217ms |
| 来源快照读取 | 345ms | 31ms |
| 连接与读阶段合计 | 666ms | 362ms |

这里没有证明 Dense 或 BM25 的渐近开销已消除；两者仍随本用户历史增长。合计也不含 LLM、公网 HTTP、Answer、完整图工作负载或最后的事务关闭开销。正式 300M tokens、64 Add/256 Search 仍是未通过的发布条件；后续索引方案必须与精确召回做同数据对照。

## P1-5：冻结输入的逐组件消融

`experiments/ablate.py` 单独捕获一次 L1 抽取、修复、时间复核和查询计划。抽取上下文排除会在某些消融中消失的 summary；回放不依赖某个 arm 当前已形成的摘要和置信度。sealed 后缺失输入直接报错，禁止重新调用抽取来补齐。所有 arm 使用相同 dataset/model/query/budget hash 和 frozen artifact hash。

relations 的消融范围是图邻域与依赖闭包；时间版本管理保留。consolidation 关闭管理层摘要，reflection 关闭反例驱动的经验状态维护；原始经历仍保留。`no-binding` 只关闭证明绑定，其他组件、模型、query plan 与证据预算不变。

[首轮结果](../experiments/reports/p1-ablations.json)，10 个自建开发问题、固定 DeepSeek Answer/judge：

| Arm | 证据完整率 | 本地 Answer 正确率 | 禁止返回证据命中率 |
|---|---:|---:|---:|
| Full | 100% | 100% | 0% |
| no-relations | 100% | 100% | 0% |
| no-consolidation | 100% | 100% | 0% |
| no-reflection | 100% | 100% | 0% |
| no-binding | 87.5% | 90% | 20% |

禁止返回证据包括时间不适用计划，并不全部等于隐私泄漏。固定相同候选、计划和预算、排除独立回退后，beam/greedy 的本地 Answer 为 90%，Top-K 为 100%；Top-K 的禁止返回证据命中率为 20%。有一道原因题出现无效绑定，Full 经独立回退恢复原文支持，这个增益不能归到 beam 上。

因此本轮不能证明三类论文机制的独立收益，也不能证明 beam 优于 Top-K。需要更长历史、语义远距依赖、经验反证和独立封存数据来验证贡献。当前样本用于开发，不作统计显著性或泛化声明。

[冻结输入复核回放](../experiments/reports/p1-ablations-verified.json) 成功复用了同一个 artifact hash `181f47be04fbce28bfd1201202dbde43edcf9b14a66a291bee8f0cd69364e7f7`，未重新生成抽取、时间复核或 query plan；五项 arm 的上述汇总结果一致。在这次回放中，固定候选的 beam/greedy/Top-K Answer 均为 100%，未重现首轮的无效绑定。证明绑定本身按各 arm 的记忆状态重新求解，仍有模型运行间变动；保留两轮结果，不能把某次成功归因为 beam 的确定收益。

单项移除观察到的是“其他组件保留时的边际效果”，不能排除组件冗余或交互。当前三个移除 arm 无退化，表示独立收益尚未获得证据，而不是证明这些机制无用。

可复现命令：

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests
.venv\Scripts\python.exe experiments\temporal_faults.py
.venv\Scripts\python.exe experiments\long_add_smoke.py --deepseek
.venv\Scripts\python.exe experiments\ablate.py --deepseek --answer
.venv\Scripts\python.exe experiments\ablate.py --deepseek --answer --frozen experiments\reports\p1-ablations.frozen.json --output experiments\reports\p1-ablations-verified.json
```

PG 合同测试设置 `EPOCH_TEST_DATABASE_URL` / `EPOCH_TEST_ADMIN_DATABASE_URL`；规模脚本设置 `EPOCH_PERF_DATABASE_URL` / `EPOCH_PERF_ADMIN_DATABASE_URL`，二者使用不同、以 `_test` 结尾的专用库。规模脚本会重建其专用测试数据，`--reuse --rows 50000` 则在已有同规模数据上只测量。16 组合交互实验可用 `ablate.py --factorial`，本轮未将其标成已完成。

## 最终检查

最终同时启用 PostgreSQL 合同测试及 P1 故障测试：

```text
Ran 102 tests in 23.331s
OK
```

`git diff --check` 和 `python -m compileall -q eviepoch experiments tests` 通过。测试包含独立的冻结回放协议检查，确认单项移除仅修改一个组件开关，三个选择器的 candidate/plan/budget hash 相同。

本轮没有运行正式 gpt-4o-mini 校准、自然公开/封存质量测试、16 组合交互、HNSW 精确召回对照或公网目标容量；这些不计为已验收。未重启已有参赛 API 容器，也未执行官方 Smoke/Full。
