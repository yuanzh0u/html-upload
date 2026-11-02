---
name: html-upload
description: >-
  把已经存在的合规 HTML 文件只读上传到 docs 服务（https://docs.zh0uyuan.com）。
  做上传前检查、API Key 引导、taxonomy 确认、提交、本地备份和结果报告。
  用户要「上传这个 HTML」「传到 docs」「html-upload」时使用。
  不生成 HTML，不改 docs-service，不重建索引。
---

只读检查已有本地 HTML，确认元数据后上传到 docs，保存本地副本并报告结果。

## 触发条件与边界

用户给出本地 `.html` 路径，要求上传、发布到 docs，或点名 html-upload 时使用。没有文件时停止并索取路径；不临时生成文件。

不生成 HTML、不改服务端、不重建索引。不要建设 html-trans、html-spec；不调用或修补 html-studio。不修改 docs-service 的任何文件、上传校验、CSP、sandbox、索引或线上配置；不处理 M-3、A-15/M-6、P7。不接受 ZIP、服务端路径、用户指定 docid 或 storage_key；原 HTML 不修改、不删除。

生产 origin 固定为 `https://docs.zh0uyuan.com`。拒绝对话里的其他上传域名，不跟随重定向。测试可通过进程环境 `DOCS_ORIGIN` 指向 HTTP/HTTPS 的 `127.0.0.1` 或 `localhost`（可带端口）；拒绝其余值。不提供 base-url 参数。

只调用 `/api/v1/me`、`/api/v1/taxonomy?include=aliases,counts` 和创建文档接口；不查文档列表、不查通讯录、不改已有 ACL、不管理用户。请求 ACL 只作用于这一次创建。

## 入口与流程

从技能目录运行，使用 Python 3.12 标准库，不安装第三方包：

```bash
python3 bin/upload.py /absolute/path/existing.html --project-name '用户指定的项目' --type-name '用户指定的类型' --dry-run --non-interactive
```

`bin/upload.py` 是唯一上传入口，不使用另一条 curl 上传捷径。完整顺序：读原字节 → 本地校验 → 取 Key → GET me → 拉 taxonomy → 精确匹配 → 必要时汇总暂停等待用户确认 → 外链提示 → 预备份与 prepared manifest → POST → 验证响应 → 本地收尾 → 报告。dry-run 也做本地校验、凭证自检、taxonomy 与外链扫描。

本地模块：[normalize.py](bin/normalize.py)、[check_html.py](bin/check_html.py)、[match_taxonomy.py](bin/match_taxonomy.py)。它们不请求网络，HTML 检查器可独立输出 JSON。

## 输入参数

`html` 位置参数必填，为本地普通 `.html` 文件。未知参数退出 2。用 `python3 bin/upload.py --help` 查看入口。

| 参数 | 默认与规则 |
|---|---|
| `--title` | 无默认；与 HTML title 不同则澄清，禁止从文件名补标题 |
| `--project-id` / `--project-name` | 互斥；运行时匹配 taxonomy |
| `--category-id` / `--category-name` | 互斥；只供新建项目，不默认「其他」 |
| `--type-id` / `--type-name` | 互斥；type 没有 category |
| `--model-id` / `--model-name` | 互斥；不猜执行者所用模型 |
| `--generated-at` | 带时区 RFC3339；缺失、缺时区或冲突须澄清 |
| `--visibility` | `private`；可选 restricted/public，公开必须显式传入 |
| `--acl-user` / `--acl-group` | 可重复，仅 restricted；最多 32 用户、16 组，至少一个目标 |
| `--force-confirm` | 整单确认，不改变匹配，不默认批准新建 |
| `--dry-run` | 优先于提交；只展示计划，不写 pending、确认快照或 manifest，不产生 docid |
| `--confirm-file` | 恢复暂停计划的用户确认 JSON，按下文核验 |
| `--non-interactive` | Agent 调用时显式使用；stdin 非 tty 也视为非交互 |
| `--resume CLIENT_UPLOAD_ID` | 从私有 pending 恢复原请求；可信成功档案只做本地收尾 |

标题若显式给出，必须为 1–200 码位，与 strip 后相同，不含 Cc/Cf/Cs。taxonomy id 规则为 `[0-9A-Za-z_-]{1,64}`。private/public 的 ACL 列表必须为空；ACL 仅接受已有 user id/group id，不把显示名、手机或邮箱转换成 id。禁止将 me.user_id 加入 acl-user；不调用 directory 猜 id。

## 凭证安全

来源顺序严格为：已设置的 `DOCS_API_KEY`（空字符串也不回退）→ `~/.config/docs/api-key` → 打印引导并退出 2。本进程不在聊天里索取 Key，失败后不切换来源。不用 OpenClaw secrets、Publishable Key、shell history 或聊天粘贴。

