# 命令参考

所有命令使用 `python3 bin/docsctl.py`，需要 Python 3.12 以上；不提供 Key 或站点参数。需要联网的业务调用先检查 docs-api 主版本；已有可信 success 的 resume 只做离线补记。`--help` 显示文本帮助且退出 0，不发业务请求。

## 公共输出与字段

JSON 外层为 `{ok, command, status, exit_code, data, report}`。正常状态为 `success`，失败通常为 `failed`，未知为 `remote_unknown`，删除预览为 `confirm_required`。直接原样转述 report。

下文「元数据」指 `{id, url, title, summary, tags, source_info, size_bytes, uploaded_at, content_updated_at, favorite, expires_at}`；收藏时 `expires_at=null`。`DOC_ID` 是服务端文档 ID，接收 1–128 位 ASCII 字母、数字、下划线或连字符（`[A-Za-z0-9_-]{1,128}`，例如 `Ab_9-x`）；不是链接。只有本地 `CLIENT_UPLOAD_ID` 才要求 32 位小写十六进制。时间 data 使用服务端 UTC，report 转本机时区。

所有命令可能退出 2：参数、文件、凭据、契约或只读失败、明确拒绝、补发替换的正文版本已变化或缺少准备时原始版本。通用错误 data 为 `{error, http_status, request_id}`，无法取得相应字段时可为 null。上传/替换的未知结果 data 为 `{client_upload_id, resume_command}`；已知成功而本地记录失败时，data 还保留服务端已知结果（含 id、url、title），report 明确成功及用 resume 补记；其他写操作未知时 data 可能为错误形状，使用 get 核对。

## me

`python3 bin/docsctl.py me`，无参数。data：`{user_name, api_version, daily_upload_limit, daily_upload_used, retention_days}`。退出 0、2。上传与替换成功各消耗一次日额度，重放及失败不计，额度按 UTC 自然日。

## upload

`python3 bin/docsctl.py upload HTML (--summary TEXT | --summary-file FILE) [--title TEXT] [--tag TAG ...] [--share-friend NAME ...] [--share-group NAME ...] [--new]`。

HTML 为本地非符号链接的普通 `.html` 文件，严格 UTF-8，1–2,097,152 字节；不接受 BOM、NUL、冲突 charset，需 HTML 起始和结束标记。工具只读源文件；无 `--title` 时用唯一页面 title，否则需明确指定。标题 1–200 字。摘要纯文本 1–1000 字；摘要文件使用 UTF-8。标签、分享按技能入口规则。

data：元数据加 `share_report: {shared: [{type, name}], failed: [{type, name, reason}]}`；失败原因 `not_friend` / `group_not_found`。工具从文件名和允许的页面元数据构造 source_info，不接收此参数。

完全相同的请求在 24 小时内重放返回同一篇；不要把重放说成新建；重放的 share_report 是首次请求的历史结果，不能据此断言当前分享范围，需到网页核对。只有首次新建成功且分享报告为空时才说明只有本人能看。只有本次 upload 的第一次实际上传请求就返回 200 时，报告才提示重复内容与 `--new`；自动重试后或 resume 返回 200 时按上传成功报告，不建议另建，仍保留历史分享报告说明。用户确认另建才使用 `--new`；重放指向已删除文档会拒绝，仍需用户确认另建。退出 0、2、4、5；4 用返回的 resume 命令，5 表示未发上传请求。

## replace

`python3 bin/docsctl.py replace DOC_ID HTML (--summary TEXT | --summary-file FILE) [--share-friend NAME ...] [--share-group NAME ...]`。

文件、摘要、分享规则同 upload，不支持 `--title`、`--tag` 或 `--new`。data：元数据加 share_report。链接、标题、标签不变；到期时间重新起算，分享只追加，收藏仍不过期。分享报告为空表示本次未追加分享对象，已有分享保持不变，不能解释为只有本人能看。退出 0、2、4、5；未知结果用 resume，勿重复发起替换。准备档案前先查询元数据并保存 content_updated_at，供补发时复核正文版本。

## resume

`python3 bin/docsctl.py resume CLIENT_UPLOAD_ID`。读取输出中的本地客户端上传 ID（32 位小写十六进制），不是 DOC_ID。data 成功时同原 upload/replace；上传补发前在清单命中时，data 为该篇清单元数据，不包含 share_report；已加载有效档案后的未知结果为 `{client_upload_id, resume_command}`。档案缺失、损坏或 ID 无效时无法加载恢复元数据，退出 4 的 data 为 `{error, http_status, request_id}`，不会提供 resume_command；按 report 停止并核对档案，不从源文件重建。退出 0、2、4。

