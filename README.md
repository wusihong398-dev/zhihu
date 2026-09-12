# TOTOD 知乎运营助手

服务器版多知乎账号内容运营系统。正式域名为 `totod.cn`，代码由 Codex 持续维护。

## 已确定的核心要求

- Ubuntu 24.04 + Docker Compose + Nginx + HTTPS。
- 每个知乎账号独立保存 Cookie、浏览器 Profile、关键词、文章、回答、商品资料、任务、配额和日志。
- 百度采集页面底部“相关搜索”，谷歌采集“用户还搜索了”。同层候选词按长度从短到长扩展，数量不足时逐层继续。
- 支持 ChatGPT / OpenAI、DeepSeek、火山方舟；官方平台预置 URL 和模型，用户只填写 API Key。
- 每个账号独立设置每日文章数量和每日回答数量。
- 任务支持开始、暂停、继续、停止、失败重试和服务器重启后续跑。

## 当前版本

`v0.3.0`：增加 AI 平台加密配置与连接测试；增加按账号隔离的百度/谷歌真实网页关键词采集、进度和关键词库。

## 快速启动

```bash
bash scripts/bootstrap_server.sh
```

脚本会在服务器本地创建权限为 `600` 的 `.env`，并生成随机数据库密码和应用密钥；不会覆盖已经存在的 `.env`。

后端只监听服务器回环地址 `127.0.0.1:18080`，公网必须通过 Nginx 访问。

## 主要接口

- `GET /api/health`：数据库、Redis和账号存储检查。
- `GET /api/version`：当前版本。
- `POST /api/auth/login`：管理员登录。
- `GET /api/auth/me`：验证当前管理员身份。
- `POST /api/accounts`：创建知乎账号记录并初始化独立目录。
- `GET /api/accounts`：账号列表。
- `GET /api/accounts/{account_id}`：账号详情。
- `PATCH /api/accounts/{account_id}`：修改备注、配额、时区和启用状态。
- `GET /api/ai/providers`：获取预置 AI 平台与已保存配置（不返回 API Key 明文）。
- `PUT /api/ai/providers/{provider}`：加密保存 API Key 和模型。
- `POST /api/ai/providers/{provider}/test`：实际请求平台测试配置。
- `POST /api/accounts/{account_id}/keyword-jobs`：启动关键词采集任务。
- `GET /api/accounts/{account_id}/keyword-jobs/latest`：查询采集进度。
- `GET /api/accounts/{account_id}/keywords`：读取该账号独立关键词库。

除健康检查、版本和登录外，业务接口均要求管理员 Bearer Token。

首次启动后，在服务器交互式创建管理员：

```bash
docker compose exec backend python -m app.create_admin
```

忘记管理员密码时，可在服务器交互式重置（不会显示输入的密码）：

```bash
docker compose exec backend python -m app.reset_admin_password
```

为 `totod.cn` 和 `www.totod.cn` 配置 Let's Encrypt HTTPS：

```bash
bash scripts/setup_https.sh
```

如需接收证书到期通知，可在执行时提供邮箱：

```bash
CERTBOT_EMAIL=your-email@example.com bash scripts/setup_https.sh
```

后续再次运行部署脚本时，会保留 Certbot 管理的 HTTPS 配置。

## 数据隔离

账号文件目录固定为：

```text
/var/lib/totod/accounts/{account_uuid}/browser-profile/
/var/lib/totod/accounts/{account_uuid}/screenshots/
/var/lib/totod/accounts/{account_uuid}/logs/
/var/lib/totod/accounts/{account_uuid}/exports/
```

数据库中的账号关联数据必须包含 `account_id`。任何跨账号复制必须由用户显式触发，禁止隐式共享。
