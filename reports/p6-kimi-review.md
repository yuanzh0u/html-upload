# P6 ③-b html-upload 安全复审报告（Kimi，第④道闸门）

复审对象：`/home/ryan/projects/html-upload` 首轮交付（`SKILL.md`、`bin/`、`references/`、`templates/`、`tests/`）。
基线：`PLAN.md`；协议基线 `docs/DESIGN-v3.md` §10（已与 `app/uploads.py` 现行实现抽验比对）。
口径：只读审计，未修改任何被审文件；未访问外网；仅运行 `python3 -m unittest discover -s tests`（56 项，OK，3 项跳过）与若干本地纯函数探针。

## 总体结论

**通过**（附 3 条低严重度发现与 4 条提示，无阻断性缺口，建议下一轮顺手修复低危项，不构成打回）。

六项复审清单逐项均有代码证据支撑；凭证链路（来源顺序、不回退、0600/0700、符号链接拒绝、输出脱敏）经本地探针实测符合基线。请求哈希算法与现行服务端 `app/uploads.py` 逐字段一致。确认状态机不存在绕过路径，dry-run 无任何 POST 路径。

## 逐项结论与证据

### 1. 凭证安全 — 通过

- 来源顺序与环境变量不回退：`bin/upload.py:122-136`。`DOCS_API_KEY` 已设置（含空串）即定来源；无效时报「来源是环境变量，该来源无效；不回退」并停止。本地探针实测：空串环境变量未回退到 0600 文件。
- Key 不进日志/报告/manifest/异常/argv：入口 `Parser.error` 不回显 argv（`upload.py:53-56`）；所有输出经 `emit()` 做 token 子串脱敏（`upload.py:662-676`）；输入、taxonomy、/me、HTML 正文、请求体、服务端成功响应、422 details 均做「含凭证即停止且不回显」检查（`upload.py:466,551`（`valid_success` 内）、`730,766,714,775`）；meta/manifest 只存幂等键与哈希，不含 API Key（`upload.py:435-438,405-411`）；`finally: del token`（`upload.py:795`）；`http.client` 调试类属性置 0（`upload.py:144,151`）。测试侧断言假 Key 不出现在输出（`tests/test_upload.py:34,65`）。
- 权限位不可被符号链接绕过：`private_read` 先 `lstat`（符号链接、非 0600、非本 euid 即拒），再 `O_NOFOLLOW` 打开并 `fstat` 比对 dev/ino（`upload.py:110-119`）；父目录 `lstat` 恰 0700 且非符号链接（`upload.py:102-107`）。探针实测：符号链接 Key 文件被拒且未读内容；0644 文件被拒且错误消息不含文件内容。
- 引导文案：逐字等于基线文案，指向 `/console/keys`，警告勿贴聊天、勿进 shell history（`upload.py:32`；`references/credentials-posix.md` 开头）。POSIX 文档含 `IFS= read -rs`、无 `read -rsp`、假值只用 `dummy-key-not-real`、明确不教 `export` 进启动文件；tty 版诚实标「待终端复跑」。Windows 文件 `verified: no`，技能在 `os.name=='nt'` 时退出 2（`upload.py:684`），未编造未验证命令。

### 2. 注入面 — 通过（1 条低危）

- HTML 解析用 `html.parser`（`bin/check_html.py:64-141`），非正则解析 HTML；注释/script/template 内的 title 与 meta 被排除；不执行脚本。CSS 扫描仅对 `<style>` 文本与 `style` 属性用正则提取 `url(`/`@import`，属扫描而非解析 HTML 结构，边界可接受。
- title/meta 值进入请求与 manifest 前均经校验：title 拒 Cc/Cf/Cs（`check_html.py:39-41`）；生成时间须 fullmatch RFC3339 并转 UTC（`check_html.py:16-27`）；worker 经 `normalize` 拒控制字符（`bin/normalize.py:6-15`）。JSON 请求统一 `json.dumps`，taxonomy 名称无注入面。
- manifest JSONL：写入走 `canonical()`（`json.dumps` 转义换行/控制字符），追加前校验尾行换行完整性，独占锁 + fsync（`upload.py:395-424`）；状态值限闭集（`upload.py:30,396`）。换行注入不可能。
- **低危**：外链 URL 只剥空白与引号（`check_html.py:75-78`），未滤控制字符；`report()` 将 URL 原样拼进人类可读报告（`upload.py:183-188`）。探针证实 HTML 属性中的 `\x1b` 原样进入报告文本（退出码 3/dry-run 的 JSON 路径因 `json.dumps` 转义不受影响；受影响的是纯文本报告行）。详见发现 F-1。

