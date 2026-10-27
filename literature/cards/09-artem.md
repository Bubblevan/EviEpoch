# ARTEM — 六问研究卡

- **论文：** *ARTEM: Enhancing Large Language Model Agents with Spatial-Temporal Episodic Memory*
- **版本：** AAAI 2026
- **原文：** [PDF](<../original/ARTEM - Enhancing Large Language Model Agents with Spatial-Temporal Episodic Memory.pdf>)
- **阅读重点：** STEM 四通道事件编码、vigilance 检索、合成长篇实验；原文没有组件消融表。

## 01｜这篇论文针对哪个具体的 Memory Failure？

针对部分线索下的情景事件漏回、顺序/最新事件判断错误，以及记忆中不存在答案时模型仍编造。它将 episodic retrieval 写成多通道模式匹配问题，试图同时控制检索精度与拒答/不确定性。

## 02｜核心算法的输入、输出和内部状态是什么？

- **输入：** 文本来源和查询；LLM 从文本抽取时间、地点、实体和事件内容。
- **内部状态：** STEM（fusion ART 的一种实现）事件记录 e=(时间, 空间向量, 实体向量, 内容向量, 匹配置信度)，各通道分别设置 vigilance 阈值；时间按全语料范围归一化，其他通道用 384 维 SentenceTransformer 向量。
- **Add：** 事件向量进入 instance-based incremental learning 网络，形成可匹配的识别节点。
- **Search：** 部分 cue 可只激活对应通道；低于 vigilance 的候选被拒绝。检索策略可取相关事件、只取最新事件或按时间排序；LLM 再基于检索事件生成回答。

## 03｜它作用于 Add、Manage 还是 Search？

Add 与 Search：Add 抽取并增量编码事件；Search 多通道匹配并按查询要求执行最新/时间顺序访问。主要创新点是事件表示与 vigilance 控制，不是跨版本冲突管理。

## 04｜作者的哪一个消融实验真正支持其创新点？

**没有真正的组件消融。** 论文比较 STEM 与加 LLM 问答合成的 ARTEM，并报告“零相关记忆”题的拒答/检测能力以及 Latest 类表现改善；这些是系统对比，不等于单独去除时间/空间/实体/内容通道的 ablation。作者也报告 ARTEM 548 题、STEM 600 题，50 题因超过 512-token 限制被丢弃，另有 2 题抽取格式失败。实验在作者合成的 200 章、约 102,870-token 长篇《Synaptic Echoes》上进行。

因此，论文支持“vigilance 拒绝 + LLM 合成在该情景任务上有潜力”，但不能据此归因某个通道是关键创新。

## 05｜在 AML 固定 Answer/Eval、同步 Add 下，哪些机制仍适用？

如果数据需要时间/地点/实体 cue，结构化事件字段可辅助 Search，拒绝不充分匹配候选也值得研究。但 Add 依赖 LLM 事件抽取；AML 同步写入时要测延迟和格式失败。更重要的是论文问答阶段与 AML 固定 Answer 不同；Search 应返回原文证据和时间元数据，不能只返回 STEM 的抽象节点。vigilance 阈值还需在不同用户/语料上校准。

## 06｜与 EviEpoch 当前代码相比，真正新增了什么？

EviEpoch 保存原文与可选原子事实，主检索基于向量相似度；没有分开的时间/地点/实体 cue 通道、ART 式增量类别学习或置信阈值拒绝。可单独试验日期元数据过滤和部分查询 cue，不应因该论文的总体 F1 就重做底层记忆网络。
