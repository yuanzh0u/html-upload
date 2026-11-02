# P6 html-upload 实施方案

本文给实施者（Codex）施工。只创建技能，不改 `/home/ryan/projects/html-platform/docs-service`。技能根目录是 `/home/ryan/projects/html-platform/html-upload/`。

## 0. 文档地位与不变量

### 0.1 读什么、冲突时信什么

按这个顺序读。后面的协议事实覆盖前面的示意：

1. 已上线的实现与锁定契约：`app/uploads.py`、`app/upload_taxonomy.py`、`app/p3_common.py`、`app/http_app.py`、`app/document_queries.py`、`app/apikeys.py`、`docs/P3-design.md` §1.1 与 §2.2、`docs/P4-design.md` 的 taxonomy seed 与 `/console/keys`。
2. 技能行为：`docs/DESIGN-v3.md` §10.1–§10.5、§5、§11，以及 §9.2「默认无外网资源」。
3. 外链提示拍板：`docs/P5-design.md` H-3 与 S10。结论是提示放在 P6 技能文案。不改上传响应，不加警告字段，不把外链改成拒绝。
4. 收口：`reports/P5-closeout.md`。本技能不做 M-3（`unsafe-inline`）、不做 A-15/M-6、不提前做 P7。

DESIGN-v3 §5.3 的 JSON 是示意。和实现不一致时，请求必须能被现行服务端接受。下表写进 `references/protocol.md`。禁止改服务端去迁就示意。

| 话题 | DESIGN-v3 示意 | 现行实现（技能必须遵守） |
|---|---|---|
| 上传正文 | 示例没有 `sha256` | `sha256` 必填，64 位小写十六进制。`app/uploads.py` 的 `validate` 必填元组含它 |
| `GET /api/v1/me` | 文字写了 Key 到期、上传限制 | 响应只有 `user_id`、`kind`、`role`、`scopes`、`display_name`。没有 `expires_at`，没有上传限额 |
| API Key 页面 | §5.1 写 `/console/api-keys` | 导航文案是「我的文档」「API Key」，路由是 `/console/keys` |
| 错误体 | `error.code` / `message` | P3 体是 `error`、`request_id`，需要时加 `reason` 与 `details` |
| 403 | 文字上能分 scope 与公开权限 | 对外同形 `forbidden`。Origin 不符、scope 不足、公开未授权都不给 reason |
| 别名 | 示例像展示名 | `aliases` 是活动别名的规范化键 |
| 通讯录 | 技能可能想按显示名查人 | Key 调 `GET /api/v1/directory` 会被拒绝（`user_only`）。不能把显示名换成 user id |

### 0.2 不变量

- 不修改 `/home/ryan/projects/html-platform/docs-service` 的代码、测试、配置、文档。
- 不建设 html-trans、html-spec，不调用、不修补 html-studio。html-studio 只作为目录参照：`SKILL.md` + `references/` + `bin/` + `templates/`。
- 不生成 HTML，不改正文，不重建远程或本地静态索引，不改 CSP，不改上传校验。
- 生产站点只有 `https://docs.zh0uyuan.com`。对话里的其他上传域名一律拒绝。不跟随重定向，不把 Key 发到 Location 的主机。
- 测试可以设置 `DOCS_ORIGIN`，只允许 `https://docs.zh0uyuan.com`、`https://127.0.0.1(:port)`、`https://localhost(:port)`、`http://127.0.0.1(:port)`、`http://localhost(:port)`。其他值退出码 2。技能参数不提供 `base-url`。
- 只使用 Python 3.12 标准库。不 `pip install`。上传用 `urllib`。禁止把 Key 放进 curl 的命令行参数。
- 任何输出、日志、异常、manifest、报告都不含 API Key 或其子串。

### 0.3 实施后的目录

```
/home/ryan/projects/html-platform/html-upload/
  PLAN.md
  SKILL.md
  references/protocol.md
  references/confirmation.md
  references/credentials-posix.md
  references/credentials-windows.md
  references/manifest.md
  references/external-links.md
  references/report.md
  templates/confirm-prompt.txt
  templates/report.txt
  bin/normalize.py
  bin/check_html.py
  bin/match_taxonomy.py
  bin/upload.py
  tests/
```

不自动挂进 `~/.agents` 或 `~/.grok`。挂载由人另决。

## 1. 触发条件与边界

### 1.1 何时使用

`SKILL.md` 的 front matter：

```yaml
---
name: html-upload
description: >-
  把已经存在的合规 HTML 文件只读上传到 docs 服务（https://docs.zh0uyuan.com）。
  做上传前检查、API Key 引导、taxonomy 确认、提交、本地备份和结果报告。
  用户要「上传这个 HTML」「传到 docs」「html-upload」时使用。
  不生成 HTML，不改 docs-service，不重建索引。
---
```

用户给出本地 `.html` 路径，并要求上传、发布到 docs、或点名 html-upload 时使用。

### 1.2 何时不用

| 用户要的事 | 技能做什么 |
|---|---|
| 把 Markdown 或笔记做成 HTML | 停止。本技能不生成页面。不去写 html-studio / html-spec |
| 改阅读页 CSP、sandbox、或让外链被服务端拒绝 | 停止。那是服务端行为 |
| 修 M-3 `javascript:` / `unsafe-inline` | 停止。独立整改，不在本技能 |
| 部署、迁移、备份作业、重建索引 | 停止 |
| 没有本地 HTML 文件 | 停止并索取路径。不临时编一份 HTML 来上传 |

### 1.3 技能只做这一条链

读本地 HTML 原字节 → 校验 → 取 Key → `GET /api/v1/me` → `GET /api/v1/taxonomy?include=aliases,counts` → 匹配标签 → 需要时停下来等人确认 → 扫描外链并提示 → 本地预备份 → `POST /api/v1/documents` → 本地副本与 manifest → 报告。

同时写死：

- 原 HTML 只读打开。不删除、不改字节。
- 不调用 `rebuild-index`，不写静态索引，不 rsync。
- 不上传 ZIP，不接收服务端路径，不让用户指定 `storage_key` 或 docid。docid 只来自成功响应。
- Key 的 scope 只有 `me:read`、`taxonomy:read`、`documents:create`。技能不 GET 文档列表，不改已有 ACL，不管理用户。请求里的 ACL 只作用于这一次创建。

### 1.4 与 §10.1 的对应

本节加上 §2 的参数、§13 的 `SKILL.md` 目录，覆盖 DESIGN §10.1：触发与边界、不生成 HTML、不写服务端、不重建索引、输入含 title/project/type/生成时间/模型/ACL/`force-confirm`/`dry-run`、非交互遇强制确认则暂停。验收见 A1。

## 2. 输入参数

`bin/upload.py` 是唯一入口。不要再做一条会绕过确认状态机的 curl 捷径。

### 2.1 参数、默认、校验

| 参数 | 必填 | 默认 | 校验 |
|---|---|---|---|
| `html` 位置参数 | 是 | 无 | 见 §5 |
| `--title` | 否 | 无。缺省用 §5 的 `<title>` | 若给出：码位 1–200，与 strip 后相同，不含类别 Cc/Cf/Cs |
| `--project-id` | 否 | 无 | 与 `--project-name` 互斥。匹配 `[0-9A-Za-z_-]{1,64}` |
| `--project-name` | 否 | 无 | 交给 §6。参数阶段不新建 |
| `--category-id` | 否 | 无 | 只在新建项目时用。与 `--category-name` 互斥 |
| `--category-name` | 否 | 无 | 新建项目且分类也要新建时用。不默认成「其他」或 `cat_other` |
| `--type-id` / `--type-name` | 否 | 无 | 互斥。类型没有 category |
| `--model-id` / `--model-name` | 否 | 无 | 互斥。不从文件名或执行者昵称猜测 |
| `--generated-at` | 否 | 无 | 必须是带时区的 RFC3339，否则进入待澄清。不接受 `YYYY-MM-DD HH:MM` 并私下补时区 |
| `--visibility` | 否 | `private` | `private`、`restricted`、`public` |
| `--acl-user` | 否，可重复 | 空 | 仅 `restricted`。每项是 id，不是显示名或手机号。最多 32 |
| `--acl-group` | 否，可重复 | 空 | 同上，最多 16 |
| `--force-confirm` | 否 | 关 | 见 §6 |
| `--dry-run` | 否 | 关 | 只展示计划。不写 pending，不 POST，不写 manifest，不产生 docid |
| `--confirm-file` | 否 | 无 | 恢复已暂停的确认。见 §6.6 |
| `--non-interactive` | 否 | 自动 | stdin 不是 tty 时视为非交互。Agent 必须能靠这个停在退出码 3 |

未知参数退出码 2。

`private` 与 `public` 的用户、组列表必须为空。`restricted` 至少一个目标。只有用户显式传入 `--visibility public` 才发送公开。没写 visibility 就是私有。

