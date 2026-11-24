# html-upload V2 实施计划（含测试方案）：U00–U09

> **执行者（Codex）：** 先读仓库根目录的 `AGENTS.md`，再按任务编号顺序执行，任务内按步骤勾选（`- [ ]`）。每一步拿到写明的结果再进入下一步；每个任务结束时全量测试必须全绿。遇到计划没写、又会影响接口或行为的选择，按 `AGENTS.md` 的「遇到问题」规则开 Issue。

**目标：** 把 html-upload 改造成 docs-api v2.0 的 Agent 端技能。具体要求：
- 上传、替换正文；
- 对文档做维护：清单、单篇、改元数据、收藏、删除、标签；
- 契约版本协商；
- 分享结果告知；
- Key 安全；
- 按错误码给出可操作的提示。

**架构：** 技能推倒重来，与服务端的 D57 同一思路：
- U00 删除 V1 的代码、测试与文档；清场前的代码已打上标签 `v1-final`，指向 V1 的最后一个提交；
- 已经验证过的部分（凭据读取、回环限制、无重定向传输、重试、预备份、manifest）按 `v1-final` 里的实现重建到新模块；
- taxonomy、确认状态机、ACL、外链提示全部不再保留。V2 允许页面引用外部资源（服务端需求 FR-07），所以不再扫描外链；
- 入口统一为 `bin/docsctl.py <子命令>`。不叫 `docs.py`：两个仓库都有 `docs/` 目录，测试里 `import docs` 容易撞上。

**技术栈：** Python 3.12 标准库，不装包；测试用 `unittest`。

**依据（冲突时靠前者为准）：**
1. 契约 docs-api v2.0（已冻结）：docs-service 的 `docs/api/v2/contract.md`。冻结提交在 docs-service 的 `feat/v2-m1` 分支上，里程碑一合入前不在 main 上；查看用 `git -C ../docs-service show feat/v2-m1:docs/api/v2/contract.md`。技能端用到的内容已全部抄在下文「契约要点」里。
2. 技能端需求 `docs/V2-requirements.md` v1.1（C-FR-01 至 C-FR-08）
3. 服务端需求与决定记录：docs-service 的 `docs/V2-requirements.md`、`docs/V2-requirements-v1.2-decisions.md`，只用来理解背景

## 契约要点（技能端以此为准）

**认证：** 只认 `Authorization: Bearer <key>`。Key 无效、已重新生成或用户已删除，一律返回同形的 `401 unauthorized`。Key 只能操作本人文档，不能读正文（契约 §2、§6）。

**接口**（前缀 `/api/v2`）：

| 方法与路径 | 输入 | 成功 |
| --- | --- | --- |
| `GET /me` | 无 | 200：`user_name`、`api_version`（`"2.0"`）、`daily_upload_limit`、`daily_upload_used`、`retention_days` |
| `POST /documents` | 体：`title`、`summary`、`tags`、`html_base64`、`sha256`、`source_info`、`share_with`；头：`Idempotency-Key` | 201，重放返回 200：元数据、`url`、`share_report` |
| `GET /documents` | 查询参数：`q`、`tag`（可重复）、`uploaded_from`、`uploaded_to`、`sort`、`limit`、`cursor` | 200：`items`、`next_cursor` |
| `GET /documents/{id}` | 无 | 200：元数据 |
| `PATCH /documents/{id}` | 体：`title`、`summary`、`tags` 任选（`tags` 为整体替换） | 200：元数据 |
| `PUT /documents/{id}/content` | 体：`html_base64`、`sha256`、`summary`（必填）、`share_with`（可选，只追加）；头：`Idempotency-Key` | 200：元数据、`url`、`share_report` |
| `DELETE /documents/{id}` | 无 | 204 |
| `PUT /documents/{id}/favorite` | 无 | 200：元数据（`favorite=true`） |
| `DELETE /documents/{id}/favorite` | 无 | 200：元数据（`favorite=false`） |
| `GET /tags` | 无 | 200：`{"items": [{"tag", "count"}]}` |

**字段规则：**
- `title`：1–200 字。
- `summary`：必填，纯文本，1–1000 个 Unicode 字符。
- `tags`：最多 20 个，每个 1–40 字。去掉首尾空白后，按原样大小写去重。
- `html_base64`：解码后不超过 2,097,152 字节，必须是合法 UTF-8。
- `sha256`：解码后正文的十六进制 SHA-256。
- `source_info`：对象，序列化后不超过 4096 字节。
- `share_with`：形如 `{"friends": [姓名], "groups": [群名]}`，两个列表各不超过 50 项。
- JSON 请求体：不超过 2,861,740 字节，超出返回 413。
- 服务端权威字段（owner、上传时间、到期时间、收藏状态）不能出现在请求体里，否则返回 400。

**元数据**（11 个字段）：`id`（契约只说是不可猜测的字符串；docs-service 实现为 32 位小写十六进制）、`url`（`origin + '/d/' + id`）、`title`、`summary`、`tags`、`source_info`、`size_bytes`、`uploaded_at`、`content_updated_at`、`favorite`、`expires_at`（收藏中为 `null`）。

**分享报告：**
- 格式：`{"shared": [{"type", "name"}], "failed": [{"type", "name", "reason"}]}`。
- `reason` 只有两种：`not_friend`、`group_not_found`。
- 名称按「去首尾空白后精确匹配」的规则处理。

**幂等：**
- 只认「用户 + Key 值」，Key 格式为 `[A-Za-z0-9_-]{1,100}`，有效期 24 小时。
- 同一个 Key、同一请求体：返回首次结果。
- 同一个 Key、不同请求体：`409 idempotency_conflict`。
- 首次结果涉及的文档已被删除：`404 not_found`。
- 超过 24 小时的记录视为不存在，再用同一个键就是一次新请求。

**日额度：** 上传、替换成功各计 1 次，按 UTC 自然日计；重放和失败都不计。`429` 的 `Retry-After` 是到下一个 UTC 零点的秒数。

**服务端实现约束（docs-service T09；契约没写，但技能端要遵守）：** 有请求体的接口必须带 `Content-Type: application/json`；GET、DELETE 和收藏接口不能带请求体，否则返回 `400`。

**清单查询：**
- `uploaded_from`、`uploaded_to`：带时区的 RFC 3339，含起点、不含终点。
- `sort`：`uploaded_desc`（默认）、`uploaded_asc`、`title`。
- `limit`：1–200，默认 50。

**错误：**
- 统一的错误体：`{"error", "message", "request_id"}`。
- 状态码与错误码：

  | 状态码 | 错误码 |
  | --- | --- |
  | 400 | `invalid_request`、`summary_required`、`summary_too_long` |
  | 401 | `unauthorized` |
  | 404 | `not_found` |
  | 409 | `idempotency_conflict` |
  | 413 | `file_too_large` |
  | 415 | `not_utf8` |
  | 429 | `daily_limit_reached`（带 `Retry-After`） |
  | 500 | `internal_error` |
  | 507 | `storage_low` |

## 全局约束

- **依赖：** 只用 Python 3.12 标准库，不装包。技能面向客户端机器，`SKILL.md` 里写 `python3`，并注明需要 3.12 或以上。
- **站点（origin）：**
  - 生产站点固定为 `https://docs.zh0uyuan.com`，不提供任何指定站点地址的参数；
  - 环境变量 `DOCS_ORIGIN` 只接受 `http(s)://127.0.0.1` 或 `localhost`（可以带端口），供测试用。
- **Key 来源：**
  - 进程里设置了环境变量 `DOCS_API_KEY` 就用它，空字符串也不回退；
  - 否则读 `~/.config/docs/api-key`。该文件必须是本人所有、权限 0600、不是符号链接，所在目录权限必须是 0700。
- **Key 保密（C-FR-07）：** Key 不得出现在 argv、URL、输出、日志、异常、manifest、pending 文件里，也不得夹在要上传的内容里（HTML、标题、摘要、标签、分享名单、`source_info`）发出去。
- **出站请求：**
  - 只发往 `/api/v2/*`；
  - 不跟随重定向，不走代理；
  - 连接超时 10 秒，读取超时 30 秒；
  - 响应体最多读 8 MiB。
- **本地存储：** 根目录 `~/docs/v2/`，权限 0700，文件 0600，拒绝符号链接。只存 manifest 和未完成请求的档案；请求一有确定结果就删掉档案，不留正文副本（C-FR-06「不存储正文」）。不碰 V1 在 `~/docs/` 下的文件。
- **输出：** 每次调用只往 stdout 打印一个 JSON 对象：`{"ok", "command", "status", "exit_code", "data", "report"}`。其中 `report` 是给人看的中文，Agent 原样转述给用户。
- **时间：** `data` 里保持服务端原值（UTC）；`report` 里换算成本机时区，格式 `YYYY-MM-DD HH:MM`。
- **退出码：**

  | 退出码 | 含义 |
  | --- | --- |
  | 0 | 成功 |
  | 2 | 失败，且没有未知的远程副作用：参数、文件、凭据错误，契约不兼容，服务端明确拒绝，或只读操作失败 |
| 3 | 需要用户确认（`delete` 的预览），什么都没改 |
  | 4 | 写操作的远程结果未知，或远程已成功而本地记录失败。上传、替换用 `resume` 恢复；其他写操作用 `get` 核对 |
  | 5 | 本地预备份失败，请求没有发出 |

- **测试：**
  - 测试文件为 `tests/test_v2_*.py`；
  - 假服务端放在 `tests/v2_fake.py`，通过注入 `transport` 使用，不开网络；
  - 回环 HTTP 只在两处出现：U03 的一个用例和 U09 联调，socket 不可用时跳过；
  - 只用临时 HOME、假 Key。

## 重点关注（容易出事、但正常用例覆盖不到的五类输入）

1. **重试必须原样重发：** 同一个 Key，请求体字节完全相同，不重新序列化。
   → U05 `test_retry_reuses_identical_body_and_key`
2. **Key 不能从任何出口泄露：** 包括异常文本、错误报告、服务端回显。
   → U07 `test_token_never_in_any_output`
3. **超过 24 小时的未知结果不能自动重发：** 服务端已经不记得那个幂等键，重发会产生第二篇文档。
   → U05 `test_resume_after_24h_does_not_resend`
4. **服务端分页异常：** 游标重复、页数失控时，`list --all` 不能死循环，也不能悄悄漏掉文档。
   → U06 `test_list_all_stops_on_repeated_cursor`
5. **含非 ASCII 字符的标签和名称：** 首尾空白、全半角、大小写，处理方式必须与服务端一致：只去首尾空白，按原样大小写去重。
   → U01 `test_tags_trim_and_case_preserving_dedupe`、`test_share_names_trimmed_not_normalized`

## 开工准备

**在单独的 worktree 里开发，不要在 `html-upload/` 里切分支。** `~/.agents/skills/html-upload` 是指向 `html-upload/` 的软链接，各个 Agent 现在每天还在用里面的 V1 技能对接线上 V1 服务；V2 技能要等服务端 V2 上线当天才切换（D50）。在 `html-upload/` 里切到 `feat/v2`，线上技能会立刻坏掉。

- [ ] **建 worktree 并确认基线**

```bash
cd /home/ryan/projects/html-platform/html-upload
git switch main && git pull
git worktree add ../html-upload-v2 -b feat/v2
cd ../html-upload-v2
python3.12 -m unittest discover -s tests
```

预期：`Ran 112 tests`，`OK (skipped=3)`。这是 V1 的基线。之后的所有命令都在 `html-upload-v2/` 里执行。

## 文件结构

| 文件 | 任务 | 职责 |
| --- | --- | --- |
| `bin/fields.py` | U01 | 契约字段的本地校验与常量 |
| `bin/local.py` | U02 | 站点限制、Key 读取、HTML 读取与检查、标题选取、`source_info` |
| `bin/client.py` | U03 | 无重定向传输、`/me` 版本协商、带重试的幂等发送、错误提示表 |
| `bin/store.py` | U04 | `~/docs/v2` 预备份、manifest、收尾、恢复 |
| `bin/docsctl.py` | U05–U07 | 命令行入口与 `run()`；子命令 `me`、`upload`、`replace`、`resume`、`list`、`get`、`edit`、`favorite`、`unfavorite`、`delete`、`tags` |
| `SKILL.md`、`references/commands.md`、`references/errors.md`、`references/manifest.md`、`references/credentials-*.md` | U08 | 给 Agent 的使用说明（V2） |
| `tests/v2_fake.py` | U03 起 | 按契约实现的内存假服务端 |
| `tests/test_v2_*.py` | 各任务 | 测试 |
| `tests/test_v2_integration.py` | U09 | 与 docs-service V2 的本机联调；找不到 docs-service V2 时跳过，CI 里总是跳过 |

