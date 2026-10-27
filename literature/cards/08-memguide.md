# MemGuide — 六问研究卡

- **论文：** *MemGuide: Intent-Driven Memory Selection for Goal-Oriented Multi-Session LLM Agents*
- **版本：** AAAI 2026
- **原文：** [PDF](<../original/MemGuide - Intent-Driven Memory Selection for Goal-Oriented Multi-Session LLM Agents.pdf>)
- **阅读重点：** 记忆库构造、Intent-Aligned Retrieval、Missing-Slot Guided Filtering 和相应消融。

## 01｜这篇论文针对哪个具体的 Memory Failure？

针对任务型对话里“语义相似但目标不同”的检索错误，以及已经取回的历史记忆仍然没覆盖当前任务缺失槽位的问题。它关注的是跨会话任务目标和 slot 连续性，不是开放域长期事实冲突。

## 02｜核心算法的输入、输出和内部状态是什么？

- **输入：** 当前会话上下文、用户长期历史记忆库及任务型对话 schema。
- **记忆库：** 每个历史意图保存一个 intent key 与一组 QA 记忆单元；每个 QA 对应一个 task slot 的问答和值。
- **阶段一：** LLM 把当前上下文压成规范化高层 intent key，按 embedding 相似度找目标一致的记忆单元。
- **阶段二：** CoT slot reasoner 推断当前未填槽位，再由微调 LLaMA-8B filter 按补全缺失槽位的效用筛记忆。
- **输出：** 过滤后的核心 slot 值、缺失槽位提示，交给响应生成器主动确认或补全。

## 03｜它作用于 Add、Manage 还是 Search？

主要作用于 **Search**：先按意图缩小跨会话候选，再围绕缺失信息过滤。Add 阶段将完成的历史任务构造成 intent key 与 QA 对；论文数据构造刻意保证同一 memory bank 中 intent 不冲突，故不能视为已解决了真实用户事实更新/冲突治理。

## 04｜作者的哪一个消融实验真正支持其创新点？

两组结果分别支持两阶段。intent-QA 格式在 Table 6 中相较 raw history 提升多个 LLM 的 GPT-4 评分（如 LLaMA3-8B 5.09→6.34）。去掉 Missing-Slot Filtering 后，Qwen2.5-7B 的 JGA 从 0.74 降到 0.41，GPT-4o-mini 的 DTE 从 3.19 增到 4.30；过滤带来的 Recall@5 平均提升 7.7%（0.792→0.832）。这有力支持目标/槽位导向检索，但评测是合成 MS-TOD 的 TOD 成功率，不是 AML 记忆证据检索。

## 05｜在 AML 固定 Answer/Eval、同步 Add 下，哪些机制仍适用？

可迁移“从问题推断缺失证据类型，再检索能补齐这些槽位的来源”的思想，用作 Search 端的证据需求规划。原论文的 CoT reasoner、专用微调过滤器与响应生成面向 TOD；迁移时应把 slot 改为待回答问题中的实体、属性、时间或来源需求，并返回对应原文。不能把它的 task success 分数当成 AML 成绩。

## 06｜与 EviEpoch 当前代码相比，真正新增了什么？

EviEpoch 目前查询/Recall Query 向量融合，不显式提取任务意图、问题槽位或缺失证据需求。可先把复合问题拆成证据槽位，测 Complete Evidence Rate 和固定 Answer 的正确率；不应一次性引入整个专用 TOD 流程。
