# html-upload V2 实施计划二：没给标签时由 Agent 自选，上传结果列出标签（D65）

> **执行者（Codex）：** 先读仓库根目录的 `AGENTS.md`，再读本文。本文前半是简短设计，后半是唯一的任务 A1。
> - `AGENTS.md` 里还留着 V2 开发期的安排，与本文冲突时以本文为准：
>   - 改动在 worktree `../html-upload-tags/`（分支 `feat/v2-auto-tags`）里做；
>   - `main` 已经是 V2；
>   - PR 何时合并由负责人决定。
>
>   本任务顺带把 `AGENTS.md` 的这几处改掉，见步骤 4。
> - 执行方式同 docs-service 的计划五至十：Codex 改代码、测试和文档，不提交、不推送；Claude Code 在沙箱外跑需要回环端口的用例，然后提交、推送、维护 PR。
> - 遇到计划没写、又会影响接口或行为的选择，按 `AGENTS.md` 的「遇到问题」处理。

**目标：**
- 用户没给标签时，Agent 先看已用标签，给新文档自选 1–3 个，优先沿用；
- 上传、替换和恢复成功的报告列出标签。

**架构：**
- 规则写在 `SKILL.md`、`references/commands.md` 和技能端需求里。工具本身不选标签，标签仍由 Agent 用 `--tag` 传入。
- `bin/docsctl.py` 新增 `tags_line()`，在成功报告的登录说明之后加一行标签。

**技术栈：** Python 3.12 标准库。

**依据：**
- docs-service 的决定 D65：负责人 2026-11-24 确认，记在 docs-service 的 `docs/V2-requirements-v1.2-decisions.md`；
- 技能端需求 C-FR-03、C-FR-06；
- 契约 docs-api v2.0 不改。

## 设计（简短，负责人 2026-11-24 确认）

- **问题：** 「我的文档」看不到标签。技能规定「只用用户给出的标签」，用户上传时又几乎不给，负责人的 20 篇文档一个标签都没有。
- **新规则（甲）：**
  - 用户给了标签就原样使用，不增不减；用户说不要标签就不加。
  - 用户没给时，上传前先运行 `tags` 看已用标签，按文档用途和内容自选 1–3 个简短标签：
    - 优先沿用已有标签，确实没有合适的才新建；
    - 标签要能把这篇和其他文档区分开，不用「HTML」「网页」这类篇篇都适用的词。
  - 上传结果列出所用标签。用户要改，随时用 `edit --tag` 整组替换，或用 `--clear-tags` 清空。
  - 已有文档的标签只在用户要求时才改：替换正文不改标签，旧文档不自动补打。
- **报告：**
  - 下面三种情况，报告在「对方需登录且在分享范围内才能打开。」之后加一行「标签：周报、项目」，没有标签时写「没有标签」：
    - 上传、替换成功；
    - `resume` 补发成功；
    - `resume` 在清单里找到同一篇。
  - 只做离线补记的 `resume` 拿不到标签，不加这一行。
  - 服务端返回的 `tags` 不是字符串列表时也不加这一行，成功与否不受影响。
- **不变：**
  - 命令、参数和请求体；
  - `get` 报告的写法；
  - 替换不改标签；
  - 标签限制：最多 20 个，每个 1–40 字。
- **生效：**
  - 合并后在 `html-upload/` 里 `git pull`，本机的技能立即生效；
  - 同事那边要同步到 Gitee 才生效，同步前先问负责人。

**比较过的做法**（负责人已选定）：
- 甲，Agent 自选、用户随时可改：已选；
- 乙，每次上传先问用户：太打扰；
- 丙，维持只用用户给的：标签会一直是空的。

## 全局约束

- 只用标准库；不改契约、不改请求体、不加命令行参数。
- 报告措辞逐字为「标签：」加用「、」连接的标签，以及「没有标签」。标签按服务端返回的顺序列出，不再排序或改写。
- 标签行只出现在成功报告里，紧跟在「对方需登录且在分享范围内才能打开。」那一行之后。
- 测试只用临时 HOME、假 Key 和 `tests/v2_fake.py`。

## 重点关注（容易出事、但正常用例覆盖不到的五类输入）

1. **服务端返回的 `tags` 缺失或不是字符串列表：** 上传仍按成功处理（退出 0，manifest 记 uploaded），报告不出标签行。
   → `test_malformed_tags_leave_success_alone`
2. **24 小时内重放同一上传，服务端返回 200：** 报告同样列出标签，位置不变。
   → `test_upload_report_lists_tags`
