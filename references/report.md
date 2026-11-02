# 最终报告

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

退出码 3 和 dry-run 的 stdout 为单个合法 JSON；同组小标题在 report 字符串中，自检在 self_check 字段中，JSON 前后不夹普通文本。
