# PaperPilot 优化落地计划

> 更新日期：2026-07-31

## 目标

把当前可用的公开测试版推进到可稳定扩容的生产版本，优先保证：

1. 每个回答都能回到真实原文与页码；
2. 索引失败可恢复，重试不会重复执行；
3. 文档变化后不会继续返回旧缓存；
4. 数据库升级可审计、可回滚；
5. 高并发下配额、队列和租户边界仍然正确。

建议验收指标：

- 引用可打开率 ≥ 99%；
- ready 文档索引覆盖率可见，静默截断为 0；
- 可重试索引失败自动恢复率 ≥ 95%；
- 检索 Recall@6 ≥ 0.85，并同时记录引用准确率；
- API 5xx < 0.5%，索引队列积压和模型成本可观测。

## P0：上线正确性、数据安全与租户边界

本轮已落地：

- 修复 pgvector 距离为 `0.0` 时的精确匹配得分错误；
- 修复标题阈值不可达及 `section` / `section_heading` 不一致；
- 引用卡片、页码和摘录在前端展示，并可鉴权打开原 PDF；
- 引用页码强制使用数据库 provenance，不接受模型自报页码；
- 上传、删除、重索引和索引状态变化时按用户失效回答缓存；
- 索引在当前 Worker 内退避重试，并去重同一文档的进程内调度；
- 长文档空页、OCR 上限和 chunk 上限写入 `status_detail`；
- 验证码改用密码学安全随机数；
- 每日配额改成数据库原子更新；
- 服务端支持模型 allowlist；
- 拒绝伪装成 `.pdf` 的非 PDF 内容；
- 增加 CSP、点击劫持和 MIME 嗅探等安全响应头；
- Compose 默认只绑定回环地址，强制配置 PostgreSQL 密码，并增加重启与 API healthcheck；
- PDF 上传改为分块流式写入临时文件，边写边校验大小和 SHA-256，再原子移动到正式路径；
- 文档记录与索引任务在同一数据库事务创建，失败时回滚记录并清理临时/正式文件；
- 删除文档或知识库时在受控存储根目录内同步删除 PDF，并清理空目录；
- 存储审计同时检查缺失、非法文件头、哈希不一致和无数据库记录的孤儿 PDF；
- 仅在请求来自 `TRUSTED_PROXY_CIDRS` 时接受代理 IP 头，避免伪造
  `X-Forwarded-For` 绕过限流；
- 移除 SlowAPI 对工作目录 `.env` 的隐式读取，避免 Windows 非 UTF-8 环境启动失败。

## P1：可靠性、可观测性与可恢复部署

当前代码已落地幂等 baseline migration、迁移脚本、Compose `migrate` 工具服务，以及
使用 PostgreSQL `FOR UPDATE SKIP LOCKED` 领取任务的独立 Worker。任务使用可续期租约
区分“仍在执行”和“Worker 已崩溃”，API health 同时暴露 Worker 存活及队列状态。
真实项目数据库已完成持久化数据库/PDF 一致性备份、从备份隔离恢复、schema diff、
向量完整性验证和正式迁移；当前 API、Worker 与数据库均已按新 Compose 形态运行。

### 2.1 Alembic 正式迁移

1. 已从当前 SQLAlchemy metadata 建立 `0002_current_schema` 幂等 baseline；
2. 已增加 `0003_worker_heartbeat`、`0004_index_job_leases`、
   `0005_schema_convergence`、`0006_portable_document_paths` 和
   `0007_postgres_trigram_search`、`0008_conversation_memory`、
   `0009_research_skills_usage` 增量迁移；
3. 已验证空 SQLite、旧版 SQLite、空 PostgreSQL、合成旧版 PostgreSQL，以及真实项目
   数据库克隆升级；
4. 迁移后的 PostgreSQL 运行 `alembic check` 无 schema 漂移，JSONB、索引、非空约束
   和历史唯一约束已收敛；
5. CI 增加真实 pgvector 服务、Alembic schema check 和 PostgreSQL Worker 并发门禁；
6. 待将 `create_all` 和手写 `_migrate_*` 降级为一次性兼容层，稳定两个版本后删除；
7. 已完成 PostgreSQL + PDF 一致性备份及从该备份进行的隔离恢复演练。