文件读取前 lstat：普通文件、当前 euid、恰好 0600、非符号链接；父目录 `~/.config/docs` 恰好 0700 且非符号链接。权限不符则本轮停止，不改好权限后当轮继续。规整只去尾部 CR/LF（兼容 Windows CRLF），token 最短 16；其余空白、空内容、超长值拒绝。

Key 不进入 argv、URL、输出、日志、异常、pending、manifest、报告或测试快照。不显示前缀、后缀、任何片段；不打印请求头，不启用 HTTP 调试。不教用户把明文 export 写到 shell 启动文件。只有当前进程内存中的 token 进入 Authorization，请求完成后释放局部引用。

找不到 Key 时逐字使用：

> 登录 https://docs.zh0uyuan.com，进入控制台 → 我的文档视图 → API Key，创建后明文只显示一次。现行页面地址是 https://docs.zh0uyuan.com/console/keys 。不要把 Key 贴进聊天，不要写进带明文的命令或 shell history。

## 操作系统分派

POSIX 读 [credentials-posix.md](references/credentials-posix.md)。bash/zsh 核心用假值和临时家目录自检；zsh 不使用 bash 的 read 提示参数。真实终端 `/dev/tty` 版仍标「待终端复跑」，不能当作已验证引导。

Windows 先读 [credentials-windows.md](references/credentials-windows.md)。当前 `verified: no`：打印「凭证引导尚未在本 PowerShell 版本验证」并退出 2，不改跑 POSIX、不写 manifest。没有真实 PowerShell 实验就不添加命令或改 verified。

## GET me 自检

预备份和 POST 前先 GET `/api/v1/me`，dry-run 也执行。必须 200、kind 为 api_key，user_id/display_name/role 为字符串，scopes 含 me:read、taxonomy:read、documents:create。角色只展示，不据此调用管理接口。

响应没有 Key 到期时间、上传限额、allow_public。必须输出「服务端未返回 Key 有效期。」和「服务端未返回上传限制。」；不补假字段，不猜 public 是否获准。401/403 不重试、不回退，指向 `/console/keys` 修正或重建。连接 10 秒、读取 30 秒，验证 TLS，无 Cookie，无重定向；细节见 [protocol.md](references/protocol.md)。

## HTML 与 meta 校验

原字节只读，大小 1–10 MiB，严格 UTF-8，拒绝 BOM/NUL；仅去起始空格/TAB/CR/LF 后以 doctype html 或 html 开头，必须含结束 html。符号链接、目录、非常规文件拒绝；字节错误退出 2，不当作元数据澄清。

用 html.parser 提取 title、plans:completed、plans:worker；忽略注释、script/template 内干扰，不执行脚本。相同 meta content 合并，不同 content 不投票、不擅选；参数与 title/meta 冲突均要确认。带时区时间转 UTC 并保留小数秒；缺时区不补机器时区、不用上传时间填生成时间。worker 只是模型名称候选；未命中时询问它是模型名还是执行者名称，不能直接新建。

`metadata_source` 仅有 time/model，值限 html_meta/explicit/confirmed。细节与时间、标题澄清项由 [check_html.py](bin/check_html.py) 产生；匹配与确认规则见 [confirmation.md](references/confirmation.md)。

## taxonomy 匹配与确认状态机

先拉 taxonomy，使用响应 version 和 selectable_for_upload。顺序：稳定 id → 规范名 → 活动别名；命中只送 id。近似名只作候选，不自动复用；候选超过扫描界限时请用户给规范名或 id。不可选项目不能因同名就悄悄新建自己的项目。

全部命中、元数据齐全、无冲突、无 force-confirm 时用 existing_default，new_terms 为空，直接提交。任一新项、缺失、冲突、worker 歧义或 force-confirm 均用 explicit，一次汇总暂停。Agent 必须把退出码 3 的 JSON 给用户，不能代替用户确认，不能将超时、空输入或一句泛泛的 yes 当作逐项新建许可。

确认快照只保存于 `~/docs/.confirm/<client_upload_id>.plan.json`（目录 0700、文件 0600，不含 Key 或 HTML 正文）。确认文件必须绑定同一个 client_upload_id、plan_sha256，accepted 必须为 true；逐项 choices 决定 reuse/id 或 create/name/create_confirmed。标题、时间冲突要选定 value；缺时区要明确 timezone。以同一组原参数加 `--confirm-file` 再运行。格式示例见 [confirmation.md](references/confirmation.md)。