`--acl-user` 等于 `/me` 的 `user_id`：退出码 2，不发请求。服务端会 422 `acl_invalid`。

显示名、手机号、邮箱不能当 ACL 目标。退出码 2，说明需要 user id 或 group id。

### 2.2 两个开关

- `--dry-run` 优先于提交。同时有确认文件也不 POST。它仍做本地校验、Key 自检、拉 taxonomy、外链扫描，并打印计划。
- `--force-confirm` 不改变匹配，只增加「整单展示后等待」。它不能让新建项默认通过。
- 两个旗标都不进请求体。

### 2.3 退出码

| 码 | 含义 | Agent 下一步 |
|---|---|---|
| 0 | 已上传，或 dry-run 已展示 | 按 §11 转述。dry-run 必须说明没有 docid |
| 2 | 参数、本地文件、凭证、401、403、413、不可重试的 4xx/409 | 停止。不要换幂等键再试 |
| 3 | 待确认 | 把清单给人。不要自己填同意，不要把超时当同意 |
| 4 | `remote_unknown`，或远程已成功而本地未完成 | 停止。按 §8 报告。禁止再开一次逻辑上传 |
| 5 | 预备份或 manifest 没写好，网络还没发 | 停止。先修本地磁盘或权限 |

验收见 A2。

## 3. 凭证安全与操作系统分派

### 3.1 固定顺序

1. 环境变量 `DOCS_API_KEY` 已设置（包括空字符串）。
2. 否则读 `~/.config/docs/api-key`。
3. 否则打印 §3.3，退出码 2。本进程不在聊天里要 Key，也不在这一轮继续上传。

环境变量已设置但 `/me` 为 401 或 403：报告「来源是环境变量，该来源无效」，然后停止。禁止再读配置文件。空字符串同样不回退。

配置文件权限、属主或类型不符：不读内容，退出码 2。同一次进程里不要 chmod 之后继续。

OpenClaw secrets、VPS 的 Publishable Key、shell history、聊天粘贴都不是来源。文案写明：Publishable Key 与用户上传 Key 是两种凭证。

### 3.2 读文件前的检查（POSIX）

全部通过才读取：

- `lstat`。符号链接则停止，不穿透。
- 普通文件，属主 uid 等于当前 euid。
- mode 恰好 `0600`。`0644`、`0640`、`0400`、`0700` 都停止。`0400` 更严，仍引导改到 600，不要将就。
- 父目录 `~/.config/docs` 是 `0700` 且不是符号链接。不符则停止，不为了读而放宽。
- 内容去掉至多一个末尾 LF。其余空白拒绝。空内容拒绝。`len("Bearer ") + len(token) <= 512`，否则拒绝。不打印前缀、后缀或内容。

### 3.3 引导文案

找不到 Key 时原文打印，再附上操作系统小节：

```
登录 https://docs.zh0uyuan.com，进入控制台 → 我的文档视图 → API Key，创建后明文只显示一次。现行页面地址是 https://docs.zh0uyuan.com/console/keys 。不要把 Key 贴进聊天，不要写进带明文的命令或 shell history。
```

不要把用户指到未实现的 `/console/api-keys`。不要改服务端路由。

### 3.4 POSIX 落盘

2026-10-20 在本机核对：bash 5.2.21、zsh 5.9。下面的核心用假 Key、把 `HOME` 指到临时目录跑通：普通文件、mode `600`、目录 mode `700`、内容无末尾换行。`IFS= read -rs` 在两个 shell 都能读入。

已验证的差异：zsh `read -p` 报 `-p: no coprocess`，输入不会进变量。bash 的 `read -p` 才是提示符。技能禁止 `read -rsp`，也禁止把 bash 的 `-p` 写进 zsh。

写入 `references/credentials-posix.md` 的核心（验收用假 Key，Key 只来自该进程 stdin）：

```bash
umask 077
mkdir -p -- "$HOME/.config/docs"
chmod 700 -- "$HOME/.config/docs"
tmp=$(mktemp --tmpdir="$HOME/.config/docs" .api-key.XXXXXXXX)
chmod 600 -- "$tmp"
IFS= read -rs KEY || exit 1
printf '%s' "$KEY" > "$tmp"
unset KEY
mv -f -- "$tmp" "$HOME/.config/docs/api-key"
chmod 600 -- "$HOME/.config/docs/api-key"
```

给人用时，提示和读取应走终端，避免吃掉管道，也避免 Key 出现在命令行：

```bash
printf '粘贴 API Key 后回车（不回显）: ' >/dev/tty
IFS= read -rs KEY </dev/tty || exit 1
printf '\n' >/dev/tty
```

这两行的 `/dev/tty` 本轮没有跑成：打开 `/dev/tty` 得到 `ENXIO`（没有控制终端）。`credentials-posix.md` 在实施者于真实终端用假 Key 复跑成功之前，把 tty 版标成「待终端复跑」。复跑失败就不要写进技能。复跑后删除临时 `HOME`，不要把假 Key 留在真正的 `~/.config/docs/api-key`。

zsh 用同一段核心。提示用 `printf` 到 stderr，不用 `read -p`。

其他约束：

- Key 不出现在 argv、URL、`ps`、`set -x`、公共临时目录、日志。脚本不要 `set -x`。`http.client` 调试关闭。
- 临时文件放在目标目录再 `mv`。`mktemp` 之后立刻 `chmod 600`。
- 写入用 `printf '%s'`，不加换行。读取侧只剥一个末尾 LF。
- 不要教用户把 `export DOCS_API_KEY=明文` 写进 `~/.bashrc`。进程环境里已经有的变量可以使用。

### 3.5 Windows

本机没有 `pwsh`，也没有 `powershell`。本节不提供落盘命令，也不提供 `icacls` 例句。没运行过的命令不写进技能。

设计已经点名、但这里没有执行过的要求：

- 读取使用 `Read-Host -AsSecureString`。
- 转成字节的范围只覆盖这一次写文件，写完丢掉明文。
- 目标是用户目录下的 `docs/api-key`。`~` 映射该用户的家目录。不要写死盘符路径。
- 验收是 NTFS 上仅当前用户可读写的 ACL。不要写「这相当于 chmod 600」。

`references/credentials-windows.md` 在验证前的文件头：

```yaml
verified: no
reason: 方案撰写与 Linux 实施机没有 PowerShell。禁止写入未运行的命令。
```

Windows 上读到 `verified` 不是 `yes`，就打印「凭证引导尚未在本 PowerShell 版本验证」，退出码 2。不得改跑 §3.4。

只有在真实 PowerShell 里用假 Key 跑通读取、落盘、ACL 检查之后，才写入命令，并把文件头改成：

```yaml
verified: yes
powershell: <来自 $PSVersionTable.PSVersion 的字符串>
date: YYYY-MM-DD
```

文件末尾要有验证记录：命令、看到的 ACL、如何确认命令行和历史里没有假 Key。没有记录，`verified` 不得为 yes。

### 3.6 进程内

`upload.py` 按 §3.1 取 Key，放进局部变量，设到 `Authorization`，函数返回前删除。不打印 headers。不把 Key 写入 pending、manifest、报告、测试快照。测试假 Key 用固定字符串 `test-key-not-secret`，并断言输出里没有它。

验收见 A3。

## 4. GET /api/v1/me 自检

预备份之前、POST 之前必须做。dry-run 也做。

### 4.1 请求

- `GET <origin>/api/v1/me`
- `Authorization: Bearer <token>`，`Accept: application/json`
- 不带 Cookie。不跟随重定向。连接超时 10 秒，读取超时 30 秒。校验证书。
- 生产 origin 是 `https://docs.zh0uyuan.com`。测试 origin 只来自 §0.2。

### 4.2 通过条件

HTTP 200，且：

- `kind` 等于 `api_key`
- `scopes` 含 `me:read`、`taxonomy:read`、`documents:create`
- `user_id`、`display_name`、`role` 是字符串。角色只展示，不据此调用管理接口

响应没有到期时间，也没有上传限额。自检输出写这两句，不编数字，也不因缺字段而失败：

```
服务端未返回 Key 有效期。
服务端未返回上传限制。
```

这是现行响应形状，不是故障。`allow_public` 同样不在响应里。公开能否成功要到 POST 才知道。技能不在自检阶段猜测，也不因此改回 private。

### 4.3 失败即停

| 结果 | 行为 |
|---|---|
| 401 | 退出码 2。凭证无效。来源是环境变量时声明该来源无效且不回退。来源是文件时不要改读环境变量。引导到 `/console/keys` 吊销后重建。不重试 |
| 403 | 退出码 2。不重试 |
| 超时、非 JSON、重定向、其他 HTTP | 退出码 2。还没上传正文，不记 `remote_unknown` |
| 缺少 `taxonomy:read` 或 `documents:create` | 退出码 2。不要等到 POST |