**运行测试**（在 `html-upload/` 下）：

| 范围 | 命令 |
| --- | --- |
| 全量 | `python3.12 -m unittest discover -s tests` |
| 单个模块 | `cd tests && python3.12 -m unittest test_v2_fields -v` |

测试文件会自行把 `bin/` 插入 `sys.path`，做法与 V1 相同。

---

### U00：清场

**依赖：** 无

- [ ] **步骤 1：推送分支并开草稿 PR**

按 `AGENTS.md` 的格式开草稿 PR，基准分支为 `main`，描述里放 U00–U09 的勾选清单。草稿 PR 需要先有提交，所以先做完步骤 2，提交后再开。

- [ ] **步骤 2：删除 V1 文件**

```bash
git rm bin/upload.py bin/check_html.py bin/match_taxonomy.py bin/normalize.py
git rm references/confirmation.md references/external-links.md references/protocol.md \
       references/manifest.md references/report.md templates/confirm-prompt.txt templates/report.txt
git rm tests/fake_server.py tests/NOTES.md tests/test_adversarial_agy.py tests/test_documents.py \
       tests/test_html.py tests/test_normalize.py tests/test_p6_regressions.py tests/test_taxonomy.py \
       tests/test_transport.py tests/test_upload.py
git rm PLAN.md
git rm -r .verify-p6
```

保留以下文件：
- `tests/test_shell.py`、`tests/credentials-core.sh`：Key 引导脚本及其测试；
- `references/credentials-posix.md`、`references/credentials-windows.md`；
- `docs/`、`prompts/`、`reports/`：历史记录。

`SKILL.md` 先改成一句占位说明：「V2 改造中，见 docs/V2-impl-plan.md」。U08 再重写。

`.github/workflows/tests.yml` 里为 V1 加的 `env: PYTHONPATH`，连同上面两行注释一起删掉；V2 的测试会自己设置导入路径。

- [ ] **步骤 3：回归并提交**

```bash
python3.12 -m unittest discover -s tests
git add -A
git commit -m "chore(V2): U00 清场，保留凭据引导脚本"
git push -u origin feat/v2
```

预期：`Ran 1 test`，`OK`（只剩 `test_shell`）。提交推送后，执行步骤 1 开草稿 PR，并勾掉 U00。

---

### U01：契约字段的本地校验

**依赖：** U00

**Files:**
- Create: `bin/fields.py`
- Test: `tests/test_v2_fields.py`

**Interfaces（Produces）：**
- 常量：`MAX_HTML = 2_097_152`、`MAX_BODY = 2_861_740`、`MAX_SUMMARY = 1000`、`MAX_TITLE = 200`、`MAX_TAGS = 20`、`MAX_TAG = 40`、`MAX_SHARE = 50`、`MAX_SOURCE_INFO = 4096`。
- `class FieldError(ValueError)`：带属性 `field`，取值为 `'title'`、`'summary'`、`'tags'`、`'share_with'`、`'doc_id'` 之一；`str()` 是给人看的中文说明。
- `clean_title(value) -> str`：去掉首尾空白后长度须在 1–200；含 Unicode 类别 `Cc`、`Cf`、`Cs` 的字符时拒绝。
- `clean_summary(value) -> str`
  - 去掉首尾空白后长度须在 1–1000；
  - 允许换行和制表符，其余 `Cc` 类控制字符一律拒绝；
  - 空或全是空白时，报「摘要不能为空」；超长时，报「摘要超过 1000 字」。
- `clean_tags(values) -> list[str]`
  - 逐个去掉首尾空白，每个长度须在 1–40；
  - 按原样大小写去重，保留首次出现的顺序；
  - 去重后不超过 20 个。
- `clean_share(friends, groups) -> dict | None`
  - 名称只去首尾空白，不做全角半角、大小写等任何规范化；
  - 去空白后为空的名称拒绝；
  - 按原样去重；
  - 两个列表各不超过 50 项；
  - 两个列表都为空时返回 `None`。
- `clean_doc_id(value) -> str`：必须匹配 `^[A-Za-z0-9_-]{1,128}$`。契约只说 ID 是不可猜测的字符串，所以本地只校验字符集和长度，防止拼进 URL 路径时出错。

- [ ] **步骤 1：写失败测试** `tests/test_v2_fields.py`

```python
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import fields
from fields import FieldError


class FieldTests(unittest.TestCase):
    def field_of(self, fn, *args):
        with self.assertRaises(FieldError) as caught:
            fn(*args)
        return caught.exception.field

    def test_limits_match_contract(self):
        self.assertEqual((fields.MAX_HTML, fields.MAX_BODY, fields.MAX_SUMMARY, fields.MAX_TITLE, fields.MAX_TAGS,
                          fields.MAX_TAG, fields.MAX_SHARE, fields.MAX_SOURCE_INFO),
                         (2_097_152, 2_861_740, 1000, 200, 20, 40, 50, 4096))

    def test_summary_rules(self):                                   # C-FR-02
        for bad in (None, '', '   ', '长' * 1001, 'a\x00b', 'a\x1bb'):
            with self.subTest(bad=bad if bad is None else bad[:5]):
                self.assertEqual(self.field_of(fields.clean_summary, bad), 'summary')
        self.assertEqual(fields.clean_summary('  第一行\n第二行\t完  '), '第一行\n第二行\t完')
        self.assertEqual(len(fields.clean_summary('长' * 1000)), 1000)

    def test_title_rules(self):
        for bad in (None, '', '  ', 't' * 201, 'a​b'):
            self.assertEqual(self.field_of(fields.clean_title, bad), 'title')
        self.assertEqual(fields.clean_title(' 周报 '), '周报')

    def test_tags_trim_and_case_preserving_dedupe(self):            # 重点关注 5
        self.assertEqual(fields.clean_tags(['A', ' a ', 'A ', '周报', '周报']), ['A', 'a', '周报'])
        self.assertEqual(fields.clean_tags([]), [])
        for bad in ([str(i) for i in range(21)], ['x' * 41], ['  '], [1]):
            self.assertEqual(self.field_of(fields.clean_tags, bad), 'tags')

    def test_share_names_trimmed_not_normalized(self):              # 重点关注 5、契约 §3.2
        self.assertEqual(fields.clean_share([' 张伟（测试组） ', '张伟(测试组)', '张伟（测试组）'], ['测试组']),
                         {'friends': ['张伟（测试组）', '张伟(测试组)'], 'groups': ['测试组']})
        self.assertIsNone(fields.clean_share([], []))
        self.assertEqual(self.field_of(fields.clean_share, ['x%d' % i for i in range(51)], []), 'share_with')
        self.assertEqual(self.field_of(fields.clean_share, ['  '], []), 'share_with')

    def test_doc_id(self):
        for ok in ('a' * 32, 'Ab_9-x'):
            self.assertEqual(fields.clean_doc_id(ok), ok)
        for bad in ('', '../x', 'a/b', 'a b', 'a' * 129, None):
            self.assertEqual(self.field_of(fields.clean_doc_id, bad), 'doc_id')
```

- [ ] **步骤 2：运行，确认失败**

运行：`cd tests && python3.12 -m unittest test_v2_fields -v`

预期：报 `ModuleNotFoundError: No module named 'fields'`。

- [ ] **步骤 3：实现** `bin/fields.py`（按 Interfaces）

- [ ] **步骤 4：确认通过，全量回归，提交**

先运行 `cd tests && python3.12 -m unittest test_v2_fields -v`，预期 `Ran 6 tests`、`OK`；再在仓库根目录运行 `python3.12 -m unittest discover -s tests`，预期 `Ran 7 tests`、`OK`。然后提交：

```bash
git add bin/fields.py tests/test_v2_fields.py
git commit -m "feat(V2): U01 契约字段的本地校验"
git push
```

---

### U02：站点限制、Key 读取、HTML 读取与检查

**依赖：** U01

**Files:**
- Create: `bin/local.py`
- Test: `tests/test_v2_local.py`

**Interfaces（Produces）：**
- `PRODUCTION = 'https://docs.zh0uyuan.com'`。
- `KEY_GUIDANCE`：一句固定的中文提示，必须同时包含两段原文：「登录后打开 https://docs.zh0uyuan.com/keys（「API Key」页面）生成或重新生成 Key，明文只显示一次」和「不要把 Key 贴进聊天，也不要写进带明文的命令或 shell history」。
- `class Stop(Exception)`：构造函数为 `Stop(message, code=2, status='failed')`，三个参数同名保存为属性。
- 以下三个函数照 `git show v1-final:bin/upload.py` 中的同名实现原样重建，只做两处改动：
  - 出错一律改为抛 `Stop`；
  - `credential` 只返回 token（V1 返回的是 token 和来源组成的二元组）。
  
  | 函数 | 说明 |
  | --- | --- |
  | `origin_from(env) -> str` | 规则同 V1：默认生产站点；`DOCS_ORIGIN` 只接受回环地址，端口须在 1–65535 |
  | `secure_directory(path, create=False)`、`private_read(path, limit)` | 同 V1 |
  | `credential(env, home) -> str` | 同 V1 的来源顺序与权限检查；去掉末尾的 CR/LF；长度不足 16 或含非可见 ASCII 时拒绝；所有报错都附上 `KEY_GUIDANCE`，且不包含 token 本身 |

- `read_html(path) -> bytes`
  - 照 `v1-final:bin/check_html.py` 的 `read_html` 重建；
  - 要求是普通文件、不是符号链接、扩展名为 `.html`；
  - 读取上限为 `MAX_HTML + 1` 字节，超出 `MAX_HTML` 时报「HTML 超过 2 MiB」。
- `inspect_html(data) -> dict`
  - 字节检查照 V1 的 `inspect_html` 前半段：
    - 长度 1 到 `MAX_HTML`；
    - 严格 UTF-8；
    - 不含 BOM 和 NUL；
    - 去掉开头空白后以 `<!doctype html` 或 `<html` 开头；
    - 含 `</html>`；
    - charset 声明只能是 utf-8。
  - 返回 `{'titles': [去重后的 <title> 文本], 'meta': {...}}`。其中 `meta` 只收 `hx:title`、`hx:generator`、`hx:generated-at`、`hx:channel`、`hx:task-id` 这五个，各取第一次出现的值。
  - 解析时忽略 `script` 和 `template` 里的内容。
- `pick_title(explicit, inspected) -> str`
  - 显式传入的标题优先，经过 `clean_title`；
  - 否则页面里恰好只有一个标题时用它；
  - 没有标题或标题不止一个时，抛 `Stop`，提示里要包含 `--title`。
- `build_source_info(inspected, path) -> dict`
  - 返回 `{'client': 'html-upload/2', 'file': path.name}`；
  - 页面里有对应 meta 时，再加上 `generator`、`generated_at`、`channel`、`task_id`；
  - 序列化后超过 4096 字节时，丢掉 `client` 以外的可选键，直到不超限。

- [ ] **步骤 1：写失败测试** `tests/test_v2_local.py`

