# 本地备份与 manifest

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

客户端不自动清理 `.pending` 的请求与元数据档案（只增不减）；请定期人工核对终态后归档，保留未完成上传的恢复材料。

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

## JSONL 两行示例（合成值）

```jsonl
{"schema_version": 1, "event_id": "00000000000000000000000000000000", "client_upload_id": "11111111111111111111111111111111", "attempt": 1, "status": "prepared", "docid": null, "title": "示例", "project": null, "type": null, "generated_at": null, "generated_model": null, "pending_reasons": ["等待上传结果"], "permission": {"visibility": "private", "users": [], "groups": []}, "backup_filename": ".pending/11111111111111111111111111111111.html", "uploaded_at": null, "attempted_at": "2026-10-20T01:02:03.000000Z", "sha256": "2222222222222222222222222222222222222222222222222222222222222222", "idempotency_key": "33333333333333333333333333333333", "request_hash": "4444444444444444444444444444444444444444444444444444444444444444", "error_code": null}
{"schema_version": 1, "event_id": "55555555555555555555555555555555", "client_upload_id": "11111111111111111111111111111111", "attempt": 1, "status": "uploaded", "docid": "66666666666666666666666666666666", "title": "示例", "project": {"id": "project_demo", "name": "示例项目"}, "type": {"id": "type_demo", "name": "示例类型"}, "generated_at": "2026-10-20T00:00:00Z", "generated_model": {"id": "model_demo", "name": "示例模型"}, "pending_reasons": [], "permission": {"visibility": "private", "users": [], "groups": []}, "backup_filename": "66666666666666666666666666666666.html", "uploaded_at": "2026-10-20T01:02:04.000000Z", "attempted_at": "2026-10-20T01:02:03.000000Z", "sha256": "2222222222222222222222222222222222222222222222222222222222222222", "idempotency_key": "33333333333333333333333333333333", "request_hash": "4444444444444444444444444444444444444444444444444444444444444444", "error_code": null}
```

恢复入口：`python3 bin/upload.py 源.html --resume CLIENT_UPLOAD_ID`。客户端从 0600 meta 和 request 读取原正文与原幂等键；远程结果已可信则仅做本地收尾，不 POST。不要手工更换键。
