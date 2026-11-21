# POSIX 凭证

登录后打开 https://docs.zh0uyuan.com/keys（「API Key」页面）生成或重新生成 Key，明文只显示一次。不要把 Key 贴进聊天，不要写进带明文的命令或 shell history。

来源顺序：已设置的 DOCS_API_KEY（空也不回退），否则 ~/.config/docs/api-key，否则引导并退出 2。环境来源无效不读文件。Publishable Key 与用户上传 Key 是两种凭证；不用 OpenClaw secrets。

家目录取非空的 `HOME`，未设置或为空时使用 Python `Path.home()`。解析结果必须为绝对路径；相对路径会在读取任何凭据或本地档案、创建档案、联网之前退出 2，并提示修正家目录配置，不打印无效路径。凭据文件与 `docs/v2` 档案使用同一个已校验的家目录。

读取前 lstat：文件不得是符号链接，必须为当前 euid 的普通文件，mode 恰为 0600；父目录 ~/.config/docs 非符号链接，mode 恰为 0700。权限不符当轮停止，不修改权限后继续读。规整只去尾部 CR/LF（兼容 Windows CRLF），token 最短 16；空值、其余空白、Bearer 加 token 超过 512 均拒绝；不打印任何凭证片段。只去尾部 CR/LF 后仍有空白即拒绝；不检查或输出 Key 的前缀。

下面核心仅在临时家目录、stdin 输入假值 dummy-key-not-real 的验收环境使用。不要将假值写入真实家目录。真实用户凭证必须通过终端无回显输入；本次没有真实终端复跑，/dev/tty 版为「待终端复跑」，不提供未验证的 tty 命令。

```bash
umask 077
mkdir -p -- "$HOME/.config/docs"
chmod 700 -- "$HOME/.config/docs"
tmp=$(mktemp --tmpdir="$HOME/.config/docs" .api-key.XXXXXXXX)
chmod 600 -- "$tmp"
IFS= read -rs KEY || exit 1
printf '%s' "$KEY" > "$tmp"
unset KEY
mv -f -- "$tmp" "$HOME/.config/docs/api-key"
chmod 600 -- "$HOME/.config/docs/api-key"
```

zsh 使用同一核心，提示用 printf 到 stderr。zsh 的 read -p 是 coprocess 参数，会报 -p: no coprocess；bash 的 -p 才是提示符，不能混用。不启用 shell 跟踪，不把凭证放 argv、URL、日志、公共临时文件或启动配置；临时文件放目标目录，mktemp 后立即 chmod 600，printf 不加换行。

TTY 交互仍待终端复跑；不要把上述假值验收流程当作已验证的真实终端配置流程。Python 客户端需要 python3 3.12 或以上。