```python
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import local
from local import Stop

HTML = ('<!doctype html><html><head><meta charset="utf-8"><title>周报</title></head>'
        '<body>x</body></html>').encode()
KEY = 'k' * 24


class LocalTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)

    def write(self, name, data):
        path = self.home / name
        path.write_bytes(data)
        return path

    def key_file(self, text, file_mode=0o600, dir_mode=0o700):
        folder = self.home / '.config/docs'
        folder.mkdir(parents=True)
        (folder / 'api-key').write_text(text)
        (folder / 'api-key').chmod(file_mode)
        folder.chmod(dir_mode)

    def test_origin_rules(self):
        self.assertEqual(local.origin_from({}), 'https://docs.zh0uyuan.com')
        for ok in ('http://127.0.0.1:8080', 'https://localhost'):
            self.assertEqual(local.origin_from({'DOCS_ORIGIN': ok}), ok)
        for bad in ('https://evil.example', 'http://127.0.0.1:0', 'http://127.0.0.1.evil.com'):
            with self.subTest(bad=bad), self.assertRaises(Stop):
                local.origin_from({'DOCS_ORIGIN': bad})

    def test_credential_sources(self):                              # C-FR-07
        self.assertEqual(local.credential({'DOCS_API_KEY': KEY + '\r\n'}, self.home), KEY)
        with self.assertRaises(Stop) as caught:
            local.credential({'DOCS_API_KEY': ''}, self.home)      # 已设置为空也不回退到文件
        self.assertIn('API Key', str(caught.exception))
        self.key_file(KEY)
        self.assertEqual(local.credential({}, self.home), KEY)

    def test_credential_file_permissions(self):
        self.key_file(KEY, file_mode=0o644)
        with self.assertRaises(Stop) as caught:
            local.credential({}, self.home)
        self.assertNotIn(KEY, str(caught.exception))

    def test_read_html_limits(self):                                # C-FR-03
        self.assertEqual(local.read_html(self.write('a.html', HTML)), HTML)
        exact = b'<html>' + b'a' * (2_097_152 - 13) + b'</html>'
        self.assertEqual(len(local.read_html(self.write('b.html', exact))), 2_097_152)
        for name, data in (('c.html', exact + b'a'), ('d.txt', HTML)):
            with self.subTest(name=name), self.assertRaises(Stop):
                local.read_html(self.write(name, data))
        link = self.home / 'e.html'
        link.symlink_to(self.home / 'a.html')
        with self.assertRaises(Stop):
            local.read_html(link)

    def test_inspect_rejects_bad_bytes(self):
        for bad in (b'\xff' + HTML, b'\xef\xbb\xbf' + HTML, HTML.replace(b'>x<', b'>\x00<'), b'hello',
                    HTML.replace(b'</html>', b''), HTML.replace(b'utf-8', b'gbk')):
            with self.subTest(bad=bad[:12]), self.assertRaises(Stop):
                local.inspect_html(bad)

    def test_title_selection(self):
        self.assertEqual(local.pick_title(None, local.inspect_html(HTML)), '周报')
        self.assertEqual(local.pick_title(' 新标题 ', local.inspect_html(HTML)), '新标题')
        for data in (HTML.replace(b'</head>', b'<title>B</title></head>'), HTML.replace('<title>周报</title>'.encode(), b'')):
            with self.assertRaises(Stop) as caught:
                local.pick_title(None, local.inspect_html(data))
            self.assertIn('--title', str(caught.exception))

    def test_source_info_from_studio_meta(self):
        page = HTML.replace(b'</head>', b'<meta name="hx:generator" content="html-studio">'
                                        b'<meta name="hx:generated-at" content="2027-01-15T08:00:00+08:00"></head>')
        self.assertEqual(local.build_source_info(local.inspect_html(page), Path('/x/report.html')),
                         {'client': 'html-upload/2', 'file': 'report.html', 'generator': 'html-studio',
                          'generated_at': '2027-01-15T08:00:00+08:00'})

    def test_source_info_without_meta(self):
        self.assertEqual(local.build_source_info(local.inspect_html(HTML), Path('a.html')),
                         {'client': 'html-upload/2', 'file': 'a.html'})
```

- [ ] **步骤 2：运行，确认失败**

运行：`cd tests && python3.12 -m unittest test_v2_local -v`

预期：报 `ModuleNotFoundError: No module named 'local'`。

- [ ] **步骤 3：实现** `bin/local.py`（按 Interfaces；需要照 V1 重建的部分，用 `git show v1-final:bin/upload.py` 和 `git show v1-final:bin/check_html.py` 查看原实现）

- [ ] **步骤 4：确认通过，全量回归，提交**

先运行 `cd tests && python3.12 -m unittest test_v2_local -v`，预期 `Ran 8 tests`、`OK`；再在仓库根目录跑全量测试，预期 `Ran 15 tests`、`OK`。然后提交：

```bash
git add bin/local.py tests/test_v2_local.py
git commit -m "feat(V2): U02 站点限制、Key 读取与 HTML 检查"
git push
```

---

### U03：传输、版本协商、重试与假服务端

**依赖：** U02

**Files:**
- Create: `bin/client.py`、`tests/v2_fake.py`
- Test: `tests/test_v2_client.py`

**Interfaces（Produces）：**

- **`http_request(method, url, token, body=None, idem=None) -> tuple[int, dict | None, dict]`**
  - 照 `v1-final:bin/upload.py` 的 `http_request` 及其连接、处理器类重建：不跟随重定向、不走代理，连接超时 10 秒、读取超时 30 秒；
  - `body` 是序列化好的 `bytes`。有请求体时带 `Content-Type: application/json`；没有就一个字节也不发；
  - 有 `idem` 时带 `Idempotency-Key`；**不发送 `Origin` 头**；
  - 返回 `(状态码, 响应 JSON 对象, 响应头)`。响应体超过 8 MiB、不是 JSON 或不是对象时，第二项为 `None`；
  - 网络层失败（含 `http.client.HTTPException`）一律以 `OSError` 抛出。
- **`TRANSIENT = frozenset({500, 502, 503, 504})`**：这些状态码和 transport 抛出的 `OSError` 都算暂时性失败。
- **`ADVICE: dict[str, str]`**：契约的 11 个错误码各对应一条中文提示（C-FR-08）：

  | 错误码 | 提示必须包含 |
  | --- | --- |
  | `summary_required`、`summary_too_long` | 「修正摘要」 |
  | `file_too_large`、`not_utf8` | 「修正文件」 |
  | `daily_limit_reached` | 「次日」 |
  | `storage_low` | 「联系管理员」 |
  | `not_found` | 「不存在或无权限」 |
  | `idempotency_conflict` | 「先确认是否为新内容」，确认后才可加 `--new` |
  | `unauthorized` | `local.KEY_GUIDANCE` |

  `invalid_request`、`internal_error` 的提示自拟。
- **`describe(status, value, headers) -> str`**：组合一行说明。
  - 格式为 `ADVICE[错误码]` 加「（请求编号 <request_id>）」；
  - 429 时追加「服务端建议 <Retry-After> 秒后再试；本工具不自动重试」；
  - 不引用服务端的 `message`：C-FR-08 规定除 `request_id` 外不向用户暴露服务端信息；
  - 错误码未知时写「服务端返回 HTTP <状态码>」；状态码在 `TRANSIENT` 里时再加「写操作可能已经生效，请用 get 核对」。
- **`idempotency_key(raw: bytes, nonce: str = '') -> str`**：上传用的幂等键。
  - 返回 `hashlib.sha256(b'POST /api/v2/documents\n' + nonce.encode() + b'\n' + raw).hexdigest()`，即 64 位十六进制，符合契约的键格式；
  - 同一请求体总得到同一个键，所以重复执行同一上传，24 小时内服务端返回的是同一篇（C-FR-03 验收）；内容或参数一变，键就变（C-FR-03「内容或参数变化则新生成」）；
  - 替换不用它，每次调用都新生成随机键（见 U05）。
- **`class RemoteUnknown(Exception)`**：写请求发出后拿不到确定结果时抛出。
- **`Client(origin, token, *, transport=http_request, sleep=time.sleep, rng=random.uniform)`**
  - **重试间隔：** 第 i 次重试（i 从 1 开始）前调用 `sleep(rng(0, min(30, 2 ** i)))`。
  - **`me() -> dict`：** 发 `GET /api/v2/me`，重试规则同 `get`。
    - 401 抛 `Stop(describe(...))`，不重试（C-FR-07）；
    - `api_version` 的主版本不是 `2` 时抛 `Stop`，文字含「契约主版本不兼容」，不做任何降级尝试（C-FR-01）。
  - **`get(path, query=None) -> dict`：** `query` 是 `(键, 值)` 列表（`tag` 可以重复），用 `urllib.parse.urlencode` 编码。
    - 暂时性失败最多重试 3 次，用尽后抛 `Stop('服务暂时不可用，请稍后再试')`；
    - 其他非 2xx 抛 `Stop(describe(...))`。
  - **`send(method, path, body: dict | None = None) -> tuple[int, dict | None, dict]`：** PATCH、DELETE、收藏用，只发一次。`OSError` 转成 `RemoteUnknown`，其余状态码原样返回。
  - **`send_idempotent(method, path, raw: bytes, idem: str) -> tuple[int, dict | None, dict]`：** 上传、替换用。
    - 暂时性失败最多重试 5 次，每次原样发送同一份 `raw` 和同一个 `idem`；
    - 其他状态码立即返回，包括 400、401、404、409、413、415、429、507；
    - 重试用尽抛 `RemoteUnknown`。

**假服务端 `tests/v2_fake.py`：**

`FakeDocs(*, key='k' * 24, user='甲', origin='http://127.0.0.1:9', api_version='2.0', friends=('乙',), groups=('测试组',), daily_limit=100, retention_days=30)` 是按契约实现的内存服务端。构造参数都存为同名属性，测试可以随时改。

| 成员 | 用途 |
| --- | --- |
| `transport(method, url, token, body=None, idem=None)` | 签名同 `http_request`，由测试注入给 `Client` 或 `run()` |
| `calls` | 每个请求先记一条 `{'method', 'path', 'query', 'idem', 'body'}`，再决定结果。`query` 是 `parse_qs` 的结果，`body` 是原始字节或 `None`；不记 token |
| `docs` | `{id: 元数据}`。测试直接 `del fake.docs[id]` 就表示文档已删除 |
| `fail_next` | 故障列表，只由写请求（POST、PUT、PATCH、DELETE）按顺序消耗 |
| `fail_reads` | 故障列表，只由 GET 按顺序消耗 |
| `echo_token` | 为真时，错误体的 `message` 和抛出的异常文本里都带上请求的 token，用来测泄露 |
| `broken_cursor` | 为真时，只要还有下一页，就总返回 `next_cursor='fixed'`；而 `'fixed'` 总被当作第二页 |
| `seed(n, *, title='文档', tags=())` | 直接造 n 篇文档，标题为 `f'{title}{i:04d}'`，上传时间逐篇递增 |
| `upload_raw(**fields) -> bytes` | 合规上传请求体的 JSON 字节。标题、摘要、HTML 有默认值，可用 `fields` 覆盖 |

故障项：

| 项 | 效果 |
| --- | --- |
| `'network'` | 不处理，抛 `OSError` |
| `'lost'` | 照常处理，然后抛 `OSError`，模拟响应丢失 |
| `'crash'` | 抛 `RuntimeError('boom ' + token)`，供 U07 使用 |
| `(status, code)` 或 `(status, code, headers)` | 不处理，返回这个错误体 |

按契约实现的行为：
- **鉴权与路由：** token 不对返回同形的 401；只有契约的 10 个路由，其他一律 404。
- **校验：**

  | 情况 | 结果 |
  | --- | --- |
  | 缺摘要或摘要全是空白 | `summary_required` |
  | 摘要超 1000 字 | `summary_too_long` |
  | 未知字段或服务端权威字段、sha256 不符、Base64 不合法 | `invalid_request` |
  | 正文解码后超过 2 MiB | 413 |
  | 正文不是 UTF-8 | 415 |
  | GET、DELETE、收藏接口带了请求体 | 400 |

- **幂等：**
  - 同键同体：返回首次响应，上传的状态码由 201 改为 200；
  - 同键异体：409；
  - 首次结果涉及的文档已不在 `docs` 里：404。
- **日额度：** 上传、替换成功各计 1 次，重放不计；超出返回 429，带 `Retry-After: 57600`。
- **分享：** 名称去首尾空白后，与 `friends`、`groups` 精确比较，生成 `share_report`。
- **清单：**
  - 按 `q`、`tag`（多个时须同时含有）、`uploaded_from`/`uploaded_to`、`sort` 过滤和排序；
  - `limit` 不在 1–200 时返回 400；
  - 游标是偏移量的字符串形式。
- **收藏：** 收藏后 `expires_at` 为 `None`，取消收藏后为非空时间。
- **ID 与链接：** `id` 为 `secrets.token_hex(16)`，`url` 为 `origin + '/d/' + id`。

- [ ] **步骤 1：写失败测试** `tests/test_v2_client.py`