### 3. 路径安全 — 通过

- docid 注入不可能：`valid_success` 要求 `id` fullmatch `[0-9a-f]{32}` 且 `url` 恰为 `<origin>/<id>.html`（`upload.py:461-466`）。探针实测 `../etc/cron` 被拒。本地路径仅由该 id 拼接（`upload.py:489`）。
- `client_upload_id` 本地生成（`secrets.token_hex(16)`），外部来源（确认文件、`--resume`）均强制 fullmatch 32 位小写 hex（`upload.py:81,289`），`.pending`/`.confirm` 路径拼接无注入面。
- 原子替换与 TOCTOU：独占 `O_EXCL|O_NOFOLLOW|0600` 写入 + `fsync` + 目录 `fsync`（`upload.py:217-225`）；`replace_json` 随机临时名 + `os.replace`（`upload.py:228-235`）；收尾在 `.finalize.lock` 内做存在性、哈希双重校验再 `os.replace`，同哈希不覆盖、异哈希不覆盖不删除（`upload.py:487-514`）。备份目录 0700 限定同 euid，残余 TOCTOU 需同 uid 攻击者，出威胁模型。
- 恢复档案交叉校验：meta/request/pending 三者哈希、origin、幂等键格式互绑（`upload.py:634-650`），篡改任一文件即拒绝恢复。

### 4. 传输安全 — 通过

- 固定站点：`origin_from` 仅放行 `https://docs.zh0uyuan.com` 或回环测试 origin，其余退出 2；无 `base-url` 参数（`upload.py:91-99`）。
- 证书校验不可禁用：`HTTPSHandler` 每次新建 `ssl.create_default_context()`（`upload.py:162`）；全库 grep 无 `unverified`/`CERT_NONE`/`check_hostname=False`。
- 重定向不转发 Key：`NoRedirect.redirect_request` 恒返回 `None`，3xx 记 `remote_unknown`，不读 Location（`upload.py:139-141`；`tests/test_transport.py:39` 断言）；`ProxyHandler({})` 关闭环境代理；无 Cookie、禁分块、Content-Length 本地先算并限 14046552。
- 超时：连接 10s、读 30s（`upload.py:147,154,172`）；响应体上限 8 MiB（`upload.py:176-177`）。
- 幂等键 128 bit 随机（`token_hex(16)`），与 `client_upload_id` 分别生成；请求哈希算法与现行服务端逐字段一致——抽验 `app/uploads.py:59-60,99-102`：`required` 去掉 `html_base64` 再加 `acl`，与客户端 `request_hash`（`upload.py:370-371`）的「全部键减 `html_base64`」集合相同（客户端正文键恰好为 required+acl），前缀 `docs-upload/v1\n` 与序列化参数一致。哈希仅作同一性对账，非安全边界，无可伪造/碰撞问题。
- 范围声明：真实 TCP/TLS 握手未端到端验证（`tests/NOTES.md` 声明沙箱禁 socket，传输用内存桩 + `test_transport` 验证 opener 构成），3 项跳过测试均属此类。属实施环境限制，已如实披露。

### 5. 确认绕过 — 通过

