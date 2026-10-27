# SimpleMem — 六问研究卡

- **论文：** *SimpleMem: Efficient Lifelong Memory for LLM Agents*
- **版本：** arXiv:2601.02553v3，PDF 标注 2026-01-29
- **原文：** [PDF](<../original/SimpleMem - Efficient Lifelong Memory for LLM Agents.pdf>)
- **阅读重点：** 三阶段算法、Table 5 分阶段消融与推理/构建成本。

## 01｜这篇论文针对哪个具体的 Memory Failure？

针对长对话里低密度冗余、指代与相对时间未规范化，以及检索时反复读大量片段造成上下文和推理成本上升。它提出先压缩、再会话内综合、最后按查询意图规划检索。

## 02｜核心算法的输入、输出和内部状态是什么？

- **输入：** 多轮/多会话对话，以及自然语言查询。
- **Add：** Semantic Structured Compression 筛选信息密度较高的内容、规范化指代与相对时间，形成结构化记忆单元。
- **Manage：** Online Semantic Synthesis 在会话内综合相关片段。
- **Search：** Intent-Aware Retrieval Planning 按查询复杂度设置范围，并行检索语义向量、BM25 词法与符号元数据视图，合并去重。
- **内部状态/输出：** 规范化后的记忆单元及其语义、词法、符号索引；输出给下游回答模型的相关上下文。

## 03｜它作用于 Add、Manage 还是 Search？

覆盖 **Add/Manage/Search**：写入压缩、同会话综合、查询复杂度驱动检索计划。与 APEX-MEM 的保留全部事实演化轨迹不同，SimpleMem 更积极地提前整理和压缩。

## 04｜作者的哪一个消融实验真正支持其创新点？

LoCoMo Table 5（GPT-4.1-mini）三阶段消融：完整模型 Average F1 43.24。去掉 Semantic Structured Compression 后 Temporal F1 从 58.62 降到 25.40；去掉 Online Semantic Synthesis 后 Multi-Hop 降 11.2%、Average 降 11.6%；去掉 Intent-Aware Retrieval Planning 后 Open-Domain 降 26.6%、Single-Hop 降 19.4%，Average 降 12.6%。这些类别差异支持组件的定向作用，但不能证明压缩对 AML 低频长尾证据是无损的。

效率实验报告 LoCoMo-10、GPT-4.1-mini 下每样本构建 92.6s、检索 388.3s、合计 480.9s；该设置和测量口径不能直接外推 AML 线上延迟。

## 05｜在 AML 固定 Answer/Eval、同步 Add 下，哪些机制仍适用？

可先迁移 BM25/符号字段等多视图 Search 和查询复杂度规划。原文要可追溯保存；压缩/综合结果作为可重建的派生索引，避免稀有事实被不可逆过滤。同步 Add 的多步压缩与综合要单独测时延；最终返回来源证据，而不是论文系统生成的答案摘要。

## 06｜与 EviEpoch 当前代码相比，真正新增了什么？

EviEpoch 有原文和可选原子事实、向量召回、去重和邻近轮扩展；缺少 BM25/符号并行视图、密度筛选、会话内综合与查询复杂度计划。适合拆开验证词法/元数据召回和 Search 计划；先不把三阶段整套搬进同步 Add。