```python
import http.server
import sys
import threading
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import client
import local
from v2_fake import FakeDocs

CODES = {'invalid_request', 'summary_required', 'summary_too_long', 'unauthorized', 'not_found',
         'idempotency_conflict', 'file_too_large', 'not_utf8', 'daily_limit_reached', 'storage_low', 'internal_error'}


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.fake, self.sleeps = FakeDocs(), []
        self.client = client.Client(self.fake.origin, self.fake.key, transport=self.fake.transport,
                                    sleep=self.sleeps.append, rng=lambda a, b: b)

    def test_me_and_version(self):                                  # C-FR-01
        self.assertEqual(self.client.me()['api_version'], '2.0')
        self.fake.api_version = '3.0'
        with self.assertRaises(local.Stop) as caught:
            self.client.me()
        self.assertIn('契约主版本不兼容', str(caught.exception))

    def test_unauthorized_is_not_retried(self):                     # C-FR-07
        bad = client.Client(self.fake.origin, 'x' * 24, transport=self.fake.transport, sleep=self.sleeps.append)
        with self.assertRaises(local.Stop) as caught:
            bad.me()
        self.assertEqual((len(self.fake.calls), self.sleeps), (1, []))
        self.assertIn('API Key', str(caught.exception))

    def test_transient_errors_retry_with_same_bytes(self):          # 重点关注 1
        self.fake.fail_next = ['network', (502, 'internal_error'), (500, 'internal_error')]
        raw = self.fake.upload_raw()
        code, _, _ = self.client.send_idempotent('POST', '/api/v2/documents', raw, 'key-1')
        self.assertEqual((code, len(self.fake.calls), self.sleeps), (201, 4, [2, 4, 8]))
        self.assertEqual({(c['body'], c['idem']) for c in self.fake.calls}, {(raw, 'key-1')})

    def test_definite_errors_are_not_retried(self):
        for status, code in ((400, 'invalid_request'), (404, 'not_found'), (409, 'idempotency_conflict'),
                             (413, 'file_too_large'), (415, 'not_utf8'), (429, 'daily_limit_reached'),
                             (507, 'storage_low')):
            with self.subTest(code=code):
                self.fake.calls.clear()
                self.fake.fail_next = [(status, code)]
                got, value, _ = self.client.send_idempotent('POST', '/api/v2/documents', self.fake.upload_raw(), 'k')
                self.assertEqual((got, value['error'], len(self.fake.calls)), (status, code, 1))

    def test_exhausted_retries_raise_remote_unknown(self):
        self.fake.fail_next = ['network'] * 6
        with self.assertRaises(client.RemoteUnknown):
            self.client.send_idempotent('POST', '/api/v2/documents', self.fake.upload_raw(), 'k')
        self.assertEqual((len(self.fake.calls), self.sleeps), (6, [2, 4, 8, 16, 30]))

    def test_reads_retry_then_give_up(self):
        self.fake.fail_reads = ['network', (503, 'internal_error')]
        self.assertEqual(self.client.get('/api/v2/tags'), {'items': []})
        self.assertEqual((len(self.fake.calls), self.sleeps), (3, [2, 4]))
        self.fake.fail_reads = ['network'] * 4
        with self.assertRaises(local.Stop) as caught:
            self.client.get('/api/v2/tags')
        self.assertIn('暂时不可用', str(caught.exception))

    def test_advice_covers_all_contract_codes(self):                # C-FR-08
        self.assertEqual(set(client.ADVICE), CODES)
        self.assertIn('次日', client.ADVICE['daily_limit_reached'])
        self.assertIn('不存在或无权限', client.ADVICE['not_found'])
        self.assertIn('先确认是否为新内容', client.ADVICE['idempotency_conflict'])
        line = client.describe(429, {'error': 'daily_limit_reached', 'message': '内部细节', 'request_id': 'r1'},
                               {'Retry-After': '60'})
        for text in ('r1', '60', '不自动重试'):
            self.assertIn(text, line)
        self.assertNotIn('内部细节', line)

    def test_upload_key_is_derived_from_body(self):                 # C-FR-03
        raw = self.fake.upload_raw()
        key = client.idempotency_key(raw)
        self.assertRegex(key, r'^[0-9a-f]{64}$')
        self.assertEqual(client.idempotency_key(raw), key)
        self.assertNotEqual(client.idempotency_key(self.fake.upload_raw(title='另一个标题')), key)
        self.assertNotEqual(client.idempotency_key(raw, 'n1'), key)

    def test_http_request_does_not_follow_redirects_or_send_origin(self):
        seen = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.update(auth=self.headers.get('Authorization'), origin=self.headers.get('Origin'))
                self.send_response(302)
                self.send_header('Location', 'http://127.0.0.1:1/elsewhere')
                self.end_headers()

            def log_message(self, *args):
                pass
        try:
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        except OSError:
            self.skipTest('本环境不允许回环 socket')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        code, _, _ = client.http_request('GET', f'http://127.0.0.1:{server.server_port}/api/v2/me', 'k' * 24)
        self.assertEqual((code, seen), (302, {'auth': 'Bearer ' + 'k' * 24, 'origin': None}))
```

- [ ] **步骤 2：运行，确认失败**

运行：`cd tests && python3.12 -m unittest test_v2_client -v`

预期：报 `ModuleNotFoundError`，缺 `client` 或 `v2_fake`。

- [ ] **步骤 3：实现** `bin/client.py` 和 `tests/v2_fake.py`（按 Interfaces 和假服务端说明）

- [ ] **步骤 4：确认通过，全量回归，提交**

先运行 `cd tests && python3.12 -m unittest test_v2_client -v`，预期 `Ran 9 tests`、`OK`；再跑全量测试，预期 `Ran 24 tests`、`OK`。然后提交：

```bash
git add bin/client.py tests/v2_fake.py tests/test_v2_client.py
git commit -m "feat(V2): U03 传输、版本协商与重试"
git push
```

---

### U04：未完成请求的档案与 manifest

**依赖：** U02

**Files:**
- Create: `bin/store.py`
- Test: `tests/test_v2_store.py`

**Interfaces（Produces）：** 照 `v1-final:bin/upload.py` 里 `exclusive_write`、`replace_json`、`sync_dir`、`checked_fd`、`file_lock`、`append_event`、`prepare`、`load_pending` 的写法重建：权限、`O_NOFOLLOW`、fsync、文件锁都照旧，目录和字段改用 V2 的。

- **常量：** `ROOT = Path('docs/v2')`（相对 HOME）；`STATUSES = ('prepared', 'uploaded', 'replaced', 'remote_unknown', 'failed')`。
- **`prepare(home, op, doc_id, raw, sha256, idem, now, *, title) -> dict`**
  - `op` 为 `'upload'` 或 `'replace'`；
  - 新建 `~/docs/v2/` 和 `.pending/`，权限 0700，拒绝符号链接；
  - 以独占方式写两个档案文件，权限 0600，各自 fsync 后再 fsync 目录：
    - `<client_upload_id>.request.json`：内容就是 `raw`；
    - `<client_upload_id>.meta.json`；
  - `meta` 字段：`client_upload_id`（`secrets.token_hex(16)`）、`op`、`doc_id`、`idempotency_key`、`sha256`、`title`、`prepared_at`（浮点秒）；
  - 追加一行 `prepared`，然后返回 `meta`；
  - 遇到任何 `OSError` 都抛 `Stop(..., code=5)`，此时请求还没发出。
- **`record(home, meta, status, now, *, doc=None, error=None) -> None`**
  - 加文件锁，向 `manifest.jsonl` 追加一行，flush 后 fsync；
  - 每行字段：`schema_version`（固定为 2）、`event_id`、`client_upload_id`、`op`、`status`、`doc_id`、`url`、`title`、`sha256`、`at`（RFC 3339 UTC）、`error`；
  - 有 `doc` 时，`doc_id`、`url`、`title` 取自 `doc`；没有时取自 `meta`，`url` 为 `None`；
  - 不写 API Key，也不写幂等键。
- **`finalize(home, meta, now, *, doc=None, error=None) -> None`**：请求有了确定结果后调用。
  - 有 `doc` 时，按 `op` 记 `uploaded` 或 `replaced`；没有时记 `failed`，`error` 为错误码；
  - 然后删掉两个档案文件并 fsync 目录。删除失败不算错：留下的档案无害，`resume` 只会拿回同一个结果；
  - 追加 manifest 失败时抛 `Stop(..., code=4)`，档案保留，之后可用 `resume` 补记。
- **`load_pending(home, client_id) -> tuple[dict, bytes]`**：返回 `(meta, raw)`。逐项检查：
  - `client_id` 是 32 位小写十六进制；
  - 两个文件都存在、权限 0600、不是符号链接；
  - `raw` 里 `html_base64` 解码后的 SHA-256、`raw` 里的 `sha256`、`meta['sha256']` 三者一致。

  任一项不符，抛 `Stop('恢复档案缺失或被改动…', code=4)`。

- [ ] **步骤 1：写失败测试** `tests/test_v2_store.py`

```python
import base64
import hashlib
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import store
from local import Stop

DATA = b'<!doctype html><html><title>t</title></html>'
SHA = hashlib.sha256(DATA).hexdigest()
RAW = json.dumps({'title': 't', 'summary': 's', 'html_base64': base64.b64encode(DATA).decode(),
                  'sha256': SHA}).encode()
T0 = 1_800_000_000.0
DOC = {'id': 'a' * 32, 'url': 'http://127.0.0.1:9/d/' + 'a' * 32, 'title': 't'}


class StoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.root = self.home / 'docs/v2'

    def manifest(self):
        return [json.loads(line) for line in (self.root / 'manifest.jsonl').read_text().splitlines()]

    def mode(self, path):
        return stat.S_IMODE(path.lstat().st_mode)

    def prepare(self):
        return store.prepare(self.home, 'upload', None, RAW, SHA, 'idem-1', T0, title='t')

    def test_prepare_writes_private_files_and_manifest(self):
        meta = self.prepare()
        self.assertEqual((self.mode(self.root), self.mode(self.root / '.pending')), (0o700, 0o700))
        for suffix in ('.request.json', '.meta.json'):
            self.assertEqual(self.mode(self.root / '.pending' / (meta['client_upload_id'] + suffix)), 0o600)
        row = self.manifest()[-1]
        self.assertEqual((row['schema_version'], row['status'], row['op'], row['sha256']),
                         (2, 'prepared', 'upload', SHA))
        self.assertNotIn('idem-1', (self.root / 'manifest.jsonl').read_text())

    def test_prepare_refuses_symlinked_root(self):
        (self.home / 'docs').mkdir()
        (self.home / 'docs/v2').symlink_to(self.home)
        with self.assertRaises(Stop) as caught:
            self.prepare()
        self.assertEqual(caught.exception.code, 5)

    def test_finalize_records_and_clears_pending(self):             # C-FR-03、C-FR-06
        meta = self.prepare()
        store.finalize(self.home, meta, T0 + 1, doc=DOC)
        row = self.manifest()[-1]
        self.assertEqual((row['status'], row['doc_id'], row['url'], row['sha256']),
                         ('uploaded', DOC['id'], DOC['url'], SHA))
        self.assertEqual(list((self.root / '.pending').iterdir()), [])
        self.assertEqual(list(self.root.rglob('*.html')), [])                   # 不留正文副本

    def test_load_pending_detects_tampering(self):
        meta = self.prepare()
        self.assertEqual(store.load_pending(self.home, meta['client_upload_id'])[1], RAW)
        request = self.root / '.pending' / (meta['client_upload_id'] + '.request.json')
        request.write_bytes(RAW.replace(base64.b64encode(DATA), base64.b64encode(DATA + b' ')))
        with self.assertRaises(Stop) as caught:
            store.load_pending(self.home, meta['client_upload_id'])
        self.assertEqual(caught.exception.code, 4)
```

- [ ] **步骤 2：运行，确认失败**

运行：`cd tests && python3.12 -m unittest test_v2_store -v`

预期：报 `ModuleNotFoundError: No module named 'store'`。

- [ ] **步骤 3：实现** `bin/store.py`（按 Interfaces）

- [ ] **步骤 4：确认通过，全量回归，提交**

先运行 `cd tests && python3.12 -m unittest test_v2_store -v`，预期 `Ran 4 tests`、`OK`；再跑全量测试，预期 `Ran 28 tests`、`OK`。然后提交：

```bash
git add bin/store.py tests/test_v2_store.py
git commit -m "feat(V2): U04 未完成请求的档案与 manifest"
git push
```

---

### U05：入口与上传、替换、恢复

**依赖：** U01–U04

**Files:**
- Create: `bin/docsctl.py`
- Test: `tests/test_v2_upload.py`

**Interfaces（Produces）：**

- **`build_parser() -> argparse.ArgumentParser`**
  - 所有子命令都在这里定义；U06 往里补维护命令，U07、U08 的测试用它列出全部子命令和选项；
  - 覆盖 `error()`：解析出错时抛 `local.Stop`，交给 `run()` 照常输出 JSON 并返回 2；
  - 只有 `-h/--help` 直接打印帮助文本。
- **`run(argv=None, *, env=None, out=None, clock=time.time, sleep=time.sleep, rng=random.uniform, transport=None) -> int`**
  - `env` 默认 `os.environ`，`HOME` 从 `env` 里取；`out` 默认 `sys.stdout`；`transport` 为 `None` 时用 `client.http_request`；
  - 每次调用只向 `out` 写一个 JSON 对象（`ensure_ascii=False`）：`{"ok", "command", "status", "exit_code", "data", "report"}`；
  - 写出之前，把整段文本里的 token 替换成 `[已隐藏]`，作为最后一道保险；
  - 出错时 `data` 为 `{"error": 错误码, "http_status": 状态码, "request_id": …}`，不放服务端的 `message`；
  - 每个联网的子命令都先调用 `Client.me()` 做版本协商（C-FR-01）。
