# G-Memory — 六问研究卡

- **论文：** *G-Memory: Tracing Hierarchical Memory for Multi-Agent Systems*
- **版本：** NeurIPS 2025
- **原文：** [PDF](<../original/G-Memory - Tracing Hierarchical Memory for Multi-Agent Systems.pdf>)
- **阅读重点：** §4 三层图的双向遍历与更新、§5 多智能体任务/成本/消融。

## 01｜这篇论文针对哪个具体的 Memory Failure？

针对多 Agent 系统只保留简单任务摘要或成功结果，忽视角色间协作轨迹；因此团队在新任务上重复犯错，且无法把跨任务经验分配给不同 Agent 角色。它不以个人用户事实的时间版本为研究对象。

## 02｜核心算法的输入、输出和内部状态是什么？

- **输入：** 新任务查询 Q、历史 query graph 和每个历史任务的多 Agent interaction graph。
- **内部状态：** 三层图：Insight Graph 存跨任务抽象经验；Query Graph 连接相似问题及其支持 insight；Interaction Graph 保存按时间排列的 Agent 发言/协作轨迹。
- **Search：** 先对历史 query embedding Top-K 并扩展 1-hop；向上取相关 insights，向下由 LLM 稀疏化相似任务的细粒度协作子图；再按每个 Agent 的角色定制 Mem_i。
- **Add/Manage：** 新任务结束后记录 Agent 交互、执行状态/反馈、token 及任务节点，再生成新 insight 并连回支持它的历史任务。

## 03｜它作用于 Add、Manage 还是 Search？

完整覆盖，但核心是多智能体场景中的 **Search + Manage**：从历史任务检索经验/轨迹，并在任务结束后更新三层图。它针对 MAS 团队自我演进，不是用户记忆接口方案。

## 04｜作者的哪一个消融实验真正支持其创新点？

§5.4 Figure 4c 分别提供“仅高层 insight”和“仅细粒度 interaction”两种变体；两者均比完整 G-Memory 低，支持抽象经验与具体协作轨迹互补。作者还发现 1-hop 通常优于更深扩展，取太多 query 会引入噪声；token 成本分析显示某些替代 MAS 记忆多耗约 2.2M tokens 仅换来 4.07% 提升，而 G-Memory 在 PDDL+AutoGen 的无记忆基线上仅增加约 1.4M tokens 得到 10.32% 提升。该实验证明的是多 Agent 任务/反馈记忆，不证明用户地址状态可追踪。

## 05｜在 AML 固定 Answer/Eval、同步 Add 下，哪些机制仍适用？

可以借鉴“高层经验与具体原始轨迹分别存取”的分层思想，但 AML 是跨会话用户事实证据检索，不存在多 Agent 角色个性化与环境执行反馈。整套图架构成本高，且不能替代固定 Answer 所需的来源证据。同步 Add 也要求清楚定义哪些异步生成的 insight 已可见。

## 06｜与 EviEpoch 当前代码相比，真正新增了什么？

EviEpoch 没有跨任务 insight graph、query graph、interaction graph 或角色定制记忆；现有原文/原子事实向量库是不同对象。对本项目最可借鉴的是把摘要或经验标为可追溯派生物，而不是直接复制三层图。此项适合作为背景参考，不是 AML 当前优先实现路线。
