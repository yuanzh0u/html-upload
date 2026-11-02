# 任务：P6 ③-b html-upload 安全复审（Kimi，第④道闸门）

你是安全复审人，独立复审。工作目录 `/home/ryan/projects/html-upload`（cwd；被审对象在此，只读审计口径）。

## 复审对象

html-upload 技能首轮交付：`SKILL.md`、`bin/`（normalize.py、check_html.py、match_taxonomy.py、upload.py）、`references/`、`templates/`、`tests/`。方案基线 `PLAN.md`；协议基线 `/home/ryan/work/docs-service/docs/DESIGN-v3.md` §10（只读参考）。

## 复审清单（逐项给结论：通过/缺口/风险建议）

1. **凭证安全**：Key 是否可能出现在日志/报告/manifest/错误消息/进程参数/shell history；环境变量无效时是否真不回退；权限位校验能否被符号链接绕过；引导文案是否会把用户引向不安全操作
2. **注入面**：HTML 解析（正则解析 HTML 的边界）、title/meta 提取值进入报告/请求前是否消毒；taxonomy 名称进入 JSON 请求是否注入安全；manifest JSONL 字段含换行/控制字符的防护
3. **路径安全**：`~/docs`、`.pending/`、manifest 路径拼接能否被 docid（服务端返回值）注入（如 `../../`）；原子替换的 TOCTOU
4. **传输安全**：HTTPS 固定站点校验、重定向不转发 Key、证书校验是否可能被禁用；幂等键与请求哈希的构造是否可伪造/碰撞
5. **确认绕过**：`existing_default` 默认确认路径能否被构造的 taxonomy 响应滥用；`force-confirm` 是否真强制；dry-run 是否有任何路径会 POST
6. **可用性副作用**：限流重试是否可能放大服务端压力；本地备份磁盘占用的防护

## 交付

报告写入 artifacts（`report.md`），结构：总体结论（通过/打回）→ 逐项结论与证据（文件:行号）→ 发现列表（每条：严重度/现象/利用场景/修复建议）→ 复核范围声明。

## 纪律

- **只读**：不改任何被审文件；不运行会上网的代码；运行测试仅限 `python3 -m unittest discover -s tests`（本地桩）
- 不编造未验证的缺陷；每个「缺口」必须给出可复现证据
- 840s 超时纪律：报告分段写盘（先骨架后逐节）
