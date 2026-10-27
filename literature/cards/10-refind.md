# ReFind — 六问研究卡

- **论文：** *When Your Agent Opens the Chat App: Agent-Controlled Search over Raw Chat Logs Rivals Structured Memory*
- **版本：** arXiv:2608.12888v2，2026-08-16
- **原文：** [PDF](<../original/ReFind - When Your Agent Opens the Chat App - Agent-Controlled Search over Raw Chat Logs Rivals Structured Memory.pdf>)
- **阅读重点：** §3 搜索接口、MemoryAgentBench 六任务、LongMemEval 对照与工具消融。

## 01｜这篇论文针对哪个具体的 Memory Failure？

针对把原始对话预先压缩/结构化后遗漏未来问题所需细节，以及单轮 BM25 无法拼合多跳证据的问题。ReFind 主张保留原档，把控制能力从离线记忆构建移到在线检索：agent 多轮改写关键词、选时间范围、看会话邻域并跳过已查过的 session。

## 02｜核心算法的输入、输出和内部状态是什么？

- **输入：** 带 turn 边界、session ID、时间戳的原始聊天记录 H 与问题 q。
- **静态索引：** turn 级 BM25；session 级分数由其中命中 turn 的 BM25 分数聚合，再与 turn 排名用 RRF 融合。
- **检索控制状态：** 每轮 σt=(已保存笔记、已查看 sessions、上次观察)；动作包括关键词、可选时间范围、结果预算。命中后默认扩展同 session 前后各 2 轮，session 边界处截断。
- **输出：** agent 保存的证据笔记；之后独立 Answer 阶段按 session 分组、按时间排列，并用原始措辞生成答案。默认最多 4 轮检索，BM25 top-5。

## 03｜它作用于 Add、Manage 还是 Search？

主要是 **Search**。Add 只追加原文词项和元数据，无 LLM 摘要/图构建；跨轮状态只记笔记与已搜索会话，不更新持久事实状态。时段过滤是检索控制，不等于事实有效期区间或 supersession 状态机。

## 04｜作者的哪一个消融实验真正支持其创新点？

有匹配控制。LongMemEval-S/M 上完整 ReFind（GPT-5-mini，5 次运行）为 93.2±3.3 / 89.3±6.0；去掉会话/时间/邻域/去重这些 chat-native 控制的通用 agentic BM25 为 78.7±4.6 / 82.2±3.8，说明迭代 lexical search 本身不足。论文另做单轮 BM25、组件移除及 dense/hybrid 后端控制。MemoryAgentBench 六项约 2,800 题的平均准确率为 58.2；在事实更新任务 FC-SH/FC-MH 分别为 62.7/8.8，故尤其多跳更新仍远未解决。

## 05｜在 AML 固定 Answer/Eval、同步 Add 下，哪些机制仍适用？

高度贴合接口边界：Add 可同步只追加原文；BM25 可增量索引；Search 用多轮、会话融合、时间缩小和邻域扩展收集证据，再把证据-only 笔记交给固定 Answer。需要先核实 AML 允许的调用次数/延迟，且用同一个 Answer/Eval 对比。论文自身的 GPT-4o-mini/GPT-5-mini Agent + Answer 结果不能直接解释为 AML 官方分数。

## 06｜与 EviEpoch 当前代码相比，真正新增了什么？

EviEpoch 当前已有原文、双向量查询融合、去重、邻近轮扩展、证据预算；ReFind 的明确新增是 BM25 与会话级 RRF、Search 控制状态、迭代检索和时间范围参数。下一步应先做 BM25/session-RRF 的离线检索对照，再按需加受预算约束的迭代控制；不必照搬其生成式 Agent。