- `existing_default` 门：`explicit = force_confirm or 任一 item 未命中/冲突`（`upload.py:263`）；explicit 且无确认文件时一律存计划、退出 3（`upload.py:750-753`），不存在交互式自批路径。构造的 taxonomy 响应无法把「未命中」伪造成「命中」——命中要求规范化键全等或活动别名命中，且 `selectable_for_upload` 为 false 的条目被排除（`bin/match_taxonomy.py:11,29-36`）；近似名只进 candidates。恶意服务端本身在信任模型内（固定 TLS 站点）。
- `force-confirm` 真强制：置 explicit 后必须提供 `accepted: true` 且哈希绑定的确认文件；新建项仍须逐项 `create/name/create_confirmed`（`upload.py:293-297,317-330`），笼统 yes 不够（`references/confirmation.md:100`）。
- 确认绑定三重校验：确认文件 `client_upload_id`+`plan_sha256` 对得上私有快照，快照自哈希再验，且重放时当前 HTML/参数/身份/taxonomy 重新生成的计划哈希须相等（`upload.py:283-297,748-749`）。
- dry-run：在 `prepare`/POST 之前返回 0（`upload.py:738-744`），不读确认文件发 POST；`--resume` 分支同样被 `not args.dry_run` 挡住（`upload.py:686`）。测试断言 POST 计数 0（PLAN A2 锚点，套件通过）。
- 422 刷新不自动确认：`TaxonomyPause` 后重新拉 taxonomy、并入净化后的服务端 items，重新退出 3（`upload.py:594-595,772-780`）；`merge_server_items` 白名单字段、长度截断（`upload.py:609-631`）；测试断言第二次请求仍无 `create_confirmed`（`tests/test_upload.py:272,277`）。

### 6. 可用性副作用 — 通过（1 条提示）

- 重试有界且跨进程持久：四类桶 processing≤3、rate≤3、transient≤5、internal≤1，计数写 `.meta.json`（`upload.py:435-436,569-592`）；429 服从 `Retry-After`（解析仅整数、单次封顶 120s + 0–250ms 抖动，`upload.py:530-532`）；503/网络失败全抖动指数退避上限 30s；`sleep_until` 落盘，恢复时继续遵守（`upload.py:537-539`）。单逻辑上传全生命周期最多 12 次受控重试，无放大效应。
- 请求体上限本地先检（`upload.py:434`）；预备份哈希不符则零 POST（`upload.py:443-447`，测试锚点 A8 通过）。
- **提示**：`~/docs` 无容量配额与轮换；失败 pending 按设计永久保留（PLAN §8.3）。单文件 ≤10 MiB，增长与使用量线性，但长期运行需人清理，建议在文档中补一句家务说明。见 F-7。

## 发现列表

### F-1（低）外链 URL 控制字符进入纯文本报告

- 现象：`bin/check_html.py:75-78` 的 `url()` 只剥空白与引号；`bin/upload.py:183-188` 的 `report()` 把 URL 列表原样拼进报告，纯文本报告行（非 JSON 包装路径）不加转义打印。
- 证据：本地探针，HTML 含 `<img src="https://example.invalid/\x1b[31mRED.png">`，`report()` 输出含裸 `\x1b`。
- 利用场景：用户上传一份他人提供的 HTML，其中外链 URL 含终端转义序列，Agent 转述报告时终端被注入控制序列（着色/清屏/伪装文本）。需要受害者主动上传攻击者文件，影响限于终端显示。
- 修复建议：`url()` 收录前拒绝含 Cc/Cf 字符的地址，或 `report()` 拼 URL 时做控制字符转义。

### F-2（低）深度嵌套 JSON 输入触发未捕获 RecursionError

- 现象：`confirmation_input`（`upload.py:287,292`）与 `load_pending`（`upload.py:638-641`）的 `json.loads` 未捕 `RecursionError`（它是 RuntimeError 子类，不在现有 `(OSError,ValueError,KeyError,TypeError)` 网内）。
- 证据：本地探针，10 万层嵌套的确认文件 → 未捕获 RecursionError，traceback 落到 stderr，退出码 1（不在约定退出码表内）。对照：网络响应路径 `http_request` 已捕 RecursionError（`upload.py:179`），此处不一致。
- 利用场景：畸形确认文件导致未受控崩溃；traceback 不含 Key，但破坏「退出码语义可机判」的契约。
- 修复建议：上述两处 `except` 增加 `RecursionError`（或在调用前限制嵌套深度）。

