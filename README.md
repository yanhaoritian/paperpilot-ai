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

## 每晚自动评估（GitHub Actions）

仓库已内置工作流：`.github/workflows/nightly-eval.yml`

- 触发方式：
  - 每天北京时间约 00:00 自动触发（UTC 16:00）
  - 支持在 GitHub Actions 页面手动点击 `Run workflow`
- 执行内容：`npm run eval`
- 结果查看：
  - Action 的 `Summary` 会直接显示“今天比昨天好没好”
  - 同时上传 `eval-output.txt` 作为 artifact

> 注意：`eval` 依赖 `data/feedback.jsonl`。如果你的反馈数据在线上服务器而不在仓库，GitHub 托管 runner 默认读不到；此时建议使用 **self-hosted runner**，或把反馈文件按日同步到仓库/对象存储后再评估。

## 3. 配置环境变量

1. 复制 `.env.example` 为 `.env`
2. 在 `.env` 中填入真实配置

示例：

```env
PORT=8787
OPENAI_API_KEY=sk-xxxx
OPENAI_BASE_URL=https://api.openai.com/v1
DEFAULT_MODEL=gpt-4.1-mini
MODEL_OPTIONS=gpt-4.1-mini,gpt-4.1,gpt-4o-mini,gpt-4o
```

说明：

- `OPENAI_API_KEY` 仅在服务器进程内使用，不要提交到 Git。
- 使用兼容 OpenAI Chat Completions 的服务时，可修改 `OPENAI_BASE_URL`。
- `MODEL_OPTIONS` 用逗号分隔可选模型，前端“生成模型”下拉框会自动读取；后续加模型直接改 `.env` 即可。
- `ANALYSIS_TEXT_MAX_CHARS` 控制送给模型的分析文本长度上限（默认 22000）；过长会自动保留前后关键段并省略中间，以提升速度。
- `RESPONSE_CACHE_TTL_MS` 为相同请求结果缓存时长（默认 10 分钟）；同一输入重复生成会明显更快。

## 4. 本地启动

```bash
npm start
```

浏览器访问：`http://localhost:8787`  
健康检查：`http://localhost:8787/api/health`

## 公网访问（简要）

若把程序部署在自己的 **VPS**，请将 `.env` 中密钥只留在服务器上，勿提交到 Git；置于 Nginx 等反向代理之后时务必设置 **`TRUST_PROXY=1`**（否则限流会按代理 IP 统计）。前端「网页地址」与站点同源时请**留空**。

### VPS + 公网 IP（固定访问：域名 + HTTPS）

适合长期对外提供 **固定域名**（如 `https://paper.example.com`）。下面以常见 **Ubuntu 22.04/24.04 LTS**、**Nginx** 反代、**Let’s Encrypt** 证书为例。

**1. 准备**

- 一台 VPS，记下 **公网 IP**。
- 一个域名：在域名 DNS 里添加 **A 记录**，主机名如 `paper` 或 `@`，指向该 **公网 IP**（生效需几分钟到几小时）。
- 若服务器在**中国大陆**且对公众提供网站服务，需按当地规定完成**备案**等手续（云厂商控制台一般有说明）。

**2. 登录服务器，开放端口**

```bash
sudo apt update && sudo apt install -y nginx git ufw
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

**3. 安装 Node.js 20 LTS**（任选一种官方推荐方式，以下为 NodeSource 示例）

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
node -v
```

**4. 拉代码与依赖**

```bash
sudo mkdir -p /var/www && sudo chown $USER:$USER /var/www
cd /var/www
git clone https://github.com/<你的用户名>/paperpilot-ai.git paperpilot
cd paperpilot
npm ci --omit=dev
cp .env.example .env
nano .env   # 填入 OPENAI_API_KEY 等；务必增加下面两行
```

在 `.env` 中建议至少包含（端口与反代一致即可）：

