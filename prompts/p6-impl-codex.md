# 任务：P6 ② 实施 html-upload 技能（按 PLAN.md）

你是实施者。工作目录 `/home/ryan/projects/html-upload`（cwd 即工作区）。

## 输入

`/home/ryan/projects/html-upload/PLAN.md`（1042 行）——Grok 撰写的实施方案，含 13 节（触发边界/输入参数/凭证安全/me 自检/meta 校验/taxonomy 状态机/提交协议幂等/备份 manifest/错误恢复/S10 外链提示文案/最终报告/验收标准/任务拆分）。**先通读，再按其「实施任务拆分」节逐一实现。**

## 交付（全部落在工作区内）

- `SKILL.md`：技能本体（frontmatter + 触发条件与边界 + 全流程指引），按 PLAN 定稿内容
- `references/`：PLAN 规划的参考文档（协议说明、确认状态机、manifest 字段表、错误恢复表等，以 PLAN 为准）
- `bin/`：PLAN 规划的脚本（如凭证引导、预检、上传等；以 PLAN 为准）
- 报告写入 artifacts（`report.md`）：文件清单、自检结果、与 PLAN 的偏差、未验证项

## 自检要求（写完必须做，结果写进报告）

- 每个 bash 脚本过 `bash -n`；有 shellcheck 则过 shellcheck（没有就注明未跑）
- zsh 段落：本机有 zsh 则用 `zsh -n` 验证语法并记录结果；没有就如实标「未验证」
- Windows/PowerShell 命令只按 PLAN 标注的口径写，不编造「已验证」
- **不得对生产服务 `https://docs.zh0uyuan.com` 发起任何请求**；涉及上传行为的自验一律 dry-run 或本地桩
- 不修改 `/home/ryan/work/docs-service` 任何文件

## 纪律

- acpx 单轮 840s 硬超时：**逐文件落盘**，先写 SKILL.md 骨架再补全，再写 references/ 和 bin/；不要试图一次输出超大文件
- 不 git commit/push；不发送外部消息；不改共享 worker 安装
- 与 PLAN 冲突处不得擅自发挥：记录在报告的「偏差」节，按 PLAN 口径实现或标出待拍板

## 完成标志

工作区内 SKILL.md 存在且非空（调度器会强制验证）；PLAN「任务拆分」节列的每个文件要么落盘、要么在报告里说明未做原因；报告含自检证据。