现行代码里，`/me` 缺 `me:read` 会走非 P3 的 `_actor`，scope 不足变成 401 而不是 403。技能不利用这个差别去重试。401 与 403 都停。

自检输出可以有 `display_name`、`user_id`、`role`、`scopes`。禁止 Authorization 和 Key。

通过之后才 `GET /api/v1/taxonomy?include=aliases,counts`。taxonomy 的 401/403 同样停止。

### 4.4 验收锚点

见 A4。假服务器要覆盖：缺 scope 的 200、401、以及没有 `expires_at` 的完整 200。最后一种必须通过，并且 stdout 含「服务端未返回 Key 有效期」。

## 5. HTML 与 meta 校验

只读。解析失败也不改文件。服务端规则在 `app/uploads.py` 的 `validate`：技能在本地先挡下同样的字节条件，避免无意义的 POST。

### 5.1 字节条件

按原字节读取，不解码再编码。

- 大小 1 到 `10 * 1024 * 1024` 字节（`MAX_HTML_BYTES`）。超出退出码 2，提示解码后上限 10 MiB。
- 严格 UTF-8。非法序列退出码 2，对应服务端 422 `html_rejected`。
- 以字节 `EF BB BF` 开头：拒绝。有 `NUL`（`\x00`）：拒绝。
- 去掉开头的空格、TAB、CR、LF（只这四个，不去 Unicode 空格）之后，小写字节必须以 `<!doctype html` 或 `<html` 开头。
- 小写字节中必须含 `</html>`。
- 符号链接、目录、非常规文件：拒绝。

这些失败是本地错误，退出码 2，不 POST，不进入「待澄清」。待澄清只用于元数据歧义，不用于非法 HTML。

### 5.2 标题

来源顺序：

1. `--title` 与文档里 `<title>` 的文本都有，且码位不完全相同：待澄清。展示两边。不静默选一边。
2. 只有 `--title` 且合法：采用。来源记 `explicit`。
3. 只有一个 `<title>` 且合法：采用。来源记 `html_title`（只出现在本地计划和 manifest 的说明里；请求体没有 title 的 source 字段，title 就是字符串）。
4. 多个 `<title>` 文本不完全相同：待澄清。
5. 没有合法标题：待澄清。禁止用文件名、路径或「未命名」填上。

提取用 `html.parser`，不执行脚本。忽略注释、`<script>` 内、`<template>` 内的 `<title>`。属性名大小写不敏感。

### 5.3 meta

收集 `name` 为 `plans:completed` 或 `plans:worker` 的 `<meta>`（name 的 ASCII 大小写不敏感）。`content` 原样保留，不用 strip 把两边收成相同。

忽略：注释、`<script>` 内、`<template>` 内。不执行脚本，所以脚本运行后才会出现的 meta 不算。不要求生成侧改造。

同一 name 的多条 `content` 完全相同：当作一条。不完全相同：待澄清。不投票、不取第一条。

#### 生成时间

优先级：`--generated-at` 与 meta 同时存在时，先各自解释成时刻再比。

- 带时区的 RFC3339：按该偏移换算成 UTC 时刻。合法。
- `YYYY-MM-DD HH:MM`、`YYYY-MM-DD HH:MM:SS`、没有 `Z` 或数字偏移的 `YYYY-MM-DDTHH:MM:SS`、只有日期：不完整。待澄清，向用户要时区。禁止用执行机器的本地时区。禁止用上传时刻填生成时间。
- 无法解析：待澄清。
- 参数与 meta 都完整但不是同一时刻：待澄清。展示原始字符串和换算后的 UTC。不静默覆盖。
- 只有 meta 且完整：采用。`metadata_source.time` = `html_meta`。
- 只有参数且完整：采用。`metadata_source.time` = `explicit`。
- 两边同一时刻：不冲突。`metadata_source.time` = `html_meta`。
- 用户在确认里选定或新给出带时区的值：`metadata_source.time` = `confirmed`。

提交值用 UTC 的 RFC3339，秒字段必有，例如 `2026-10-15T04:00:00Z`。源字符串若有小数秒，保留小数秒。这与服务端 `timestamp()` 接受的形式一致。展示时同时给出用户看到的原文字。

#### 模型

- 没有唯一的 `plans:worker`，也没有 `--model-id` 或 `--model-name`：待澄清。不猜测模型。
- worker 的 `content` 经 §6.1 的 `normalize` 失败（空、超长、控制字符）：待澄清。
- worker 唯一且能规范化：它只是候选名称，不是自动新建。能否直接提交取决于 §6 的精确命中。未命中 id、规范名、活动别名时，进入待澄清，问「这是模型名，还是执行者名称？若是执行者，请给出模型名」。禁止在这一问之前设置 `create_confirmed`。
- 参数与 worker 规范化键不同：待澄清。相同：不冲突。
- 命中已有模型：`metadata_source.model` = `html_meta`（来自 meta）或 `explicit`（只有参数）。
- 确认后才确定：`metadata_source.model` = `confirmed`。

`metadata_source` 只能有键 `time` 和 `model`，值只能是 `html_meta`、`explicit`、`confirmed`。

### 5.4 验收锚点

见 A5。夹具至少包括：BOM、NUL、非法 UTF-8、缺少 doctype、缺少 `</html>`、两条不同的 `plans:completed`、缺时区、带 `+08:00` 的时间、`<template>` 里的干扰 meta、参数与 meta 时刻不同。最后一种必须退出码 3（或在非交互下暂停），不得 POST。

## 6. taxonomy 匹配与确认状态机

提交前拉 taxonomy。匹配在本地做。模糊相似不是命中。

### 6.1 规范化

把 `app/p3_common.py` 的 `normalize` 抄进 `bin/normalize.py`，不要 import 服务端包。行为保持：

- 非字符串或含 NUL：非法。
- Unicode NFKC，去首尾空白。
- 把连续的空格、TAB、CR、LF、垂直制表、换页、`U+00A0`、`U+1680`、`U+2000`–`U+200A`、`U+202F`、`U+205F`、`U+3000` 折成一个普通空格。
- 结果非空，码位长度和 casefold 后的长度都不超过 80。不含类别 Cc/Cf/Cs。
- 返回展示名（折叠后的原文大小写）和匹配键（`casefold`）。

不删除标点，不删模型版本号，不把中英文收成同一个词。别名只有响应里出现的活动别名才算命中。技能不发明别名。

测试向量：`"  设计稿  "` → 展示名 `设计稿`；`"ExampleModel"` 的匹配键是 `examplemodel`。

### 6.2 拉取与字段

`GET /api/v1/taxonomy?include=aliases,counts`。使用响应里的 `version` 整数，原样放进 `taxonomy_version`。版本不是锁。过期版本不在客户端预先拒绝。

每一项用 `id`、`name`、`aliases`（规范化键列表，可能没有）、`selectable_for_upload`。项目还有 `category_id`。分类的 `builtin` 只用于展示。

`selectable_for_upload` 为 false 的项目可以显示「看得见但不能用于上传」（通常是别人的项目）。它不是命中。不要用同名悄悄新建，除非用户在确认里明确要新建自己的项目。

内置 id（`cat_software`、`type_design` 等）只许出现在测试夹具和示例里。运行时以响应为准，不内置一份会过期的模型表。

### 6.3 匹配顺序

对 type、model、project、以及新建项目时的 category，各自按这个顺序，命中即停：

1. 调用方给了 id：响应中有此 id、kind 相符、且 `selectable_for_upload` 不为 false。请求体只送 `{"id":"..."}`。id 不存在或不可选：待澄清。不把 id 改写成 name 再新建。
2. 展示名的匹配键等于某条 `name` 的匹配键：命中。送 `{"id": 该条 id}`。
3. 匹配键等于某条活动 `aliases`：命中。送 id。
4. 否则未命中。用与服务端相同的建议算法做候选，仅供展示：`difflib.SequenceMatcher(None, key, other).ratio()`，保留两位小数；`ratio >= 0.80`，或者两边长度都至少 2 且互为前缀。按分数降序、id 升序，最多 5 条。该 kind 的可选词超过 200 条时，候选为空，并写「候选过多，请给出规范名或 id」。候选不是命中，不自动选用。

服务端在未命中且没有 `create_confirmed` 时返回 422 `taxonomy_confirmation_required`，`details.items[]` 含 `field`、`kind`、`input`、`normalized`、`candidates`。客户端本地没建议到，仍以服务端候选为准进入 §6.5，不自动打确认标记。

已命中时，即使用户文案像「新建」，也复用 id。请求里不要带 `create_confirmed`。

### 6.4 两种确认模式

全部满足才允许 `confirmation.mode = existing_default`，且 `new_terms` 为 `[]`：