- **本任务的子命令**（`--summary-file` 都按 UTF-8 读取）：

  | 子命令 | 参数 | 作用 |
  | --- | --- | --- |
  | `me` | 无 | `data` 为 `/me` 的响应；`report` 含「今日已用 x/y 次」 |
  | `upload` | `HTML`；`--summary TEXT` 与 `--summary-file PATH` 二选一，必填；`--title`；`--tag`、`--share-friend`、`--share-group`（均可重复）；`--new` | 上传 |
  | `replace` | `DOC_ID`、`HTML`；`--summary` 或 `--summary-file`，必填；`--share-friend`、`--share-group` | 替换正文 |
  | `resume` | `CLIENT_UPLOAD_ID` | 用原请求体和原幂等键补发 |

**`upload`、`replace` 的执行顺序：** 第 1–4 步都在联网之前。其中任何一步失败都返回 2，一个请求也不发。
1. 用 `fields` 校验摘要、标题、标签和分享名单。
2. `read_html`，再 `inspect_html`。
3. 上传时用 `pick_title` 定标题，用 `build_source_info` 生成 `source_info`。
4. 读取 Key，拼请求体，用 `canonical` 序列化（照 V1：`sort_keys`、紧凑分隔符、`ensure_ascii=False`），超过 `MAX_BODY` 就返回 2。
   - 上传的字段：`title`、`summary`、`tags`（有才带）、`html_base64`、`sha256`、`source_info`、`share_with`（有才带）；
   - 替换的字段：`html_base64`、`sha256`、`summary`、`share_with`（有才带）。
5. `Client.me()`。
6. 确定幂等键，然后 `store.prepare`。
   - 上传用 `client.idempotency_key(raw, nonce)`，`nonce` 平时为空，带 `--new` 时为 `secrets.token_hex(16)`；
   - 替换用 `secrets.token_hex(16)`。
7. 用 `send_idempotent` 发送。上传发 `POST /api/v2/documents`，替换发 `PUT /api/v2/documents/{id}/content`。结果分三种情况：
   - **2xx 且响应可信：** `store.finalize(..., doc=响应)`，返回 0。响应可信需同时满足：
     - `id` 通过 `clean_doc_id`，替换时还要等于 `DOC_ID`；
     - `url` 等于 `origin + '/d/' + id`；
     - `share_report` 的 `shared`、`failed` 都是列表，各项 `type` 为 `friend` 或 `group`，`failed` 各项的 `reason` 为 `not_friend` 或 `group_not_found`。
   - **其他确定的状态码：** `store.finalize(..., error=错误码)`，返回 2，`report` 用 `describe` 生成。上传收到 404 时（契约 §3.3），`report` 改写为：「24 小时内用完全相同的内容和参数上传过一次，那篇文档已经删除。确实要重新上传，加 --new」。
   - **`RemoteUnknown`，或 2xx 但响应不可信：** 记 `store.record(..., 'remote_unknown')`，保留档案，返回 4。`data` 为 `{"client_upload_id": …, "resume_command": "python3 bin/docsctl.py resume <client_upload_id>"}`，`report` 写出这条命令。

**`resume` 的规则：**
1. `store.load_pending`。
2. 距 `prepared_at` 超过 24 小时：不发送，返回 4。`report` 写「超过 24 小时，服务端已不记得这次请求，不会自动重发；请先用 list 核对是否已经上传成功」（重点关注 3）。
3. 24 小时以内：先 `me()`，再用原字节、原幂等键发送，之后的处理与第 7 步相同。

**`report` 的固定措辞**（测试会检查）：

| 场景 | 必须出现 |
| --- | --- |
| 上传成功（C-FR-03） | 「链接：<url>」；「对方需登录且在分享范围内才能打开。」 |
| 上传收到 200（重放） | 「同一篇」，并说明 24 小时内提交过完全相同的内容和参数、返回的是那一篇；要另起一篇就加 `--new` |
| 没有指定分享对象 | 「未指定分享对象，目前只有你自己能看。」 |
| 分享全部成功 | 「已全部分享成功：」，后面列出名称 |
| 分享有失败项（C-FR-04） | 先列已分享的对象，再写「以下对象没有分享成功：」，逐项写「<名称>（不是你的好友）」或「<名称>（没有这个群组）」，最后写「可以到网页的分享管理里补分享。」；不得出现「全部分享成功」 |
| 替换成功（C-FR-05） | 「链接不变」「到期时间已从现在重新起算」「替换只会新增分享，不会移除已有分享」 |
| 任一成功 | 到期时间（本机时区）；收藏中的文档写「收藏中，不会过期」 |

- [ ] **步骤 1：写失败测试** `tests/test_v2_upload.py`

```python
import base64
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import docsctl
from v2_fake import FakeDocs

HTML = '<!doctype html><html><head><meta charset="utf-8"><title>周报</title></head><body>x</body></html>'
FIELDS = {'title', 'summary', 'tags', 'html_base64', 'sha256', 'source_info', 'share_with'}
T0 = 1_800_000_000.0


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


class UploadTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.page = self.home / 'report.html'
        self.page.write_text(HTML, encoding='utf-8')
        self.fake, self.clock, self.sleeps = FakeDocs(), Clock(), []

    def cli(self, *argv, key=None):
        out = io.StringIO()
        env = {'HOME': str(self.home), 'DOCS_API_KEY': key or self.fake.key, 'DOCS_ORIGIN': self.fake.origin}
        code = docsctl.run(list(argv), env=env, out=out, clock=self.clock, sleep=self.sleeps.append,
                           rng=lambda a, b: 0, transport=self.fake.transport)
        result = json.loads(out.getvalue())
        self.assertEqual(result['exit_code'], code)
        return code, result

    def upload(self, *extra):
        return self.cli('upload', str(self.page), '--summary', '本周进展与下周计划。', *extra)

    def posts(self):
        return [c for c in self.fake.calls if c['method'] in ('POST', 'PUT')]

    def manifest(self):
        lines = (self.home / 'docs/v2/manifest.jsonl').read_text().splitlines()
        return [json.loads(line) for line in lines]

    def test_upload_success_reports_url_and_login_note(self):       # C-FR-03
        code, result = self.upload()
        doc = result['data']
        self.assertEqual(code, 0)
        for text in ('链接：' + doc['url'], '对方需登录且在分享范围内才能打开。', '未指定分享对象，目前只有你自己能看。'):
            self.assertIn(text, result['report'])
        body = json.loads(self.posts()[0]['body'])
        self.assertLessEqual(set(body), FIELDS)
        self.assertEqual(hashlib.sha256(base64.b64decode(body['html_base64'])).hexdigest(), body['sha256'])
        self.assertEqual([(r['status'], r['doc_id']) for r in self.manifest()],
                         [('prepared', None), ('uploaded', doc['id'])])
        self.assertEqual(list((self.home / 'docs/v2/.pending').iterdir()), [])

    def test_local_rejections_send_no_request(self):                # C-FR-02、C-FR-03
        big = self.home / 'big.html'
        big.write_bytes(b'<html>' + b'a' * 2_097_200 + b'</html>')
        gbk = self.home / 'gbk.html'
        gbk.write_bytes(HTML.replace('周报', '').encode() + '中'.encode('gbk'))
        cases = [('upload', str(self.page), '--summary', ''), ('upload', str(self.page), '--summary', '长' * 1001),
                 ('upload', str(big), '--summary', 's'), ('upload', str(gbk), '--summary', 's')]
        for argv in cases:
            with self.subTest(argv=argv[1:3]):
                self.assertEqual(self.cli(*argv)[0], 2)
        self.assertEqual(self.fake.calls, [])

    def test_share_failures_are_listed(self):                       # C-FR-04
        code, result = self.upload('--share-friend', '乙', '--share-friend', '丁',
                                   '--share-group', '测试组', '--share-group', '接口组')
        self.assertEqual(code, 0)
        for text in ('以下对象没有分享成功：', '丁（不是你的好友）', '接口组（没有这个群组）', '可以到网页的分享管理里补分享。'):
            self.assertIn(text, result['report'])
        self.assertNotIn('全部分享成功', result['report'])

    def test_all_shares_succeed_reported_truthfully(self):          # C-FR-04
        code, result = self.upload('--share-friend', '乙', '--share-group', '测试组')
        self.assertEqual(code, 0)
        self.assertIn('已全部分享成功：', result['report'])
        self.assertNotIn('没有分享成功', result['report'])

    def test_retry_reuses_identical_body_and_key(self):             # 重点关注 1
        self.fake.fail_next = ['network', (503, 'internal_error')]
        self.assertEqual(self.upload()[0], 0)
        posts = self.posts()
        self.assertEqual(len(posts), 3)
        self.assertEqual(len({(p['body'], p['idem']) for p in posts}), 1)
        self.assertEqual(len(self.fake.docs), 1)

    def test_repeat_upload_returns_same_document(self):             # C-FR-03 验收
        first = self.upload()[1]['data']
        code, result = self.upload()
        self.assertEqual((code, result['data']['id'], len(self.fake.docs)), (0, first['id'], 1))
        self.assertIn('同一篇', result['report'])
        self.assertEqual(len({p['idem'] for p in self.posts()}), 1)

    def test_reupload_after_delete_needs_new(self):                 # 契约 §3.3
        first = self.upload()[1]['data']['id']
        del self.fake.docs[first]
        code, result = self.upload()
        self.assertEqual(code, 2)
        self.assertIn('--new', result['report'])
        code, result = self.upload('--new')
        self.assertEqual(code, 0)
        self.assertNotEqual(result['data']['id'], first)

    def test_remote_unknown_then_resume_returns_same_document(self):
        self.fake.fail_next = ['lost'] + ['network'] * 5            # 服务端已处理，但响应丢失
        code, result = self.upload()
        self.assertEqual(code, 4)
        client_id = result['data']['client_upload_id']
        self.assertIn('resume ' + client_id, result['report'])
        code, result = self.cli('resume', client_id)
        self.assertEqual((code, len(self.fake.docs)), (0, 1))
        self.assertIn('链接：' + result['data']['url'], result['report'])

    def test_resume_after_24h_does_not_resend(self):                # 重点关注 3
        self.fake.fail_next = ['network'] * 6
        client_id = self.upload()[1]['data']['client_upload_id']
        before = len(self.posts())
        self.clock.now += 24 * 3600 + 1
        code, result = self.cli('resume', client_id)
        self.assertEqual((code, len(self.posts())), (4, before))
        self.assertIn('超过 24 小时', result['report'])

    def test_replace_reports_link_unchanged(self):                  # C-FR-05
        doc = self.upload()[1]['data']
        code, result = self.cli('replace', doc['id'], str(self.page), '--summary', '更新后的摘要。',
                                '--share-friend', '乙')
        self.assertEqual((code, result['data']['id'], result['data']['url']), (0, doc['id'], doc['url']))
        for text in ('链接不变', '到期时间已从现在重新起算', '替换只会新增分享，不会移除已有分享'):
            self.assertIn(text, result['report'])

    def test_replace_missing_document(self):                        # C-FR-08
        code, result = self.cli('replace', 'f' * 32, str(self.page), '--summary', 's')
        self.assertEqual(code, 2)
        self.assertIn('不存在或无权限', result['report'])

    def test_incompatible_version_blocks_writes(self):              # C-FR-01
        self.fake.api_version = '3.0'
        code, result = self.upload()
        self.assertEqual((code, [c['path'] for c in self.fake.calls]), (2, ['/api/v2/me']))
        self.assertIn('契约主版本不兼容', result['report'])

    def test_unauthorized_stops_without_retry(self):                # C-FR-07
        code, result = self.cli('upload', str(self.page), '--summary', 's', key='w' * 24)
        self.assertEqual((code, len(self.fake.calls)), (2, 1))
        self.assertIn('API Key', result['report'])
        self.assertNotIn('w' * 24, json.dumps(result, ensure_ascii=False))

    def test_daily_limit_not_retried(self):                         # C-FR-08
        self.fake.daily_limit = 1
        self.assertEqual(self.upload()[0], 0)
        code, result = self.cli('upload', str(self.page), '--summary', '另一段摘要。')
        self.assertEqual((code, len(self.posts())), (2, 2))
        self.assertIn('次日', result['report'])

    def test_me_command(self):
        code, result = self.cli('me')
        self.assertEqual((code, result['data']['api_version']), (0, '2.0'))
        self.assertIn('今日已用 0/100 次', result['report'])
```

- [ ] **步骤 2：运行，确认失败**

运行：`cd tests && python3.12 -m unittest test_v2_upload -v`

预期：报 `ModuleNotFoundError: No module named 'docsctl'`。

- [ ] **步骤 3：实现** `bin/docsctl.py` 的 `build_parser`、`run` 以及 `me`、`upload`、`replace`、`resume`（按 Interfaces）