### 2.2 独立索引 Worker

当前已完成：

- PostgreSQL `FOR UPDATE SKIP LOCKED` 多 Worker 互斥领取；
- 指数退避、最大尝试次数、永久失败分类；
- 崩溃后的 processing/failed 任务重新入队；
- API 只入队，独立 Worker 执行 OCR、Embedding 与 Vision；
- Compose 部署形态调整为 `api + worker + db`；
- `lease_owner` / `lease_expires_at` 周期续期，避免长任务被误回收；
- Worker heartbeat、离线判断，以及 pending/running/failed 队列计数。

`/api/health` 已补齐数据库/Worker 状态、心跳延迟、持久化任务队列
pending/running/failed、最老等待时间，以及最近一小时成功/失败任务数。后续再接入
Prometheus/OpenTelemetry，形成趋势、告警和分位延迟指标。

### 2.3 容器运行时

- 当前源码镜像已完成 API + Worker 联合启动烟测；
- 验证了 Worker 正常退出后 health 降级，以及重启后的自动恢复；
- 修复 RapidOCR/OpenCV 在 slim 镜像中缺少 `libxcb`、`libGL` 和 GLib 运行库的问题；
- 移除不必要的编译工具，并使用 BuildKit pip 缓存，测试镜像由约 975 MB 降至约 862 MB；
- PDF 在数据库中改存相对路径，并兼容解析旧 Windows/Linux 绝对路径，避免跨主机或容器后
  原文打开与重索引失效；
- CI 会实际构建生产镜像并导入 `cv2` / `RapidOCR`，防止 OCR 运行时依赖再次缺失。

### 2.4 备份与测试隔离

- Linux 与 Windows 备份脚本都会生成 PostgreSQL custom-format dump，并先验证归档目录；
- 备份同时写入数据库 SHA-256、逐 PDF SHA-256、表计数和元数据；
- SQLite 存在时使用一致性 `.backup`，不再直接复制活动数据库文件；
- 测试环境变量在 pytest 收集前统一固定为隔离 SQLite，防止导入顺序使测试误连生产库；
- 已用测试前后 PostgreSQL 行数对比验证完整回归不会写入正式本地数据库。

## P2：RAG 质量、缓存一致性与规模化

本轮已完成：

- 将静态 Agent 计划改成“检索—观察—再调用工具”的迭代循环；
- 所有 PDF 工具强制校验 `owner_id + selected library_ids`，并拒绝模型虚构的文档、页码和 chunk；
- 多文献对比设置最大文档数，文献清单 Prompt 设置独立上限；
- 显式把检索内容、PDF 内容和工具结果标为不可信数据，降低文献内 Prompt Injection 风险；
- 缓存键加入文档语料 revision、Prompt version 和实际默认模型，文档状态变化后不命中旧答案；
- 为检索证据、工具证据、历史消息、文献清单和 Context 卡片建立统一字符预算；
- 历史消息优先保留最近轮次，检索证据按文档得分分配预算且保留多文档覆盖；
- PostgreSQL 启用 `pg_trgm`，以 GiST KNN 索引先缩小稀疏检索候选，再用原有中文
  bigram BM25 重排，避免每次扫描租户范围内全部 chunk；
- 修复单文档覆盖兜底查询的结果读取错误，并增加回归测试；
- 参考 LangGraph、Anthropic Context Engineering、MemGPT 与 Generative Agents，
  建立“最近原文轮次 + 滚动摘要 + 可检索旧情景 + 当前 PDF 证据”的分层记忆；
- 含“它/这篇/两者”的追问先改写为独立检索问题，并在服务端校验原问题谓词与对象未丢失；
- 模型返回非 JSON 或改变问题语义时，自动降级为确定性“前文主题 + 原问题”；
- 较早消息在流式回答后分批压缩，摘要更新使用 revision 防止并发覆盖；
- `conversation_memories` 按用户和会话隔离，使用 pgvector HNSW、pg_trgm 与文本降级召回；
- 消息增加会话内单调 `sequence`，消除同一事务时间戳相同时的历史顺序不确定性；
- 用户可按会话关闭并清除派生记忆，原始消息不受影响；删除会话时全部级联清除；
- 长会话 API 默认返回最近 200 条，避免一次加载无限增长的完整消息历史。

