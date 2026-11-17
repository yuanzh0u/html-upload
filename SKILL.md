---
name: html-upload
description: 用户需要上传本机的单个 HTML、拿回链接、替换正文、查清单、改标题、摘要和标签、收藏或删除时使用；包括「上传这个 HTML」「传到 docs」「更新那篇」「删掉那篇」。不生成 HTML，不读正文。
---

# HTML 上传与维护

## 依赖与前提

依赖契约 docs-api v2；工具先检查主版本，不符时报错退出，不降级。需要 `python3` 3.12 或以上，只用标准库。以下命令从技能目录执行。

Key 的配置和平台边界见 [POSIX 凭据](references/credentials-posix.md)、[Windows 凭据](references/credentials-windows.md)；按当前平台读取。站点固定为 `https://docs.zh0uyuan.com`，没有指定站点的参数；`DOCS_ORIGIN` 的 localhost / 127.0.0.1 回环地址仅供隔离测试。

## 命令速查

参数、返回数据和退出码查 [命令参考](references/commands.md)。`DOC_ID` 是文档 ID，`CLIENT_UPLOAD_ID` 来自失败输出；执行时替换示例值。

```bash
python3 bin/docsctl.py me
python3 bin/docsctl.py upload ./report.html --summary '用户提供的摘要' --tag '用户标签'
python3 bin/docsctl.py replace DOC_ID ./report.html --summary-file ./summary.txt --share-friend '张三'
python3 bin/docsctl.py resume CLIENT_UPLOAD_ID
python3 bin/docsctl.py list --all
python3 bin/docsctl.py get DOC_ID
python3 bin/docsctl.py edit DOC_ID --title '新标题'
python3 bin/docsctl.py favorite DOC_ID
python3 bin/docsctl.py unfavorite DOC_ID
python3 bin/docsctl.py delete DOC_ID
python3 bin/docsctl.py tags
```

## 摘要规则

上传、替换必须提供纯文本摘要，目标 100–300 字，硬上限 1000 字（Unicode 字符）；用户给了摘要就用用户的。没有摘要时，依据已有的内容上下文概括用途、主要内容和结论；上下文不足就请用户提供摘要，不通过读取远端正文补齐。

不要照抄页面开头的导航、标题、目录或样板文字；不要写入 Key、手机号等敏感信息。`--summary` 与 UTF-8 的 `--summary-file` 二选一。

## 标签与分享规则

只用用户给出的标签，不推断或自行分类。`--tag` 可重复；`edit --tag` 整体替换标签，`--clear-tags` 清空，不指定就不改。最多 20 个标签，每个 1–40 字，仅去首尾空白、按原样大小写去重。

只向用户指定的对象分享；`--share-friend` 与 `--share-group` 可重复，各最多 50 个名称，去首尾空白后精确匹配。链接需登录且在分享范围内才能打开；首次新建成功且无分享对象时只有本人能看。替换未追加对象时已有分享保持不变。上传重放的分享报告来自首次请求，当前分享范围可能已变化，需到网页核对。分享部分失败仍可能上传成功，以 report 中逐项结果为准。

## 结果转述与退出码

正常调用 stdout 只有一个 JSON 对象：`ok`、`command`、`status`、`exit_code`、`data`、`report`。把 report 原样转述给用户，不要把部分成功说成全部成功。`data` 保留服务端 UTC，report 使用本机时区。错误对应的用户建议查 [错误参考](references/errors.md)。

| 退出码 | 含义与 Agent 行动 |
| --- | --- |
| 0 | 成功；原样转述 report，包括链接、分享结果和到期说明。 |
| 2 | 参数、文件、凭据、契约不兼容、远端明确拒绝或只读失败；按 report 修正，不声称已完成。 |
| 3 | 删除预览，需要用户确认；未作修改，展示 report 并取得明确同意。 |
| 4 | 写操作远端结果未知，或远端成功而本地记录失败；上传、替换用输出的 resume 命令恢复，其他写操作用 get 核对，不盲目重做。 |
| 5 | 本地预备份失败，请求没有发出；按 report 处理本地问题后再执行。 |

## 替换、维护与删除

更新已有页面正文时，用 replace，不要另传一篇。替换不改链接、标题或标签；到期时间重新起算，分享只追加、不移除；收藏中仍不过期。改标题、摘要、标签用 edit，收藏与取消收藏用相应命令。取消收藏后有 24 小时宽限，新的到期时间见 report。

`list --all` 最多读取 1000 页；第 1000 页仍有后续游标时退出 2，保留已取得的数据并明确结果不完整，请缩小筛选范围。

24 小时内完全相同的上传内容和参数会返回同一篇；原文档已删除时不会自动另建。只有用户确认要另起一篇时才加 --new。未知结果使用 resume 原样重发同一请求；超过 24 小时先用 list/get 核对，不自动重发。档案与恢复规则查 [本地记录](references/manifest.md)。

删除必须先不带 --yes 预览，把永久删除、所有人立即无法打开且不能恢复的影响告诉用户；得到用户明确同意后才加 --yes。删除结果未知时先 get 核对。

## 不要做的事

不要绕过本工具直接请求接口；不生成 HTML、不读远端正文，不修改源文件。不要将 Key 放入参数、URL、输出、日志、异常、本地记录或上传内容。不要让用户把 Key 贴进聊天；工具会拒绝含当前 Key 的原始 HTML、摘要文件以及待发送内容，也检查 URL 编码和最终 JSON 请求字节；不会改写待重发的请求来脱敏，先让用户移除敏感内容再试。不要为恢复修改 pending 档案或生成新幂等键；不要在测试中访问生产站点或真实凭据、真实档案。
