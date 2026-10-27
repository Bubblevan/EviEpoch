# EviEpoch 实现与验收状态

日期：2026-10-27，Asia/Shanghai。本文描述可执行研究实现；PRD/TRD 的目标指标保持不变。

## 已实现的纵向链路

Add → 权威原始 Source + MemOS locator → 结构化抽取/精确 UTF-8 span 校验 → 支持/变化/纠正/冲突与反例协调 → 可回钻巩固 → 原文与导航 embedding/postings → 同事务发布 generation、幂等 ledger、治理及 Cognee 来源归属。

Search → 查询义务与时间语义 → Dense/user-BM25/时间/关系候选 → Hindsight RRF → 同一来源快照中的状态与依赖证明绑定 → AND/OR 证据集合 beam/greedy/top-k → 合法原始来源或 span → AML evidence 合同。

正式模式的 LLM 阶段使用 gpt-4o-mini，embedding 使用 text-embedding-v4；用户指定的开发验证使用 DeepSeek，明确标为 research。当前运行默认没有独立 cross-encoder；可通过 EPOCH_RERANK_URL 接入只返回候选 ID/score 的评分服务。

## 需求追踪

| PRD | 当前实现 | 已有验证与实际边界 |
|---|---|---|
| F01–F02 | 原始 role/content/time、稳定 ID、唯一 quote、UTF-8 byte span/hash | 原文含首尾空白/换行/中文 round-trip；引用核验只证明文本存在，不能形式化证明全部语义蕴含 |
| F03 | subject/attribute/scope/cardinality；模型在已知节点上下文中消歧 | 同名/多值/作用域控制测试；尚无独立实体合并模型或校准实体置信度 |
| F04–F05 | asserted/stored/occurrence/valid time、年月日精度、模态、版本边、当前/as-of 证明选择 | 变化/纠正/计划/晚到旧历史与 current/as-of 控制；相对日期仍依赖模型正确归一化，不宣称解决所有日历/开放区间 |
| F06 | hot/long_term/scenario、关键约束标记、extractive coverage/source sets | 罕见约束和回钻测试；巩固不删 L0/L1，尚未证明减少总体检索成本，暂无任务效用学习的归档策略 |
| F07 | hypothesis/supported/challenged/revised；支持源/反例源、独立来源支持指数、状态版本 | 新反例挑战旧经验；指数不是校准概率。反思只取自明确表达的经验，不是已训练的跨任务反思模型 |
| F08 | suppression、deactivate、在线 erase；源/节点/边/versions/postings/vector/provenance 闭包 | 禁止披露、重放防复活与新来源重引入；目标为来源消息级，可能同时禁止该消息的其他事实。WAL/备份/外部副本擦除仍需运维确认，不能称全副本物理擦除完成 |
| F09–F11 | QueryPlan、独立义务、OR-of-AND 来源证明、图路径、源 ID 校验、一次缺口取证、beam/greedy/top-k | 因果/规则/例外与完整前提测试；模型证明绑定是候选假设，不是经过形式验证的定理证明 |
| F12 | 原文或可定位 span、真实时间、排序分数、数量/字符/token 预算 | API/选择器测试；长来源回退目前选择单个 span，可能不足以支持同消息内的多个远距前提，需专项评测 |
| F13–F14 | payload-hash 幂等、租约、同步原子发布、repeatable-read、namespace 参数与 PG RLS、无 Search 写回 | 失败重试、并发、Streaming、用户隔离及 options 不污染；SQLite 用进程内锁，是验证适配器，不是高并发部署方案 |
| F15 | 来源/引用/当前节点/版本/关系/操作；Search 需求与证明/选中源/缺口轨迹 | opt-in 本地 Workbench；默认正式服务关闭，尚无全量操作 diff 可视化与自然数据错误归因标注 |
| F16 | PostgreSQL/pgvector、数据库内 BM25、稀疏图、有界 admission、无正文指标、Docker | 真实 PG 集成测试与模型公开小样本；未实测目标容量/公网尾延迟/恢复，暂无 PG 连接池/HNSW/Redis/OTel 服务 |
| F17 | 固定上游 commit、完整第三方许可、代码/prompt/config manifest、本地 fixed Answer/judge runner | 仓库尚未冻结 commit，主体 LICENSE/历史来源待确认；公网 HTTPS/官方 Smoke/Full/真实容量、自然公开与封存数据实验未完成 |

## 评测状态与解释

`tests/` 的确定性 provider 是抽取 oracle/合成向量，只验证合同与算法状态，不估计 LLM 实际准确率。可选择在真实 PostgreSQL 上复用整个生命周期测试集。

真实开发验证使用 DeepSeek + text-embedding-v4；`experiments/reports/` 保存公开自建样本的证据、运行配置与实际结果。数据包含地址变化/计划、事件顺序、上游原因、规则例外、中文饮食约束、删除、无答案及反例修订。它们均为开发控制，不是独立封存测试。初次失败报告保留，不能从失败前后对比推断自然基准提升。

需要继续完成：自然公开基准数据适配、无泄漏的 train/dev/sealed-test、相同候选与预算的完整组件交互消融、配对区间，以及正式 gpt-4o-mini + 公网链路的容量与官方 Smoke。只有实际通过的冻结版本才可进入 Full。

## 数据与恢复边界

正式 API 不写正文日志，不将查询/选项写回，无内容型 Search cache；metrics 只保留计数与有界耗时样本。公开研究脚本可以留完整轨迹，其运行空间不得接入正式私有数据。

`erase` 删除在线可查询行及其派生表示，保留不含正文的 request hash、来源 ID 和操作凭据。SQL 删除不证明介质、WAL、快照或远程备份已擦除；operation 中的 backup_erasure 明确为运维待确认。正式比赛运行需另外落实数据保留期限与删除凭据。

旧库没有保留的原始 payload 无法无损重建，因此不自动迁移为精确来源；保留 B0 可复现入口，使用合法原始输入导入独立 v1 库。
