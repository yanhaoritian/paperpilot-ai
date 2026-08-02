# 科研 Skill 与 AI 用量成本架构

## 1. 科研 Skill

公共证据政策始终生效；Skill 只能细化任务、检索覆盖和输出结构，不能放宽引用、安全或
拒答规则。当前注册表位于 `backend/app/services/research_skills.py`，首批包含：

- `paper_qa`：论文证据问答；
- `paper_deep_read`：单篇论文精读；
- `multi_paper_synthesis`：多文献对比综述；
- `claim_evidence_audit`：观点证据核查；
- `academic_writing`：证据约束写作。

客户端可以传 `skill_id=auto` 由确定性规则选择，也可以显式指定。每次回答会把
`skill_id`、`skill_version` 和确定性合同检查结果写入消息元数据；缓存键也包含 Skill
及版本，避免不同任务合同复用同一答案。

接口：

```text
GET  /api/research/skills
POST /api/query                         body.skill_id
POST /api/conversations/{id}/messages body.skill_id
```

## 2. Token 与成本账本

`ai_usage_events` 是追加式明细账，记录请求、用户、会话、消息、文档、Skill、操作、
供应商、模型、输入/缓存输入/输出/推理 Token、来源、延迟、状态、价格版本和成本。

覆盖操作包括：

```text
answer_generate          query_rewrite
query_embedding          document_embedding
memory_query_embedding   memory_embedding
memory_summary            rerank
agent_plan                contextual_prefix
vision_page               response_cache_hit
```

供应商返回 `usage` 时使用精确值；流式端点会请求最终 usage chunk。兼容端点不支持时
自动退回原流式协议，并用 CJK/Latin 混合估算器记录，`usage_source=estimated`。估算事件
不会冒充供应商精确账单。本地向量计算与回答缓存分别标记为 `local`、`cache`，不混入
供应商精确事件；失败调用保留观测记录，但不计入金额。

## 3. 价格版本

价格通过 `.env` 的单行 JSON 注入，API 和 Worker 启动时幂等写入
`model_price_versions`：

```dotenv
AI_MODEL_PRICES_JSON=[{"provider":"deepseek","model":"your-model","operation_kind":"chat","version":"2026-08","currency":"CNY","input_per_million":1.0,"cached_input_per_million":0.2,"output_per_million":4.0,"effective_from":"2026-08-01T00:00:00Z"}]
```

这里的金额单位是“每一百万 Token 的普通货币单位”。数据库内部使用微货币整数，避免
浮点误差。每条用量事件保存命中的价格版本，因此未来调价不会改写历史成本。未配置单价
的事件保留 Token 并标记为 `UNPRICED`，成本为零。

成本公式：

```text
(输入 - 缓存输入) × 输入价 / 1,000,000
+ 缓存输入 × 缓存价 / 1,000,000
+ 输出 × 输出价 / 1,000,000
+ 按次价格
```

## 4. 用户用量接口

```text
GET /api/research/usage?days=30
```

接口只汇总当前登录用户，返回总 Token、精确/估算事件数、缓存命中、按币种成本，以及
按操作和 Skill 的拆分。不同币种始终分别汇总，不进行无汇率依据的相加。工作台顶栏展示
近 30 天金额；未配置价格时展示 Token。点击“AI 消耗”可打开明细面板，查看输入、缓存
输入、输出、推理 Token、调用环节、科研模式及待定价事件。

## 5. 上线步骤

生产升级必须先备份 PostgreSQL 与 `data/pdfs`，再运行：

```bash
docker compose --profile tools run --rm migrate
docker compose up -d --build api worker
```

迁移后确认 Alembic head 为 `0009_research_skills_usage`，并验证：

1. `/api/research/skills` 返回 5 个 Skill；
2. 问答后 `/api/research/usage?days=1` 出现 `answer_generate` 等事件；
3. 上传并索引 PDF 后出现 `document_embedding`，启用视觉时出现 `vision_page`；
4. 已配置价格的模型产生对应币种成本，未配置模型显示为 `UNPRICED`。