meta 已有可信 `success: {id, url, title}` 时，核对私有元数据、Key 和站点后只离线补写 manifest 并删除档案；不查询账户、清单或正文版本，不重发，即使 request 文件已清理或准备时间已超过 24 小时也可补记。成功 data 为 `{id, url, title}`，report 给出链接及本地补记说明。成功元数据或 manifest 写入失败时仍退出 4，data 保留已知文档结果、client_upload_id、resume_command，report 含「文档已经上传成功：<url>；本地记录没写进去，用 resume 补记」与文档 ID。若成功元数据本身未能落盘，后续 resume 仍按正常恢复规则核对。

先核对档案 origin 与当前站点；不一致或档案未记录站点时退出 2，提示「请在原来的环境里补发」，不联网并保留档案。有效档案的账户查询或当前元数据查询失败时退出 4，记 remote_unknown，保留档案并返回 client_upload_id、resume_command。先前未知的写入不会因后续明确错误而变成失败，只有可信成功响应才能确定成功；需要时用 list/get 核对。核对通过后，替换还会查询当前 content_updated_at，与准备时保存的版本一致才补发。版本已变化时退出 2，记 failed（error=superseded）并删除档案，报告说明可能是本次已生效或后来又被替换，要求用 get 核对、需要时重新 replace；档案缺少有效的原始正文版本时退出 2 并保留档案，不补填版本。上传补发在发送前先 GET /documents，q 为档案标题，uploaded_from 为 prepared_at 往前 48 小时的 UTC 时间；沿用 list --all 的分页校验与 1000 页上限，完整读取所有页后才判断。标题完全相同且 size_bytes 等于解码后 HTML 正文字节数时，记 uploaded、删除档案、退出 0，报告「链接：<url>」与「补发前在清单里找到了同一篇，没有重复发送」，不发送 POST，不推断当前分享范围。清单查询失败、分页异常或达到上限仍有下一页时，退出 4、记 remote_unknown，保留档案和恢复命令；不得继续发送。查无匹配且未过期时才原样发送档案请求字节、原幂等键；超过 24 小时也先完成上传的只读查找，查无匹配时才拒绝重发。不重新读 HTML 或摘要；档案缺失、被改动或从准备时间起已满 24 小时退出 4，不自动重发。每次实际内容请求及重试前都会检查原始期限；版本检查或等待期间到期也会停止，记 remote_unknown 并保留恢复档案。先 list/get 核对；用户明确确认另起一篇后才重新 upload --new。详见 [本地记录](manifest.md)。

## list

`python3 bin/docsctl.py list [--query TEXT] [--tag TAG ...] [--from TIME] [--to TIME] [--sort uploaded_desc|uploaded_asc|title] [--limit N] [--cursor CURSOR | --all]`。

时间是带时区的 RFC 3339，含起点、不含终点；默认 sort 为 uploaded_desc，limit 为 50（1–200）。data 默认 `{items: [元数据], next_cursor}`；`--all` 为 `{items, count}`。`--all` 最多读取 1000 页（默认每页 50 条，最多取得 50000 条；limit=200 时最多 200000 条）；第 1000 页 next_cursor 为空时仍完整成功，否则停止并建议缩小筛选范围。游标重复、重复文档、达到页数上限或异常分页退出 2，data 保留已取得的 `{items, count}`，report 明示不完整，不声称全量成功。退出 0、2。

## get

`python3 bin/docsctl.py get DOC_ID`。data 为元数据，不能读取正文。退出 0、2。写操作未知后用它核对标题、摘要、标签、收藏等状态；删除后 not_found 表示不存在或无权限，照 report 告知，不自行断定原因。

## edit

`python3 bin/docsctl.py edit DOC_ID [--title TEXT] [--summary TEXT | --summary-file FILE] [--tag TAG ... | --clear-tags]`。至少改一项。标签整体替换；未给的字段不变。标题、摘要和标签约束同 upload；不改正文。data 为元数据，退出 0、2、4；4 用 get 核对，不盲目重发。

## favorite

`python3 bin/docsctl.py favorite DOC_ID`。data 为元数据（`favorite=true, expires_at=null`），收藏中不会过期。退出 0、2、4；4 用 get 核对。

## unfavorite

`python3 bin/docsctl.py unfavorite DOC_ID`。data 为元数据（`favorite=false`），取消收藏后有 24 小时宽限，新的到期时间见 report。退出 0、2、4；4 用 get 核对。

## delete

`python3 bin/docsctl.py delete DOC_ID [--yes]`。不带 --yes 只取元数据预览：data 为元数据，退出 3，没有删除。转述 report，取得用户对这篇文档永久删除的明确同意后才加 --yes。

删除成功为 HTTP 204，无响应体，data 为 null，退出 0。也可能退出 2、4；4 先用 get 核对。删除后所有人立即无法打开，不能恢复。

## tags

`python3 bin/docsctl.py tags`，无参数。data：`{items: [{tag, count}]}`。用于查看已用标签，不表示可自动替用户选标签。退出 0、2。
