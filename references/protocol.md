# 现行上传协议

按这个顺序读。后面的协议事实覆盖前面的示意：

1. 已上线的实现与锁定契约：`app/uploads.py`、`app/upload_taxonomy.py`、`app/p3_common.py`、`app/http_app.py`、`app/document_queries.py`、`app/apikeys.py`、`docs/P3-design.md` §1.1 与 §2.2、`docs/P4-design.md` 的 taxonomy seed 与 `/console/keys`。
2. 技能行为：`docs/DESIGN-v3.md` §10.1–§10.5、§5、§11，以及 §9.2「默认无外网资源」。
3. 外链提示拍板：`docs/P5-design.md` H-3 与 S10。结论是提示放在 P6 技能文案。外链相关上传响应警告字段不新增、不修改，不把外链改成拒绝。
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

常量：解码后 10 MiB；JSON 上限 14046552；租约 120 秒；新词确认窗口为过去 7 天到未来 5 分钟。taxonomy 使用响应中的 selectable_for_upload，不可选项目不算命中。

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
