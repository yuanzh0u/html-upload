# 任务：P6 ⑤ html-upload 独立验收（DSH，最终闸门）

你是独立验证人。你**没有参与**本项目的方案、实施、测试与复审，请保持这个视角。工作目录 `/home/ryan/projects/html-upload`（cwd；只读审计 + 本地运行，不改任何被验文件）。

## 验收对象与基线

- 交付物：`SKILL.md`、`bin/`（4 个脚本）、`references/`（7 篇）、`templates/`（2 个）、`tests/`（112 项）
- 验收基线：`PLAN.md` 第 12 节验收标准；协议口径 `/home/ryan/work/docs-service/docs/DESIGN-v3.md` §10（只读参考）
- 修复轮证据：`/home/ryan/projects/acp-dispatch/state/tasks/20261020T223221Z-codex-97efb2be1b/artifacts/report.md`（11 项修复清单）

## 验收动作（逐项留证据）

1. **全量复跑**：`python3 -m unittest discover -s tests -v`，记录计数与结果；3 项 skip 须与「沙箱禁 socket」的披露一致，不得多出新的 skip
2. **修复抽查**（至少各 1 个直接证据）：CRLF 凭证被接受、charset=gbk 被拒（退出 2）、连续 422 保留已确认 choices、ESC URL 不进纯文本报告、10 万层嵌套 JSON 落约定退出码、`--resume --dry-run` 退出 2
3. **端到端 dry-run 走查**：造一篇含 plans:completed/plans:worker meta 的合规 HTML 与一篇缺 meta 的，用临时 HOME + 假 Key + 本地桩（tests/fake_server.py 或等价）分别走一遍完整流程，核对：dry-run 零 POST、缺 meta 暂停（退出 3）且不提交、成功路径本地备份/manifest/报告三件套齐全且字节一致
4. **SKILL.md 与实现一致性**：技能文档写的每个命令/参数在 bin/ 里真实存在；文档承诺的退出码与实现一致；触发边界（不生成 HTML/不改服务端/不重建索引）在实现里没有暗门
5. **供应链与痕迹**：确认交付物里没有外网请求硬编码以外的地址、没有假 Key 以外的密钥形态残留、没有调试输出残留 Key 的路径

## 交付

报告写入 artifacts（`report.md`）：总体结论（**通过 / 打回**）→ 五个验收动作的证据（命令+输出摘要）→ 发现列表（若有，按严重度）→ 验收范围声明（没验什么、为什么）。

## 纪律

- 不改任何被验文件（包括测试）；需要辅助脚本就放 artifacts/ 或 /tmp
- 不得请求 `https://docs.zh0uyuan.com` 或任何外网
- 打回必须有可复现证据；通过必须逐项有证据，不接受「看起来对」
- 840s 纪律：报告分段写盘
