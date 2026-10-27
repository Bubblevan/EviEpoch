# MemoTime — 六问研究卡

- **论文：** *MemoTime: Memory-Augmented Temporal Knowledge Graph Enhanced Large Language Model Reasoning*
- **版本：** WWW 2026
- **原文：** [PDF](<../original/MemoTime - Memory-Augmented Temporal Knowledge Graph Enhanced Large Language Model Reasoning.pdf>)
- **阅读重点：** §4.1–4.4、Table 4 消融；核心任务是时序知识图谱问答，不是 AML 对话记忆端点。

## 01｜这篇论文针对哪个具体的 Memory Failure？

针对时序 QA 中三类失败：自然语言问题里的时间约束没有被显式 grounding；多实体各自检索后无法对齐到同一条时间一致路径；不同时间操作符（如 Before/After/Last）却被用同一轮改写和检索策略处理，造成漏证或噪声。

## 02｜核心算法的输入、输出和内部状态是什么？

- **输入：** 时间知识图谱中的带时间三元组，以及自然语言时序问题。
- **输出：** 由分解子问题、工具执行和证据路径支撑的答案；完成后写回已验证的推理经验。
- **查询过程：** 时间 grounding 识别实体、时间子图和问题类型；Tree of Time 将问题递归分解为带时间约束的子问题；按需选择时间工具包；图路径与嵌入检索混合取证，经时间一致性、语义排序和 LLM 选择后合成答案。
- **内部状态：** 时序图子图；问题树及每个节点的实体/关系/时间约束指示器；Experience Pool，按时序问题类型保存验证过的路径、工具选择、推理轨迹、问题/指示器双嵌入；另有高频缓存，低价值记录可被周期性修剪。

## 03｜它作用于 Add、Manage 还是 Search？

主贡献在 **Search/Reasoning**：识别时间算子、生成子问题树、用时间约束检索与排序。它也有经验记忆的 Add/Manage——把已验证的 QA 推理轨迹写回、按类型检索、按时效和成功率调整/修剪——但不是面向用户对话事实更新的通用 Manage 系统。

## 04｜作者的哪一个消融实验真正支持其创新点？

MultiTQ 的 Table 4 最有针对性：GPT-4o-mini 完整模型 Overall Hits@1 为 64.2；移除 Temporal Evidence Retrieval 后降至 11.2，移除 Graph Retrieval 后为 52.9，移除 Question Tree 后为 58.3，移除 Experience Memory 后为 59.8。时间证据检索的巨大落差支持其对时序路径的依赖；但这不是 append-only 对 overwrite 的比较，也不能单独证明每种时间操作符都被正确识别。

## 05｜在 AML 固定 Answer/Eval、同步 Add 下，哪些机制仍适用？

可迁移的是 Search 前对问题做 current / as-of / before-after 等时间意图与约束解析，再据此过滤、排序原文证据；需要控制调用数和失败回退。Tree of Time、LLM 工具包与经验自更新是多步生成式 QA 系统的一部分，不宜直接塞入 AML Search。原文评测是 temporal KG QA，未验证同步对话 Add、证据-only 输出或固定 Answer 模型。

## 06｜与 EviEpoch 当前代码相比，真正新增了什么？

当前 EviEpoch 将查询向量与 Recall Query 融合，没有显式时间算子解析、日期区间过滤、时间路径检索或带问题类型的推理经验库。最小可测增量是把查询分类为“当前值 / 某时点值 / 时间顺序或变化”，对候选证据实施条件化时间约束，并比较各类证据召回与固定 Answer 的结果。
