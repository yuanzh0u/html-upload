# 原生 Windows 支持设计

负责人已于 2026-11-24 批准 W1–W7 方案并授权实施。

## 目标与范围

- 原生 Windows x64、PowerShell 5.1+、Python 3.12+、本地 NTFS。
- 共用上传、替换、恢复、维护业务逻辑和 docs-api v2 契约。
- 只用 Python 标准库；Windows API 通过 ctypes 调用。
- 保持 JSON 字段、退出码 0/2/3/4/5、24 小时恢复期限和原样重发语义。
- 不迁移其他平台的 pending；不支持 UNC、网络盘、非 NTFS、重解析私有路径。

## 平台边界

`bin/fs_posix.py` 保留现有 POSIX 文件操作；`bin/fs_windows.py` 提供相同接口。
`bin/private_fs.py` 按 os.name 选择实现。local.py 和 store.py 只委托文件系统操作。
所有新创建的 Windows 私有文件和目录使用显式、受保护 DACL：只向当前用户 SID
授权；不接受 Everyone、Users、Authenticated Users 或其他账户的允许 ACE。
验证属主为当前用户、DACL 非空且无继承、对象类型与预期一致。
管理员提权获取所有权属于操作系统管理边界，不声称能阻止管理员。

## 文件与并发

Windows 使用原生句柄打开与校验；拒绝 reparse point、备用数据流、设备命名空间、
路径穿越和非本地 NTFS。读取 HTML 也拒绝可重定向路径。
创建时应用私有 ACL，不采用先默认创建后 chmod 的方式。
稳定的独立锁文件使用 LockFileEx，支持阻塞与非阻塞；恢复和清理使用同一把锁。
文件写入后 flush/fsync，同目录替换使用带写穿透的 Windows 操作。
不对 Windows 目录执行无效的 POSIX fsync，也不承诺断电时与 Linux 完全相同的保证；
明确验证进程中断、文件占用、替换失败和恢复。

## 凭据与命令

保持 HOME 非空优先、否则 Path.home()，必须为绝对路径；同一次解析用于所有记录。
保持 DOCS_API_KEY 优先（已设置但为空也不回退），否则 .config/docs/api-key。
增加 PowerShell 无回显配置脚本，通过 stdin 传递 Key 给 Python；不放命令参数或日志。
CLI 输出 UTF-8，Windows 恢复命令引用实际解释器和脚本绝对路径。

## 验证与交付

先写失败测试，再实现；Linux 保留已有断言。POSIX 专属检查按平台隔离，Windows
增加实际 ACL、junction、句柄、锁和失败恢复检查；不整体跳过业务或安全测试。
公共业务测试在两个平台运行，Windows 专属测试在 JS Mini 36 与 CI 实际运行。
测试使用临时 HOME、假 Key、临时回环服务；不读用户凭据、真实 docs 档案或生产服务。
Linux 真实服务端联调只读使用 docs-service；Windows 使用真实 HTTP 回环假服务。
交付分支、PR、测试与环境记录，不合并 PR，不切换线上技能。