- project、type、model 都是 id 命中（含别名命中）。
- 若项目是命中，不带新建分类。
- 生成时间与模型都完整，且没有 §5 的冲突。
- 标题完整，且没有标题冲突。
- 没有未决候选。
- 没开 `--force-confirm`。

这是默认策略。不要再逐个标签追问。

下列任一成立，则模式必须是 `explicit`，未确认不得 POST：

- 任一新标签，或用户选定「新建」而不是复用候选。
- 时间、模型、标题缺失或冲突。
- worker 未命中已有模型（§5.3）。
- `--force-confirm`。

一次汇总给人：已命中项（id 与规范名）、候选、准备新建的展示名、生成时间原文与 UTC、模型、ACL、外链提示（§10）。新建项每项单独要一句确认。用户改了一个名字之后，新项重新确认，不能沿用上一份确认。

`existing_default` 与 `explicit` 不能同时理解成「每次必问」或「新建也可以默认」。

### 6.5 新建项怎么进请求

用户明确同意新建之后：

- type 或 model：`{"name":"<展示名>","create_confirmed":true}`。不要同时给 id。
- project：`{"name":"...","create_confirmed":true,"category_id":"<已有 id>"}`，或者 `category` 换成 `{"name":"...","create_confirmed":true}`。`category_id` 与 `category` 互斥。缺分类就继续问，不默认。
- `confirmation`：

```json
{"mode":"explicit","new_terms":[{"kind":"type","normalized":"<casefold 后的键>","confirmed_at":"<UTC 微秒，形如 2026-10-20T01:02:03.000000Z>"}]}
```

`kind` 只许 `project_category`、`project`、`type`、`model`。`normalized` 必须已经是匹配键。展示名放在选择器的 `name` 里，不放进 `normalized`。`confirmed_at` 用确认当时的 UTC，须落在服务端窗口 `[now-7天, now+5分钟]`。本地先检查这个窗口，避免明知会 422 还 POST。

只要本请求会新建词条，mode 就必须是 `explicit`，并且每个实际新建的 `(kind, normalized)` 都在 `new_terms` 里。多写了既不新建也不命中的项，服务端 422 `taxonomy_invalid`。不要多写。

### 6.6 非交互与确认文件

非交互（`--non-interactive` 或 stdin 不是 tty）遇到 §6.4 的 explicit 条件或 `--force-confirm`：

- 不 POST。
- stdout 打一份 UTF-8 JSON，退出码 3。
- 无人回复、超时、空输入、Agent 自己说「可以」都不是确认。

JSON 至少包括：`status` = `needs_confirmation`，`client_upload_id`，`plan_sha256`，`items`（每项含 field、kind、命中或候选或缺失原因、建议新建名），`generated_at`，`model`，`title`，`acl`，`external_links`（§10），`prompt`（来自 `templates/confirm-prompt.txt` 的原文）。`plan_sha256` 是这份 JSON 去掉 `plan_sha256` 键之后的规范 JSON（`sort_keys=True`，`separators=(',', ':')`，`ensure_ascii=False`）的 SHA-256。

恢复用 `--confirm-file`。文件是 JSON：

- `client_upload_id` 与 `plan_sha256` 必须相等。不等则退出码 2，不 POST。
- `accepted` 必须是 true。
- 每个待新建或待选择的 field 都有 `choices` 项：`reuse` 加 id，或 `create` 加 `name` 且 `create_confirmed` 为 true。
- 缺时区的项要有 `timezone`，形如 `+08:00` 或 `-05:00`。由技能拼成 RFC3339，仍不使用机器本地时区。
- `confirmed_at` 由这次恢复运行的时钟生成，不信任文件里一个过期的时间戳去硬凑窗口。文件若带了 `confirmed_at`，只用于展示，以本次时钟为准，并且因此请求体与当初暂停时不同，这是预期的。暂停发生在 POST 之前，幂等键还没使用或按 §7 尚未领取，允许为这份已确认的请求体新建幂等键。见 §7.3。

`--force-confirm` 且没有任何新建时，确认文件仍要 `accepted: true`。一个笼统的 yes 不能代替新建项的逐项 `create`。

dry-run 打印同一份计划 JSON（`status` = `dry_run`），退出码 0，不读确认文件来发 POST。

### 6.7 确认期间 taxonomy 变了

POST 若返回 422 `taxonomy_confirmation_required`：重新 GET taxonomy，把服务端 `details.items` 并进清单，回到 §6.4。不自动对候选打 `create_confirmed`。

422 `taxonomy_invalid` 且 `details.successor_id` 存在：把继任 id 放进清单请用户确认，不自动改挂。

只修复确定性规范化（例如用户输入的首尾空白，本地 `normalize` 已经能做）不额外打断。换了候选、换了新建名、或服务端给出新的 `items`，必须再确认。禁止循环自动确认。

同名并发由服务端复用先建成的 id。技能看到 201 的 `normalized_metadata` 与请求名一致即可，不要因为本地预想的新 id（技能本来也不分配 id）对不上而再 POST 一次。

### 6.8 验收锚点

见 A6。必须有：别名命中不新建、近似名只出现在候选、`existing_default` 在齐全时直接提交、缺模型时退出码 3、确认文件哈希不符时退出码 2、422 后第二次仍不自动 `create_confirmed`。

## 7. 提交协议与幂等

只用 HTTPS 固定站点。Key 不跨重定向。HTML 用原字节的标准 Base64。服务端自己算 SHA-256 再和 `sha256` 字段比。

### 7.1 请求

`POST <origin>/api/v1/documents`

头：

| 头 | 值 |
|---|---|
| `Authorization` | `Bearer <token>` |
| `Content-Type` | `application/json` |
| `Accept` | `application/json` |
| `Origin` | 与 origin 完全相同，例如 `https://docs.zh0uyuan.com` |
| `Idempotency-Key` | `[A-Za-z0-9_-]{16,128}`。用 `secrets.token_hex(16)`（32 个十六进制字符） |
| `Content-Length` | 十进制字节长度。禁止分块传输。上限 14046552。本地先算，超出则退出码 2，不发送 |

不带 Cookie。关闭自动重定向。3xx：若请求体已经写出，记 `remote_unknown`（退出码 4），不向 Location 再发 Key。若连接在写出正文前就失败，按 §9 的网络失败处理。

正文是一个 JSON 对象，键只许这些：`title`、`project`、`type`、`model`、`generated_at`、`metadata_source`、`taxonomy_version`、`confirmation`、`acl`、`sha256`、`html_base64`。未知键服务端 400。

`html_base64` 是标准字母表，无空白、无换行。`sha256` 是原字节 SHA-256 的 64 位小写十六进制。

`acl` 总是显式发送。默认：

```json
{"visibility":"private","users":[],"groups":[]}
```

标题用 §5 确定的字符串，不用文件名。

### 7.2 请求哈希

服务端（`app/uploads.py` 的 `validate`）的算法，技能用同一算法存档，便于判断「是不是同一请求」：

1. 取除 `html_base64` 以外的必填字段，再加上 `acl`。
2. `json.dumps(..., sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)`，编码为 UTF-8。`generated_at` 用客户端发出的原文，不是服务端归一化结果。
3. `sha256(b"docs-upload/v1\n" + canonical + b"\n" + html_bytes)`，得到小写十六进制。

这个哈希不含 Authorization，不含 Idempotency-Key。

重试时不重新序列化。把当时 POST 的原始正文保存在 `~/docs/.pending/<client_upload_id>.request.json`，重试原样发送。

### 7.3 幂等键何时复用

`client_upload_id` 与 Idempotency-Key 是两个值，都在第一次准备发送时用 `token_hex(16)` 生成，并写进预备份。不要共用同一个字符串。

只在「同一请求重试」时复用 Idempotency-Key：正文原始字节不变，包括 `confirmed_at`、`html_base64`、`sha256`、标签和 ACL。

下列情况另开逻辑上传：新的 `client_upload_id`、新的 Idempotency-Key、新的 pending 文件。

- 用户改了标题、HTML、时间、模型、标签、ACL 或确认内容。
- 422 之后用户做了选择，请求体因此不同。暂停若发生在任何 POST 之前，旧键还没送给服务器，可以丢弃不用，仍要用新键绑定新正文，避免和半成品记录混在一起。
- 已经得到 409 `idempotency_conflict`。旧键不能改挂新正文。

不要复用的反面就是：网络超时、503、429、409 `upload_in_progress`、以及无响应。这些必须原键、原正文重试。禁止为了「避开冲突」再生成一把键，那会在服务端已经成功时再创建一篇文档。

成功重放是 HTTP 200，`replayed: true`，`id` 与当初相同。技能把它当作同一次成功，不新建本地逻辑上传。

