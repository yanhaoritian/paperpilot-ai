# PaperPilot 文献阅读助手

面向日常读论文、做对比、写总结的场景：上传 PDF 抽取摘要，用 AI 生成结构化要点，支持多篇横向对比，并可导出 Markdown。

**主要能力**

- PDF 上传与文本抽取（需带可选中文字层的 PDF）
- 后端调用大模型（API Key 仅在后端使用，不进入浏览器）
- 单篇：一句话总结、创新点、风险、行动建议、提纲等
- 多篇：主题、差异、机会与建议
- 阅读目标（是否深读 / 讲解 / 落地）与读者角色会影响输出结构
- 导出 Markdown 报告

## 目录结构

- `index.html` 前端页面
- `styles.css` 样式
- `script.js` 前端交互逻辑
- `server.js` 后端代理与 PDF 抽取
- `.env.example` 环境变量模板
- `tunnel-quick.ps1` 本机 + Cloudflare Quick Tunnel（可选）
- `Dockerfile` 容器运行（可选）

## 1. 环境要求

需要 **Node.js 18+**。

```bash
node -v
npm -v
```

## 2. 安装依赖

```bash
npm install
```

## 3. 配置环境变量

1. 复制 `.env.example` 为 `.env`
2. 在 `.env` 中填入真实配置

示例：

```env
PORT=8787
OPENAI_API_KEY=sk-xxxx
OPENAI_BASE_URL=https://api.openai.com/v1
DEFAULT_MODEL=gpt-4.1-mini
```

说明：

- `OPENAI_API_KEY` 仅在服务器进程内使用，不要提交到 Git。
- 使用兼容 OpenAI Chat Completions 的服务时，可修改 `OPENAI_BASE_URL`。

## 4. 本地启动

```bash
npm start
```

浏览器访问：`http://localhost:8787`  
健康检查：`http://localhost:8787/api/health`

## 公网访问（简要）

若把程序部署在自己的 **VPS / 内网服务器**，请将 `.env` 中密钥只留在服务器上，勿提交到 Git；置于 Nginx 等反向代理之后时建议设置 `NODE_ENV=production`、`TRUST_PROXY=1`。前端「后端地址」与网站同源时请**留空**。

### 本机 + Cloudflare Tunnel（当前常用）

适合临时把 HTTPS 地址发给别人试用：本机需能访问配置的模型 API，电脑需保持在线。

1. 安装：`winget install Cloudflare.cloudflared`（装完新开终端）
2. 终端 A：配置 `.env` 后执行 `npm start`
3. 终端 B：在项目根目录执行  
   `powershell -NoProfile -ExecutionPolicy Bypass -File .\tunnel-quick.ps1`  
4. 使用终端输出的 `https://……trycloudflare.com`；页面上「后端地址」留空。

Quick Tunnel 域名可能每次变化；固定域名需按 [Cloudflare Tunnel 文档](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/) 配置命名隧道。

## 5. 使用流程

1. 输入标题与摘要，或上传 PDF 并点击抽取
2. 选择读者视角与**阅读目标**
3. 点击「使用 AI 生成结果」
4. 多篇对比：加入列表后点「生成对比」
5. 需要可「导出 Markdown」

## 后端 API

- `POST /api/extract-pdf`：`multipart/form-data`，字段名 `pdf`
- `POST /api/summarize`：JSON，示例：
  ```json
  {
    "paperTitle": "Attention Is All You Need",
    "paperAbstract": "...",
    "personaKey": "researcher",
    "personaLabel": "算法 / 研究",
    "goal": "decision",
    "goalLabel": "快速判断这篇论文值不值得深入读",
    "model": "gpt-4.1-mini",
    "temperature": 0.3
  }
  ```
- `POST /api/compare`：JSON，`papers` 至少两篇，可带 `goal` / `goalLabel` 与单篇一致

## 后续可扩展方向

1. 论文来源（如 arXiv 链接）一键拉取
2. 本地历史记录或小知识库
3. 对比结果的评分与实验笔记
4. 账号与协作（若有多用户需求）
