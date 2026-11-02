# taxonomy 确认状态机

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

## 本地入口的确认文件示例

`choices` 是按 field 索引的对象；不要把样例自动填成用户批准。保留暂停时的原参数，仅追加 `--confirm-file /path/confirmation.json`。例如：

```json
{"client_upload_id":"<清单里的值>","plan_sha256":"<清单里的值>","accepted":true,"choices":{"type":{"action":"reuse","id":"<用户选定的 id>"},"model":{"action":"create","name":"<用户确认的模型名>","create_confirmed":true},"generated_at":{"value":"2026-10-15 12:00","timezone":"+08:00"},"title":{"value":"用户选定的标题"}}}
```

仅 force-confirm 且无待选项时 choices 可以为空。类别的 field 是 `project.category`，kind 是 `project_category`。项目选择复用时不发送分类、不生成分类 new_terms。纯缺时区时 generated_at 的 choice 可以仅有 timezone；只有日期还须明确时分秒，不能补当前时间。

暂停快照存 `~/docs/.confirm/<client_upload_id>.plan.json`（0700/0600）。客户端不信任确认文件自填 confirmed_at，使用本次恢复时钟。确认后的实际提交可以分配新的逻辑 id 和幂等键；计划 id 仅负责绑定这一份人工选择。
