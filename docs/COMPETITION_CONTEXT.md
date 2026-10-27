# 赛事、来源与当前实现上下文

**核对日期：** 2026-10-27（Asia/Shanghai）。此文件为 PRD 和技术设计提供规则/数据来源；“官方事实”“历史调研”“实现观察”“产品目标”分别标识。

## 1. 官方事实

参赛选择为第二届 Agent Memory Challenge 的 Textual / Open-source Methods。系统自托管 Add/Search，平台固定 Answer/Eval；开源方法的 embedding 使用 text-embedding-v4，LLM 组件使用 gpt-4o-mini，reranker 不限。提交需绑定公开仓库、固定版本及运行配置。材料截止 2026-10-31 23:59，评测停止 2026-11-04 23:59（UTC+8）。每赛道两次 Full，第二次需首次完成后等待 30 天；当前参赛窗口按一轮正式 Full 组织，平台异常使用原任务续跑。[赛事官网](https://agentmemories.ai/competition/)

官方普通文本分段为 20 messages 或 2,000 Adapter-counted words；Streaming 使用增量单元。Add 成功意味着持久化且立即可检索。user_id 为隔离边界，session_id 为来源组织；Search 保留原始语言和顶层 options，返回数量不超过 top_k，正式值为 100。单请求上限 30 分钟；这不是本产品延迟目标。未声明 metadata 不会传给 Answer；关键依据须在 content 中有源。[接入合同](https://agentmemories.ai/api-guide)

私有评测材料仅用于当前任务，不用于训练、产品分析或数据重建；完成后 30 天内删除相关副本。版本需保留脱敏运行元数据和官方 Run/Suite/Pipeline 标识。[数据责任与结果复核](https://agentmemories.ai/api-guide)

正式发布使用当期实际冻结合同。上述页面曾同时出现 Add 模型规则的“expected”表述和整体模型限定；本产品统一采用 gpt-4o-mini 完成所有 LLM 操作，避免依赖不含 LLM Add 的解释。

## 2. 两组排行榜依据

- 旧调研报告截止 2026-10-23：开源榜 InvMem 45.06、ReFind 44.97、ActiveMemoryIndex 44.84；MemMachine Overall 42.18，C/D 37.92/38.73。
- 新增 [官方公开 JSON 快照](../literature/systems/sources/aml-cycle1-leaderboard-2026-10-27.json)：包含本次用户指定的商业 Top 10 及叶子分数；[CSV](../literature/systems/sources/commercial-top10.csv) 与 [系统档案](../literature/systems/README.md) 可复核。
- 商业第一 MemoraX v0.5 Overall 58.02；MemOS v2.0.1 为 45.89；Vectorize Hindsight Cloud v1.0.1 为 38.54。
- 它们为 Cycle 1 历史结果，不能充当 Cycle 2 的直接同分布阈值。商业与开源模型条件、论文实验、同名当前仓库分别记录。Overall 不由七项展示分数简单平均。

## 3. 文献出发点

[综述研究议程](../literature/survey-agenda.md) 已按 Du 原文 §8、§9.1–9.10、§10 校对。第 10 章的四题为：巩固不丢失、因果检索、可信反思、安全遗忘。生日地址只是 §7.3 的一个说明性案例。

[14 篇研究卡](../literature/cards/README.md) 和 [系统启发](../literature/systems/eviepoch-implications.md) 提供机制与消融边界。多路检索、图扩散、统一资源对象、事实/意见分型已有先例；项目贡献由状态/依赖与证据集合的联合方法及实验证明。

## 4. 当前实现快照

以下为此次实现前读取的基线快照。新的分层研究实现及验收状态见 [IMPLEMENTATION_STATUS](IMPLEMENTATION_STATUS.md)，不以旧容量报告推断新系统性能。

本次读到的工作区 HEAD：1e6f8c199fb6b3d3f5dd33a29a339bc1627d89d9。当前运行代码是 FastAPI + SQLite + NumPy，原文展示条目与可选原子事实双通道，双 query embedding，去重/邻近扩展/预算/raw-first；没有本文目标设计的完整管理与图规划。

需要在实现迁移中显式处理的事实：

- 当前 raw 存储正文经过 strip，并转成带前缀的展示文本；新的来源层应保留原始内容和 role，展示渲染另做。
- fact 与 chunk 关联，没有每条事实的精确 source span 和完整有效期。
- 以 user/request 幂等，但未绑定 payload hash；全空消息直接成功不留 request marker。
- Search 对单用户全部向量读取、复制和全量评分。
- 当前 replay 只有 ID 召回和完整证据率；没有统一固定 Answer 的完整端到端框架。
- [容量报告](../CAPACITY_REPORT.md) 是 fake provider 的应用基线；10,000 条记录、1024 维、并发 16 的 Search p95 约 5.67s，未测真实模型/公网 Full 容量。

[提交草稿](../SUBMISSION_DRAFT.md) 绑定的是 4e7a614b47286241d94330a7c4329c660de45561，与本地 HEAD 不同；公网版本不能由本地 HEAD 推断。发布 manifest 应给出实际部署镜像和 commit 的绑定。本次根目录未见 LICENSE/第三方许可文件，草稿却写“保留上游 MIT”；正式发布应补齐可核验许可及上游作者/commit 的披露材料。

## 5. 技术依据

PostgreSQL 的行级安全支持按行限制查询/写入，但 owner/superuser 可绕过；服务账号应不带绕过权限，并使用相应强制策略。[PostgreSQL RLS](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)

pgvector 在 PostgreSQL 内提供精确和近似向量检索，支持与普通数据联接和同一事务底座；它解决访问路径，不直接提供时态推理或证据完备性。[pgvector 原作者文档](https://github.com/pgvector/pgvector)

## 6. 文档状态

[PRD](PRD.md) 定义完整目标产品，[技术设计](TECHNICAL_DESIGN.md) 定义拟实现方案；其中的指标均为设计目标，不是当前代码测得结果。当前行为以上述代码与已有容量报告为依据。
