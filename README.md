# PaperPilot

个人科研文献知识库：把 PDF 收进自己的库里，用自然语言提问，回答基于你上传的原文，并可对照多篇文献。

面向个人研究者与小团队实验室——账号私有、多库隔离、本地或内网即可跑起来。

---

## 它能做什么

- **多知识库**：按课题、方向建多个库；问答时勾选要参与的库。
- **上传 PDF 入库**：后台自动解析、切分、建索引；扫描件可走 OCR。
- **基于文献回答**：只依据库内检索到的内容作答；证据不足会说明，而不是硬编。
- **多文献对比**：例如「两篇文章有何共同点和不同点」，会综合多篇并尽量用表格对照。
- **连续对话**：会话可保存、切换；回答流式输出，阅读更顺畅。
- **账号隔离**：中心化服务，每个账号只能看到自己的库与文档。

---

## 适合谁

| 你是… | PaperPilot 适合你因为… |
|--------|------------------------|
| 研究生 / 博后 | 论文堆在盘里不好翻，想对着自己的 PDF 提问 |
| 课题组长 | 小团队各自私有库，不必上重型企业知识库 |
| 实验室管理员 | 一台服务器就能服务多人，数据按账号隔离 |

---

## 怎么用（用户视角）

1. 打开站点 → **注册 / 登录**（邮箱验证码）。
2. 左侧 **新建知识库**，点进你要用的库。
3. 右侧 **上传 PDF**，等待状态变为可检索（ready）。
4. 中间勾选要提问的知识库，输入问题，例如：
   - 「这篇摘要里的主要结论是什么？」
   - 「库里两篇文章有何共同点和不同点？」
   - 「当前库有哪些文献？」（会直接列出清单）
5. 阅读回答；对比类问题可能以表格呈现异同。

提示：换了一套更强的解析/检索能力后，旧文档可点「重索引」再问一次，效果更稳。

---

## 界面一览

| 地址 | 作用 |
|------|------|
| `/` | 按登录状态跳转 |
| `/login.html` | 登录 / 注册 |
| `/app.html` | 工作台：左知识库 · 中对话 · 右上传与文档 |

默认本地地址：`http://localhost:8787`

---

## 快速启动（本机）

需要：Python 3.11+、Docker（推荐只用来跑数据库）。

### 1. 配置环境

```powershell
copy .env.example .env
# 编辑 .env：填写 JWT_SECRET、聊天与向量 API Key、SMTP（发注册验证码）等
```

正式使用请保持 `AUTH_EXPOSE_CODE=0`，并配置可用的邮箱 SMTP。

### 2. 启动数据库（推荐）

```powershell
docker compose up -d db
```

在 `.env` 中设置（示例已有）：

```env
DATABASE_URL=postgresql+psycopg2://paperpilot:paperpilot@localhost:5432/paperpilot
```

若 Docker Hub 拉取缓慢，可先用镜像再打标签：

```powershell
docker pull docker.m.daocloud.io/pgvector/pgvector:pg16
docker tag docker.m.daocloud.io/pgvector/pgvector:pg16 pgvector/pgvector:pg16
```

### 3. 启动服务

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONPATH = "."
uvicorn app.main:app --reload --host 0.0.0.0 --port 8787
```

浏览器打开：<http://localhost:8787>

也可用一整套 Compose（API + 数据库）：`docker compose up --build`。

**说明：** 从 SQLite 换到 Postgres 不会自动迁移旧数据，需重新注册并上传文献。

### 可选：纯 SQLite 轻量模式

不设 `DATABASE_URL` 时，默认使用 `data/paperpilot.db`，适合单机试用；正式实验室环境仍建议 Postgres + pgvector。

---

## 你需要准备的密钥

| 配置 | 用途 |
|------|------|
| `JWT_SECRET` | 登录令牌（≥24 位随机串） |
| 聊天 API（如 DeepSeek） | 生成回答 |
| 向量 API（如智谱 embedding） | 文献检索 |
| SMTP | 注册邮箱验证码 |

切勿把 `.env` 提交到 Git。

### 公网开放注册时请额外设置

```env
# 改成你的前端域名（不要用 *）
CORS_ORIGINS=https://your.domain.com

AUTH_EXPOSE_CODE=0
QUOTA_DAILY_QUERIES=80
QUOTA_DAILY_UPLOADS=20
RATE_LIMIT_SEND_CODE_IP_MAX=10
AUTH_CODE_DAILY_MAX_PER_TARGET=8
```

前面建议用 Nginx/Caddy 终结 HTTPS。每用户日配额在 `/api/auth/me` 的 `quota` 字段可见。

---

## 数据与隐私

- 文献与问答数据按 **账号** 隔离，他人看不到你的库。
- PDF 与索引保存在服务端（默认本机 `data/` 与数据库），不是散落在每位用户电脑上。
- 备份（管理员）：

```powershell
.\scripts\backup.ps1
```

会备份数据库转储与 `data/pdfs/`。恢复方式见脚本说明或仓库内备份文档注释。

---

## 项目结构（简图）

```
.
├── app.html / login.html / index.html   # 页面
├── app.js / auth.js / styles.css        # 前端
├── backend/                             # API、解析、检索、问答
├── scripts/                             # 备份等
├── docker-compose.yml                   # Postgres（及可选 API）
├── .env.example                         # 配置模板
└── README.md
```

---

## 常见问题

**上传后一直不能问？**  
看右侧文档状态是否为 ready；失败可点「重索引」，或查看服务日志。

**回答说证据不足？**  
可能勾选的库不对、文档未索引完，或问题超出文献内容——这是预期行为。

**对比两篇却偏到一篇？**  
确认两篇都在同一所选库且均为 ready；必要时对两篇都重索引后再问。

**换电脑 / 重装后数据没了？**  
数据在服务端磁盘与数据库里；请用备份脚本定期备份。

---

PaperPilot：把你自己的 PDF，变成可以对话的私人文献库。
