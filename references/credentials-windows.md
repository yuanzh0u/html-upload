# Windows 凭据与运行环境

使用 Windows x64、PowerShell 5.1 或以上和 Python 3.12 或以上。从本地 NTFS 磁盘上的技能目录运行。解释器未加入 PATH 时，使用 PowerShell 的调用运算符 `&` 和解释器的完整路径。

## 首次配置

在自己的交互终端执行以下命令。仅在出现输入提示后输入 Key。需要生成 Key 时，可在已登录的 [API Key 页面](https://docs.zh0uyuan.com/keys) 操作。

```powershell
python.exe .\bin\configure_key.py --prompt
```

配置器使用 Python 标准库隐藏输入，终端无法隐藏时会停止。成功时显示 `API Key saved.`。此入口适用于限制运行 PowerShell 脚本的环境。主机策略允许运行本地脚本时，也可执行 `.\bin\configure-key.ps1 -Python python.exe`，通过 PowerShell 的安全提示输入。

## 文件位置与权限

非空 `HOME` 优先，否则使用 Python 当前用户的家目录。工具将 Key 原子写入该目录下的 `.config\docs\api-key`。已有 `DOCS_API_KEY` 环境变量优先，空值会报错。不要将 Key 写入命令参数、聊天或示例脚本。

凭据与本地档案存放在固定 NTFS 磁盘的普通目录。配置器检查现有 `.config`、`.config\docs` 和凭据文件的私有权限，客户端也检查档案父目录 `docs`。如果这些目录由其他工具管理且 ACL 不兼容，可为技能设置独立的绝对 `HOME`，始终在同一环境恢复档案。工具拒绝网络盘、重解析路径及授予其他用户访问权的私有对象。

## 验证

从技能目录运行 `python.exe .\bin\docsctl.py --help` 可以离线检查解释器与 CLI。原生实机与联调的范围见[验收记录](../docs/windows-validation.md)。