- [ ] **步骤 4：确认通过，全量回归，提交**

先运行 `cd tests && python3.12 -m unittest test_v2_upload -v`，预期 `Ran 15 tests`、`OK`；再跑全量测试，预期 `Ran 43 tests`、`OK`。然后提交：

```bash
git add bin/docsctl.py tests/test_v2_upload.py
git commit -m "feat(V2): U05 上传、替换与恢复"
git push
```

---

### U06：文档维护命令

**依赖：** U05

**Files:**
- Modify: `bin/docsctl.py`
- Test: `tests/test_v2_manage.py`

**Interfaces（Produces）：** 在 `build_parser()` 里补齐子命令（C-FR-06）：

| 子命令 | 参数 | 请求 |
| --- | --- | --- |
| `list` | `--query TEXT`（对应 `q`）、`--tag T`（可重复，多个须同时含有）、`--from TIME`、`--to TIME`、`--sort {uploaded_desc,uploaded_asc,title}`、`--limit N`、`--cursor C`、`--all` | `GET /documents` |
| `get` | `DOC_ID` | `GET /documents/{id}` |
| `edit` | `DOC_ID`、`--title`、`--summary` 或 `--summary-file`、`--tag T`（可重复，整体替换）或 `--clear-tags` | `PATCH /documents/{id}` |
| `favorite`、`unfavorite` | `DOC_ID` | `PUT` 或 `DELETE /documents/{id}/favorite` |
| `delete` | `DOC_ID`、`--yes` | 不带 `--yes` 时只发 GET；带了才发 `DELETE` |
| `tags` | 无 | `GET /tags` |

**本地校验**（不合格返回 2，一个请求也不发）：
- `DOC_ID` 经过 `clean_doc_id`；
- `--from`、`--to` 必须是带时区的 RFC 3339（`datetime.fromisoformat` 能解析，且 `tzinfo` 不为空），原样传给服务端的 `uploaded_from`、`uploaded_to`；
- `--limit` 在 1–200，默认 50；
- 两组选项互斥：`--all` 与 `--cursor`、`--tag` 与 `--clear-tags`；
- `edit` 至少要改一项，各字段经过 `fields` 校验。PATCH 请求体只带用户给出的字段。

**各命令的行为：**
- **`list`**
  - 不带 `--all` 时只取一页，`data` 为服务端响应；有 `next_cursor` 时，`report` 末尾写「还有下一页：加 --cursor <c> 继续，或用 --all 取全部」。
  - 带 `--all` 时，从无游标开始一路翻到 `next_cursor` 为空，`data` 为 `{"items": […], "count": n}`；`report` 写「共 n 篇」，最多列 50 行，其余看 `data`。
  - 翻页中出现以下任一情况，立即停止，返回 2，`report` 写「服务端分页异常（<原因>），已停止，结果不完整」（重点关注 4）：
    - 某个游标第二次出现；
    - 同一个 `id` 在不同页重复出现；
    - 某页是空的，却还有 `next_cursor`。
- **`get`**：`report` 列出标题、链接、摘要、标签、上传时间、到期时间（或「收藏中，不会过期」）。
- **`edit`**：`report` 写清改了哪几项；改了标签时写「标签已整体替换为：…」，清空时写「标签已清空」。
- **`favorite`**：`report` 含「收藏中，不会过期」。
- **`unfavorite`**：`report` 含「有 24 小时宽限」和新的到期时间（C-FR-06）。
- **`delete`**
  - 不带 `--yes`：先 GET 这篇文档，返回 3，`status` 为 `confirm_required`，`data` 为元数据。`report` 写：「将永久删除《<标题>》（<链接>）。删除后所有人立即无法打开，不能恢复。请先向用户确认；用户同意后执行：python3 bin/docsctl.py delete <id> --yes」。
  - 带 `--yes`：发 DELETE，收到 204 返回 0。
- **`tags`**：`report` 写「共 n 个标签：周报（2）、月报（1）…」；没有标签时写「还没有用过标签」。
- **单次写请求失败**（`edit`、`favorite`、`unfavorite`、`delete --yes`）：
  - 抛 `RemoteUnknown` 时返回 4，`report` 写「结果未知，请用 python3 bin/docsctl.py get <id> 核对」；
  - 其他非 2xx 返回 2，`report` 用 `describe`。

- [ ] **步骤 1：写失败测试** `tests/test_v2_manage.py`

```python
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import docsctl
from v2_fake import FakeDocs


class ManageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home, self.fake = Path(tmp.name), FakeDocs()

    def cli(self, *argv):
        out = io.StringIO()
        env = {'HOME': str(self.home), 'DOCS_API_KEY': self.fake.key, 'DOCS_ORIGIN': self.fake.origin}
        code = docsctl.run(list(argv), env=env, out=out, sleep=lambda s: None, transport=self.fake.transport)
        result = json.loads(out.getvalue())
        self.assertEqual(result['exit_code'], code)
        return code, result

    def calls(self, method):
        return [c for c in self.fake.calls if c['method'] == method and c['path'].startswith('/api/v2/documents')]

    def one_doc(self):
        self.fake.seed(1)
        return next(iter(self.fake.docs))

    def test_list_page_passes_filters(self):
        self.fake.seed(3)
        code, _ = self.cli('list', '--query', '文档', '--tag', '周报', '--tag', '月报', '--from', '2027-01-01T00:00:00Z',
                           '--to', '2027-02-01T00:00:00+08:00', '--sort', 'title', '--limit', '10')
        self.assertEqual(code, 0)
        self.assertEqual(self.calls('GET')[-1]['query'],
                         {'q': ['文档'], 'tag': ['周报', '月报'], 'uploaded_from': ['2027-01-01T00:00:00Z'],
                          'uploaded_to': ['2027-02-01T00:00:00+08:00'], 'sort': ['title'], 'limit': ['10']})

    def test_list_rejects_bad_options_locally(self):
        for argv in (('--from', '2027-01-01'), ('--limit', '0'), ('--limit', '201'), ('--sort', 'size'),
                     ('--all', '--cursor', 'x')):
            with self.subTest(argv=argv):
                self.assertEqual(self.cli('list', *argv)[0], 2)
        self.assertEqual(self.fake.calls, [])

    def test_list_all_1000_without_duplicates_or_gaps(self):        # C-FR-06 验收
        self.fake.seed(1000)
        for sort in ('uploaded_desc', 'uploaded_asc', 'title'):
            with self.subTest(sort=sort):
                code, result = self.cli('list', '--all', '--limit', '200', '--sort', sort)
                ids = [item['id'] for item in result['data']['items']]
                self.assertEqual((code, len(ids), set(ids)), (0, 1000, set(self.fake.docs)))

    def test_list_all_stops_on_repeated_cursor(self):               # 重点关注 4
        self.fake.seed(300)
        self.fake.broken_cursor = True
        code, result = self.cli('list', '--all', '--limit', '100')
        self.assertEqual((code, len(self.calls('GET'))), (2, 2))
        self.assertIn('分页异常', result['report'])

    def test_get_edit_and_favorite_cycle(self):
        doc_id = self.one_doc()
        self.assertEqual(self.cli('get', doc_id)[1]['data']['id'], doc_id)
        code, result = self.cli('edit', doc_id, '--title', ' 新标题 ', '--tag', '月报')
        self.assertEqual((code, result['data']['title'], result['data']['tags']), (0, '新标题', ['月报']))
        self.assertEqual(json.loads(self.calls('PATCH')[-1]['body']), {'title': '新标题', 'tags': ['月报']})
        code, result = self.cli('favorite', doc_id)
        self.assertEqual((code, result['data']['favorite'], result['data']['expires_at']), (0, True, None))
        self.assertIn('收藏中，不会过期', result['report'])
        code, result = self.cli('unfavorite', doc_id)
        self.assertEqual((code, result['data']['favorite']), (0, False))
        self.assertIsNotNone(result['data']['expires_at'])
        self.assertIn('有 24 小时宽限', result['report'])

    def test_edit_requires_a_field(self):
        doc_id = self.one_doc()
        for argv in ((), ('--tag', 'a', '--clear-tags')):
            with self.subTest(argv=argv):
                self.assertEqual(self.cli('edit', doc_id, *argv)[0], 2)
        self.assertEqual(self.fake.calls, [])

    def test_delete_requires_confirmation(self):                    # C-FR-06 验收
        doc_id = self.one_doc()
        code, result = self.cli('delete', doc_id)
        self.assertEqual((code, result['status'], self.calls('DELETE')), (3, 'confirm_required', []))
        self.assertIn(f'delete {doc_id} --yes', result['report'])
        self.assertIn(doc_id, self.fake.docs)
        self.assertEqual(self.cli('delete', doc_id, '--yes')[0], 0)
        self.assertNotIn(doc_id, self.fake.docs)
        code, result = self.cli('delete', doc_id, '--yes')
        self.assertEqual(code, 2)
        self.assertIn('不存在或无权限', result['report'])

    def test_tags_command(self):
        self.fake.seed(2, tags=['周报'])
        self.fake.seed(1, tags=['月报'])
        code, result = self.cli('tags')
        self.assertEqual(code, 0)
        for text in ('周报（2）', '月报（1）'):
            self.assertIn(text, result['report'])

    def test_bad_doc_id_rejected_locally(self):
        for command in ('get', 'favorite', 'delete'):
            with self.subTest(command=command):
                self.assertEqual(self.cli(command, '../x')[0], 2)
        self.assertEqual(self.fake.calls, [])

    def test_write_with_lost_response_is_unknown(self):
        doc_id = self.one_doc()
        self.fake.fail_next = ['lost']
        code, result = self.cli('favorite', doc_id)
        self.assertEqual(code, 4)
        self.assertIn(f'get {doc_id}', result['report'])
```

- [ ] **步骤 2：运行，确认失败**

运行：`cd tests && python3.12 -m unittest test_v2_manage -v`

预期：报 `invalid choice: 'list'` 之类的子命令错误，用例 FAIL。

- [ ] **步骤 3：实现**（按 Interfaces 改 `bin/docsctl.py`）

- [ ] **步骤 4：确认通过，全量回归，提交**

先运行 `cd tests && python3.12 -m unittest test_v2_manage -v`，预期 `Ran 10 tests`、`OK`；再跑全量测试，预期 `Ran 53 tests`、`OK`。然后提交：

```bash
git add bin/docsctl.py tests/test_v2_manage.py
git commit -m "feat(V2): U06 文档维护命令"
git push
```

---

### U07：安全加固

**依赖：** U06

**Files:**
- Modify: `bin/docsctl.py`
- Test: `tests/test_v2_security.py`

**Interfaces（Produces）：**
- **不许把 Key 发出去**（C-FR-02、C-FR-07）
  - 适用于 `upload`、`replace`、`edit`。把 U05 第 4 步的「读取 Key」提前到最前面；读完 Key、`--summary-file` 和 HTML 文件后，在任何字段校验之前检查全部原始输入：命令行参数的每个值、`--summary-file` 的内容、HTML 文件的字节。`source_info` 的值都取自 HTML 和文件名，所以也一并覆盖了；
  - Key 出现在任何一处，就返回 2，`report` 为「要发送的内容里出现了你的 API Key，已停止。请先删掉再试。」，不复述 Key；
  - 放在字段校验之前，是为了让任何字段里的 Key 都由这同一条提示拦下，比如超长标签里的 Key。
- **兜底**：`run()` 捕获所有 `Exception`，按进度处理：
  - 已经 `prepare` 过（有 `client_upload_id`）：尽量记一行 `remote_unknown`（记不进也不再抛），返回 4，给出 resume 命令；
  - 已经用 `send` 发出写请求：返回 4，`report` 写「结果未知，请用 python3 bin/docsctl.py get <id> 核对」；
  - 其他情况：返回 2。

  `report` 只写异常的类名，不写异常文本，因为文本里可能有服务端回显或 Key。
- **解析器里不能有接收 Key 的选项**：任何选项名都不得包含 `key` 或 `token`（C-FR-07：Key 不进 argv）。

- [ ] **步骤 1：写失败测试** `tests/test_v2_security.py`

