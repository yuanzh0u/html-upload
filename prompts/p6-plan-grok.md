# 任务：撰写 P6（html-upload 技能）实施方案

你是方案撰写人。本轮**只写方案，不写实现代码**。

## 背景

docs-service（仓库 `/home/ryan/work/docs-service`）P0–P5 已完成并收口（见 `reports/P5-closeout.md`）。P6 范围已在 `docs/DESIGN-v3.md` §10 完整定义：创建 **html-upload** 技能——只读消费任意来源的合规 HTML 并上传到 docs 服务；另含 S10：上传外链提示**进技能文案**（不改服务端、不加警告字段、不把外链改成拒绝，拍板见 `docs/P5-design.md` H-3）。

## 必读材料（先读再写）

1. `docs/DESIGN-v3.md`：**§10 全文**（技能职责、Key 引导、确认状态机、manifest、错误恢复）+ **§5**（上传 JSON 协议）+ **§11**（标签字典与治理）
2. `docs/P5-design.md`：H-3、S10 两节（外链提示的拍板口径）
3. `reports/P5-closeout.md`：上下文与残余
4. `/home/ryan/projects/html-platform/html-studio/SKILL.md` 及其目录结构：技能组织方式参考（SKILL.md + references/ + bin/ + templates/）

## 产出（写入 `/home/ryan/projects/html-upload/`）

唯一交付物：`/home/ryan/projects/html-upload/PLAN.md`——给实施者（Codex）的完整实施方案。

必须覆盖（与 §10.1–§10.5 一一对应，缺一不可）：

1. **触发条件与边界**：技能做什么、不做什么（不生成 HTML、不改服务端、不重建索引）
2. **输入参数**：title/project/type/生成时间/模型/ACL、`force-confirm`、`dry-run`，默认值与校验
3. **凭证安全与 OS 分派**：DOCS_API_KEY → `~/.config/docs/api-key`（600）→ 引导 的固定顺序；bash/zsh/PowerShell 各自的安全读取与落盘命令草案（bash `read -rs`、PowerShell `Read-Host -AsSecureString`、NTFS 仅本人 ACL，不伪称 Windows chmod 等效）；不回显 Key、不进 history
4. **GET /api/v1/me 自检**：身份/scope/有效期确认，401/403 即停
5. **HTML/meta 校验**：UTF-8 无 BOM 无 NUL、doctype、`plans:completed`/`plans:worker` 提取与冲突处理（缺时区、多 meta 冲突、不明 worker 均转待澄清，不猜）
6. **taxonomy 匹配与确认状态机**：稳定 ID/规范名/别名匹配 → 近似建议复用 → 新建逐项 create_confirmed；`existing_default` 与显式确认两种模式的边界；确认期间 taxonomy 变化（422 刷新）的处理；非交互执行遇强制确认输出待确认清单并暂停
7. **提交协议与幂等**：§5 JSON 协议、Base64 原字节、标题来源、请求哈希、幂等键复用规则（仅同一请求重试复用）
8. **本地备份与 manifest**：`~/docs/<docid>.html`、`.pending/` 预备份、原子替换、hash 校验、`manifest.jsonl` 追加事件日志（含 §10.4 表全部字段）、`remote_unknown` 恢复路径、"远程成功本地失败"的报告口径
9. **错误恢复**：401/403/409/413/422/429/503 各自的处置（Retry-After 有界重试、指数退避加抖动、409 三分支不混）
10. **S10 外链提示文案**：具体文案（技能在提交前扫描外链并在报告/确认中提示「默认无外网资源」），明确**不改服务端**
11. **最终报告格式**：线上链接、docid、本地副本、manifest 状态、待办；绝不输出 Key
12. **验收标准**：每一节配可机器检查或可人工点检的验收条目
13. **实施任务拆分**：给 Codex 的任务清单（文件级：SKILL.md、references/、bin/ 脚本各自内容与顺序），每任务带边界与验收

## 硬性要求（流程教训，必须遵守）

- **acpx 单轮 840s 硬超时**：先把 PLAN.md 完整骨架（全部一二级标题）落盘，再逐节追加写盘；**严禁**试图一次输出全文
- 边界：方案不改 docs-service 服务端任何文件；不建设 html-trans/html-spec
- Windows 段落只给「经实施验证的语法差异说明」，不编造未验证命令
- 写完自检：对照 §10.1–§10.5 逐节打勾，缺节即返工；把自检清单附在 PLAN.md 末尾

## 完成标志

`/home/ryan/projects/html-upload/PLAN.md` 存在、覆盖上述 13 节、末尾附 §10 对照自检清单。