领取前的 400 不占键。若是技能自己的字段错误，修软件后原正文仍用原键；若修的是用户数据，正文变了，就新键。

### 7.4 成功响应

201 是首次，200 是重放。都要有：

- `id`：`^[0-9a-f]{32}$`
- `url`：`<origin>/<id>.html`，不多一个斜杠，不接受其他主机
- `sha256` 等于本地原字节哈希
- `uploaded_at`、`acl_version`、`normalized_metadata`（含规范化后的 project、type、model、generated_at、metadata_source）

manifest 用这份 `normalized_metadata`，不用发送前的猜测名称覆盖服务端名字。缺任一字段：响应不可信，退出码 4，保留 pending，不伪造 docid。

### 7.5 验收锚点

见 A7。测试断言：同一假响应重放不产生第二个 docid；改标题后的第二次调用使用了不同的 Idempotency-Key；请求体含 `sha256` 且 Base64 无空白。

## 8. 本地备份与 manifest

`~/docs/` 是 Agent 这台机器上的备份根。它不是 VPS 的 `/srv/docs`。原 HTML 不删除、不修改。

### 8.1 目录与权限

- `~/docs` 与 `~/docs/.pending`：创建为 `0700`，拒绝符号链接。
- 文件 `0600`。
- manifest 也是 `0600`，因为里面有标题和 ACL 快照。

本地预备份失败（磁盘、权限、哈希对不上）：退出码 5，不 POST。

### 8.2 POST 之前

`client_upload_id` 是 32 位小写十六进制。

1. 独占创建 `~/docs/.pending/<client_upload_id>.html`，写入与源文件相同的字节，`fsync`，再读回比对 SHA-256。
2. 独占创建 `~/docs/.pending/<client_upload_id>.request.json`，内容是将要 POST 的原始 JSON 字节，`fsync`。
3. 独占创建 `~/docs/.pending/<client_upload_id>.meta.json`，`0600`，含幂等键、请求哈希、`attempt`、标题与标签、ACL 快照、源路径。不含 API Key。`fsync`。
4. 按 §8.4 追加一条 `prepared`。`fsync` manifest。
5. 然后才 POST。

第 4 步失败：不 POST。退出码 5。已写的 pending 保留。报告「预备份在，manifest 没写上」。

dry-run 不做这五步。

### 8.3 成功之后

响应可信之后：

- 目标 `~/docs/<docid>.html` 不存在：把 pending 的 html `os.replace` 到该路径（同一目录所在文件系统，原子换名）。再读回比对 SHA-256。然后 `fsync` 所在目录。这是换名，不是把字节丢掉。
- 目标已存在且哈希相同：不覆盖。pending 留在原地。`backup_filename` 仍写 `<docid>.html`。
- 目标已存在且哈希不同：不覆盖，不删除 pending，不删除目标。状态 `local_finalize_failed`。退出码 4。报告原文必须含「远程已成功、本地待修复」。禁止再 POST。

无论远程还是本地后处理失败，都不删除已写好的本地副本，也不要求服务端删除已成功的文档。

无 docid 的失败：pending 保留，manifest 里 `docid` 为 null。不编一个本地 id 充当 docid。

### 8.4 manifest.jsonl

路径 `~/docs/manifest.jsonl`。追加式，一行一个 JSON 对象，行尾一个 `\n`。它不是 JSON 数组。

写入时对 `~/docs/.manifest.lock` 加独占锁（POSIX 用 `fcntl.flock`）。写入、flush、`fsync` 之后解锁。Windows 在 §3.5 未验证前不写 manifest，技能直接退出码 2。

`schema_version` 固定为 `1`。每次尝试一行。字段：

| 字段 | 语义 |
|---|---|
| `schema_version` | `1` |
| `event_id` | 32 位十六进制，本行唯一 |
| `client_upload_id` | 逻辑上传 |
| `attempt` | 从 1 递增的发送尝试 |
| `status` | `prepared`、`upload_failed`、`remote_unknown`、`uploaded`、`local_finalize_failed`、`reconciled` 之一 |
| `docid` | 只有服务端成功才是 32 位十六进制，否则 null |
| `title` | 字符串或 null |
| `project` | `{"id","name"}` 或 null。成功行用 `normalized_metadata` |
| `type` | 同上 |
| `generated_at` | UTC 字符串或 null |
| `generated_model` | `{"id","name"}` 或 null |
| `pending_reasons` | 字符串数组。字段为 null 时说明原因。可为空数组 |
| `permission` | `{"visibility","users","groups"}`。上传时快照。不含电话、不含 Key。不是线上 ACL 的当前权威 |
| `backup_filename` | 相对 `~/docs`。成功是 `<docid>.html`，未成功是 `.pending/<client_upload_id>.html` |
| `uploaded_at` | 服务端时间，否则 null |
| `attempted_at` | 客户端 UTC 微秒时间 |
| `sha256` | 原字节哈希，或尚未读到文件时 null |
| `idempotency_key` | 幂等键。这不是 API Key |
| `request_hash` | §7.2 |
| `error_code` | 如 `401`、`409:idempotency_conflict`、`422:html_rejected`，否则 null |

成功行的标签与时间用服务端 `normalized_metadata`。不要把 Key、验证码、HTML 正文、Base64 写进 manifest。

磁盘满导致追加失败：保留已有文件和 `.meta.json`，退出码 4 或 5（若还没 POST 则为 5）。告警写明「日志没写上」。不要声称失败事件已入账。磁盘恢复后由人再运行一次恢复命令（§8.5），追加 `reconciled`，而不是把旧行改掉。

### 8.5 remote_unknown 与恢复

记 `remote_unknown` 的情况：超时、连接在请求送出后断开、3xx 且正文已送出、503 在有界重试之后仍无确定拒绝、409 `upload_in_progress` 在有界等待后仍在处理、成功响应缺 docid 或哈希不符。

恢复：用 `.request.json` 的原始字节和同一个 Idempotency-Key 再 POST。

- 200 且 `replayed: true`：拿到原 docid，走 §8.3，再追加 `reconciled`（或 `uploaded`，若这是第一次写成功行）。
- 201：同样按成功收尾。只应发生在上次其实没领取成功时。
- 409 `upload_in_progress`：继续按 §9 等，仍用原键。
- 409 `idempotency_conflict`：停止。不新键。退出码 4。
- 401/403：停止。上一回是否成功仍未知。先按 §3 处理 Key，再用原键恢复。不换键。

远程成功、本地改名或追加失败：报告必须出现「远程已成功、本地待修复」，列出 docid、url、pending 路径、manifest 最后状态。不 POST 新文档。人修好磁盘后再运行，技能只做 §8.3 的本地收尾并追加 `reconciled`。

### 8.6 验收锚点

见 A8。测试在临时 `HOME` 里做：预备份哈希不符则不连接假服务器；目标文件哈希冲突时不覆盖；JSONL 可逐行 `json.loads`；状态闭集之外的字符串不会被写出。

## 9. 错误恢复

先分清「同一请求可以再试」和「必须停」。同一请求再试永远用原正文、原 Idempotency-Key。409 的三个 reason 不混用同一套动作。

有界重试的计数写在该 `client_upload_id` 的 `.meta.json` 里，进程崩溃后续跑仍然有效。不要只放在内存里。

### 9.1 对照

