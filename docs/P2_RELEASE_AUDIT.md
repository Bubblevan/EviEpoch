# EviEpoch P2 — Release Audit and Evidence-Utility Validation

**审查日期：** 2026-10-27（Asia/Shanghai）
**固定代码基准：** `739c0d61f6b44d4dbaae8e8ca1e6797560dd9a70`
**结论：NO-GO（不得绑定新正式版本或启动 AML Full）**

本审查不增加线上功能。它核对基准代码、P1 运行记录、现有测试和 AML 第二期公开要求，并冻结了公开数据的本地分层清单。自然公开数据上的 B0–B5 尚未运行，所以不能把当前证据解释成复杂检索优于强混合基线。

## 判定摘要

| 门槛 | 状态 | 可核查依据 |
|---|---|---|
| 固定基准与工作区状态 | PASS | HEAD 是 `739c0d6`；本轮未改产品代码；用户原有 `README.md`、`docs/IMPLEMENTATION_STATUS.md` 改动保留 |
| 公开数据来源、许可、拆分 | FAIL（自然性门槛） / PARTIAL（协议预注册） | LongMemEval-S 的 MIT 许可和 60/60/380 分层 manifest 已核实；但对话证据由 LLM 模拟并经人工编辑，不是自然的人类长对话，不能满足“自然公开数据”的严格要求；题目也尚未跑分 |
| B0–B5 匹配对照 | FAIL | 没有同一自然公开题集上的逐级 arm 结果；既有 10 题是自建控制集 |
| Empty / incomplete / false-withhold / 503 | PARTIAL | 10 题控制集能给出部分选择器和 obligation 指标；真实公开集和 HTTP 503 率未知 |
| 答案质量、逐题胜负和置信区间 | FAIL | 有 10 题代理答案记录；没有自然集 P1–P5 逐题对照；小样本区间很宽 |
| Add/Search 延迟与 token 成本 | PARTIAL | 有单次长 Add 和短 smoke；缺少自然集逐阶段 token、稳定 p95/p99、失败率及匹配 arm 成本 |
| 容量 / 重试 / 并发 / 隧道 | FAIL | 只有假 provider 的合同测试和少量在线 smoke；没有真实 DeepSeek 并发退避、重试幂等和隧道稳定性测量 |
| AML API Smoke 与材料审查 | FAIL | 本轮未调用 AML；本地测试不能替代官方 Smoke |
| 新版本冻结和正式 Full | NOT STARTED | 按要求不自动改线上版本，也未启动官方 Full |

