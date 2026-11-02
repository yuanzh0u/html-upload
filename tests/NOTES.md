# 实施记录

T0：开工只读执行 `git -C /home/ryan/projects/html-platform/docs-service status --porcelain`；输出为空。
技能工作区不是 Git 仓库；按调用方指定目录直接交付，不创建 worktree、不提交、不安装共享技能。
先落 SKILL.md 骨架是调用方明确要求，优先于 PLAN T12 的“最后写”；T12 再完成正文。
测试只用临时 HOME、127.0.0.1 假服务器与假凭证；禁止请求生产站点。

## 分段实施与 RED → GREEN

- T1–T7：先运行 test_documents，7 项因对应文件未落盘而失败；逐项落盘及复验后 7 项通过。
- T8：normalize 初始行为没有裁剪、casefold 或拒绝非法值；test_vectors/test_reject 先失败，实现后通过。
- T9：inspect_html 初始行为未校验字节与 meta，test_byte_rejections/test_time/test_conflicts/test_external 等先失败，实现后通过。
- T10：match 初始始终 unresolved，无命中/候选，test_alias_and_name/test_approximate/test_over_200 先失败，实现后通过。
- T11.1：先尝试 fake_server，socket.socket 被沙箱以 EPERM 拒绝；保留 127.0.0.1 动态端口 HTTP 夹具，并在不能创建 socket 时切换进程内传输桩。改用桩后测试因缺自检行为失败，实现后通过。
- T11.2：force-confirm、缺模型、缺时区初始返回 2，测试要求 3；实现确认门后通过。
- T11.3：预备份/manifest 初始未落盘，哈希故障未返回 5；实现后通过，零 POST。
- T11.4：成功、逐项复用/新建、别名、冲突收尾、重放恢复测试先失败，实现后通过。
- T11.5：409 processing、429、503、500 初始只发送一次；加入有界重试、跨进程预算和原字节复用后通过。422 刷新计划先不能恢复，补齐服务端候选并入及新确认后通过。
- T11.6：before-POST 观察测试先发现 stdout 为空；采用 JSON external_links 前缀先 flush，再补齐结果，确保 422 的 stdout 仍为一个合法 JSON。
- 补充回归先暴露：非法 worker 搭配 model-id 返回 2、成功缺 me 两条说明、未知恢复的 401 变回 2、过期新词确认仍 POST、预备份失败报告缺 pending 路径、IncompleteRead 未进入重试。分别修正并复验。
- T12：按调用方要求先写骨架，再在所有引用存在后完成正文。skill-creator 的 quick_validate 检查通过。

## 已验证与未验证边界

Python 3.12.3，bash 5.2.21，zsh 5.9。tests/test_shell.py 将假值通过 stdin 输入、以临时 HOME 运行核心；bash/zsh 均不输出假值，文件 0600、目录 0700、无尾 LF，临时目录自动删除。文档 shell 核心与脚本同源。

没有 shellcheck、pwsh、powershell。PowerShell 文件保持 verified: no；未提供未执行命令。没有真实控制终端复跑，TTY 版本仍为待终端复跑。

所有上传场景采用本地传输桩（本沙箱禁止 socket）；urllib 的 Request/头/超时/无代理/禁止重定向另由 test_transport 用内存响应检查。真实 TCP/TLS/30 秒读取超时和真实服务联调未验证。没有向生产服务发起任何请求。

未做独立 Agent 的真实行为前测/后测；不将文档结构检查当成模型行为验证。最终命令及退出码保存在随交付的 validation.log，服务仓库结束状态见 report.md。