| HTTP 与 reason | 动作 | manifest | 退出码 |
|---|---|---|---|
| 401 | 停。按 §4.3 引导重建或修正 Key。不回退到另一个来源。不新键 | `upload_failed`，若此前已是 `remote_unknown` 则再追加一行，状态仍记未知被凭证挡住，`error_code`=`401`，`docid` 保持 null | 2；若已有 `remote_unknown` 则 4 |
| 403 | 停。不重试。说明可能是 scope 不足、Origin 不符、或 public 未被该 Key 允许，三者对外相同，技能不猜哪一个，也不自动改成 private | 同 401，`error_code`=`403` | 同 401 |
| 400 | 停。当作客户端错误。不盲重试 | `upload_failed` | 2 |
| 413 `request_too_large` | 停。说明解码后 HTML 上限 10 MiB，JSON 体上限 14046552 | `upload_failed` | 2 |
| 415 | 停。修客户端的 Content-Type 后，正文未变才可用原键 | `upload_failed` | 2 |
| 422 `taxonomy_confirmation_required` | 不重试原样。刷新 taxonomy，进入 §6.7。未确认不 POST | 追加 `upload_failed`，`error_code` 写明 reason。pending 保留 | 3 |
| 422 `html_rejected`、`hash_mismatch`、`acl_invalid`、`taxonomy_invalid` | 停。展示 reason 与 `details` 里允许给人看的 `field`、`items`、`successor_id`。不展示 HTML。`hash_mismatch` 是客户端缺陷，原样再送没有意义 | `upload_failed` | 2；`taxonomy_invalid` 且需要人选择继任 id 时为 3 |
| 409 `upload_in_progress` | 同一请求。等待响应头 `Retry-After` 的整数秒（缺省按 1，上限按服务端的 120 理解，客户端单次睡眠不超过 120 秒），再加 0–250 毫秒抖动。最多 3 次。仍在处理则停 | 停之后 `remote_unknown`。它可能在租约内完成 | 4 |
| 409 `idempotency_conflict` | 停。不重试，不新键，除非用户明确要求改内容并同意另开逻辑上传。默认不另开 | `upload_failed` | 4（结果可能已有一篇不同正文的文档，不能假装失败了就再传） |
| 409 `quota_exceeded` | 停。若有 `details.scope`（`user` 或 `site`）就写进报告。不重试 | `upload_failed` | 2 |
| 429 | 同一请求。先睡 `Retry-After` 的整数秒（缺省 1，单次不超过 120），再加 0–250 毫秒。最多 3 次。这是限流，不是指数退避。服务端上传桶是每 Key 每 60 秒 10 次，同时未过期 processing 最多 2 | 用尽仍 429：`upload_failed`。键可留给人稍后原样再跑 | 2 |
| 503 `storage_unavailable` 或 `busy` | 同一请求。指数退避加全抖动：起点 1 秒，每次乘 2，上限 30 秒，睡眠为 `random(0, delay)`。`busy` 的 `Retry-After: 1` 当作第一次的下限。最多 5 次 | 仍失败：`remote_unknown` | 4 |
| 500 | 不重试成新文档。同一请求最多再试 1 次。仍 500 则 `remote_unknown`。锁失败在现行服务端可以是 500，技能不把它改写成 503 | `remote_unknown` | 4 |
| 超时或连接在送出后断开 | 同一请求。并入 503 的退避预算（合计最多 5 次） | `remote_unknown` | 4 |
| 3xx | 不跟随。正文已送出则为 `remote_unknown`，否则 `upload_failed` | 见左 | 4 或 2 |

无副作用的 4xx（领取前）不会留下 ready 文档。技能仍不删除 pending。

### 9.2 禁止的混用

- 不要把 `quota_exceeded` 当成 `upload_in_progress` 去等 Retry-After。
- 不要把 `idempotency_conflict` 当成网络错误去换一把新键自动重放。
- 不要在 429 上使用指数退避来代替 `Retry-After`。
- 不要在 503 上忽略退避、紧循环重试。
- 401/403 不做「未知身份」的盲目重试。

### 9.3 验收锚点

见 A9。假服务器按 reason 分支各至少一次。断言三条 409 的退出码和是否发出第二把 Idempotency-Key。429 的测试睡眠可以注入时钟，不要真的睡满。

## 10. S10 外链提示文案

H-3 与 S10 的落点只在技能文案。不改 `app/uploads.py`，不加响应字段，不把外链改成 422 或 400。外链文档仍然上传。A-7 的浏览器封禁不依赖这段提示。

### 10.1 扫描

在确认清单和 POST 之前扫描。dry-run 也扫描。不发网络请求去探测这些 URL。不改正文。

用 `html.parser` 看起始标签，不执行脚本。另在 `<style>` 文本和 `style` 属性里找 `url(` 与 `@import`。

算作要提示的引用：

- 属性 `src`、`href`、`poster`、`data`、`action`、`formaction`、`srcset` 里的地址。
- SVG 的 `href`。
- CSS `url(` 与 `@import` 的地址。

地址去掉空白和引号后，以 `http:`、`https:`（大小写不敏感）或 `//` 开头，就列入。相对路径、`data:`、`blob:`、`mailto:`、纯片段不列入。`javascript:` 不列入本提示，也不改写，也不因此拒绝上传。M-3 不在本技能处理。

同一地址只列一次。报告里最多列 20 条，并给出总条数。

### 10.2 固定文案

有命中时，确认清单和最终报告都原文包含（URL 列表接在这段后面）：

```
外链提示：这份 HTML 含有 http(s) 或协议相对的资源引用（见下列出）。阅读页默认无外网资源，这些引用在桌面 Chrome 里通常加载失败（缺图、缺字、外链脚本不执行）。这只是兼容性提示：不改正文，不拒绝上传，服务端不新增警告字段。
```

无命中时原文包含：

```
外链提示：未发现 http(s) 或协议相对资源引用。阅读页仍默认无外网资源。
```

这段提示不改变 §6 的确认模式。它本身不要求退出码 3。`--force-confirm` 或别的待澄清已经要停时，把这段放进同一份清单。非交互且会直接 POST 时，在 POST 之前把这段打到 stdout，然后继续。

`templates/confirm-prompt.txt` 与 `references/external-links.md` 保存这两段原文。`SKILL.md` 让模型整段抄出，不要改写。

### 10.3 验收锚点

见 A10。夹具含 `<script src="https://example.invalid/a.js">` 与一张 `data:` 图片。断言：stdout 含「默认无外网资源」和「不拒绝上传」；源文件字节不变；假服务器仍收到 POST；响应断言里不要求警告字段。无外链夹具含第二段文案。

## 11. 最终报告格式

成功、失败、暂停、dry-run 都用同一组小标题。没有的项写「无」，不要省略小标题。模板在 `templates/report.txt`。Agent 只填空，不另写一套。

```
状态: <uploaded | dry_run | needs_confirmation | upload_failed | remote_unknown | local_finalize_failed | prepared_only>
线上链接: <url 或 无>
docid: <32 hex 或 无>
本地副本: <绝对路径或 无>
manifest: <最后一条 status> <manifest.jsonl 的绝对路径>
外链提示: <§10.2 的对应段落，有命中时再列最多 20 个 URL 与总条数>
待办: <无，或具体下一步>
```

按状态填：

| 状态 | 链接与 docid | 本地副本 | 待办 |
|---|---|---|---|
| `uploaded` | 响应里的 url 与 id | `~/docs/<docid>.html` | 无。若 pending 因哈希相同而未移动，待办写明 pending 仍在且未覆盖 |
| `dry_run` | 无 | 无 | 无。正文第一句写「未提交，没有 docid」 |
| `needs_confirmation` | 无 | 无 | 把退出码 3 的清单交给用户，得到确认文件后再运行 |
| `upload_failed` | 无 | pending 路径，若还没准备则为无 | 按 §9 的 reason 写能做的一件事。不写「请换一把幂等键再试」除非用户要改内容 |
| `remote_unknown` | 无 | pending 路径 | 用原请求和原幂等键恢复。不要新开逻辑上传 |
| `local_finalize_failed` | 有 url 与 docid | pending 路径，并写目标路径为何不能覆盖 | 必须含「远程已成功、本地待修复」 |
| `prepared_only` | 无 | pending 路径 | 网络未发送。先修本地 manifest 或磁盘 |

自检块可以附在报告后面，只含 §4 允许的身份字段和那两句「服务端未返回…」。

禁止出现在报告、异常字符串和 Agent 转述里的内容：API Key、`Authorization`、`DOCS_API_KEY` 的值、配置文件内容、幂等键以外的任何密钥。幂等键也不要放进给人的报告；它留在 `0600` 的 meta 文件里。人需要恢复时，技能读那个文件，而不是让人把键贴回聊天。

验收见 A11。用假 Key 跑通一条成功路径和一条 401 路径，对 stdout 与 stderr 做子串断言。

## 12. 验收标准

每条都要留下命令输出或测试名。未跑的条目标「未跑」和原因，不得标成通过。测试放在 `/home/ryan/projects/html-platform/html-upload/tests/`，用临时 `HOME` 和假 HTTP 服务器，不访问 `docs.zh0uyuan.com`。

### A1 触发与边界

- `rg -n "不生成 HTML|不改服务端|不重建索引" SKILL.md` 三句都有。
- `rg -n "html-trans|html-spec" SKILL.md references bin` 只出现在「不要建设」的句子里。
- 假测试：上传器不写源 HTML。比对上传前后的源字节与 mtime 可以变，字节必须相同。mtime 不作要求，字节是要求。

### A2 参数

- 缺 `html`、未知旗标、`--visibility public` 未显式时请求体为 private：各一条测试。
- `--project-id` 与 `--project-name` 同时给出：退出码 2，零次 POST。
- `--dry-run` 时假服务器的 POST 计数为 0，stdout 含「未提交，没有 docid」。
- `--force-confirm` 且非交互、标签已全部命中：退出码 3，POST 计数为 0。

### A3 凭证