AML 官网当前列出的 Cycle 2 材料截止为 **2026-10-31 23:59（UTC+8）**，且要求 Open-source Methods 的 Add 使用 `gpt-4o-mini`、容量经确认、Smoke 通过、版本与评测代码一致，并披露复用代码/论文或明确说明个人原创。DeepSeek 只能是本地代理实验，不能被称为正式模型结果。[官方 Cycle 2 指南](https://agentmemories.ai/docs)

## 数据与预注册清单

已取得 LongMemEval 官方 cleaned LongMemEval-S 文件到忽略目录 `experiments/datasets/.cache/longmemeval_s_cleaned.json`，**未加入 Git**。文件字节数为 277,383,467，SHA-256：

```text
d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442
```

该发布包含 500 题，类别数为 multi-session 133、temporal-reasoning 133、knowledge-update 78、single-session-user 70、single-session-assistant 56、single-session-preference 30。官方仓库列出 LongMemEval 的作者、任务范围和数据获取方式；仓库许可证为 MIT。[官方数据与说明](https://github.com/xiaowu0162/LongMemEval) · [MIT License](https://github.com/xiaowu0162/LongMemEval/blob/main/LICENSE)

**自然性核验修正：** LongMemEval 论文说明，其关键 evidence sessions 是由 LLM 模拟后再由人工编辑；干扰历史也混合了模拟自聊和公开 user-assistant 对话。因此这是有用的公开、长历史、人工核验 benchmark，但不是真实人类私聊语料。本次只把它登记成可复现的**次级合成 benchmark**，不宣称它满足“自然公开小数据”门槛，也不以它单独给出 release GO。[LongMemEval 官方论文的数据构建说明](https://openreview.net/pdf?id=pZiyCaVuti)

拆分由 [split_longmemeval.py](../experiments/split_longmemeval.py) 确定性生成，题目 ID 与哈希保存在 [longmemeval_split_v1.json](../experiments/datasets/longmemeval_split_v1.json)：

| 集合 | 题数 | 用途 |
|---|---:|---|
| dev | 60 | 开发与调试；每类 10 题 |
| validation | 60 | 方案/门槛验证；每类 10 题 |
| held_out | 380 | 预留确认候选后评估；每类为剩余题目 |

拆分种子为 `eviepoch-p2-split-v1-20261027`，manifest SHA-256：`b8a12318aa424da4796829ec7a926bf1114bc9c6b76776b0afd5ee4a6d929497`。拆分按类别内 `SHA256(seed || NUL || category || NUL || question_id)` 排序。脚本只把 ID、类型、数量、来源和文件哈希写入 manifest，不复制题目正文或答案。

**限制：** LongMemEval 是公开题集，因此这里的 380 题只是本项目预先保留的 holdout，不是隐藏或 AML 封存集；既然题面公开，它不能替代真正未见数据。当前没有对 dev、validation 或 held_out 运行 EviEpoch，也没有用 held_out 调参。满足“自然、人类对话 + 明确可用数据许可 + 记忆问题与证据标注”的主评测数据源尚未选定；例如 REALTALK 官方仓库声明其对话来自真实参与者，但其 README 没有单独说明数据许可，不能仅凭仓库代码的 Apache 标识推断原始对话许可，因此本轮不导入原文或据此跑分。[REALTALK 数据仓库](https://github.com/danny911kr/REALTALK)

## 现有可量化证据（均非正式成绩）

### P1 小型控制集消融

数据是 [public_controls.json](../experiments/datasets/public_controls.json) 中 10 道自建 CC0 开发题，不是自然公开 benchmark。DeepSeek + `text-embedding-v4`；冻结回放含 hash；Answer/judge 是本地代理。现有记录：

| 配置 / 反事实 | 完整证据 | Evidence Recall | 本地代理 Answer | 禁止证据命中 | 逐题结论 |
|---|---:|---:|---:|---:|---|
| Full（首轮） | 8/8 可回答题 | 8/8 可回答题 | 10/10 | 0/10 | 对 no-binding：证据 1 win / 0 loss / 7 tie（8 个可回答题）；Answer 1 / 0 / 9（全部 10 题） |
| Full（冻结复核） | 8/8 可回答题 | 8/8 可回答题 | 10/10 | 0/10 | 复核没有重现首轮的无效绑定；不应把单轮成功解释成稳健性证明 |
| No Binding | 7/8 可回答题 | 15/16（逐题平均） | 9/10 | 2/10 | 相对 Full 暴露一个证据不完整和一个答案错误；样本极小 |
| Beam（固定候选 / 计划 / 预算） | 8/8 可回答题 | 8/8 可回答题 | 10/10 | 0/10 | 对 Top-K：证据 0 win / 0 loss / 8 tie，答案 0 / 0 / 10；禁止证据指标 Beam 2 win / 0 loss / 8 tie |
| Greedy（固定候选 / 计划 / 预算） | 8/8 可回答题 | 8/8 可回答题 | 10/10 | 0/10 | Answer、证据和泄漏均与 Beam 打平 |
| Top-K（固定候选 / 计划 / 预算） | 8/8 可回答题 | 8/8 可回答题 | 10/10 | 2/10 | 答案正确没有反映治理违规；相对 Beam 多返回两条禁用证据 |

以上逐题胜负按已有 JSON 行重新计数；没有作显著性结论。完整证据/Evidence Recall 的分母只含 8 个有 gold evidence 的可回答题；两道预期 abstention 不进入这两个分数。95% Wilson 区间示例：10/10 为 72.2–100%，9/10 为 59.6–98.2%，7/8 为 52.9–97.8%，2/10 禁止证据命中为 5.7–51.0%。区间宽、开发题人工构造且有 ceiling effect，不能推广至自然数据。

初轮和复核的完整记录分别为 [p1-ablations.json](../experiments/reports/p1-ablations.json) 与 [p1-ablations-verified.json](../experiments/reports/p1-ablations-verified.json)；冻结选择器反事实的候选、计划和证据预算一致，冻结 artifact hash 为 `181f47be04fbce28bfd1201202dbde43edcf9b14a66a291bee8f0cd69364e7f7`。反事实选择时间被记录为 0 秒，**不可用作 Beam/Top-K 延迟证据**。

### 需要分开的 Search 失败

首轮 Full 10 次 Search 中，2 次空选择是预期 abstention；8 个可回答问题没有 false withhold。另有 1 次无效 obligation binding，随后独立 evidence audit 回退，最后 gold evidence 和本地答案恢复；**但 trace 的 `missing_obligations` 仍非空**，因为 fallback 没有把来源写回 proof alternatives。因此首轮 Full 是 gold evidence 8/8，但内部 obligation completeness 为 7/8。冻结复核中没有重现该绑定错误。No Binding 在 8 个可回答题中有 1 个不完整 gold evidence（12.5%），首轮为 7/8 complete、9/10 答对。

这些数字说明当前小控制集里的回退有一次成功案例，不表示线上 `Search 503` 为零：本地实验没有通过真实 HTTP API 统计 503。代码路径仍有实质风险：绑定失败清空 proof alternatives；若没有任何被选证据，回退需要另一次 audit 调用，audit 返回 uncertain/坏 ID/超时可能变成 provider error；若选择结果**非空但 obligations 仍缺失**，当前空集回退不会启动。响应 `data` 非空不能代表 AND 证明完整，必须同时计 `missing_obligations` 与 gold evidence。

Temporal fault suite 的 12/12 是 oracle extraction 与合成向量上的定向故障合同，不是自然语言模型准确率。长文本代理记录仅一条：50,706 字符、3 条消息；第一次刻意截断后恢复；Add 12.62 秒、Search 1.47 秒，8 次 chat、9 次 embedding，输入/输出共 75,661 / 602 tokens。[原始记录](../experiments/reports/p1-real-long-add.json)

### 当前代码不能支撑的逐级结论

| Arm | 当前证据状态 | 不能宣称的结论 |
|---|---|---|
| B0 Dense | 没有在 LongMemEval 上跑基准；旧 B0 路径不是 P2 固定协议输出 | 不知道 Dense 的 Recall@K、Answer、时延和 token 成本 |
| B1 Dense + BM25 + RRF | P1 有固定候选的 selector 对照，不是 Dense 对 RRF 的检验 | 不知道 RRF 是否优于 Dense |
| B2 + 时间排序 | 有时间故障控制，没有 B1/B2 自然集成对照 | 不知道时间排序带来的净提升或错误代价 |
| B3 + Governance Filter | 当前 P1 没有独立 B3 对照；LongMemEval 不提供本实验所需的删除/抑制治理真值标签 | 不知道治理过滤的自然集净收益；不能从普通历史题推断删除正确率 |
| B4 + Obligation Binding | 只在 10 个自建问题上做过 no-binding 消融；观测到一次重放间波动 | 不能说真实 multi-hop 下稳定降低 incomplete proof 或 false withhold |
| B5 + Beam | 固定候选回放里 Answer/证据与 Top-K 打平；仅控制集的两条禁用证据显示约束价值 | 不能说 Beam 提高答案正确率、证据召回或容量效率 |

**因此 B0–B5 的当前 net-benefit 判定除小控制集局部现象外均为 UNKNOWN。** 不能把组件数量或现有 Full 的 100% 当成胜过强基线。

## 指标和比较合同（后续运行必须遵守）

1. **同源输入：** 每个问题仅创建同一份 Add 语料；记录源文本、数据行 ID、embedding identity 和初始 candidate universe hash。各 arm 不允许各自重新生成候选后再声称 selector-only 比较。
2. **分层对照：** B0 到 B5 仅逐步打开一项变化。比较选择器时固定候选、Query Plan、模型、top-k、字符/token 预算和 Answer/judge 提示。Query Plan、抽取、绑定输出按问题落盘并做规范化 SHA-256；sealed 后禁止模型补生成缺失中间产物。
3. **答案与证据：** Recall@K 以标注 gold evidence session ID 对 top-K 候选计数；Evidence Recall 以最终披露证据中的 gold session/来源计数；Answer Accuracy 用固定同一个 Answer 模型与 judge，DeepSeek 只记作 proxy。报告每题 W/L/T 和 95% 区间。
4. **失败率：** `Empty Selection Rate = 空 data / 有效 Search 请求`；单独报告预期 abstention。`Incomplete Obligation Rate` 只在可回答题分母上计算未满足至少一项必需 AND 前提；`False Withhold` 是可回答题返回空 data。通过 HTTP 端到端计真实 503，不用 Python exception 数代替。
5. **成本：** Add 与 Search 分别记录 p50/p95/p99、成功数、timeout/503、每阶段 chat/embedding 调用数和 input/output token；选择器反事实的离线耗时单列，不能记为零延迟。
6. **失败归因：** 每题主标签唯一归因为：retrieval miss、candidate truncation、plan miss、binding invalid/unavailable、obligation incomplete、governance false block/leak、budget overflow、provider timeout/503、answer/judge error。可另带多标签细节，保留原始 trace。

LongMemEval 不包含足够的治理删除/抑制 ground truth；B3 需单独的有来源、有许可治理数据或只报告合成合同测试，不能用合成项混进自然集主分数。Temporal correction 也需预先独立标注“已经搬迁 / 原来一直如此 / 将来计划 / 同名实体”等自然表达，由盲于模型输出的标注流程作真值，不能把同一 LLM 的二次复核当作独立裁判。

## P3 容量现状

| 场景 | 当前观察 | 发布判定 |
|---|---|---|
| 普通 Add | 本地 DeepSeek smoke 只有 3 次 Add（约 1.53–4.65 秒） | 样本不足，p95/成功率未知 |
| 超长 Add | 单次 50,706 字符、12.62 秒；一次人为截断后成功恢复 | 功能路径通过，不代表容量/尾延迟 |
| 长历史 Search | 上述长输入后的 1 次 Search，1.47 秒 | 不能估计长历史分布，也未覆盖高并发 |
| 并发退避 | 假 provider 有并发/Admission 合同测试 | 真实代理的限流、退避和 503 未测 |
| 重试幂等 | 本地测试验证相同 request_id；网络断开后服务端提交但客户端超时的在线场景未测 | 仍有风险 |
| 隧道稳定性 | 没有连续公网探测或失联恢复记录 | 未通过 |
| LLM 放大 | 单条长 Add 为 8 chat + 9 embedding；不同 Add 长度/分块策略未系统扫测 | 未知成本曲线 |

当前代理配置实际是 DeepSeek `deepseek-flash` + `text-embedding-v4`。本轮没有发起新的付费模型调用；引用的在线代理记录来自已存在的 P1 JSON。正式 gpt-4o-mini 结果为空，亦未调用 AML。

## 验证执行记录

- `.venv\Scripts\python.exe experiments\split_longmemeval.py` 对本地 500 题生成 60/60/380 manifest；数据和 manifest hash 已写入。重跑可检查拆分复现性。
- 项目 `.venv` 的确定性测试通过：`test_pipeline.py` 4 项、`test_review_regressions.py` 20 项、`test_ablation_protocol.py` 3 项、`test_lifecycle.py` 29 项，共 **56 项通过**。
- `.venv` 下完整 `unittest discover` 在 `test_api.py` 首个 `TestClient` 测试没有结束；默认系统 Python 另缺 FastAPI，因此系统 Python 下完整发现也导入失败。为区分运行器问题与接口逻辑，我用相同的临时 SQLite / 假 provider 对 TestClient Add/Search 合同做了独立断言回放：health/ready、401、Add/同请求重试 200、payload 冲突 409、Search 200、Workbench 404、空白输入 422 均通过。完整套件仍未通过证明，官方 API Smoke 也未运行。
- 还需在已配置测试 PostgreSQL 的隔离库运行 Postgres 合同测试；本轮没有把此前报告中的结果当作当前在线部署/容量证明。

## GO / NO-GO 决策

**NO-GO：当前不满足发布候选冻结条件。** 质量门禁未知、自然集 B0–B5 缺失、API TestClient 未完成、真实代理并发/503/网络重试/隧道尚未测量，AML Smoke、材料检查及线上版本一致性也未通过。当前不启动官方 Full，不创建新正式版本绑定。

进入候选冻结前，最小必需闭环：

1. 在固定 dev 上跑完同模型、同 embedding、同候选与预算的 B0–B5；逐题留 hash、失败归因、W/L/T 与置信区间。
2. 仅在 dev 诊断后冻结实现和 Query Plan，再跑 validation；不触碰 380 项 holdout 直至候选冻结。
3. 另补有真值的治理删除集与时间纠正自然集；通过绑定失败/部分 AND 缺项的端到端 API fault injection，得到独立的空选、incomplete、false withhold 与 HTTP 503 统计。
4. 完成 DeepSeek 代理的低成本容量阶梯（普通/超长 Add、长历史 Search、受控并发退避、客户端超时重试幂等、隧道持续探测），逐请求记录 token 和延迟；这些仍标为代理结果。
5. 解决 `test_api.py` 卡住并让完整本地套件通过；核对 AML 材料中的原创/复用说明、接口/容量说明与固定 commit。
6. 到所有质量、接口、容量、披露门禁通过后，再由操作者决定是否申请 AML Smoke；按用户要求，Full 仍需单独授权。
