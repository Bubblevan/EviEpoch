# 上游代码与方法披露

本文件区分直接迁移的代码与方法参考。`eviepoch/_vendor` 只包含下面列出的独立组件，无需安装三个完整上游项目。

| 项目 | 实际参考 commit | 迁移范围 | EviEpoch 修改与接入 | 许可证 |
|---|---|---|---|---|
| [Hindsight](https://github.com/vectorize-io/hindsight) | `95efb9cca8e6eaea41cb1972d511c2aee58cb66e` | `hindsight-api-slim/hindsight_api/engine/search/fusion.py`，以及 `types.py` 中的 RetrievalResult、ArmScores、MergedCandidate | 保留融合函数实现；抽取仅依赖标准库的三个 DTO；`retrieval/fusion.py` 适配字典候选、每路上限与可检查的来源排名 | [MIT](third_party/licenses/HINDSIGHT-MIT.txt)，Copyright (c) 2025 Vectorize AI, Inc. |
| [MemOS](https://github.com/MemTensor/MemOS) | `adcb9f270c982f2b9ff8db9714792829d3bd6ab8` | `src/memos/memories/textual/item.py` 的 SourceMessage | 独立抽取类型与必要 imports；用作 Source.locator，权威原始消息仍由 EviEpoch 原样保存；不复制正文到 locator | [Apache-2.0](third_party/licenses/MEMOS-APACHE-2.0.txt) |
| [Cognee](https://github.com/topoteretes/cognee) | `37f788bd287750e10d7610173e5332b3843f6ebd` | `cognee/infrastructure/databases/provenance/{constants,source_refs,source_ref_state}.py` | 保留纯函数实现；`governance/provenance.py` 将 namespace/source/publication 映射到其来源归属合同；写入与撤销均在 Add 的同一事务中执行 | [Apache-2.0](third_party/licenses/COGNEE-APACHE-2.0.txt)、[原 NOTICE](third_party/licenses/COGNEE-NOTICE.txt) |

所有 vendored 文件均加了上游与固定 commit 的文件头。Hindsight 的类型文件和 MemOS 文件明确标记了抽取范围。原许可证与 Cognee NOTICE 随仓库保留。构建镜像需包含这些声明与许可证。

MemOS 的 WorkingMemory/LongTermMemory/RawFileMemory/UserMemory 分层、Hindsight 的 retain/recall/reflect 边界与 Cognee 的来源原生删除亦作为架构参考；这些既有机制不被宣称为 EviEpoch 的原创。EviEpoch 自行实现查询时间状态、AND/OR 证据义务、预算内集合选择，以及禁止披露优先于历史查询的治理语义。

原 EviEpoch 双通道 runner 保存在 `baselines/`；继承本项目工作区的历史代码。原仓库在本次工作开始时没有顶层 LICENSE，不能据此推断未披露上游的许可。正式开源前仍需由项目所有者确认主体许可证、历史代码来源与作者。本文不将未完成核验标为已通过。

运行依赖包括 FastAPI、Uvicorn、Pydantic、NumPy、OpenAI Python SDK、tiktoken、psycopg、httpx 与 python-dotenv；完整发行时应同时生成依赖/SBOM 与各包许可清单。模型 API、上游代码、公开数据和正式评测私有数据分别管理。