```env
NODE_ENV=production
HOST=0.0.0.0
PORT=8787
TRUST_PROXY=1
OPENAI_API_KEY=sk-xxxx
```

**5. systemd 常驻进程**（路径按你实际目录修改）

创建 `/etc/systemd/system/paperpilot.service`（需 `sudo`）：

```ini
[Unit]
Description=PaperPilot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/var/www/paperpilot
EnvironmentFile=/var/www/paperpilot/.env
ExecStart=/usr/bin/node /var/www/paperpilot/server.js
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable paperpilot
sudo systemctl start paperpilot
sudo systemctl status paperpilot
```

**6. Nginx 反代 + 证书**

站点配置示例 `/etc/nginx/sites-available/paperpilot`（把 `paper.example.com` 换成你的域名）：

```nginx
server {
    listen 80;
    server_name paper.example.com;

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    client_max_body_size 40m;
}
```

```bash
sudo ln -sf /etc/nginx/sites-available/paperpilot /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d paper.example.com
```

完成后用浏览器访问 **https://paper.example.com**。更新代码后：`cd /var/www/paperpilot && git pull && npm ci --omit=dev && sudo systemctl restart paperpilot`。

**仅用 IP、不配域名（不推荐生产）**：可临时 `http://<公网IP>:8787` 访问（需在云安全组/防火墙放行 `8787`），无 HTTPS，且本应用默认 PDF 等限制仍生效；正式使用仍建议域名 + 443。

仓库内 **`Dockerfile`** 也可在 VPS 上配合 Docker 运行，思路仍是：容器映射端口 → Nginx 反代 → HTTPS。

### 本机 + Cloudflare Tunnel（备选，无需 VPS）

适合临时把 HTTPS 地址发给别人试用：本机需能访问配置的模型 API，电脑需保持在线。

1. 安装：`winget install Cloudflare.cloudflared`（装完新开终端）
2. 终端 A：配置 `.env` 后执行 `npm start`
3. 终端 B：在项目根目录执行  
   `powershell -NoProfile -ExecutionPolicy Bypass -File .\tunnel-quick.ps1`  
4. 使用终端输出的 `https://……trycloudflare.com`；页面上「后端地址」留空。

Quick Tunnel 域名可能每次变化。若要**固定网址**（例如 `https://paper.你的域名.com`），需改用 **命名隧道**，并准备一个**已接入 Cloudflare** 的域名（把域名的 DNS 交给 Cloudflare 即可，控制台可免费使用）。

**一次性配置（完成后日常只需跑 `cloudflared tunnel run …`，地址不变）**

1. 在 [Cloudflare Dashboard](https://dash.cloudflare.com) 添加站点并完成 DNS 托管（按向导改域名注册商处的 NS）。
2. 本机执行 `cloudflared tunnel login`，浏览器授权。
3. 创建隧道：`cloudflared tunnel create paperpilot`（名称可自定），记下输出的 **Tunnel ID**。
4. 在用户目录创建配置，例如 Windows：`%USERPROFILE%\.cloudflared\config.yml`，内容示例（把 UUID、域名改成你的）：
   ```yaml
   tunnel: <你的 Tunnel UUID>
   credentials-file: C:\Users\<用户名>\.cloudflared\<UUID>.json

   ingress:
     - hostname: paper.example.com
       service: http://127.0.0.1:8787
     - service: http_status:404
   ```
5. 把子域指到隧道（示例）：  
   `cloudflared tunnel route dns paperpilot paper.example.com`  
   （`paperpilot` 为第 3 步隧道名，`paper.example.com` 换成你的子域。）
6. 以后每次开机：终端 A 运行 `npm start`，终端 B 运行  
   `cloudflared tunnel run paperpilot`  
   浏览器始终用 **https://paper.example.com**（与第 4 步 `hostname` 一致）。

说明：电脑关机或关掉 `cloudflared` 后外网仍打不开，但**链接本身固定**，不必再换随机 trycloudflare 地址。更细的说明见官方文档：[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/)。

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