后续工作：

- 为超过上限的多文献问题增加逐篇摘要 + Map-Reduce，而不只是拒绝；
- 引入中文、扫描件、双栏、表格、长论文和多文献冲突评测集；
- 分别评测检索召回、回答忠实度、引用准确率、拒答率与延迟/成本；
- 为超大租户评估 PostgreSQL FTS/PGroonga 或独立搜索服务，并通过压测决定切换阈值；
- 接入对象存储（OSS/S3）与恶意文件扫描，为多实例部署消除本地共享卷依赖；
- 在用户明确授权后，再评估跨会话偏好记忆；默认继续保持会话级命名空间，防止课题串扰。

## 本轮验收

- 默认回归：75 passed，3 skipped；隔离 PostgreSQL Worker 门禁：2 passed；
- Alembic 空库、旧库和真实数据克隆升级均通过，当前 head 为
  `0008_conversation_memory`；
- 真实数据克隆升级前后保持 1 个用户、2 篇文档、593 个 blocks、560 个 chunks 和
  12 条消息；560 个 1024 维向量均保留，精确近邻距离为 0；
- 8 路 PostgreSQL 并发领取无重复；活跃租约不被抢占，过期租约正确重新入队；
- API + Worker 容器、Worker 离线/恢复、三次失败重试归档、OCR 导入均通过；
- 真实数据克隆中的 2 个 Windows 绝对 PDF 路径已转为可移植相对路径，并在 Linux
  容器挂载下验证 2/2 文件存在且具有有效 `%PDF-` 文件头；
- 使用只读存储审计脚本执行逐文件 SHA-256 和反向孤儿扫描，2/2 有效文档均通过，
  无缺失文件、非法 PDF、哈希不一致或孤儿文件；
- 4 份历史无数据库归属的 PDF 已在记录 SHA-256 后移入可恢复隔离备份，未直接删除；
- PostgreSQL `pg_trgm` 1.6、`ix_chunks_text_trgm` 与
  `ix_chunks_embedding_hnsw` 均已验证存在，中文关键词门禁能命中预期 chunk；
- 真实备份克隆完成 `0007 → 0008`：20/20 消息顺序唯一，情景记忆实际生成 1024 维向量，
  查询计划使用 `ix_conversation_memories_embedding_hnsw`，Alembic 无 schema 漂移；
- 60 条合成长会话压缩为滚动摘要、28 条情景记忆和最近 4 条原文消息，消息范围无缺口；
- 真实模型独立问题改写评测 5/5 通过；一次非 JSON 输出由确定性路径正确降级；
- `node --check app.js`、`python -m compileall`、`pip check`、
  `docker compose config --quiet` 和 `git diff --check` 均通过；
- 实际 `newproject3-db-1` 已在持久化备份和隔离恢复验证后迁移至
  `0008_conversation_memory`，API 与 Worker 健康运行；
- 数据库与 API 宿主机端口已分别收敛为 `127.0.0.1:5432` 和
  `127.0.0.1:8787`，Worker 不发布宿主机端口。

本节描述的是当前工作区和本地 Docker 正式切换的验收结果；只有在阿里云生产机完成
备份、迁移、镜像重建、健康检查和灰度验证后，才代表 `paperpilot.xin` 已切换到本版本。

## 发布顺序

1. 运行单元测试、检索回归、JavaScript 语法检查和 Compose 配置检查；
2. 使用备份脚本同时备份生产 PostgreSQL 与 PDF，并验证 dump 和 SHA-256 清单；
3. 在生产数据副本升级到 `0009`，验证 schema、用量账本、消息序号、文献/记忆向量维度、稀疏索引和引用打开；
4. 在生产机执行迁移并重建 `api + worker`，先通过 `/api/health` 和存储全量审计；
5. 灰度开放前端，执行注册、登录、上传、索引、问答、引用打开和删除的冒烟测试；
6. 观察 24 小时错误率、Worker 心跳、队列积压、索引失败、缓存命中和模型成本；
7. 指标稳定后再扩大注册量，并保留可快速回滚的数据库/PDF 成套备份。
