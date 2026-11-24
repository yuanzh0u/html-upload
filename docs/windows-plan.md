# 原生 Windows 支持 Implementation Plan

> 执行者使用 executing-plans 逐项实施；本会话继续执行已批准的 W1–W7。

**Goal:** 在保留 Linux 安全和恢复行为的前提下支持原生 Windows。
**Architecture:** 公共业务逻辑 + POSIX/Windows 私有文件系统实现。
**Tech Stack:** Python 3.12 标准库、ctypes、Win32、unittest、GitHub Actions。
**Spec:** [windows-design.md](windows-design.md)

## Global Constraints

- 只用 Python 标准库；不改 docs-api v2 契约。
- 不修改生产凭据、运行档案和线上技能；不合并 PR。
- Windows x64、本地 NTFS、PowerShell 5.1+、Python 3.12+。
- 先写失败测试，再实现；原有 POSIX 安全断言不减弱。
- 临时 HOME、假 Key、回环服务；不得访问生产站点。

## W1 环境与设计

- [x] 建立 /tmp/html-upload-windows-20261124，分支 feat/windows-native。
- [x] 记录批准方案与安全边界。
- [ ] 记录 Linux 基线和 Windows 版本、文件系统、Python；在独立目录准备解释器。
- [ ] 提交 `docs(windows): W1 原生 Windows 设计与隔离环境`，推送并开草稿 PR。

## W2 平台边界

Files: bin/private_fs.py、bin/fs_posix.py、bin/local.py、bin/store.py；tests/test_platform_fs.py。
Interfaces: secure_directory(path, create=False)、private_read(path, limit)、checked_fd(path, flags, create=True)、
exclusive_write(path, data)、sync_dir(path)、lock(fd, blocking=True)、unlock(fd)、shared_directory(path, create=False)。

- [ ] 编写行为测试：独占创建不覆盖已有字节，私有读取拒绝符号链接，非阻塞锁报告忙碌。
- [ ] `python -m unittest discover -s tests -p test_platform_fs.py -v`，确认缺失平台模块导致失败。
- [ ] 提取 POSIX 实现，local/store 委托平台接口，保留原有可注入故障边界。
- [ ] 跑新测试和全部 Linux 测试，提交 `refactor(windows): W2 隔离私有文件系统操作`。

## W3 Windows 凭据与句柄

Files: bin/fs_windows.py、bin/configure_key.py、bin/configure-key.ps1；tests/test_windows_fs.py。
Interfaces: 与 W2 相同；write_credential(home, token) 仅接受私有 stdin 输入。

- [ ] Windows 失败测试：import docsctl；创建私有目录并读取凭据；放宽 DACL 后拒绝读取；
      junction 路径与 ADS 源文件拒绝；独占碰撞保留原文件。
- [ ] 在 JS Mini 36 运行，确认当前 fcntl 导入和 POSIX 调用失败。
- [ ] 实现 SID/DACL、NTFS/路径验证、句柄安全读取及私有创建；不接受不安全既有 ACL。
- [ ] PowerShell 安全输入使用 `Read-Host -AsSecureString`，通过重定向 stdin 交给配置程序。
- [ ] 运行原生 Windows 安全测试，提交 `feat(windows): W3 NTFS 私有凭据与安全文件访问`。

## W4 并发与持久化

Files: bin/fs_windows.py、bin/store.py；tests/test_windows_store.py。

- [ ] 失败测试：子进程持锁时非阻塞失败；子进程终止后可重获锁；两个 resume 仅一次发送；
      元数据替换失败保留原字节；已知成功离线补记；清理跳过活动恢复。
- [ ] 实现 LockFileEx/UnlockFileEx、flush 与安全替换，验证原样请求和幂等键。
- [ ] 使用 `multiprocessing.get_context('spawn')` 实际运行，提交 `feat(windows): W4 并发恢复与持久化`。

## W5 命令与编码

Files: bin/docsctl.py、references/commands.md；tests/test_windows_cli.py。

- [ ] 失败测试：默认 Windows 编码下 JSON 中文输出；实际解释器恢复命令可执行；中文空格路径；
      UTF-8/CRLF 摘要，Key 不出现在输出或 argv。
- [ ] Windows stdout/stderr UTF-8，恢复命令使用引用后的实际解释器与脚本路径。
- [ ] 运行子进程与 HTTP 回环全流程，提交 `fix(windows): W5 命令路径与 UTF-8 输出`。

## W6 测试矩阵

Files: tests/ 平台夹具及平台专属用例；.github/workflows/tests.yml。

- [ ] 公共测试使用平台私有创建夹具；POSIX chmod/fork 专属断言仅 POSIX 执行，Windows 有对应 ACL/spawn 测试。
- [ ] `python -m unittest discover -s tests` 在 JS Mini 36 实际通过，Linux 全量与 U09 联调通过。
- [ ] CI Ubuntu/Windows + Python 3.12，详细报告专属测试与跳过原因。
- [ ] 提交 `ci(windows): W6 原生 Windows 与 Linux 回归矩阵`。

## W7 文档与验收

Files: SKILL.md、references/credentials-windows.md、references/manifest.md、docs/windows-validation.md。

- [ ] 记录真实执行过的命令和环境版本；更新 Windows 支持范围与 PowerShell 配置步骤。
- [ ] 审查 Key、ACL、锁、重试、恢复、退出码，修复审查发现；复跑受影响测试。
- [ ] 提交 `docs(windows): W7 安装配置与实机验收`；推送、更新 PR 清单、ready 和交付评论。
- [ ] PR 不合并；线上技能不切换。