3. **恢复时在清单里找到同一篇：** 先列出标签，再说明没有重复发送。
   → `test_found_upload_report_lists_tags`
4. **用户给的标签顺序与服务端不同：** 报告照服务端返回的顺序（请求体已按原样字符串排序）。
   → `test_upload_report_lists_tags`
5. **旧规则残留：** 「只用用户给出的标签」「不表示可自动替用户选标签」「技能不得自行编造标签」要从技能说明、命令参考和需求里删干净，否则 Agent 会读到两条相反的规则。
   → `test_old_tag_rule_is_gone`

---

### A1：没给标签时自选，报告列出标签

**Files:**
- Modify: `bin/docsctl.py`、`SKILL.md`、`references/commands.md`
- Test: `tests/test_v2_upload.py`（加 4 个用例）、`tests/test_v2_review_u5.py`（加 1 个用例）、`tests/test_v2_docs.py`（改 `PHRASES`，加 1 个用例）
- Docs: `docs/V2-requirements.md`、`docs/V2-impl-plan.md`、`AGENTS.md`

**Interfaces:**
- **`docsctl.tags_line(doc) -> str | None`：**
  - `doc.get('tags')` 是列表、且每项都是字符串时：非空返回 `'标签：' + '、'.join(tags)`，空列表返回 `'没有标签'`；
  - 其他情况（缺失、不是列表、有非字符串项）返回 `None`。
- **`success_report()`：** 在开头两行（链接、登录说明）之后插入 `tags_line(doc)` 的结果，为 `None` 时不插；其余各行和顺序不变。
- **`resume` 在清单里找到同一篇时的报告：** 同样在登录说明之后插入 `tags_line(found)`，再接「补发前在清单里找到了同一篇，没有重复发送」。

- [ ] **步骤 1：写失败测试**

**`tests/test_v2_upload.py`**，在 `UploadTests` 里加：

```python
    def test_upload_report_lists_tags(self):                        # D65、重点关注 2、4
        code, result = self.upload('--tag', '项目', '--tag', '周报')
        self.assertEqual(code, 0)
        self.assertIn('对方需登录且在分享范围内才能打开。\n标签：周报、项目\n', result['report'])
        code, again = self.upload('--tag', '周报', '--tag', '项目')   # 24 小时内重放
        self.assertEqual((code, again['data']['id']), (0, result['data']['id']))
        self.assertIn('对方需登录且在分享范围内才能打开。\n标签：周报、项目\n24 小时内', again['report'])

    def test_upload_without_tags_says_so(self):                     # D65
        code, result = self.upload()
        self.assertEqual(code, 0)
        self.assertIn('对方需登录且在分享范围内才能打开。\n没有标签\n', result['report'])

    def test_replace_and_resume_list_tags(self):                    # D65
        self.fake.fail_next = ['lost'] + ['network'] * 5            # 服务端已处理，但响应丢失
        client_id = self.upload('--tag', '周报')[1]['data']['client_upload_id']
        code, result = self.cli('resume', client_id)
        self.assertEqual(code, 0)
        self.assertIn('对方需登录且在分享范围内才能打开。\n标签：周报\n以下分享报告来自首次请求', result['report'])
        code, result = self.cli('replace', result['data']['id'], str(self.page), '--summary', '更新后的摘要。')
        self.assertEqual(code, 0)
        self.assertIn('对方需登录且在分享范围内才能打开。\n标签：周报\n链接不变', result['report'])

    def test_malformed_tags_leave_success_alone(self):              # 重点关注 1
        real = self.fake.transport
        for broken in (None, '周报', ['周报', 1]):
            with self.subTest(tags=broken):
                def transport(method, url, token, body=None, idem=None):
                    status, value, headers = real(method, url, token, body, idem)
                    if method == 'POST' and isinstance(value, dict):
                        if broken is None:
                            del value['tags']
                        else:
                            value['tags'] = broken
                    return status, value, headers
                self.fake.transport = transport
                code, result = self.upload('--tag', '周报', '--new')
                self.assertEqual((code, result['status']), (0, 'success'))
                self.assertNotIn('标签', result['report'])
                self.assertEqual(self.manifest()[-1]['status'], 'uploaded')
```

**`tests/test_v2_review_u5.py`**，在 `ResumeLookupTests` 里加：

