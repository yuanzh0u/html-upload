---
verified: no
reason: 方案撰写与 Linux 实施机没有 PowerShell。禁止写入未运行的命令。
---

# Windows 凭证边界

凭证引导尚未在本 PowerShell 版本验证。原生 Windows 尚不受当前 POSIX 客户端支持，不改跑 POSIX 段落，不写 manifest；不要承诺该平台能返回规范 JSON 或退出码。

未执行的 API 名称：`Read-Host -AsSecureString`。本文件不提供命令。设计要求后续真实 PowerShell 实验只在一次写文件期间转换明文，目标为用户家目录 .config/docs/api-key，并核验 NTFS 仅当前用户可读写的 ACL。只有真实实验附版本、日期、命令、ACL 与历史检查记录后，才能改为 verified: yes。

Key 页面为 https://docs.zh0uyuan.com/keys；登录后生成或重新生成，明文只显示一次，不要贴进聊天或写进明文命令、历史记录。客户端需要 python3 3.12 或以上；当前实现依赖 POSIX 文件权限与锁，不能将未验证的原生 Windows 环境当作可用平台。已配置的 DOCS_API_KEY 优先，空值也不回退文件；文件来源为 ~/.config/docs/api-key，但本文不提供未经验证的配置命令。
