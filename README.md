# PaperPilot AI 文献总结助手

这是一个面向 AI 产品经理岗位的作品型项目，当前版本已从静态 Demo 升级为完整工作流：

- PDF 上传与文本抽取
- 后端代理调用模型（前端不暴露 Key）
- 单篇论文结构化总结
- 多篇论文对比分析
- 导出 Markdown 报告

## 目录结构

- `index.html` 前端页面
- `styles.css` 样式
- `script.js` 前端交互逻辑
- `server.js` 后端代理与 PDF 抽取服务
- `.env.example` 环境变量模板
- `render.yaml` [Render](https://render.com) Blueprint（可选一键部署）

## 1. 启动前准备

你需要安装 Node.js 18+。

检查：

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
2. 在 `.env` 填入你的真实配置

示例：

```env
PORT=8787
OPENAI_API_KEY=sk-xxxx
OPENAI_BASE_URL=https://api.openai.com/v1
DEFAULT_MODEL=gpt-4.1-mini
```

说明：

- `OPENAI_API_KEY` 只在后端使用，不会暴露到浏览器。
- 如果你使用兼容 OpenAI 协议的平台，可替换 `OPENAI_BASE_URL`。

## 4. 启动项目

```bash
npm start
```

启动后打开：

- `http://localhost:8787`

健康检查：

- `http://localhost:8787/api/health`

## 公网部署（简要）

1. **环境变量**（在云平台「Environment」里配置，勿提交 `.env` 到仓库）  
   - 必填：`OPENAI_API_KEY`  
   - 建议：`NODE_ENV=production`、`TRUST_PROXY=1`（前面有反向代理或负载均衡时必开，否则限流 IP 不准）  
   - `PORT` 多数平台会自动注入，无需手写。

2. **启动命令**  
   - 直接 Node：`npm start`（入口为 `server.js`，默认监听 `0.0.0.0` + `PORT`）。

3. **Docker**（可选）  
   - 仓库根目录已提供 `Dockerfile`，构建后运行镜像即可；仍需在同一环境注入上述变量。

4. **前端「后端地址」**  
   - 页面里该项**留空**时，浏览器会请求**当前域名**（与 API 同源），适合公网；本地调试可填 `http://localhost:8787`。

5. **HTTPS**  
   - 正式对外请使用平台提供的 TLS，或在前面加 Caddy / Nginx 终止 HTTPS。

其他方式：VPS + PM2、Railway、Fly.io 等，思路相同（环境变量 + `npm start` 或 Docker）。

### 使用 Render（推荐你当前方案）

仓库根目录已有 **`render.yaml`**（Blueprint）：会为 Node Web Service 设置 `npm ci` / `npm start`、健康检查 `/api/health`、`NODE_ENV=production`、`TRUST_PROXY=1`，并在首次部署时提示你填写 **`OPENAI_API_KEY`**（不会写进仓库）。

1. 把本仓库推送到 **GitHub** 或 **GitLab**（需包含 **`package-lock.json`**，否则把 `render.yaml` 里的 `buildCommand` 改成 `npm install`）。
2. 打开 [Render Dashboard](https://dashboard.render.com/)，登录后点 **New → Blueprint**。
3. 连接你的仓库，选中包含 `render.yaml` 的分支，点应用；在向导里填入 **`OPENAI_API_KEY`**。
4. 等待首次构建与部署完成，打开 Render 给的 **`https://xxxxx.onrender.com`** 即访问本站。
5. 页面上 **「后端地址」保持留空**（已默认同源）。

说明：免费实例在无流量一段时间后会 **休眠**，首次访问可能多等几秒；`PORT` 由 Render 自动注入，无需在面板里再加。若改用第三方 API 基址，可在 Environment 里改 `OPENAI_BASE_URL` / `DEFAULT_MODEL`。

## 5. 使用流程

1. 在页面输入论文标题，或直接上传 PDF
2. 点击“从 PDF 抽取文本”，自动回填摘要区
3. 选择角色视角与阅读目标
4. 点击“使用 AI 生成结果”
5. 如需多篇对比，点击“加入对比列表”，加入至少 2 篇后点击“生成对比”
6. 点击“导出 Markdown”生成可复用报告

## 后端 API

- `POST /api/extract-pdf`  
  `multipart/form-data`，字段名 `pdf`，返回抽取文本与统计信息

- `POST /api/summarize`  
  请求体示例：
  ```json
  {
    "paperTitle": "Attention Is All You Need",
    "paperAbstract": "....",
    "personaKey": "pm",
    "personaLabel": "AI 产品经理",
    "goal": "decision",
    "goalLabel": "快速判断这篇论文值不值得深入读",
    "model": "gpt-4.1-mini",
    "temperature": 0.3
  }
  ```

- `POST /api/compare`  
  请求体示例：
  ```json
  {
    "papers": [
      { "title": "Paper A", "abstract": "..." },
      { "title": "Paper B", "abstract": "..." }
    ],
    "personaKey": "pm",
    "personaLabel": "AI 产品经理",
    "model": "gpt-4.1-mini",
    "temperature": 0.3
  }
  ```

## 已实现的岗位信号

- AI 理解：能从论文抽取到结构化分析
- 产品思维：支持 persona、目标导向输出、对比决策
- 动手能力：有可运行 Demo + 后端代理 + 可导出结果

## 下一步可迭代

1. 增加论文来源接入（arXiv URL 直接解析）
2. 增加历史记录与研究知识库
3. 增加对比结果评分与实验追踪
4. 增加登录和团队协作能力
