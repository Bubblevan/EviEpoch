# EviEpoch

面向 Agent Memory Challenge II 的长期文本记忆服务。Add 保存精确来源并同步发布记忆与治理变化；Search 结合时间状态、关系依赖和证据义务，返回来源可定位的原文，交由平台统一 Answer。

开发模型按本次约定使用 DeepSeek；正式参赛配置强制校验 `gpt-4o-mini` 与 `text-embedding-v4`。当前是研究候选实现，尚未完成公网官方 Smoke、Full 或目标容量验收。

## 代码结构

```text
eviepoch/
  api/              # AML Add/Search、鉴权、就绪检查、公开研究工作台
  domain/           # 来源、UTF-8 引用、时间精度、断言、关系、证据义务
  ingest/           # 结构化抽取、引用与 ID 校验
  manage/           # 支持/变化/纠正、保真巩固、反例修订
  retrieval/        # 多路候选融合、查询条件化证明集、预算内选择
  governance/       # 抑制/在线擦除、依赖闭包、Cognee 来源归属状态转换
  storage/          # PostgreSQL/pgvector、namespace RLS、SQLite 验证适配器
  providers/        # 独立 LLM/embedding 客户端、结构化操作与调用计量
  observability/    # 无正文指标、配置/代码/prompt/upstream manifest
  baselines/        # 原始 B0 向量 runner
  datasets/         # 自建公开控制集与清单
  reports/          # 实际运行报告；开发控制结果不是官方成绩
  evaluate.py       # 相同 provider 的证据指标、固定本地 Answer/judge、方法对照
  provider_smoke.py # 真实 provider 小样本验证
  start_research.py # 本地公开样本工作台
```

原有 `eviepoch.pipeline.MemoryPipeline`、`eviepoch.models`、`eviepoch.api:app` 入口保留。B0 通过 `EPOCH_PROFILE=b0` 显式启用。完整要求见 [PRD](docs/PRD.md)、[TRD](docs/TECHNICAL_DESIGN.md)，实际实现边界见 [实现与验收状态](docs/IMPLEMENTATION_STATUS.md)。

## 本地运行

本次环境已创建 `.venv`。新环境安装：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:TIKTOKEN_CACHE_DIR = "$PWD\.cache\tiktoken"
.venv\Scripts\python.exe -c "import tiktoken; tiktoken.get_encoding('o200k_base')"
```

开发使用现有 `.env` 中的 `DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`，不修改其内容，也不把 key 放进命令行。使用已核验的 DeepSeek `deepseek-flash` 和百炼 `text-embedding-v4`：

```powershell
.venv\Scripts\python.exe experiments\start_research.py --deepseek --demo
```

打开 [本地工作台](http://127.0.0.1:8013/workbench)，user 为 `public-demo`，点击“加载记忆”。该启动器仅绑定 localhost，使用单独的 `data/public-research.sqlite3`，只导入自建公开住所样本。正式私有运行关闭 `EPOCH_WORKBENCH`。

正式模型服务请按 `.env.example` 配置 `OPENAI_API_KEY`、`EPOCH_LLM_BASE_URL`、`EPOCH_EMBED_API_KEY`、`EPOCH_EMBED_BASE_URL`、`EPOCH_AUTH_TOKEN`：

```powershell
.venv\Scripts\python.exe -m uvicorn eviepoch.api:app --env-file .env --host 127.0.0.1 --port 8000 --no-access-log
```

`full` 不会因模型失败而静默切换成 raw-only。不同 payload 重用 request_id 返回 409；未提交、容量满或模型故障不会返回成功。

## PostgreSQL 部署

```powershell
docker compose -f compose.postgres.yaml -p eviepoch up -d --build
```

Compose 启动 PostgreSQL/pgvector、独立迁移进程与 API；服务使用非 owner、非 superuser、无 BYPASSRLS 的角色，每个事务设置 namespace。DDL 只由迁移角色执行。Compose 内置凭据仅供本机开发，正式环境由部署方配置独立凭据和 HTTPS 网关。

默认 PostgreSQL 向量检索在数据库内按 namespace 精确计算；BM25 也在数据库内聚合。当前未部署 HNSW、连接池、Redis 或生产备份治理，不据此声称 300M tokens 或 64 Add/256 Search 容量已通过。SQLite 后端供本地验证，Search 仍会做本用户向量精确扫描。

原 SQLite 基线库不自动冒充精确来源库：旧展示字符串已丢失的空白或 role 无法恢复。新库默认 `eviepoch-v1.sqlite3`；参赛数据应从合法原始输入重新导入。

## 接口

`GET /health` 为存活检查，`GET /ready` 检查存储；`GET /metrics` 经鉴权返回无正文计数与有界延迟样本。Add/Search 支持 Bearer、Token、X-API-Key。

```json
{"request_id":"r1","user_id":"u1","session_id":"s1","messages":[{"role":"user","content":"I moved to Kyoto.","timestamp":1684540800000}]}
```

```json
{"query":"Where did I move?","user_id":"u1","top_k":20,"options":[]}
```

Add 在来源、索引、状态/治理与 request ledger 同一 generation 提交后返回原始标识。Search 返回：

```json
{"data":[{"id":"stable-source-or-span-id","content":"[2023-05-20T00:00:00Z][user] I moved to Kyoto.","score":0.03,"created_at":"2023-05-20T00:00:00Z"}]}
```

正文保留原始语言和文字。缺失来源时间时不补服务器日期；生成摘要/规划文本不会出现在 evidence content 中。数量、字符与 token 预算均限制输出。原文过长时可返回精确引用片段。options 只用于规划核验，不写入记忆。

## 验证与实验

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests
.venv\Scripts\python.exe experiments\provider_smoke.py --deepseek
.venv\Scripts\python.exe experiments\evaluate.py --deepseek --answer --arms full topk greedy hybrid
```

本地测试包含精确来源、时间/模态、作用域、变化/纠正、巩固、因果/规则、反例、遗忘重放、并发幂等、跨 user 隔离、AND/OR 选择器及上游迁移合同。PostgreSQL 集成测试需设置 `EPOCH_TEST_DATABASE_URL` 与 `EPOCH_TEST_ADMIN_DATABASE_URL`；测试库名必须以 `_test` 结尾，测试会清空该专用库。

`evaluate.py` 只使用自建公开控制集。Full、Top-K、Greedy 可复用同次公开实验的抽取/计划/embedding；该缓存只存在于评测进程，API 无正文缓存。失败案例保留在分母中。热缓存时延不是容量测量；DeepSeek 本地 Answer/judge 是代理评测，不能标成官方 AML Overall。自然公开基准、隔离的封存测试、配对区间和全交互消融仍需扩展。

冻结候选配置：

```powershell
.venv\Scripts\python.exe -m eviepoch.manifest --output experiments\reports\release-manifest.json
```

manifest 不输出密钥；记录 dirty 状态、代码/prompt/schema/配置及上游 commit。镜像 digest、官方 Smoke ID、目标容量与许可核验未完成时保持未通过状态。
