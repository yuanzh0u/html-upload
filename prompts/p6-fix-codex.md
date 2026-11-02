# 任务：P6 ④ html-upload 缺陷修复（Codex）

你是修复实施者。工作目录 `/home/ryan/projects/html-upload`（cwd 即工作区）。③ 两道闸门（AGY 对抗测试 36 项全过 + Kimi 安全复审通过附低危）共发现以下缺陷，逐一修复。

## 缺陷清单（按优先级）

### P0 功能正确性（AGY 发现）

**D2｜凭证文件 CRLF 残留拒绝合法 Key**
`bin/upload.py` 读文件只剥单个 `\n`，CRLF 残留的 `\r` 触发 `ord(c)<33` 拒绝。修复：剥除尾部 `\r\n`/`\n`（`rstrip('\r\n')` 一类，但不得动 token 中部字符）。复现与现状断言：`tests/test_adversarial_agy.py::test_cred_file_crlf_trailing_newline_defect`。

**D3｜环境变量尾随换行未规整**
环境变量分支无任何剥除。修复：与文件分支同一规整口径。现状断言：`test_cred_env_trailing_newline_defect`。

**D4｜连续 422 重规划丢失已确认 choices**
`TaxonomyPause` 后 `make_plan(original_args,…)` 从原始参数重建，丢弃上一轮确认文件中的 choices。修复：重规划时合并前一轮已确认且不受本次 422 影响的 choices。现状断言：`test_taxonomy_consecutive_422_cascade_and_state_loss`。

### P1 校验完整性（AGY + Kimi）

**D1｜meta charset 冲突未检测**
HTML 声明 `charset=gbk/utf-16/...` 与字节实际 UTF-8 冲突时 `check_html.py` 直接放行。修复：检测到非 UTF-8 charset 声明时进入待澄清（退出码 3）或拒绝，口径与 PLAN §5 一致；现存断言见 `test_html_meta_charset_conflict_defect`。

**F-1｜外链 URL 控制字符进纯文本报告**（Kimi）
`check_html.py:75-78` 的 `url()` 只剥空白引号；`upload.py:183-188` 纯文本报告原样打印 URL。修复：收录前拒绝含 Cc/Cf 字符的 URL，或报告拼接时转义（二选一，优先源头拒绝）。

**D5｜CSS url() 正则遇括号截断**
`[^)]*` 在带括号参数的 URL 上提前截断。修复：支持引号包裹的完整 URL（先匹配引号内内容，裸值仍按原规则）。

### P2 健壮性（Kimi）

**F-2｜RecursionError 未捕获**
`upload.py:287,292`（confirmation_input）与 `upload.py:638-641`（load_pending）的 json.loads 未捕 RecursionError。修复：except 网增加 RecursionError，落入约定退出码。

**F-3｜凭证无最小长度**
1 字符 token 通过校验后令 emit() 脱敏误伤输出。修复：加最小长度 ≥16（或站点签发格式校验）。

### P3 顺手项（Kimi 提示，低成本）

**F-4**：`--resume` 与 `--dry-run` 同用 → 参数校验阶段拒绝（退出 2）。
**F-5**：`merge_server_items` 党选值 id/name/score 加长度上限与 normalize 校验；`valid_success` 对 normalized_metadata.title 加同样校验。
**F-7**：`references/manifest.md` 补一句家务说明（pending 只增不减、定期人工归档）。

## 纪律（测试循环规则）

- AGY 的 `_defect` 后缀测试记录的是**现状缺陷行为**：修好实现后必须**更新这些测试断言为修复后行为**，不是删测试。每修一项，对应测试从「记录缺陷」翻转为「断言正确行为」
- 修完全量跑绿：`python3 -m unittest discover -s tests -v`（基线 92 项 OK、3 跳过；你更新缺陷断言后仍须全绿，跳过项不动）
- 不改 SKILL.md 的行为承诺与 references/ 的协议口径（F-7 的家务说明除外）；不碰 docs-service；不请求外网；不 commit；不发外部消息
- 840s 纪律：逐文件落盘
- 报告到 artifacts（`report.md`）：每项缺陷的修复点（文件:行）、对应测试翻转证据、全量测试结果、未修项及原因

## 完成标志

92+ 项测试全绿（含翻转后的断言）；报告含逐项修复证据。