```python
import argparse
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import docsctl
from v2_fake import FakeDocs

HTML = '<!doctype html><html><head><meta charset="utf-8"><title>周报</title></head><body>{}</body></html>'


class SecurityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home, self.fake = Path(tmp.name), FakeDocs(key='Sk.' + 'z' * 40)
        self.page = self.home / 'a.html'
        self.page.write_text(HTML.format('x'), encoding='utf-8')
        self.outputs = []

    def cli(self, *argv):
        out = io.StringIO()
        env = {'HOME': str(self.home), 'DOCS_API_KEY': self.fake.key, 'DOCS_ORIGIN': self.fake.origin}
        code = docsctl.run(list(argv), env=env, out=out, sleep=lambda s: None, rng=lambda a, b: 0,
                           transport=self.fake.transport)
        self.outputs.append(out.getvalue())
        return code, json.loads(out.getvalue())

    def assert_no_token_in_outputs(self):
        self.assertEqual([text for text in self.outputs if self.fake.key in text], [])

    def test_token_never_in_any_output(self):                       # 重点关注 2、C-FR-07
        self.fake.echo_token = True
        doc_id = self.cli('upload', str(self.page), '--summary', 's')[1]['data']['id']
        runs = [([], ('upload', str(self.page), '--summary', 's2')),
                ([(400, 'invalid_request')], ('upload', str(self.page), '--summary', 's3')),
                (['network'] * 6, ('upload', str(self.page), '--summary', 's4')),
                (['lost'], ('edit', doc_id, '--title', 't')),
                ([], ('get', 'f' * 32)), ([], ('list', '--all')), ([], ('delete', doc_id)),
                ([(500, 'internal_error')], ('favorite', doc_id)), ([], ('me',))]
        for failures, argv in runs:
            self.fake.fail_next = list(failures)
            self.cli(*argv)
        self.assert_no_token_in_outputs()
        for path in self.home.rglob('*'):
            if path.is_file():
                self.assertNotIn(self.fake.key.encode(), path.read_bytes(), str(path))
        self.assertEqual([c for c in self.fake.calls if self.fake.key in c['path'] + json.dumps(c['query'])], [])

    def test_unexpected_exception_is_contained(self):
        self.fake.fail_next = ['crash']
        code, result = self.cli('upload', str(self.page), '--summary', 's')
        self.assertEqual(code, 4)
        self.assertIn('RuntimeError', result['report'])
        self.assertIn('resume ' + result['data']['client_upload_id'], result['report'])
        self.fake.fail_reads = ['crash']
        self.assertEqual(self.cli('tags')[0], 2)
        self.assert_no_token_in_outputs()

    def test_key_in_content_is_refused(self):                       # C-FR-02、C-FR-07
        leaky = self.home / 'leak.html'
        leaky.write_text(HTML.format(self.fake.key), encoding='utf-8')
        self.fake.seed(1)
        doc_id = next(iter(self.fake.docs))
        for argv in (('upload', str(leaky), '--summary', 's'),
                     ('upload', str(self.page), '--summary', '密钥 ' + self.fake.key),
                     ('upload', str(self.page), '--summary', 's', '--share-friend', self.fake.key),
                     ('edit', doc_id, '--title', self.fake.key)):
            with self.subTest(argv=argv[0]):
                code, result = self.cli(*argv)
                self.assertEqual(code, 2)
                self.assertIn('API Key', result['report'])
        self.assertEqual(self.fake.calls, [])
        self.assert_no_token_in_outputs()

    def test_no_option_accepts_a_key(self):                         # C-FR-07：Key 不进 argv
        parser = docsctl.build_parser()
        subparsers = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        options = {o for p in (parser, *subparsers.choices.values()) for a in p._actions for o in a.option_strings}
        self.assertEqual([o for o in options if 'key' in o or 'token' in o], [])
        self.assertEqual(self.cli('upload', str(self.page), '--summary', 's', '--api-key', 'x' * 24)[0], 2)
        self.assertEqual(self.fake.calls, [])
```

- [ ] **步骤 2：运行，确认失败**

运行：`cd tests && python3.12 -m unittest test_v2_security -v`

预期：至少 `test_key_in_content_is_refused` 和 `test_unexpected_exception_is_contained` FAIL。U05、U06 已经做到的部分可能先通过，这是正常的。

- [ ] **步骤 3：实现**（按 Interfaces 改 `bin/docsctl.py`）

- [ ] **步骤 4：确认通过，全量回归，提交**

先运行 `cd tests && python3.12 -m unittest test_v2_security -v`，预期 `Ran 4 tests`、`OK`；再跑全量测试，预期 `Ran 57 tests`、`OK`。然后提交：

```bash
git add bin/docsctl.py tests/test_v2_security.py
git commit -m "feat(V2): U07 安全加固"
git push
```

---

### U08：技能说明与参考文档

**依赖：** U07

**Files:**
- Modify: `SKILL.md`（重写）、`references/credentials-posix.md`、`references/credentials-windows.md`
- Create: `references/commands.md`、`references/errors.md`、`references/manifest.md`
- Test: `tests/test_v2_docs.py`

**要求：**
- **`SKILL.md`**：中文，按下面的顺序分节。测试会检查下表里的短语逐字出现。
  1. frontmatter：`name: html-upload`。`description` 写明适用场景和边界：
     - 适用：上传本机的单个 HTML、拿回链接、替换正文、查清单、改标题、摘要和标签、收藏、删除；
     - 用户说「上传这个 HTML」「传到 docs」「更新那篇」「删掉那篇」时使用；
     - 不生成 HTML，不读正文。
  2. 依赖与前提：
     - 依赖契约 docs-api v2，主版本不符时工具报错退出，不降级；
     - 需要 `python3` 3.12 或以上；
     - Key 的配置方法见 `references/credentials-*.md`。
  3. 命令速查：11 个子命令各给一行 `python3 bin/docsctl.py <子命令> …` 示例。
  4. 摘要规则（C-FR-02）。
  5. 标签与分享规则。
  6. 结果转述与退出码表（0、2、3、4、5 各一行，写明 Agent 该怎么做）。
  7. 替换、维护与删除。
  8. 不要做的事。

  | 必须出现的短语 | 来源 |
  | --- | --- |
  | `docs-api v2`、`主版本` | C-FR-01 |
  | `目标 100–300 字`、`硬上限 1000 字`、`用户给了摘要就用用户的`、`不要照抄页面开头的导航、标题、目录或样板文字`、`不要写入 Key、手机号等敏感信息` | C-FR-02 |
  | `只用用户给出的标签` | C-FR-06 |
  | `把 report 原样转述给用户`、`不要把部分成功说成全部成功` | C-FR-03、C-FR-04 |
  | `用 replace，不要另传一篇` | C-FR-05 |
  | `先不带 --yes 预览`、`得到用户明确同意后才加 --yes` | C-FR-06 |
  | `只有用户确认要另起一篇时才加 --new` | 契约 §3.3 |
  | `不要绕过本工具直接请求接口` | C-FR-07 |

- **`references/commands.md`**：每个子命令一节，标题为 `## <子命令>`，写明参数、`data` 的形状和可能的退出码。
- **`references/errors.md`**：契约的 11 个错误码，每个写明 HTTP 状态码、含义，以及该怎么告诉用户（与 `client.ADVICE` 一致）。
- **`references/manifest.md`**：`~/docs/v2/` 的目录结构、manifest 各字段、五种状态、档案什么时候删、`resume` 和 24 小时规则。
- **`references/credentials-posix.md`**：Key 页面地址改为 `https://docs.zh0uyuan.com/keys`。**`references/credentials-windows.md`**：核对一遍，删掉 V1 的说法。

- [ ] **步骤 1：写失败测试** `tests/test_v2_docs.py`

```python
import argparse
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
import client
import docsctl
import local

PHRASES = ('docs-api v2', '主版本', '目标 100–300 字', '硬上限 1000 字', '用户给了摘要就用用户的',
           '不要照抄页面开头的导航、标题、目录或样板文字', '不要写入 Key、手机号等敏感信息', '只用用户给出的标签',
           '把 report 原样转述给用户', '不要把部分成功说成全部成功', '用 replace，不要另传一篇',
           '先不带 --yes 预览', '得到用户明确同意后才加 --yes', '只有用户确认要另起一篇时才加 --new',
           '不要绕过本工具直接请求接口')


def read(path):
    return path.read_text(encoding='utf-8')


class DocTests(unittest.TestCase):
    def setUp(self):
        self.skill = read(ROOT / 'SKILL.md')
        self.refs = {p.name: read(p) for p in (ROOT / 'references').glob('*.md')}

    def test_skill_frontmatter(self):
        self.assertTrue(self.skill.startswith('---\nname: html-upload\ndescription: '))

    def test_skill_required_phrases(self):
        for phrase in PHRASES:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.skill)

    def test_every_subcommand_documented(self):
        parser = docsctl.build_parser()
        action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        self.assertEqual(set(action.choices), {'me', 'upload', 'replace', 'resume', 'list', 'get', 'edit',
                                               'favorite', 'unfavorite', 'delete', 'tags'})
        for name in action.choices:
            with self.subTest(name=name):
                self.assertIn(f'docsctl.py {name}', self.skill)
                self.assertIn(f'## {name}', self.refs['commands.md'])

    def test_every_error_code_documented(self):                     # C-FR-08
        for code in client.ADVICE:
            with self.subTest(code=code):
                self.assertIn(f'`{code}`', self.refs['errors.md'])

    def test_exit_codes_documented(self):
        for code in ('0', '2', '3', '4', '5'):
            with self.subTest(code=code):
                self.assertRegex(self.skill, rf'\|\s*{code}\s*\|')

    def test_no_v1_leftovers(self):
        texts = {'SKILL.md': self.skill, **self.refs, **{p.name: read(p) for p in (ROOT / 'bin').glob('*.py')}}
        for name, text in texts.items():
            for word in ('taxonomy', '/console/', 'upload.py', '/api/v1', 'confirm-prompt'):
                with self.subTest(file=name, word=word):
                    self.assertNotIn(word, text)
        for text in (self.refs['credentials-posix.md'], local.KEY_GUIDANCE):
            self.assertIn('https://docs.zh0uyuan.com/keys', text)
```

- [ ] **步骤 2：运行，确认失败**

运行：`cd tests && python3.12 -m unittest test_v2_docs -v`

预期：多个用例 FAIL，因为 `SKILL.md` 还是 U00 的占位，参考文档也还没写。

- [ ] **步骤 3：按要求写文档**

- [ ] **步骤 4：确认通过，全量回归，提交**

先运行 `cd tests && python3.12 -m unittest test_v2_docs -v`，预期 `Ran 6 tests`、`OK`；再跑全量测试，预期 `Ran 63 tests`、`OK`。然后提交：

```bash
git add SKILL.md references/ tests/test_v2_docs.py
git commit -m "docs(V2): U08 技能说明与参考文档"
git push
```

---

### U09：与 docs-service V2 本机联调

**依赖：**
- U08；
- docs-service 里程碑一的 T09 已完成，即 `../docs-service/app/api_v2.py` 已经存在。

**Files:**
- Create: `tests/test_v2_integration.py`

**要求：**
- 用真实的 HTTP 请求，对接 docs-service V2 的真实代码，数据库和存储都用临时的；不连外网。
- **只读使用 docs-service：**
  - 那个仓库可能正由另一个会话在 `feat/v2-m1` 上开发，不改它的任何文件，不在里面切分支；
  - 导入前设 `sys.dont_write_bytecode = True`，不在它的目录里留下缓存文件。
- **找 docs-service：** 优先用环境变量 `DOCS_SERVICE_DIR`，默认为 `tests/` 往上两级的 `docs-service`（`html-upload/` 和 `html-upload-v2/` 里都能找到）。
- **找不到 docs-service V2 时**（导入失败），整组跳过并写明原因。CI 里没有 docs-service，所以 CI 上总是跳过。
- **联调失败时：**
  - 原因在本仓库：修本仓库。
  - 原因是服务端与契约不符：不改契约，也不改 docs-service。在 PR 里写明是哪个调用、预期是什么、实际是什么，然后停下这一项。

- [ ] **步骤 1：确认 docs-service V2 可用**

```bash
test -f ../docs-service/app/api_v2.py && git -C ../docs-service log --oneline -1
```

如果文件还不存在，照常完成步骤 2–4（测试会跳过），在 PR 里写「U09 联调待补跑：docs-service T09 未完成」，U09 先不勾选。

- [ ] **步骤 2：写测试** `tests/test_v2_integration.py`

