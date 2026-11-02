# 任务：P6 ③-a html-upload 对抗性测试（AGY）

你是测试工程师。工作目录 `/home/ryan/projects/html-upload`（cwd 即工作区）。

## 背景

html-upload 技能由 Codex 按 PLAN.md 实施完毕：bin/（normalize.py、check_html.py、match_taxonomy.py、upload.py）、tests/（56 项 unittest 全绿）、SKILL.md、references/。你的任务是**找出他们没测到的洞**，不是复述已有覆盖。

## 必读

- `PLAN.md` 第 12 节验收标准 + §10 对应原文（必要参考 `/home/ryan/work/docs-service/docs/DESIGN-v3.md` §10，只读）
- `bin/upload.py` 全文（主流程）
- `tests/NOTES.md`（已有覆盖边界）

## 交付

1. `tests/test_adversarial_agy.py`（新增 unittest 文件，不改动现有测试与实现）：针对下列方向至少各 1 例——
   - 凭证：权限位边界（0600 通过/0640 拒绝之外的组合，如 0604、目录 0755、符号链接指向的 key 文件）、环境变量为空串/纯空白、Key 含尾随换行
   - HTML 校验：UTF-16 伪装、极小文件、title 含控制字符、meta charset 与字节实际编码冲突、超大文件行为
   - meta 时间：闰秒、时区缩写歧义（CST）、`Z` 与 `+00:00` 混用、年份 0001/9999
   - taxonomy：同名不同大小写、全角/半角近似、候选恰好 200 个/第 201 个、确认期间 422 连续变化
   - manifest：并发追加（两个进程）、磁盘满模拟、JSONL 某行损坏后的恢复
   - 传输：响应 200 但 body 非 JSON、docid 含路径分隔符、Content-Length 与实际不符、重定向到异形 URL
   - 外链扫描：HTML 实体编码的 URL、protocol-relative `//evil`、CSS `url()` 大小写混合、`@import`
2. 报告到 artifacts（`report.md`）：每项新测试的意图、通过/失败；**发现的每一个真实缺陷**按「现象/复现命令/期望/实际」格式列出

## 纪律

- **只加测试，不改实现**（bin/、SKILL.md、references/ 一律不动）；发现的缺陷写报告，由后续 Codex 修复
- 不得请求 `https://docs.zh0uyuan.com`；一律本地桩/dry-run
- 跑全量：`python3 -m unittest discover -s tests -v`，结果贴报告
- 不 commit、不发外部消息；840s 超时纪律：测试文件分段写盘
- 若某方向写不出有效对抗用例，在报告说明原因，不凑数
