# Windows 客户端验收记录

2026-11-24 在 x64 Windows NT 10.0.26300、本地 NTFS、Windows PowerShell 5.1 和独立目录中的 CPython 3.12.10 embeddable 上完成验证。回归使用临时 HOME、测试凭据和本机回环 HTTP 服务。

| 环境 | 执行方式 | 结果 |
| --- | --- | --- |
| Windows 实机 | 专用解释器运行 `-B -m unittest discover -s tests -v` | 323 项，316 通过，7 跳过 |
| Linux | Python 3.12 运行相同套件，`DOCS_SERVICE_DIR` 指向相邻服务端源码 | 323 项，302 通过，21 项 Windows 专属检查跳过 |

Windows 跳过四项依赖相邻 docs-service 的联调、两项 POSIX 专属检查，以及当前脚本策略禁止的 PowerShell 包装脚本测试。Linux 执行了真实服务端回环联调，包括 U09 完整流程和 1000 条同排序键文档的分页检查。

原生测试覆盖 UTF-8 JSON、中文和空格路径、CRLF 摘要、从另一目录执行恢复命令、相同请求字节与幂等键、跨进程互斥、占用时替换失败和私有文件清理。权限检查在测试创建的对象上执行。

实机 PowerShell 的有效策略为 `Restricted`。另在独立 SSH 交互终端运行 `python.exe .\bin\configure_key.py --prompt`，在提示出现后输入随机假 Key。配置成功，退出码为 0，终端输出未出现该值。测试没有读取用户的历史文件。

验证对象为技能客户端及其命令。从 Windows Codex 会话自动选择技能、跨机器迁移 pending、突然断电后的持久性均未实测。