```python
"""与 docs-service V2 的本机联调：真实 HTTP、真实服务端代码、临时数据库；不连外网，不改 docs-service。"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

HERE = Path(__file__).resolve().parent
SERVICE = Path(os.environ.get('DOCS_SERVICE_DIR', HERE.parents[1] / 'docs-service'))
sys.path.insert(0, str(HERE.parent / 'bin'))
import docsctl

SKIP = None
try:
    sys.dont_write_bytecode = True
    sys.path.append(str(SERVICE))
    from app.api_v2 import AgentAPI
    from app.services import apikeys, sharing
    from app.services.storage import MIN_FREE_BYTES
    spec = importlib.util.spec_from_file_location('docs_service_v2_support', SERVICE / 'tests/v2_support.py')
    support = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(support)
except Exception as exc:                                    # docs-service 还没有 V2：整组跳过
    SKIP = f'docs-service V2 不可用：{type(exc).__name__}'
finally:
    sys.dont_write_bytecode = False


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


@unittest.skipIf(SKIP, SKIP)
class IntegrationTests(unittest.TestCase):
    def setUp(self):
        connect = support.file_db(self)
        self.root = support.new_storage(self)
        self.conn = connect()
        self.addCleanup(self.conn.close)
        self.me = support.add_user(self.conn, '甲')
        friend = support.add_user(self.conn, '乙')
        sharing.add_friend(self.conn, self.me, friend, support.T0)
        group = sharing.create_group(self.conn, self.me, '测试组', support.T0)
        sharing.add_member(self.conn, self.me, group, friend, support.T0)
        self.key = apikeys.generate(self.conn, self.me, support.T0)
        try:
            server = make_server('127.0.0.1', 0, None, handler_class=QuietHandler)
        except OSError:
            self.skipTest('本环境不允许回环 socket')
        self.origin = f'http://127.0.0.1:{server.server_port}'
        server.set_app(AgentAPI(connect, storage_root=self.root, origin=self.origin, clock=support.Clock(),
                                free_bytes=lambda: MIN_FREE_BYTES))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.home = Path(home.name)

    def cli(self, *argv, key=None):
        out = io.StringIO()
        env = {'HOME': str(self.home), 'DOCS_API_KEY': key or self.key, 'DOCS_ORIGIN': self.origin}
        code = docsctl.run(list(argv), env=env, out=out)
        return code, json.loads(out.getvalue())

    def page(self, name, title, body='x'):
        path = self.home / name
        path.write_text(f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title></head>'
                        f'<body>{body}</body></html>', encoding='utf-8')
        return str(path)

    def test_full_flow(self):                                       # 契约 §4 的 10 个接口逐个走一遍
        self.assertEqual(self.cli('me')[1]['data']['api_version'].split('.')[0], '2')
        args = ('upload', self.page('a.html', '周报'), '--summary', '本周进展。', '--tag', '周报',
                '--share-friend', '乙', '--share-friend', '丁', '--share-group', '测试组', '--share-group', '接口组')
        code, result = self.cli(*args)
        doc = result['data']
        self.assertEqual((code, doc['url']), (0, f"{self.origin}/d/{doc['id']}"))
        for text in ('丁（不是你的好友）', '接口组（没有这个群组）'):
            self.assertIn(text, result['report'])
        self.assertEqual(self.cli(*args)[1]['data']['id'], doc['id'])                     # 重放：同一篇
        self.assertEqual([i['id'] for i in self.cli('list', '--tag', '周报')[1]['data']['items']], [doc['id']])
        edited = self.cli('edit', doc['id'], '--title', '新标题', '--tag', '月报')[1]['data']
        self.assertEqual((edited['title'], edited['tags']), ('新标题', ['月报']))
        self.assertIsNone(self.cli('favorite', doc['id'])[1]['data']['expires_at'])
        self.assertIsNotNone(self.cli('unfavorite', doc['id'])[1]['data']['expires_at'])
        code, result = self.cli('replace', doc['id'], self.page('b.html', '周报 v2', 'y'), '--summary', '更新后的摘要。')
        self.assertEqual((code, result['data']['id'], result['data']['url']), (0, doc['id'], doc['url']))
        self.assertIn({'tag': '月报', 'count': 1}, self.cli('tags')[1]['data']['items'])
        self.assertEqual(self.cli('delete', doc['id'])[0], 3)
        self.assertEqual(self.cli('delete', doc['id'], '--yes')[0], 0)
        self.assertEqual(self.cli('get', doc['id'])[0], 2)
        self.assertEqual(self.cli('me', key='x' * 24)[0], 2)

    def test_list_all_1000_with_duplicate_sort_keys(self):          # C-FR-06 验收、D55
        ids = {support.add_doc(self.conn, self.root, self.me, title=f'标题{i % 7}', uploaded_at=support.T0 + i // 10)
               for i in range(1000)}
        for sort in ('uploaded_desc', 'uploaded_asc', 'title'):
            with self.subTest(sort=sort):
                code, result = self.cli('list', '--all', '--limit', '37', '--sort', sort)
                got = [item['id'] for item in result['data']['items']]
                self.assertEqual((code, len(got), set(got)), (0, 1000, ids))
```

- [ ] **步骤 3：运行**

运行：`cd tests && python3.12 -m unittest test_v2_integration -v`

预期：docs-service T09 已完成时为 `Ran 2 tests`、`OK`，且没有 skipped；还没完成时为 `OK (skipped=2)`。

全量测试：`Ran 65 tests`、`OK`。

- [ ] **步骤 4：提交**

```bash
git add tests/test_v2_integration.py
git commit -m "test(V2): U09 与 docs-service V2 本机联调"
git push
```

---

## 测试方案

### 分层

| 层 | 测什么 | 测试文件 | 网络 |
| --- | --- | --- | --- |
| L1 单元 | 字段规则、本地检查、档案与 manifest、Key 引导脚本 | `test_v2_fields`、`test_v2_local`、`test_v2_store`、`test_shell` | 无 |
| L2 组件 | 传输、重试、错误提示；对照按契约写的假服务端 | `test_v2_client`（含一个回环用例） | 回环 |
| L3 命令行 | `docsctl.run()` 全流程：注入 transport、clock、sleep、rng；含安全与文档检查 | `test_v2_upload`、`test_v2_manage`、`test_v2_security`、`test_v2_docs` | 无 |
| L4 联调 | 对接 docs-service V2 的真实代码 | `test_v2_integration` | 回环 |
| 人工 | 摘要质量、真实站点、Key 重新生成 | H1–H3 | 生产站点，只在上线当天由人操作 |

### 环境

- Python 3.12，只用标准库；
- 一律用临时 HOME、假 Key：
  - 不读真实的 `~/.config/docs/api-key` 和 `~/docs/`；
  - 不访问 `docs.zh0uyuan.com`。
- CI（GitHub Actions）：
  - 跑 `python3.12 -m unittest discover -s tests`；
  - 没有 docs-service，L4 跳过，结果是 `OK (skipped=2)`。
- 本机：`../docs-service` 有 V2 代码，或设了 `DOCS_SERVICE_DIR` 时，L4 真正运行。

### 需求追溯

| 需求 | 验收要点 | 测试 |
| --- | --- | --- |
| C-FR-01 版本协商 | 主版本不符就停，不发写请求 | `client::test_me_and_version`、`upload::test_incompatible_version_blocks_writes` |
| C-FR-02 摘要 | 空摘要、超长摘要在本地拦下；摘要里不能有 Key；摘要质量 | `fields::test_summary_rules`、`upload::test_local_rejections_send_no_request`、`security::test_key_in_content_is_refused`；质量见 H1 |
| C-FR-03 上传 | 不超过 2 MiB、UTF-8、sha256；只发契约字段；重复执行不出第二篇；交出链接和登录说明；manifest 不含 Key | `local::test_read_html_limits`、`local::test_inspect_rejects_bad_bytes`、`upload::test_upload_success_reports_url_and_login_note`、`upload::test_repeat_upload_returns_same_document`、`upload::test_retry_reuses_identical_body_and_key`、`client::test_upload_key_is_derived_from_body`、`store::test_prepare_writes_private_files_and_manifest` |
| C-FR-04 分享结果 | 逐项列出失败项和原因；全部成功时如实说 | `upload::test_share_failures_are_listed`、`upload::test_all_shares_succeed_reported_truthfully`、`integration::test_full_flow` |
| C-FR-05 替换 | ID、链接不变；只追加分享；告知到期时间重算 | `upload::test_replace_reports_link_unchanged`、`integration::test_full_flow` |
| C-FR-06 维护 | 1000 条不重不漏；删除要确认；告知收藏宽限；只处理元数据、不留正文 | `manage::test_list_all_1000_without_duplicates_or_gaps`、`integration::test_list_all_1000_with_duplicate_sort_keys`、`manage::test_delete_requires_confirmation`、`manage::test_get_edit_and_favorite_cycle`、`store::test_finalize_records_and_clears_pending` |
| C-FR-07 Key | 不进 argv、URL、输出、manifest；401 零重试 | `security::test_token_never_in_any_output`、`security::test_no_option_accepts_a_key`、`client::test_unauthorized_is_not_retried`、`upload::test_unauthorized_stops_without_retry`、`local::test_credential_file_permissions` |
| C-FR-08 错误 | 11 个错误码都有提示；429 不自动重试；除 `request_id` 外不暴露服务端信息 | `client::test_advice_covers_all_contract_codes`、`client::test_definite_errors_are_not_retried`、`upload::test_daily_limit_not_retried`、`upload::test_replace_missing_document`、`docs::test_every_error_code_documented` |
| 契约 §3.1 字段 | 长度、个数、去重、名称不做规范化 | `test_v2_fields` 全部 |
| 契约 §3.3 幂等 | 重放、24 小时、文档已删除时 404 | `upload::test_reupload_after_delete_needs_new`、`upload::test_remote_unknown_then_resume_returns_same_document`、`upload::test_resume_after_24h_does_not_resend` |
| 契约 §4 接口 | 10 个接口对着真实服务端各走一遍 | `integration::test_full_flow` |

### 门禁

- **每个任务：** 先看到新测试失败，再实现到通过；全量测试全绿，用例数与该任务末尾写的一致。
- **全部完成：**
  - `Ran 65 tests`、`OK`；
  - 本机 L4 真正运行过，不是跳过；
  - CI 绿；
  - Claude Code 审查过 PR。

### 人工验收

在上线当天做，与 docs-service 测试计划的 M4（技能切换）一起：

| 编号 | 项目 | 做法 |
| --- | --- | --- |
| H1 | 摘要质量（C-FR-02 验收） | 让 Agent 分别上传三份不同类型的 HTML：html-studio 报告、长文章、带导航栏的网页。逐份检查摘要：纯文本；不超过 1000 字；概括了主题；没有照抄导航或目录；没有敏感信息 |
| H2 | 真实站点全流程 | 上传 → 浏览器登录后打开链接 → 分享给一位好友，对方能打开 → 替换正文，链接不变 |
| H3 | Key 重新生成 | 在 `/keys` 重新生成 Key，旧 Key 调 `me` 立即得到 401 提示，且零重试；换上新 Key 后恢复正常 |
| H4 | 自选标签（D65） | 让 Agent 上传一篇、不给标签：报告列出 1–3 个标签，优先沿用已有的，不是「HTML」「网页」这类词；再让它改这篇的标签，确认是整组替换 |

## 完成标准与交接

- **测试与提交：**
  - 全部步骤已勾选；`python3.12 -m unittest discover -s tests` 输出 `Ran 65 tests`、`OK`（CI 上为 `skipped=2`）；
  - `feat/v2` 上共 10 个提交，U00–U09 各一个。
- **U09 联调：** 已在本机真正运行并通过。如果那时 docs-service 的 T09 还没完成，在 PR 里写明「U09 联调待补跑」，其余照常交付。
- **交出 PR：** 执行 `gh pr ready`，并在 PR 里留一条总结：
  - 各任务的提交；
  - 测试数；
  - 联调结果；
  - 采用了哪些默认做法（见下面的「待确认」）。
- **合并时机（D50）：** 审查通过后，PR 保持打开，不合并；等服务端 V2 上线当天由人合并。合并后：
  1. 在 `html-upload/` 里 `git pull`，线上技能随之切到 V2；
  2. 执行 `git worktree remove ../html-upload-v2`。

## 待确认（已按默认做法实现，主人可改）

1. **入口名称：** `bin/docsctl.py`（V1 是 `bin/upload.py`）。
2. **平台地址：** 仍硬编码生产站点，`DOCS_ORIGIN` 只接受回环地址。
   - 需求 §4 写的是「平台地址通过配置注入」；
   - 放开任意地址，Key 就可能被一个环境变量引到别的站点；
   - 以后要支持别的正式站点时，再加白名单。
3. **幂等键：** 上传的幂等键由请求体派生，同样的内容和参数在 24 小时内只出一篇；要另起一篇用 `--new`。替换每次用新键。
4. **本地留存：** 成功后不留正文副本，只在 manifest 里记一行。V1 会在 `~/docs` 下留备份。
5. **删除确认：** 删除分「预览」和「`--yes`」两步，退出码 3 表示在等用户确认。
6. **版本协商时机：** 每次联网都先调 `GET /me`。需求只要求每次会话开始时调一次。
7. **外部资源：** 不再提示页面引用了外部资源。V2 允许引用外部资源（服务端需求 FR-07）。
