# TOTOD 知乎运营助手

服务器版多知乎账号内容运营系统。正式域名为 `totod.cn`，代码由 Codex 持续维护。

## 已确定的核心要求

- Ubuntu 24.04 + Docker Compose + Nginx + HTTPS。
- 支持系统管理员创建多个授权用户、设置到期时间和停用状态；每个用户的数据空间相互隔离。
- 每个知乎账号独立保存 Cookie、浏览器 Profile、关键词、文章、回答、商品资料、任务、配额和日志。
- 百度采集页面底部“相关搜索”，谷歌采集“用户还搜索了”。同层候选词按长度从短到长扩展，数量不足时逐层继续。
- 支持 ChatGPT / OpenAI、DeepSeek、火山方舟；官方平台预置 URL 和模型，用户只填写 API Key。
- 每个账号独立设置每日文章数量和每日回答数量。
- 任务支持开始、暂停、继续、停止、失败重试和服务器重启后续跑。

## 当前版本

`v0.10.0`：加入用户独立的文章提示词模板库，支持文件夹、新建、切换、保存修改、重命名和删除。文章生成改为持久化进度任务，可选择保存草稿或生成后立即发布；文章列表固定每页 100 篇，新增所选文章批量发布、实时百分比、成功/失败数量与失败原因，并支持暂停、继续和停止。服务重启时未完成任务会安全转为暂停状态，可由原用户继续执行。

`v0.10.1`：修复知乎文章发布结果误判。编辑页 `/p/<id>/edit` 不再被视为发布成功，只有知乎未登录公开接口返回同一文章 ID 和标题后才标记为“已发布”。升级时会将历史上保存编辑页地址的错误记录改为“失败”，保留文章内容并提示重新发布。

`v0.10.2`：文章列表改为按知乎账号独立管理。管理员和普通用户每次只查看一个账号的文章，不再把所有账号文章混合在同一页；每个账号的文章再分为“草稿箱、待发布、已发布、发布失败”四个栏目。切换账号或栏目后从第 1 页开始加载，单选、全选和批量操作都限制在当前账号、当前栏目内；每篇文章的发布按钮精简为“发布”，避免操作栏拥挤换行。发布公开 API 被知乎返回 403 时自动改用无登录浏览器核验公开页面，失败栏目会醒目显示每篇文章的具体失败原因。

`v0.10.3`：修复知乎新版发布面板的最终确认。发布器可识别弹窗、浮层、抽屉和页面 Portal 中的确认按钮，并监听知乎正式发布接口；只有接口真实提交并通过公开核验后才标记成功。若被话题要求、账号限制、安全验证或接口错误拦截，失败栏目直接显示知乎返回的具体原因。

`v0.10.4`：文章列表增加真实发布时间。每次执行发布后，无论成功还是失败都记录最近一次发布完成时间；失败重试会更新时间。历史已发布和失败记录自动补齐时间，草稿及尚未执行发布的文章显示为空。知乎账号新增完整编辑与删除操作，已登录账号修改资料不会清除登录状态；删除账号会同步删除该账号独立保存的商品、关键词、文章及浏览器登录资料，并继续保持用户数据隔离。

`v0.10.5`：知乎登录预览改为全屏自适应大窗口，强制覆盖通用小弹窗宽度，并按窗口空间完整等比缩放知乎页面截图；电脑和小屏幕均可完整查看，另增加“查看原图”按钮用于打开原始分辨率页面。

`v0.9.0`：加入真实知乎扫码登录和单篇真实发布。每个知乎账号通过服务器 Chromium 打开知乎官方登录页，后台显示二维码并轮询登录结果；成功后的浏览器资料和 Cookie 保存在该账号自己的私有目录，其他系统用户不能查看二维码或复用会话。待发布文章可从文章列表使用对应账号发布到知乎，并保存发布链接；发布前再次核验真实 Cookie。账号列表显示真实登录状态，支持重新登录；遇到知乎安全验证时只提示人工完成，不尝试绕过。普通用户同时可以配置和使用自己的 AI API Key。文章、关键词和商品数据继续按系统用户与知乎账号隔离。

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
- `GET/POST /api/users`：管理员查询或新增授权用户。
- `PATCH /api/users/{user_id}`：管理员调整用户到期时间和启用状态。
- `POST /api/users/{user_id}/reset-password`：管理员重置普通用户密码。
- `GET /api/ai/providers`：获取预置 AI 平台与已保存配置（不返回 API Key 明文）。
- `PUT /api/ai/providers/{provider}`：加密保存 API Key 和模型。
- `POST /api/ai/providers/{provider}/test`：实际请求平台测试配置。
- `POST /api/accounts/{account_id}/keyword-jobs`：启动关键词采集任务。
- `GET /api/accounts/{account_id}/keyword-jobs/latest`：查询采集进度。
- `GET /api/accounts/{account_id}/keywords`：读取该账号独立关键词库。
- `GET/POST /api/accounts/{account_id}/keyword-folders`：查询或新建关键词文件夹。
- `PUT/DELETE /api/accounts/{account_id}/keyword-folders/{folder_id}`：重命名或删除文件夹（删除文件夹时保留关键词）。
- `PATCH /api/accounts/{account_id}/keywords/folder`：单个或批量移动关键词。
- `POST /api/accounts/{account_id}/keywords/bulk-delete`：单个或批量删除关键词。
- `GET/POST /api/accounts/{account_id}/products`：查询或新增该账号的推广商品。
- `GET/PATCH/DELETE /api/accounts/{account_id}/products/{product_id}`：读取、编辑或删除推广商品。
- `POST /api/accounts/{account_id}/articles/generate`：按关键词、商品和模型批量生成文章。
- `POST /api/accounts/{account_id}/articles`：手动创建文章草稿。
- `GET /api/articles`：按当前系统用户权限汇总查询文章，支持账号、状态和关键词筛选。
- `GET/PATCH/DELETE /api/accounts/{account_id}/articles/{article_id}`：读取、编辑或删除文章。
- `POST /api/articles/bulk-status`：批量调整文章状态。
- `POST /api/articles/bulk-delete`：批量删除文章。

除健康检查、版本和登录外，业务接口均要求 Bearer Token。普通业务接口允许有效期内的授权用户访问，但会按用户身份强制过滤数据；用户管理接口只允许系统管理员访问。升级启动时会自动增加多租户字段和索引，保留现有管理员、知乎账号、关键词、商品及 AI 配置。

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
