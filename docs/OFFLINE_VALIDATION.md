# 检索、时间、治理与流式记忆修复

日期：2026-10-29（Asia/Shanghai）。本轮使用独立合成合同与故障控制，不调用真实模型，不导入私有评测材料，不运行官方评测。

## 已实现行为

| 范围 | 行为与约束 |
|---|---|
| 并发读取 | SQL 在短事务内执行；planning、embedding、binding、audit、disclosure 均在事务外。返回前校验 generation 和 policy_generation，默认最多重试 2 次；连续变化返回带阶段信息的 503。 |
| 时间 | 识别受限格式的原始消息时间，保留外层时间与锚点引用；相对日历日期可确定性校验，近似半个月保留区间。冲突锚点不生成精确相对日期。事件、首次提及、记录顺序采用独立排序轴。 |
| 状态与身份 | 完整的结构化 subject/relation/object ID 形成导航键；同名不同 ID 不合并，scope 与 modality 保留。显式替换形成定向版本边，兼容晚到旧记录；计划不覆盖当前事实，历史可恢复。普通自然语言纠正继续走模型复核。 |
| 全局综合 | global/list/order 增加跨会话候选与经核验的来源覆盖义务；达到上限显式留下缺项。episode 按本次 Add 的 session 分组，只作来源导航，不作为 API 事实或证明；跨多个 Add 的完整综合仍由检索覆盖完成。 |
| 规则与执行 | binder 输入由原文头部截断改为有偏移的相关窗口，包含远端例外；单行/转义长文本也可保留相距很远的规则与例外。工具轨迹建立步骤/工具/来源报告结果的导航，保留原文；不同 task_index 不作迁移硬过滤。 |
| 披露 | 模型按当前请求收窄证据，程序仅接受已选来源中的精确片段，拒绝虚构引用、无效 ID、遗漏决定；最终重算数量、字符、token 预算。片段不会自动计为完整来源证明。 |
| Add 故障 | 无效 JSON 与输出截断均可触发有界分片；源校验仍失败则整次 Add 不提交。支持的结构化记录可直接建立来源索引，减少脆弱抽取调用；不支持的格式仍走抽取校验。embedding 返回格式错误归为 provider 故障，而非客户端输入错误。 |
| 诊断 | 加入随机 trace_id、provider 成败阶段、检索分段耗时、候选/覆盖数量及选择状态。常规诊断不写题目、证据、用户 ID 或路径；原有显式 payload audit 是独立开关。 |

## 离线结果

SQLite 与本机独立 PostgreSQL 测试库的 **171 项测试全部通过，0 失败、0 错误、0 跳过**。覆盖原有来源/图/规则/遗忘/幂等/隔离合同，以及新增的并发遗忘、读取一致性、身份更新、历史恢复、全局覆盖、披露、分片、episode 依赖失效等控制。

无网络并发探针：8 个独立用户、8 个线程，每次模拟模型等待 100ms；模型阶段并发峰值为 **8**，总耗时约 **112ms**，全部返回证据。此结果验证模型不再被 SQLite 全局锁串行化；不能外推 HTTP、真实 provider 或大规模数据库吞吐。

原始可复核产物：

- [离线测试记录及源码 SHA-256](../experiments/reports/offline-validation.json)
- [合成并发探针](../experiments/reports/offline-concurrency.json)
- [独立合成控制](../tests/test_memory_improvements.py)
- [PostgreSQL 控制复用](../tests/test_memory_improvements_postgres.py)

开关对照保持相同查询、oracle 抽取、模型接口及证据预算，验证 coverage、state projection、disclosure 的局部行为。状态投影对照验证排除旧候选与保留历史，不宣称每次都会改变最终答案。oracle 测试验证控制流及约束，不估计模型自然语言准确率。

## 重现

```powershell
$env:TIKTOKEN_CACHE_DIR = "$PWD\.cache\tiktoken"
.venv\Scripts\python.exe experiments\offline_validation.py
.venv\Scripts\python.exe experiments\offline_concurrency.py
.venv\Scripts\python.exe -m compileall -q eviepoch experiments tests
git diff --check
```

完整 PostgreSQL 验证需设置 `EPOCH_TEST_DATABASE_URL` 和 `EPOCH_TEST_ADMIN_DATABASE_URL`，指向允许清空且名字以 `_test` 结尾的隔离测试库，然后运行：

```powershell
.venv\Scripts\python.exe experiments\offline_validation.py --require-postgres
```

测试会清空该专用库；不要传入生产数据库。验证 JSON 的 source_hashes 绑定本次被测试的源码，commit_before_changes 是提交修复之前的基点。

新增 retrieval 组件也支持公开数据上的冻结模型消融：

```powershell
.venv\Scripts\python.exe experiments\ablate.py --deepseek --improvements --output experiments\reports\improvement-ablations.json
```

该命令会调用模型，**本轮未执行**。新的冻结产物校验提示词摘要，拒绝将旧提示词产物误用于新协议。来源召回与完整来源证明分别记录；任意摘录不自动获得完整证据标签。

## 发布与剩余边界

- 本轮交付代码、离线测试和报告，不改变线上部署或线上数据库。外部模型语义判断、当前正式模型配置及端到端 Answer 质量仍需单独验证，不能据此承诺官方分数提高。
- 同一模型的绑定、候选核验、披露仍可能相关地犯错；quote 校验只证明引用真实，不能证明语义判断正确。披露开启时会增加模型调用与 token 成本，真实延迟未测。
- 全局覆盖、上下文窗口、图扩展与重试均有预算。episode 不替代所有历史，工具轨迹不自动晋升为通用技能；新任务完成率未在本轮测量。
- Dense 仍精确扫描，BM25 仍运行时聚合；没有新增 HNSW、连接池或大规模容量承诺。
- 新字段通过已有 payload 向后兼容，不需要表结构迁移。旧已提交请求的幂等重试不会重建索引；既有数据如需新增结构化 ID/episode 导航，应从获准的原始来源导入隔离新库并验证后再切换。
- 所有新增开关在 `.env.example`。离线验证完成不等于建议不经真实模型与部署检查直接替换参赛实例。
