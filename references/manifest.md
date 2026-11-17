# 本地记录与恢复

只读源 HTML，不留成功正文副本。上传与替换发请求前，工具先保存可恢复的待处理档案；失败退出 5 时没有发出该写请求。

```text
~/docs/v2/                         # 本人所有，0700，拒绝符号链接
  manifest.jsonl                  # 0600，只追加事件，不存正文
  .manifest.lock                  # 0600，保护事件追加
  .pending/                       # 0700
    <client_upload_id>.request.json # 0600，原始请求字节（含正文编码）
    <client_upload_id>.meta.json    # 0600，恢复元数据与幂等键
```

不修改 `~/docs/` 下其他旧记录。Key 不得出现在 manifest、pending、上传内容或其他输出里；含当前 Key 的内容会停止处理，先移除敏感内容再试。

## manifest 字段

每行是独立 JSON 事件，同一 client_upload_id 可能有多行；不是一行对应一篇文档。

| 字段 | 含义 |
| --- | --- |
| schema_version | 固定 2 |
| event_id | 本次事件随机 ID |
| client_upload_id | 本次上传/替换的客户端 ID，32 位小写十六进制，供 resume 使用 |
| op | upload 或 replace |
| status | 下述五种状态之一 |
| doc_id | 文档 ID，1–128 位 ASCII 字母、数字、下划线或连字符；新上传准备阶段为 null |
| url | 确定成功的文档链接，否则 null |
| title | 文档标题；替换准备阶段是占位描述「替换文档」 |
| sha256 | HTML 原始字节的 SHA-256 |
| at | 事件 UTC 时间，RFC 3339 |
| error | 明确拒绝的错误码，或正文版本已变化时的 superseded；其余通常为 null |

## 五种状态

| 状态 | 含义 |
| --- | --- |
| prepared | 已完成本地预备份，准备发送 |
| uploaded | 上传已确认成功 |
| replaced | 替换已确认成功 |
| remote_unknown | 写入结果未知，需恢复或核对 |
| failed | 远端明确拒绝，或补发替换时发现正文版本已变化（error=superseded），不再发送 |

准备档案元数据包括 client_upload_id、op、doc_id、idempotency_key、sha256、title、prepared_at（Unix 时间秒）、origin（准备请求时的目标站点）、content_updated_at（替换在准备之前查询的正文版本；上传为 null）。request.json 保存完整原请求字节，不得手工编辑或重新序列化。

## 档案删除与 resume

成功或从未出现未知尝试的远端明确拒绝后，先追加确定事件，再删除两份 pending 档案；无长期正文副本。结果未知或远端成功但本地记录失败时保留档案用于恢复。若清理文件失败可能残留档案；残留不表示远端失败，也不要据此重复上传。manifest 只追加，不随成功删除。

使用输出中的 `python3 bin/docsctl.py resume CLIENT_UPLOAD_ID`；恢复会先核对档案记录的 origin 与当前站点。站点不一致、缺失或为空时，退出 2，提示「请在原来的环境里补发」，不联网并保留档案；不要手工补填或修改 origin。恢复会检查档案完整性；上传补发先查询清单（q 为标题，uploaded_from 为 prepared_at 往前 48 小时的 UTC 时间），沿用 list --all 的分页校验和 1000 页上限，必须读完全部页。标题完全相同且 size_bytes 等于解码后 HTML 正文字节数时，当作已经上传，记 uploaded、删除两份档案、退出 0，报告「链接：<url>」与「补发前在清单里找到了同一篇，没有重复发送」，不发送 POST；data 为清单元数据，不含分享报告，不推断当前分享范围。查询失败、分页异常或到达页数上限仍未完整读取时，记 remote_unknown、退出 4，保留档案、client_upload_id 和 resume_command，不得继续发送。查无匹配才在原期限内重发完全相同的字节和原幂等键。档案缺失或被改动退出 4，停止并核对，不从源文件重建档案。

替换在准备档案前先读取文档元数据，保存当时的 content_updated_at。补发替换前再次读取；与档案不一致时，记 failed、error=superseded，删除两份档案，退出 2，不发送替换请求。报告为：

> 这篇文档在准备这次替换之后被改动过（可能正是这次替换已经生效，也可能是之后的另一次替换）。为避免覆盖较新的内容，不再补发；请用 get 查看当前状态，需要时重新执行 replace

档案未记录有效的准备时正文版本时，无法安全判断，退出 2 并保留档案；不要手工补填当前版本。准备时无法取得有效版本则不建档、不发送；补发时无法取得可信的当前版本则不发送，记 remote_unknown、退出 4，并保留档案、client_upload_id 和 resume_command。补发前账户查询（包括契约版本检查）失败也按此处理。

同一请求若有网络中断、暂时性 5xx 或不可信响应，后续明确错误仍按 remote_unknown 保留档案，不能当作请求从未生效。有效 pending 补发沿用先前的不确定性，即使本轮首次写请求被明确拒绝也退出 4；需人工 list/get 核对；到期后按报告处理。后续取得可信成功响应才记确定成功并清档。

从 prepared_at 起满 24 小时不再自动重发；上传补发仍先完成上述只读清单查找，找到则按成功处理，未找到才按超期处理：每次实际内容请求（包括重试）前都检查同一个原始期限；版本检查、请求或重试等待期间到期也会停止，记 remote_unknown、退出 4 并保留档案。服务端幂等记录只有 24 小时，再发可能另建一篇或再次替换。先 list/get 核对实际结果；只有用户确认另起一篇才 upload --new。恢复成功、明确拒绝、仍未知时分别按原操作的结果处理。其他维护命令不使用这些上传档案；结果未知时 get 核对。