```python
    def test_found_upload_report_lists_tags(self):                  # D65、重点关注 3
        doc = self.candidate(tags=['周报', '项目'])
        code, result = self.cli()
        self.assert_found(code, result, doc)
        self.assertIn('对方需登录且在分享范围内才能打开。\n标签：周报、项目\n' + NOTICE, result['report'])
```

**`tests/test_v2_docs.py`：**
- `PHRASES` 去掉 `'只用用户给出的标签'`，在原位置换成六项，整体改为：

  ```python
  PHRASES = ('docs-api v2', '主版本', '目标 100–300 字', '硬上限 1000 字', '用户给了摘要就用用户的',
             '不要照抄页面开头的导航、标题、目录或样板文字', '不要写入 Key、手机号等敏感信息',
             '用户给了标签就原样使用', '用户说不要标签就不加', '先运行 `tags` 查看已用标签', '自选 1–3 个简短标签',
             '优先沿用已有标签', '已有文档的标签只在用户要求时才改',
             '把 report 原样转述给用户', '不要把部分成功说成全部成功', '用 replace，不要另传一篇',
             '先不带 --yes 预览', '得到用户明确同意后才加 --yes', '只有用户确认要另起一篇时才加 --new',
             '不要绕过本工具直接请求接口')
  ```

- 在 `DocTests` 里加：

```python
    def test_old_tag_rule_is_gone(self):                            # D65、重点关注 5
        requirements = read(ROOT / 'docs' / 'V2-requirements.md')
        for text, old in ((self.skill, '只用用户给出的标签'), (self.refs['commands.md'], '不表示可自动替用户选标签'),
                          (requirements, '技能不得自行编造标签')):
            with self.subTest(old=old):
                self.assertNotIn(old, text)
        self.assertIn('上传前用它查看已用标签', self.refs['commands.md'])
        self.assertIn('自选 1–3 个', requirements)
```

- [ ] **步骤 2：运行，确认失败**

Run: `python3.12 -m unittest discover -s tests`
Expected:
- 新加的 6 个用例中 5 个失败；
- `test_malformed_tags_leave_success_alone` 在实现前后都通过，它守的是不该变的地方；
- `test_skill_required_phrases` 里新加的六项短语失败；
- 其余用例通过。

- [ ] **步骤 3：按 Interfaces 实现 `tags_line()` 和两处报告**

- [ ] **步骤 4：更新技能说明与文档**

1. **`SKILL.md`：**
   - 「命令速查」里的上传示例 `--tag '用户标签'` 改为 `--tag '周报'`。
   - 「标签与分享规则」的第一段整段换成：

     ```markdown
     用户给了标签就原样使用，不增不减；用户说不要标签就不加。用户没给时，上传前先运行 `tags` 查看已用标签，按文档的用途和内容自选 1–3 个简短标签：优先沿用已有标签，确实没有合适的才新建；标签要能把这篇和其他文档区分开，不用「HTML」「网页」这类篇篇都适用的词。上传结果会列出所用标签，原样转述即可；用户要改，随时用 `edit --tag` 整组替换，或用 `--clear-tags` 清空。已有文档的标签只在用户要求时才改，替换正文不改标签。`--tag` 可重复；`edit --tag` 整体替换标签，`--clear-tags` 清空，不指定就不改。最多 20 个标签，每个 1–40 字，仅去首尾空白、按原样大小写去重。
     ```

   - 退出码表 `0` 那一行改为「成功；原样转述 report，包括链接、标签、分享结果和到期说明。」。
2. **`references/commands.md`：**
   - **upload：** 「data：元数据加 `share_report: …`；失败原因 `not_friend` / `group_not_found`。」这句之后加「成功的 report 在登录说明之后列出标签，写作「标签：a、b」，没有标签时为「没有标签」。」。
   - **replace：** 「链接、标题、标签不变；」之后加「report 同样列出标签；」。
   - **resume：** 第一段「上传补发前在清单命中时，data 为该篇清单元数据，不包含 share_report；」之后加「补发成功和清单命中时，report 同样列出标签，只做离线补记时没有标签信息，不列这一行；」。
   - **tags：** 「用于查看已用标签，不表示可自动替用户选标签。」改为「上传前用它查看已用标签；用户没给标签时，按技能入口的规则优先沿用其中的标签。」。
