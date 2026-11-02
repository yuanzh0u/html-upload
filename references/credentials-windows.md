---
verified: no
reason: 方案撰写与 Linux 实施机没有 PowerShell。禁止写入未运行的命令。
---

# Windows 凭证边界

凭证引导尚未在本 PowerShell 版本验证。Windows 因此退出码 2，不改跑 POSIX 段落，不写 manifest。

未执行的 API 名称：`Read-Host -AsSecureString`。本文件不提供命令。设计要求后续真实 PowerShell 实验只在一次写文件期间转换明文，目标为用户家目录 docs/api-key，并核验 NTFS 仅当前用户可读写的 ACL。只有真实实验附版本、日期、命令、ACL 与历史检查记录后，才能改为 verified: yes。