新建时间戳来自恢复时的 UTC 时钟，逐项 new_terms 只记录实际新建词的 kind、规范键、confirmed_at；窗口为过去 7 天到未来 5 分钟。改名、改候选、改输入或 taxonomy 改变后重新确认，不复用旧批准。422 会刷新 taxonomy，把服务端候选/继任 id 并入新清单，再次暂停；不自动 create_confirmed 或自动改挂继任。

## 提交协议与幂等

读 [protocol.md](references/protocol.md)，不要从设计示意另造协议。正文含 sha256 与原字节的标准 Base64；ACL 始终显式发送。使用 urllib 与 JSON，固定 Origin、Content-Length（上限 14046552），禁用分块和重定向。

client_upload_id 与 Idempotency-Key 分别生成，不能共用。请求哈希遵守 docs-upload/v1 算法；原始 POST JSON 保存为 .request.json。超时、503、429、处理中只复用原键、原正文（包含 confirmed_at），不重新序列化。用户改内容或 422 后重新选择才另开逻辑上传。409 idempotency_conflict 停止，禁止为了绕开冲突换键自动再传。

成功响应必须可信：201 首次或 200 重放、32 hex id、同 origin 的精确 url、匹配 sha256、uploaded_at、acl_version 与 normalized_metadata。缺字段或哈希不符按 remote_unknown 处理，不伪造 docid。

## 备份与 manifest

读 [manifest.md](references/manifest.md)。备份根是本机 `~/docs`，不是 `/srv/docs`。POST 前独占写入 .pending 下的 html、原请求和 meta，fsync 并回读验证哈希，再锁定追加 prepared manifest；任一步失败不 POST。目录 0700、文件 0600，拒绝符号链接。

成功后原子移到 `<docid>.html`；已有同哈希副本不覆盖，冲突不覆盖、不删除 pending。manifest 是逐行 JSONL，锁定、flush、fsync，状态只用六个约定值。成功标签与时间采用服务端 normalized_metadata；permission 是上传快照，不代表实时 ACL。

退出码 4 时不新开逻辑上传。恢复入口：

```bash
python3 bin/upload.py /original/source.html --resume CLIENT_UPLOAD_ID --non-interactive
```

恢复从原 request/meta/pending 读原字节与原幂等键，不依赖原源文件仍存在；该位置参数只保留入口形状。不要加改变元数据的参数来修改这次请求。远程结果已可信时不 POST，只做本地收尾并追加 reconciled。远程未知时先自检，再按原请求取回原 docid；预算跨进程保存。修好磁盘后人工运行恢复，不自动无限续跑。

## 错误恢复

按 [protocol.md 的错误恢复表](references/protocol.md) 分支，不混用 409 的处理方式。401/403、413、不可重试 4xx 停止；429 按 Retry-After 有界等待，503/网络失败指数退避加抖动；重试次数和等待时间写 meta。未知结果保留 pending 与原键。远程成功、本地失败必须说「远程已成功、本地待修复」，列链接、docid、pending、目标和 manifest 最后状态，不再创建第二篇。

| 退出码 | 含义与 Agent 动作 |
|---|---|
| 0 | 上传完成，或 dry-run 展示完成；按模板报告 |
| 2 | 参数、文件、凭证或确定拒绝；停止，不换键盲试 |
| 3 | 将单个合法 JSON 清单交给用户，等待逐项确认文件 |
| 4 | 远程未知或远程成功、本地未完成；原请求恢复或仅本地修复 |
| 5 | 预备份/manifest 失败且本次 POST 未发；先修本地磁盘权限 |

## 外链提示

读 [external-links.md](references/external-links.md)。只扫描，不探测 URL，不改正文、不拒绝上传、不改服务端。外链相关响应警告字段不新增、不修改。M-3 不在此技能处理。

确认清单与最终报告必须整段抄用 [confirm-prompt.txt](templates/confirm-prompt.txt) 对应文案，不改写。有命中时列最多 20 个 URL 与总条数；无命中也给出固定提示。提示本身不增加强制确认。

提交时 stdout 先输出 JSON 的 external_links 字段并 flush（POST 前可见），随后补齐结果为一个合法 JSON；report 字符串承载报告小标题。这样 422 刷新时 stdout 仍是可解析的单份确认计划，不能把前缀当作完整结果。

## 最终报告

只填 [report.txt](templates/report.txt)，按 [report.md](references/report.md) 的状态表转述。成功、失败、暂停和 dry-run 都保留状态、线上链接、docid、本地副本、manifest、外链提示、待办；缺项写「无」。dry-run 必须说「未提交，没有 docid」。JSON 结果中的 report 为完整报告文本，self_check 为允许展示的自检字段。

报告与 Agent 转述不包含 API Key、Authorization、配置文件内容或幂等键；幂等键仅留在私有 meta 中，恢复由技能读取。日志写不出就说「日志没写上」，不能声称事件已入账。