3. **`docs/V2-requirements.md` 升到 v1.2：**
   - 版本行改为 `- 版本：v1.2`；日期行改为 `- 日期：2026-11-24（v1.1 于 2026-11-03；v1.0 于 2026-10-22）`；
   - 「服务端需求见 … v1.2」改为 v1.8；
   - C-FR-03 在「用户指定了分享对象时填入 `share_with`…」那一条之后加：

     ```markdown
     - **标签（D65）：** 用户给了标签就原样使用；没给时，Agent 先查看本人已用的标签，给新文档自选 1–3 个简短标签，优先沿用已有标签，确实没有合适的才新建，不用「HTML」「网页」这类篇篇都适用的词。成功后把所用标签告诉用户，没有标签时如实说明。
     ```

   - C-FR-03 的验收末尾加「；上传成功的报告列出所用标签，用户没给标签时 Agent 自选的标签不超过 3 个、优先沿用已有标签（人工 H4）」；
   - C-FR-06 的「技能不得自行编造标签或替用户决定删除；不确定时向用户确认。」改为「技能不得替用户决定删除，已有文档的标签也只在用户要求时才改；不确定时向用户确认。」；
   - 变更记录表加一行：

     ```markdown
     | v1.2 | 2026-11-24 | 依据服务端 D65：用户没给标签时由 Agent 自选 1–3 个、优先沿用已有标签，取代「不得自行编造标签」；上传、替换的报告列出标签；已有文档的标签只在用户要求时才改 |
     ```

4. **`docs/V2-impl-plan.md`** 的「人工验收」表末尾加：

   ```markdown
   | H4 | 自选标签（D65） | 让 Agent 上传一篇、不给标签：报告列出 1–3 个标签，优先沿用已有的，不是「HTML」「网页」这类词；再让它改这篇的标签，确认是整组替换 |
   ```

5. **`AGENTS.md`**，改掉 V2 开发期的旧安排：
   - 开头一句改为「本仓库是 docs 平台的 Agent 端技能。`main` 自 2026-11-21 起就是 V2（PR #4）；V1 的代码保留在标签 `v1-final` 上，不再维护。」；
   - 「线上技能目录」一段改为「**线上技能目录，不要在这里切分支。** `~/.agents/skills/html-upload` 是指向 `html-upload/` 的软链接，各个 Agent 每天都在用其中的 `main`。改动一律在单独的 worktree 里做，路径和分支见当前实施计划的「开工准备」；合并后在 `html-upload/` 里 `git pull`，线上技能才随之更新。」；
   - 文件表在「实施计划与测试方案」一行之后加：

     ```markdown
     | 实施计划二：没给标签时自选（D65） | `docs/V2-impl-plan-2.md` |
     ```

   - 「命令」下的「开发期间即 `html-upload-v2/`」改为「即当前所在的 worktree」；
   - 「分支与 PR」里：
     - 「开工」一条连同其下的 `gh pr create` 代码块和任务清单说明，换成「**开工：** 按当前实施计划的「开工准备」建 worktree 和分支。第一个提交推送后，立即开一个草稿 PR，正文列出计划里的全部任务，每项一行，写成 `- [ ] A1 …` 这样。」；
     - 「**不合并任何 PR。**」之后的说明改为「合并由负责人决定。」。

- [ ] **步骤 5：确认通过（Codex 跑沙箱里能跑的，Claude Code 跑全量）**

- Codex：`python3.12 -m unittest discover -s tests` 共 288 个测试（282 + 6），沙箱拒绝回环端口时相应用例跳过，其余全过。
- Claude Code：沙箱外同一命令 288 个全过、没有跳过，联调用例对 `../docs-service` 真正运行。

提交（Claude Code）：

```bash
git add bin tests SKILL.md references docs AGENTS.md
git commit -m "feat(V2): A1 没给标签时由 Agent 自选，上传结果列出标签（D65）"
git push
```

## 开工准备与交接（Claude Code）

- **开工：**
  1. worktree `../html-upload-tags/` 已从 `origin/main`（`4306c65`）建好，分支 `feat/v2-auto-tags` 不跟踪 `main`；
  2. 提交本计划，提交信息「docs(V2): 自选标签与报告列出标签的计划（D65）」；
  3. `git push -u origin feat/v2-auto-tags`，开草稿 PR「html-upload：没给标签时由 Agent 自选，上传结果列出标签（D65）」。
- **交接：** A1 完成后，标记 PR 为可审查，并留一条总结评论。合并先问负责人。
- **合并后：**
  1. 在 `html-upload/` 里 `git pull --ff-only`，本机技能随之更新；
  2. `git worktree remove ../html-upload-tags`；
  3. 同步 Gitee 前先问负责人，做法见工作区的 `CLAUDE.md`。
