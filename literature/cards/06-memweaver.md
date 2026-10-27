# MemWeaver — 六问研究卡

- **论文：** *MemWeaver: Weaving Hybrid Memories for Traceable Long-Horizon Agentic Reasoning*
- **版本：** Findings of ACL 2026
- **原文：** [PDF](<../original/MemWeaver - Weaving Hybrid Memories for Traceable Long-Horizon Agentic Reasoning.pdf>)
- **阅读重点：** §4 三层记忆与双通道检索、Table 3–4 组件消融、§5 成本/人工证据评价。

## 01｜这篇论文针对哪个具体的 Memory Failure？

针对纯文本召回缺关系和时间结构、结构化事实积累冲突、跨会话摘要缺少原文支撑三类问题。它的关键风险判断是：图事实可以支持多跳和时间推理，但若没有原文 passage 作证，抽象结论难以追溯、纠错。

## 02｜核心算法的输入、输出和内部状态是什么？

- **输入：** 长期多会话对话与查询。
- **内部记忆：** Graph Memory 保存实体—关系事实并规范化时间；Passage Memory 保存原文证据；Experience Memory 将重复交互聚类、验证簇一致性后抽象可复用经验。三者通过实体节点与结构链接相连。
- **写入：** 对新事实规范化、复核和跨会话协调后再合并至图；原文及经验项挂接到实体，保留证据路径。
- **Search 输出：** 先取相关实体/关系子图，再取其所连原文和经验，同时保留一个全局稠密召回通道；最终构造紧凑的图上下文与文本上下文供回答模型使用。

## 03｜它作用于 Add、Manage 还是 Search？

三者都有，重点是 **Manage 与 Search 协同**：Graph Memory 汇总关系事实并做 session-level reconciliation；经验记忆从重复交互中抽象；查询时再联合结构化图与原文证据，避免把抽象图节点当成唯一依据。

## 04｜作者的哪一个消融实验真正支持其创新点？

Table 4 的原子组件消融最直接。Qwen2.5-1.5B 下，完整模型 Multi-Hop F1 为 21.91、Temporal F1 为 46.07；去掉 Graph Memory 分别降至 13.48 和 5.78；去掉 Experience Memory 为 19.59 和 45.30。图结构对多跳/时间任务的贡献远大于经验抽象。Table 3 的 KG/EXP/PASS 单组件比较和 Table 4 的 leave-one-out 指向互补性，但论文没有做“原始对话与图事实均保留 vs 覆盖删除”的专门对照，不能据此声称其已解决所有冲突和来源问题。

## 05｜在 AML 固定 Answer/Eval、同步 Add 下，哪些机制仍适用？

“结构化关系负责找证据，原文负责交付可核验依据”与 AML Search 很契合。可先实现轻量事实关系和来源指针，把图或结构字段作为派生检索索引。同步 Add 下要测事实抽取、实体消歧和跨会话协调成本；论文的回答上下文输出不是 AML evidence-only 契约，也没有比赛固定 Answer 的端到端结果。

## 06｜与 EviEpoch 当前代码相比，真正新增了什么？

EviEpoch 有原文、可选原子事实与向量搜索，但没有实体—关系图、时间规范化与跨会话 reconciliation、经验聚类，也未做“图检索 + 原文证据”双通道联动。优先测最小部分：提取的事实若被召回，能否链接并返回对应原文来源；随后再单独测试时间关系与跨会话整合。