- 环境变量设为假 Key 且 `/me` 返回 401：不读取配置文件（用一个会被检测到的 0644 文件放在临时 HOME 里，断言没有读取它的内容；实现上可以在读文件前就因 401 返回）。更直接的断言：401 的错误文案含「来源是环境变量」，且没有发生第二次 `/me`。
- 文件 mode `0644`：退出码 2，假服务器连接数为 0，stderr/stdout 不含文件内容。
- `references/credentials-posix.md` 含 `IFS= read -rs`，且不含 `read -rsp`。
- `references/credentials-windows.md` 的 `verified` 在没有 PowerShell 实验记录时为 `no`。`rg -n "icacls|chmod 600" references/credentials-windows.md` 不得出现把两者写成等效的句子。
- 全测试输出不含 `test-key-not-secret`。

### A4 `/me`

- 200 且 scopes 齐全、无 `expires_at`：继续拉 taxonomy，stdout 含「服务端未返回 Key 有效期」和「服务端未返回上传限制」。
- scopes 缺 `documents:create`：退出码 2，POST 计数 0。
- 401 与 403：退出码 2，且没有第三次请求。

### A5 HTML 与 meta

- BOM、NUL、非法 UTF-8、无 doctype、无 `</html>`：退出码 2，POST 计数 0。
- 两条不同 `plans:completed`、缺时区、参数与 meta 不同时刻：退出码 3。
- `<template>` 内的 meta 不参与冲突。
- 带 `+08:00` 的唯一 meta 时间：请求里的 `generated_at` 是对应 UTC，`metadata_source.time` 为 `html_meta`。

### A6 确认状态机

- 别名命中：请求体是 `{"id":...}`，`confirmation.mode` 为 `existing_default`，`new_terms` 为 `[]`。
- 近似名：出现在清单的 candidates 里，请求在确认前不发出；确认文件若写 reuse 该 id，则送 id 而不是 create。
- 新建 type：`create_confirmed` 为 true，`new_terms` 含该 kind 与 casefold 键，mode 为 `explicit`。
- 非交互缺模型：退出码 3，stdout 是合法 JSON，`status` 为 `needs_confirmation`。
- `plan_sha256` 不符：退出码 2。
- 假服务器第一次 POST 返回 422 `taxonomy_confirmation_required`：下一次在没有新的确认文件时不得带 `create_confirmed`。

### A7 提交与幂等

- 成功请求含 `sha256`，Base64 解码等于原字节，`Content-Type` 为 `application/json`，`Origin` 为允许的 origin，`Idempotency-Key` 匹配正则。
- 超时后的下一次请求：同一 Idempotency-Key，同一原始正文。
- 改标题后的下一次：不同的 Idempotency-Key，不同的 `client_upload_id`。
- 200 `replayed: true` 不写出第二个 docid 文件。

### A8 备份与 manifest

- pending 写到一半让 `fsync` 前的哈希检查失败（测试钩子）：POST 计数 0，退出码 5。
- 成功后 `~/docs/<docid>.html` 的哈希等于源。
- 预先放一个不同哈希的同名 docid 文件：文件字节不变，stdout 含「远程已成功、本地待修复」，退出码 4，POST 只有第一次。
- `manifest.jsonl` 每一行可 `json.loads`，含 §8.4 的全部字段名。
- 状态不在闭集内的测试钩子若存在，不得被生产路径调用。用样例行断言 `status` 属于那六个词。

### A9 错误恢复

- 三条 409 分三个测试：`upload_in_progress` 在注入时钟下等待且键不变；`idempotency_conflict` 只发一次且不生成第二把键；`quota_exceeded` 只发一次且退出码 2。
- 429：第二次请求在记录的睡眠之后，键相同，次数不超过 4（1 次加 3 次重试）。
- 503：次数不超过 6（1 次加 5 次），键相同，最后状态 `remote_unknown`。
- 401：一次，退出码 2。

### A10 外链

- 含 `https://example.invalid/a.js` 的文件：stdout 含 §10.2 第一段的「默认无外网资源」和「不拒绝上传」；源字节不变；POST 发生。
- 只有 `data:` 图片：stdout 含第二段「未发现」。
- `rg -n "警告字段|uploads.py" SKILL.md references` 里，涉及外链的句子必须是「不新增、不修改」。

### A11 报告

- 成功路径的 stdout 五行小标题都在：线上链接、docid、本地副本、manifest、待办。外链提示也在。
- 401 路径的 stdout 与 stderr 不含假 Key。
- dry-run 含「未提交，没有 docid」。

### A12 不改服务端

- 在 `/home/ryan/projects/html-platform/docs-service` 执行 `git status --porcelain`。实施开始前与结束后都为空（或只有实施前已经存在的用户改动，且 diff 与技能无关）。技能仓库的 diff 不得出现在 docs-service 里。
- `rg -n "external_warning|warnings" app/uploads.py` 仍无上传警告字段。本条是抽查「没改」，不是要求实施者去改测试。

### A13 任务完成定义

§13 的 T1–T12 都有对应文件。`python3 -m unittest discover -s tests` 通过。Windows 文件仍是 `verified: no`，除非另附 PowerShell 实验记录。

## 13. 实施任务拆分

按序号做。前一项的验收没过，不开始下一项。全部文件都在 `/home/ryan/projects/html-platform/html-upload/`。不要把测试或脚本写进 docs-service。

每个实现任务先写会失败的测试，再写最少的实现。失败原因必须是行为还没做，而不是语法错误或导入错误。

### T0 开工检查

- 边界：只读查看 docs-service 的 `git status --porcelain`。不提交、不格式化、不改文件。
- 验收：把开工时的 porcelain 输出抄进实施笔记（可以是 `tests/NOTES.md` 里的一段）。结束时再跑一次，多出来的行必须为空。对应 A12。

### T1 `references/protocol.md`

- 内容：§0.1 的差异表、§7 的请求头与正文字段、哈希算法、201/200 字段、P3 错误表里和上传有关的行、大小常量（10 MiB、14046552、幂等键正则、租约 120 秒、确认窗口 7 天与 5 分钟）。
- 边界：只描述现行契约。不写「建议改服务端」。
- 验收：`rg -n "sha256|Idempotency-Key|docs-upload/v1|selectable_for_upload" references/protocol.md` 都有命中。A7 的字段名以此文件为准，代码不得另起一套。

### T2 `references/confirmation.md`

- 内容：§6 的匹配顺序、两种模式、确认文件、退出码 3、422 刷新。
- 边界：不把近似匹配写成自动复用。
- 验收：文件同时含 `existing_default` 与 `explicit`，并含「非交互」和「不自动」`create_confirmed`。对应 A6。

### T3 `references/credentials-posix.md`

- 内容：§3.3 的引导原文、§3.4 已跑通的核心脚本、zsh `read -p` 的 `-p: no coprocess` 差异、tty 版标成待终端复跑（除非实施者已经在真实终端复跑并写了记录）。
- 边界：不写 `read -rsp`。不把 Key 样例写成真密钥。假 Key 只用 `dummy-key-not-real` 这个说法，不要求读者执行到真实家目录。
- 验收：A3 的 rg 条件。

### T4 `references/credentials-windows.md`

- 内容：只有 §3.5 的文件头 `verified: no`、原因、以及未执行的 API 名称 `Read-Host -AsSecureString`。说明技能在 Windows 上因此退出码 2。
- 边界：不写 `icacls` 命令，不写「等效 chmod 600」。
- 验收：A3 的 Windows 两条。没有 PowerShell 就不把 `verified` 改成 yes。这不是未完成，是本阶段的正确结果。

### T5 `references/manifest.md`

- 内容：§8 的路径、六种 status、字段表、`remote_unknown` 恢复、远程成功本地失败的报告句。
- 边界：不把 JSONL 写成数组示例以外的「一个大 JSON」。示例必须是两行独立对象。
- 验收：示例两行都能 `json.loads`。字段名与 §8.4 一致。

### T6 外链文案

- 文件：`references/external-links.md`、`templates/confirm-prompt.txt`。
- 内容：§10.2 的两段原文，以及扫描范围（含「不改正文、不拒绝、不改服务端」）。
- 边界：不写服务端补丁。
- 验收：A10 的字符串能在模板里原样找到。

### T7 `references/report.md` 与 `templates/report.txt`

- 内容：§11 的小标题和状态表。
- 边界：模板不含 Key 占位符。不要 `{{api_key}}` 这类空。
- 验收：A11 的小标题都能在模板里找到。

### T8 `bin/normalize.py` 与测试

- 内容：§6.1。提供 `normalize(value) -> (display, key)`。
- 边界：不请求网络。
- 验收：§6.1 的两个向量，加上超长 81 码位、含 NUL 的失败用例。先看到测试失败再实现。

### T9 `bin/check_html.py` 与测试

- 内容：§5 的字节条件、title、meta、时间与模型分类。输出 JSON 到 stdout，供 `upload.py` 调用或被它导入。
- 边界：不 POST。不改源文件。
- 验收：A5。缺时区与冲突的结果是 `needs_clarification`，不是猜测后的时间戳。

