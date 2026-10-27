# 开发验证记录

日期：2026-10-27，Asia/Shanghai。当前为 research 候选，非官方参赛成绩。

## 合同与存储

在 Windows Python 3.12.4 的项目 `.venv` 中运行 unittest，启用独立 PostgreSQL 17 + pgvector 0.8.1 测试库：

```text
Ran 62 tests in 10.846s
OK
```

覆盖 SQLite 与真实 PostgreSQL 的同一批生命周期场景、API、用户 RLS、来源与原始时间、UTF-8 引用、序列化预算、并发幂等、状态/版本/反例、规则与三来源因果路径、遗忘的索引/版本/归属闭包及上游组件合同。

`git diff --check` 通过；包与实验脚本 compileall 通过。Docker 镜像构建和 API/Hindsight/Cognee 模块导入检查通过。最后镜像为：

```text
eviepoch:dev-v1
sha256:c22e60e98204fb467897d7a919b0f19647b8fa9dfba18bda298caffcdb3dc2c6
```

它是本机 image ID，不冒充已推送镜像的 registry digest。工作区 dirty，未绑定冻结 release commit。

## 真实 provider 小样本

[最终 provider smoke](../experiments/reports/provider-smoke-current.json)：DeepSeek `deepseek-flash` + 百炼 `text-embedding-v4`，3 次 Add、当前/历史两次 Search，通过。记录 7 次结构化调用、5 次 embedding 调用、8,427 输入 tokens 与 810 输出 tokens。仅使用自建公开住所样本。

## 公开开发控制集

[实际报告](../experiments/reports/public-controls.json)，8 个自建场景、10 个查询，包含中文与英文。使用固定 DeepSeek 本地 Answer/judge；选择器对照复用同次公开实验的抽取、计划和 embedding。

| 方法 | 证据完整率 | 禁止返回证据命中率 | 应拒答问题返回空证据 | 本地 Answer/judge 正确率 | 执行错误 |
|---|---:|---:|---:|---:|---:|
| Full | 100% | 0% | 100% | 100% | 0 |
| 独立 Top-K | 100% | 20% | 0% | 100% | 0 |
| Hybrid | 100% | 20% | 50% | 90% | 0 |

“禁止返回证据”包括标注为时间不适用的未来计划，不全部等同于隐私泄漏。证据完整率仅在有正向证据组的查询上计算；拒答与禁止披露独立计分。Top-K 的 Answer 能在这个小样本中忽略部分无关证据，不代表其证据披露通过。

这是已用于调试的开发控制集，非封存测试，不能给出泛化区间或推断竞赛增益。公开配对进程的热缓存时延不代表端到端吞吐或生产 p95。本报告不将开发自测 100% 外推为自然公开基准或 AML Overall。

初次失败的 [原始报告](../experiments/reports/public-controls-initial.json) 保留；其早期 runner 只对完成案例汇总，已明确标记 partial scope。后续 runner 将失败案例保留在分母中。前后数字不能直接用作有效的算法增益估计。

## 本地服务与剩余发布条件

开发工作台地址：[localhost:8013/workbench](http://127.0.0.1:8013/workbench)，只绑定本机，`model_policy=research`。HTTP 就绪、工作台页面和当前住所证据请求已通过。

上游固定 commit/许可及修改记录见 [THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES.md)，候选配置见 [research manifest](../experiments/reports/research-manifest.json)。仍需完成主体 LICENSE/历史来源确认、自然与封存公开评测、正式模型和真实容量、公网 HTTPS 与官方 Smoke，之后才能冻结并运行 Full。完整差距见 [实现状态](IMPLEMENTATION_STATUS.md)。
