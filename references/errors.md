# 服务端错误与转述

错误体为 `{error, message, request_id}`；按工具 report 原样转述，保留请求编号，不输出凭据或服务端回显的敏感内容。以下建议与 client.ADVICE 一致；退出码取决于操作和结果是否确定，不能仅凭 HTTP 状态断言写入失败。

| 错误码 | HTTP | 含义 | 告诉用户 |
| --- | --- | --- | --- |
| `invalid_request` | 400 | 参数或请求字段不合法 | 请求格式不合法，请检查参数 |
| `summary_required` | 400 | 缺少必填摘要 | 请修正摘要 |
| `summary_too_long` | 400 | 摘要超出 1000 个 Unicode 字符 | 请修正摘要 |
| `unauthorized` | 401 | Key 无效、已重新生成或用户已删除，同形返回 | 登录后打开 https://docs.zh0uyuan.com/keys（「API Key」页面）生成或重新生成 Key，明文只显示一次；不要把 Key 贴进聊天，也不要写进带明文的命令或 shell history |
| `not_found` | 404 | 文档不存在、被删除或不属于用户 | 文档不存在或无权限 |
| `idempotency_conflict` | 409 | 同幂等键对应不同请求体 | 先确认是否为新内容，确认后才可加 --new |
| `file_too_large` | 413 | HTML 或请求体过大 | 请修正文件 |
| `not_utf8` | 415 | HTML 非法 UTF-8 | 请修正文件 |
| `daily_limit_reached` | 429 | 已达 UTC 当日上传/替换额度 | 已达日额度，请次日再试 |
| `internal_error` | 500 | 服务端内部错误，写入可能已生效 | 服务端内部错误，请稍后再试 |
| `storage_low` | 507 | 服务端可用存储不足 | 存储空间不足，请联系管理员 |

429 的 report 还带 Retry-After 秒数，不自动重试。500 等暂时错误和网络失败有有界重试；上传/替换重试保持原字节和幂等键。重试后退出 4 时用 resume；edit、favorite、unfavorite、delete 结果未知时用 get 核对。不要为清除错误擅自另建文档。