### T10 `bin/match_taxonomy.py` 与测试

- 内容：§6.3 的纯函数。输入是 taxonomy JSON 和调用方意图，输出命中或候选。
- 边界：不把 `ratio >= 0.80` 的候选改成命中。超过 200 条不产生候选。
- 验收：A6 的前两条可以不启 HTTP 就测过。

### T11 `bin/upload.py` 与 `tests/fake_server.py`

拆开做，每段有自己的测试：

1. `/me` 与 taxonomy 客户端。验收 A4。Key 只从环境或 0600 文件来。
2. 确认门。非交互退出码 3。验收 A2 的 force-confirm 与 A6 的哈希。
3. 预备份与 manifest。验收 A8 的「哈希失败则 POST 计数为 0」。
4. POST、哈希、幂等键。验收 A7。
5. §9 的重试。注入时钟。验收 A9。
6. 外链提示与报告模板。验收 A10、A11。

边界：假服务器监听 `127.0.0.1` 的临时端口。`DOCS_ORIGIN` 指向它。生产域名不出现在测试的连接目标里。不跟随重定向。不打印 Authorization。

### T12 `SKILL.md`

最后写，避免指向还不存在的文件。

- 结构对照 html-studio：front matter、一句话职责、然后按 §10.1 的目录写小节：触发条件与边界、输入参数、凭证安全、操作系统分派、GET me 自检、HTML/meta 校验、taxonomy 匹配与确认状态机、提交协议与幂等、备份与 manifest、错误恢复、外链提示、最终报告。每节用几句指向 `references/` 与 `bin/upload.py`，不把协议再抄一份走样。
- 写明：退出码 3 时把 JSON 给人，Agent 不代替用户确认；退出码 4 时不新开逻辑上传。
- 边界：不写生成 HTML 的步骤，不写服务端修改步骤。
- 验收：A1。`python3 -m unittest discover -s tests` 通过。然后做 A12 的 git status。

### 实施者不要做的事

- 不要为了让 Windows 小节「看起来完整」编造命令。
- 不要在技能里默认 `--visibility public`。
- 不要把外链扫描失败变成拒绝上传。
- 不要把 `~/docs` 写到 `/srv/docs`。
- 不要把本方案的验收说成已经在生产环境传过一份真文档。生产联调要人自己准备 Key，不在实施 PR 里进行。

## 14. 对照自检清单

撰写日期 2026-10-20。对照的是 `docs/DESIGN-v3.md` §10.1–§10.5，以及任务要求的 13 项。缺一行就不能把方案交给实施。

### 14.1 对 §10.1–§10.5

| 设计小节 | 要求 | 本方案 | 勾 |
|---|---|---|---|
| §10.1 | 只做检查、Key、taxonomy、元数据、提交、备份、报告 | §1.3 | 有 |
| §10.1 | 不生成 HTML、不写服务端、不重建索引、不改线上配置 | §0.2、§1.2、§1.3、T0、A12 | 有 |
| §10.1 | 输入含 title/project/type/生成时间/模型/ACL、`force-confirm`、`dry-run` | §2.1 | 有 |
| §10.1 | 默认 private，公开必须显式 | §2.1 | 有 |
| §10.1 | SKILL.md 含触发、参数、凭证、OS、me、HTML、状态机、提交、备份、错误、报告 | §1.4、T12 | 有 |
| §10.1 | 非交互遇强制确认则列出待确认并暂停，超时不是批准 | §2.3、§6.6、A2 | 有 |
| §10.2 | 顺序：环境变量 → `~/.config/docs/api-key`（600）→ 引导 | §3.1、§3.2 | 有 |
| §10.2 | 环境变量无效不回退 | §3.1、A3 | 有 |
| §10.2 | 权限不符则停止 | §3.2 | 有 |
| §10.2 | 不回显 Key | §3.2、§3.6、§11、A3、A11 | 有 |
| §10.2 | bash `read -rs`；zsh 验证差异；PowerShell `Read-Host -AsSecureString`；NTFS ACL；不伪称 chmod 等效 | §3.4、§3.5。bash/zsh 核心已用假 Key 跑通。Windows 命令未编造 | 有 |
| §10.2 | 引导文案含登录、控制台路径、明文一次、不贴聊天、不进 history | §3.3 | 有 |
| §10.2 | 本地落盘，不用 Publishable Key，不用 OpenClaw secrets | §3.1 | 有 |
| §10.2 | 先 GET `/api/v1/me`，401/403 停止 | §4 | 有 |
| §10.3 | meta `plans:completed` / `plans:worker`；显式参数冲突要确认 | §5.3 | 有 |
| §10.3 | 有时区转 UTC；缺时区询问；不用机器时区；冲突与不明 worker 不猜；不用上传时间补生成时间 | §5.3 | 有 |
| §10.3 | 稳定 id、规范名、别名；近似只建议；新建才 `create_confirmed` | §6.3、§6.5 | 有 |
| §10.3 | 齐全则 `existing_default`；`force-confirm` 仍要显式确认 | §6.4 | 有 |
| §10.3 | 新建或缺失一次汇总；未确认不提交；改口后重新确认 | §6.4、§6.5 | 有 |
| §10.3 | taxonomy 变化：能命中则复用；422 刷新；不循环自动确认 | §6.7 | 有 |
| §10.3 | 两种模式不能说成每次必问或新建默许；dry-run 不 POST、无 docid | §2.2、§6.4、§6.6 | 有 |
| §10.4 | 本地根 `~/docs/<docid>.html`，不是 `/srv/docs`；原文件不动 | §8 | 有 |
| §10.4 | POST 前 `.pending/<client_upload_id>.html` 原子保存、哈希、元数据与幂等键、仅本人可读 | §8.2 | 有 |
| §10.4 | 成功后原子落到 docid；哈希相同则复用，不覆盖冲突 | §8.3 | 有 |
| §10.4 | 失败无 docid 则留 pending；不伪造 docid；不回滚远程；预备份失败不发网络 | §8.2、§8.3 | 有 |
| §10.4 | `manifest.jsonl` 追加、一行 JSON、锁、同步；字段含设计表全部 | §8.4 | 有 |
| §10.4 | 成功行用服务端规范化结果；权限只是上传快照 | §8.4 | 有 |
| §10.4 | `remote_unknown` 用原请求和原键取回 docid | §8.5、§9.1 | 有 |
| §10.4 | 远程成功本地失败要说「远程已成功、本地待修复」，不新建第二篇 | §8.3、§8.5、§11 | 有 |
| §10.4 | 日志写不出时告警，事后 `reconciled`，不声称已入账 | §8.4 | 有 |
| §10.5 | JSON 协议、原字节 Base64、标题来自参数或 `<title>`、不用文件名 | §5.2、§7.1 | 有 |
| §10.5 | 请求哈希；仅同一请求复用幂等键 | §7.2、§7.3 | 有 |
| §10.5 | 401/403 引导重建或修正；413 提示体积；422 进确认；429 按 Retry-After 有界；503 与网络失败指数退避加抖动 | §9.1 | 有 |
| §10.5 | 409 分开 processing、内容冲突、配额 | §9.1、§9.2、A9 | 有 |
| §10.5 | 报告含链接、docid、本地副本、manifest 状态、待办；绝不输出 Key | §11 | 有 |

### 14.2 对任务要求的 13 节

| 要求 | 位置 | 勾 |
|---|---|---|
| 1 触发条件与边界 | §1 | 有 |
| 2 输入参数、默认与校验 | §2 | 有 |
| 3 凭证安全与 OS 分派 | §3 | 有 |
| 4 GET `/api/v1/me` | §4 | 有 |
| 5 HTML/meta 校验 | §5 | 有 |
| 6 taxonomy 与确认状态机 | §6 | 有 |
| 7 提交协议与幂等 | §7 | 有 |
| 8 本地备份与 manifest | §8 | 有 |
| 9 错误恢复 | §9 | 有 |
| 10 S10 外链文案，且不改服务端 | §10 | 有 |
| 11 最终报告 | §11 | 有 |
| 12 验收标准 | §12 的 A1–A13 | 有 |
| 13 文件级任务、边界与验收 | §13 的 T0–T12 | 有 |

### 14.3 撰写时核对过、实施时不得当成已联调

- bash 5.2.21 与 zsh 5.9：§3.4 的核心脚本用假 Key 跑通，文件 mode 600。
- zsh `read -p` 报 `-p: no coprocess`。
- `/dev/tty` 在撰写进程里是 `ENXIO`，tty 版未复跑。
- 本机没有 PowerShell，所以没有 Windows 命令。
- 没有对 `https://docs.zh0uyuan.com` 发起上传。生产行为以代码与 `docs/P3-design.md` 为准。
- `GET /api/v1/me` 不返回有效期。技能只记录「未返回」，不补一个假到期日。