### F-3（低）凭证无最小长度校验，极短 token 令脱敏误伤输出

- 现象：`credential()` 只校验非空、可打印 ASCII、总长 ≤512（`upload.py:133-134`）。1 字符 token（如 `a`）通过校验；随后 `emit()` 的 `value.replace(token,'[已隐藏]')`（`upload.py:664,672,675`）会把输出中所有该字符替换掉，报告文本错乱。
- 证据：本地探针，`credential({'DOCS_API_KEY':'a'}, home)` 返回成功。
- 评估：无机密泄漏（方向相反：过度脱敏）；可用性/误导性问题，且此类 token 必然 401。
- 修复建议：加最小长度（如 ≥16）或要求 token 匹配站点实际签发格式。

### F-4（提示）`--resume` 与 `--dry-run` 同用时 resume 被静默忽略

- 现象：`upload.py:686` 的 `if args.resume and not args.dry_run` 使该组合落入普通 dry-run 分支，`--resume` 值无任何提示地被忽略。
- 评估：行为安全（不 POST、不写盘），但调用方可能误读为「恢复了」。建议参数校验阶段拒绝该组合（退出 2）。

### F-5（提示）服务端并入党选值未限长

- 现象：`merge_server_items` 的 items 字段限长 200（`upload.py:618`），但 candidates 的 `id/name/score` 值未限长/限字符（`upload.py:619`）；`append_event` 成功行的 `title` 取自服务端 `normalized_metadata` 且 `valid_success` 未校验该 title（`upload.py:406,467-476`）。
- 评估：两处写入均经 `json.dumps`，结构安全；服务端为固定 TLS 站点，属信任模型内残余。建议顺手加长度上限与 `normalize` 校验。

### F-6（提示）传输层未经真实 TCP/TLS 端到端验证

- 现象：`tests/NOTES.md` 如实声明实施沙箱禁 socket，HTTP 行为用内存桩验证；套件 3 项跳过均属此类。
- 评估：TLS 上下文、禁重定向、禁代理由代码审查与 `test_transport` 的 opener 构成断言覆盖；建议生产联调时补一条真实握手用例。不构成打回。

### F-7（提示）本地备份无容量管理

- 现象：`~/docs` 与 `.pending/` 只增不减（设计使然，PLAN §8.3 禁止删除）。
- 建议：在 `references/manifest.md` 补一句家务说明（定期归档/清理由人执行），或后续版本加 `--gc` 类手动命令。

## 复核范围声明

- 审过的文件：`PLAN.md`、`SKILL.md`、`bin/normalize.py`、`bin/check_html.py`、`bin/match_taxonomy.py`、`bin/upload.py`（799 行全量）、`references/` 七份、`templates/` 两份、`tests/test_transport.py` 全量，其余测试文件以断言抽查 + 全量运行覆盖。
- 只读参考：`/home/ryan/work/docs-service/app/uploads.py`（仅 `validate`/哈希段）、`docs/DESIGN-v3.md` §10 全节。未修改 docs-service 任何文件。
- 验证手段：静态逐行审读；`python3 -m unittest discover -s tests`（56 项 OK、3 跳过，跳过原因见 F-6）；7 个本地纯函数探针（空环境变量不回退、符号链接拒绝、0644 拒绝且不回显内容、恶意 docid 拒绝、1 字符 token 接受、ESC URL 进报告、嵌套 JSON 崩溃），均为不联网的进程内调用。
- 未覆盖：生产站点真实联调（任务纪律禁止外网）；Windows 路径（`os.name=='nt'` 即退出 2，代码路径极短，已静态确认）；HTML 解析器对极端畸形输入的 fuzz。
