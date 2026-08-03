# PaperPilot

把你自己的 PDF 收进私人文献库，用自然语言提问；回答依据你上传的原文，也可对照多篇文献。

**在线使用：** [https://paperpilot.xin](https://paperpilot.xin)

面向研究生、博后与小团队实验室——账号私有、多库隔离，打开浏览器即可用。

---

## 它能做什么

- **多知识库**：按课题、方向建多个库；提问前勾选要参与的库。
- **上传 PDF**：后台自动解析、切分并建索引；扫描件可走 OCR。
- **基于文献回答**：只依据库内检索到的内容作答；证据不足会说明，而不是硬编。
- **多文献对比**：例如「两篇文章有何共同点和不同点」，会综合多篇并尽量用表格对照。
- **连续对话与长记忆**：会话可保存、切换；最近轮次、滚动摘要和相关旧记忆分层参与追问。
- **科研 Skill**：可自动或手动选择论文问答、单篇精读、多文献综述、观点核查和证据约束写作。
- **Token 与成本账本**：聊天、向量化、精排、Agent、记忆和视觉调用均按用户与 Skill 归集；点击工作台“AI 消耗”可查看近 30 天明细与成本。
- **账号隔离**：每个账号只能看到自己的库与文档。

---

## 适合谁

| 你是… | 可以用它来… |
|--------|-------------|
| 研究生 / 博后 | 对着自己堆着的 PDF 提问、做对比、写笔记前先摸清结论 |
| 课题组成员 | 各自建私有库，互不干扰 |
| 小团队协作者 | 共用同一站点，数据仍按账号隔离 |

---

## 快速开始

1. 打开 [https://paperpilot.xin](https://paperpilot.xin)
2. **注册**：填写用户名、邮箱与密码，获取邮箱验证码后完成注册
3. **登录** 后进入工作台

工作台布局：

| 区域 | 作用 |
|------|------|
| 左侧 | 知识库、库内论文、上传与本轮检索范围 |
| 中间 | 对话：选择科研模式和知识库后提问 |

---

## 怎么用

### 1. 建库并上传

1. 左侧输入名称 → **新建知识库**，并点选进入该库  
2. 左侧「库内论文」区域选择 PDF → **添加并索引**
3. 等待文档状态变为可检索（ready）后再提问  

同一库可上传多篇；提问时在左侧勾选一个或多个知识库。

### 2. 提问示例

- 「这篇工作的主要贡献是什么？」
- 「方法部分用了什么数据集 / 实验设置？」
- 「库里两篇文章有何共同点和不同点？」
- 「当前库有哪些文献？」（会列出清单）

对比类问题尽量写清「两篇 / 多篇」，并确保相关 PDF 都已 ready。

### 3. 会话

可新建或切换会话，便于按课题分开追问。回答不足时可换种问法，或确认勾选的库是否正确。

---

## 使用提示

- **先等索引完成**：上传后若仍显示处理中，稍候再问；失败可点「重索引」。
- **勾选要对的库**：答非所问时，优先检查左侧勾选是否漏选或多选。
- **证据不足是正常的**：问题超出文献内容、或检索未命中时，系统会如实说明。
- **日使用额度**：站点对每日提问次数与上传次数有上限，以账号内提示为准；用完后次日恢复。
- **文件要求**：请上传带文字层的 PDF；纯扫描件依赖 OCR，耗时更长，效果因扫描质量而异。
- **单文件大小**：请控制在站点允许的上传上限内（过大可能失败）。

---

## 隐私说明

- 文献、对话与知识库按 **账号** 隔离，其他用户看不到你的内容。
- PDF 与索引保存在服务端，便于你在任意设备登录后继续使用。
- 请勿上传含敏感个人信息或未获授权分享的资料。
- 请妥善保管账号密码；不要与他人共用登录。

---

## 常见问题

**收不到注册验证码？**  
检查垃圾邮件箱；确认邮箱填写正确；稍后再试。若持续失败，可联系站点维护者。

**上传后一直不能问？**  
看左侧库内论文的文档状态是否为 ready；失败请点「重索引」后重试。

**回答说证据不足？**  
可能勾选的库不对、文档未索引完，或问题超出文献内容——这是预期行为。

**对比两篇却偏到一篇？**  
确认两篇都在所选库中且均为 ready；必要时对两篇都重索引后再问。

**换电脑后数据还在吗？**  
在。数据在服务端，用同一账号登录即可。

**忘记密码？**  
当前请使用注册邮箱联系维护者协助处理（以站点公告为准）。

---

## 自行部署（可选）

若你希望在自己的服务器上运行一份 PaperPilot，请复制 `.env.example` 为 `.env`，填写聊天 / 向量 API、JWT、SMTP 等，然后：

```bash
docker compose up --build -d
```

详细变量说明见 `.env.example`。如需金额统计，请配置 `AI_MODEL_PRICES_JSON`；未配置价格时
系统仍记录 Token，但将事件标为 `UNPRICED`，不会伪造成本。`POSTGRES_PASSWORD` 必须填写；API 与数据库默认只绑定
`127.0.0.1`，如确需对其他主机开放，请显式设置 `API_BIND_HOST` / `DB_BIND_HOST` 并配合防火墙。
公网部署时请设置 `CORS_ORIGINS` 为你的域名，并保持 `AUTH_EXPOSE_CODE=0`。

问答接口使用 SSE 流式返回。Nginx 反代至少应为 `/api/` 关闭响应缓冲，并把相邻两次
上游读取之间的超时提高到 300 秒，避免模型生成期间被默认的 60 秒超时截断：

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8787;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
}
```

DeepSeek V4 默认开启高强度思考；网站默认通过 `DEEPSEEK_THINKING_ENABLED=0` 使用低延迟
非思考模式。只有在确实需要长推理、且反代超时已经正确配置时才建议将其设为 `1`。

生产升级前先备份数据库与 PDF，再显式执行迁移：

```bash
./scripts/backup.sh
docker compose --profile tools run --rm migrate
docker compose up --build -d
```

Windows 本地环境可使用 `.\scripts\backup.ps1`。备份同时包含 PostgreSQL custom-format
dump、数据库表计数、PDF 副本及 SHA-256 清单；只有在备份归档可读取、文件清单可校验后，
才应继续生产迁移。

Compose 默认启用独立 `worker` 服务；API 只写入持久化索引队列，OCR、Embedding 与
Vision 不再占用 Web 请求进程。单进程本地调试可设置 `INDEX_EXTERNAL_WORKER=0`。
Worker 会持续写入存活心跳，并为正在处理的任务续租；`/api/health` 可查看 Worker
是否在线、心跳延迟、pending/running/failed 队列数、最老等待时间及最近一小时成功/
失败任务数，也会显示会话记忆数量、待压缩会话数和最近更新时间。租约时长可通过
`INDEX_WORKER_LEASE_SECONDS` 调整，建议至少为心跳间隔的三倍。
生产镜像已包含 RapidOCR/OpenCV 所需的最小 Linux 运行库；CI 会同时验证 PostgreSQL
迁移零漂移、多 Worker 互斥领取以及 OCR 模块可导入。
PDF 路径以 `用户/知识库/文档.pdf` 的相对形式保存；迁移会自动兼容旧版 Windows 或
Linux 绝对路径，因此同一数据库可在本地与容器部署之间迁移。

正式迁移或备份后可执行只读一致性审计：

```bash
docker compose run --rm --no-deps api \
  python /app/backend/scripts/audit_storage.py --verify-hash --check-orphans
```

命令会检查数据库中的每篇文档是否有对应 PDF、文件头是否有效，以及 SHA-256 是否与
上传时记录一致，同时检查磁盘上是否存在没有数据库记录的孤儿 PDF；任一检查失败都会
以非零状态退出。

用户、知识库、文档元数据、会话、原始消息、派生会话记忆、配额、索引任务、文本块和向量都记录在
PostgreSQL；PDF 原文件保存在 `data/pdfs/<用户ID>/<知识库ID>/<文档ID>.pdf`，数据库
只保存可移植相对路径、文件哈希和解析状态。删除文档或知识库时会同步删除对应 PDF；
数据库与 `data/pdfs` 必须作为一个恢复单元一起备份。

### 开发与验证

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\pip install -r requirements.txt
# Linux/macOS: .venv/bin/pip install -r requirements.txt
python -m pytest -q
```

完整的分阶段工程化计划见 [`docs/OPTIMIZATION_PLAN.md`](docs/OPTIMIZATION_PLAN.md)。
长上下文、滚动摘要、记忆召回与隐私边界见
[`docs/MEMORY_ARCHITECTURE.md`](docs/MEMORY_ARCHITECTURE.md)。
科研 Skill、Token 归集、价格版本和用量接口见
[`docs/RESEARCH_SKILLS_AND_USAGE.md`](docs/RESEARCH_SKILLS_AND_USAGE.md)。

---

PaperPilot：把你自己的 PDF，变成可以对话的私人文献库。  
线上地址：[https://paperpilot.xin](https://paperpilot.xin)
