# P6 ⑤ html-upload 独立验收报告（DSH，最终闸门）

- 任务：`20261020T231520Z-dsh-4b348dc677`
- 工作区：`/home/ryan/projects/html-upload`（只读审计 + 本地运行，未改任何被验文件）
- 验收对象：`SKILL.md`、`bin/`（4 个脚本）、`references/`（7 篇）、`templates/`（2 个）、`tests/`（112 项）
- 验收基线：`PLAN.md` §12；协议口径 `/home/ryan/work/docs-service/docs/DESIGN-v3.md` §10；修复轮清单 `/home/ryan/projects/acp-dispatch/state/tasks/20261020T223221Z-codex-97efb2be1b/artifacts/report.md`
- 验收时间：2026-10-20T23:19Z 前后（本地 2026-10-21 07:19 +08）
- **总体结论：通过**（无 P0/P1/P2 阻断项；有 1 条 P3 非阻断文档偏差，见第 6 节）

## 0. 结论摘要

五项验收动作均执行并留有命令与输出摘要：

1. 全量复跑：**112 项，0 失败，0 错误，3 跳过，退出码 0**；3 项 skip 与测试夹具的 socket/传输桩分支一一对应，未多出 skip。
2. 修复抽查：6 项全部通过；其中嵌套 JSON 用 **100,000 层**独立复测，确认文件返回 2、pending 返回 4。
3. 端到端 dry-run 走查：dry-run 零 POST；缺 meta 退出 3 且零 POST；成功路径本地备份、manifest、报告齐全，备份字节与源一致。
4. `SKILL.md` 与实现一致性：18 个文档参数全部存在；退出码 0/2/3/4/5 均有动态证据且无未约定退出码；触发边界无暗门。
5. 供应链与痕迹：非测试代码只硬编码生产 origin；只有假 Key 形态；无调试输出泄漏 Key 的路径；docs-service 零改动。

唯一发现是凭证策略文字滞后于 D2/D3/F-3 的实现（`SKILL.md:60`、`references/credentials-posix.md:7` 仍写“只剥一个末尾 LF”，实现为 `rstrip('\r\n')` 且要求最短 16）。该偏差不造成泄漏或绕过，PLAN §12 A3 未检查此句，故列为 P3 非阻断，但建议发布前同步文档。

## 1. 全量复跑

### 1.1 默认环境

命令：

```bash
cd /home/ryan/projects/html-upload
python3 -m unittest discover -s tests -v
```

输出摘要（完整日志 `.verify-p6/full-tests-final.log`）：

```text
Ran 112 tests in 6.874s

OK (skipped=3)
EXIT=0
```

112 个测试行按模块分布：`test_adversarial_agy` 39、`test_documents` 7、`test_html` 10、`test_normalize` 2、`test_p6_regressions` 13、`test_shell` 1、`test_taxonomy` 4、`test_transport` 1、`test_upload` 35。失败 0、错误 0、跳过 3。

3 项 skip 的原文与位置：

```text
tests/test_upload.py:256  test_05_network_retry_original_body ... skipped '仅传输桩注入异常；真实 HTTP 由 fixture 验证'
tests/test_upload.py:283  test_06_external_notice_before_post_and_json_pause ... skipped 'before-send observation uses local transport stub'
tests/test_upload.py:438  test_09_partial_http_response_uses_same_request_retry ... skipped 'HTTP exception injection uses local stub'
```

### 1.2 为什么恰好是这 3 项

`tests/fake_server.py:53-74`：创建 `ThreadingHTTPServer` 时捕获 `PermissionError`；socket 被禁则设置 `state.transport` 为进程内传输桩，socket 可用则 `state.transport=None` 并走真实 loopback HTTP。上述 3 个测试都在 `state.transport is None` 时 skip，因为它们需要向传输桩注入 Timeout、观察 POST 前 stdout、注入 `IncompleteRead`。

本次默认环境实际允许 loopback bind（探针）：

```text
SOCKET_BIND_OK ('127.0.0.1', 52395)
```

因此默认跑法下 3 项跳过，且只跳过这 3 项，没有新增 skip。为核对“沙箱禁 socket”披露，用 `sitecustomize.py` 强制 `socket.bind` 抛 `PermissionError`，复现披露环境：

```bash
PYTHONPATH=.verify-p6 python3 -m unittest discover -s tests -v
```

```text
Ran 112 tests in 0.741s

OK
```

对照运行 **0 skip**，且上述 3 项全部 `ok`：

```text
test_05_network_retry_original_body ... ok
test_06_external_notice_before_post_and_json_pause ... ok
test_09_partial_http_response_uses_same_request_retry ... ok
```

结论：3 项 skip 与夹具的 socket/传输桩分支一一对应；修复报告所说的“0 skip”是其 socket 被禁环境下的结果，本次默认环境允许 socket，故呈现 3 skip。两种环境都不产生第 4 项 skip。

## 2. 修复抽查

独立探针脚本 `.verify-p6/verify_fixes.py`（不修改交付文件）9 项全部通过；另外定向跑了 14 个交付测试，全部通过。

```text
SUMMARY {"total": 9, "failed": []}
Ran 14 tests in 1.705s
OK
```

| 抽查项 | 直接证据 | 结果 |
|---|---|---|
| CRLF 凭证被接受 | 0600 文件内容 `test-key-not-secret\r\n`，走完整 upload：`exit=0 posts=1 auth_ok=True src_bytes_unchanged=True`；定向测试 `test_cred_file_crlf_trailing_newline_defect`、`test_cred_env_trailing_newline_defect` 通过 | 通过 |
| `charset=gbk` 被拒、退出 2 | `inspect_html` 抛 `ValueError`；upload `exit=2 gets=0 posts=0`；定向测试 `test_html_meta_charset_conflict_defect`、`test_charset_conflicts_are_invalid_html` 通过 | 通过 |
| 连续 422 保留已确认 choices | 第一轮确认 `project_v2`；服务端 422 只要求重选 `model`；第二轮 `exit=3` 且计划保留 `confirmed_choices.project=project_v2`；第三轮只确认 model，`exit=0`，最终请求 `project={"id":"project_v2"}`，共 2 次 POST；定向测试 4 项通过 | 通过 |
| ESC URL 不进纯文本报告 | HTML 含 `https://bad.invalid/a\x1b[31m` 与安全 URL；`inspect_html` 只收录安全 URL；成功报告 `report` 中无 ESC、无 `bad.invalid`，安全 URL 保留；定向测试 2 项通过 | 通过 |
| 100,000 层嵌套 JSON 落约定退出码 | 确认文件 100k 层：`exit=2 posts=0`；pending `.meta.json` 100k 层：`--resume` `exit=4 posts=0` 且报告含 `remote_unknown`；交付测试用 10k 层，本次用 100k 层复测 | 通过 |
| `--resume --dry-run` 退出 2 | 独立探针：`exit=2 gets=0 posts=0 docs_dir=False`；CLI 直跑：`exit=2`，提示“--resume 与 --dry-run 不能同时使用”；定向测试通过 | 通过 |

补充：交付测试中的 12 项退出码/核心路径测试也全部通过，覆盖 dry-run、force-confirm、备份哈希故障退出 5、manifest 不可写退出 5、成功协议与备份、本地冲突、重放、重试矩阵、prepared-only 等。

```text
Ran 12 tests in 1.657s
OK
```

## 3. 端到端 dry-run 走查

环境：临时 `HOME`、假 Key `test-key-not-secret`、`tests/fake_server.py` 本地 loopback 桩。未请求生产站点或任何外网。

夹具：

- 合规 HTML：含 `<title>`、`<meta name="plans:completed" content="2026-10-15T12:00:00+08:00">`、`<meta name="plans:worker" content="ExampleModel">`。
- 缺 meta HTML：只有 `<title>`，没有 `plans:completed` / `plans:worker`。
- 命令：`python3 .verify-p6/verify_e2e.py`，8 项全部通过。

| 场景 | 结果 | 关键断言 |
|---|---|---|
| 合规 HTML `--dry-run` | `exit=0 posts=0 gets=2` | 未创建 `~/docs`；无 `.pending`、无 manifest；status=`dry_run`；stdout 含“未提交，没有 docid”；含“服务端未返回 Key 有效期”“服务端未返回上传限制” |
| 缺 meta 正常走 | `exit=3 posts=0` | status=`needs_confirmation`；无 `.pending`；源文件字节不变；不提交 |
| 合规 HTML 成功路径 | `exit=0 posts=1` | docid 32 hex；`~/docs/<docid>.html` 字节等于源字节，SHA-256 等于源；manifest 最后一条 `uploaded` 且 docid 一致；报告含状态/线上链接/docid/本地副本/manifest/外链提示/待办；源字节与 mtime 不变；`.pending` 无残留 `.html` |
| 缺 meta `--dry-run`（补测） | `exit=0 posts=0` | 未创建 `~/docs`；dry-run 优先于暂停 |

`SUMMARY {"total": 8, "failed": []}`。三件套（本地备份、manifest、报告）齐全，备份与源字节一致；dry-run 零 POST、零 pending/manifest；缺 meta 零提交。

## 4. SKILL.md 与实现一致性

### 4.1 参数与命令

用 `bin/upload.py --help` 提取实现参数，与 `SKILL.md` 反引号参数比对：

```text
SKILL flags 18 个：--acl-group --acl-user --category-id --category-name
--confirm-file --dry-run --force-confirm --generated-at --model-id
--model-name --non-interactive --project-id --project-name --resume
--title --type-id --type-name --visibility
SKILL-only: []
IMPL-only : ['--help']
```

即文档写的参数全部真实存在，没有文档有而实现没有的参数；`--help` 是标准入口额外项。

命令走查：

- `python3 bin/upload.py --help`：正常输出 usage 与全部参数，退出 0。
- `python3 bin/upload.py`：退出 2，报告“参数无效”。
- `python3 bin/upload.py /nonexistent.html --resume <32hex> --dry-run`：退出 2，零 GET/POST，未创建 `docs`。
- `python3 bin/check_html.py <file>`：独立输出合法 JSON；合规文件退出 0，gbk charset 文件输出 `{"status":"invalid_html"}` 且退出 2，证明“本地模块不请求网络、检查器可独立输出 JSON”。

### 4.2 退出码

静态枚举：`bin/upload.py` 的退出码字面量只有 `0/2/3/4/5`，没有 1；`Stop` 默认码为 2。动态证据：

| 退出码 | SKILL 含义 | 本次证据 |
|---|---|---|
| 0 | 上传完成，或 dry-run 展示完成 | E2E dry-run 与成功路径均 0 |
| 2 | 参数、文件、凭证或确定拒绝 | 缺参数、未知旗标、`--resume --dry-run`、gbk charset、401 均为 2 |
| 3 | 待逐项确认 | 缺 meta、`--force-confirm` 均 3，且零 POST |
| 4 | 远程未知或远程成功本地未完成 | 100k pending meta `--resume` 为 4；本地同 docid 冲突测试为 4；503 预算耗尽为 4 |
| 5 | 预备份/manifest 失败且 POST 未发 | 备份钩子破坏 pending、manifest 路径不可写两项测试均 5，零 POST |

没有发现实现返回文档未承诺的退出码，也没有发现文档承诺而实现不存在的码。

### 4.3 触发边界与暗门

- `SKILL.md:7,16,134` 三句“不生成 HTML、不改服务端、不重建索引”均在；`html-trans`/`html-spec` 只出现在 “不要建设” 句中（`SKILL.md:16`）。
- `bin/` 没有写源 HTML 的代码：`read_html` 只读；所有写操作目标是 `~/docs/.pending`、`~/docs/<docid>.html`、`.confirm`、manifest。`bin/normalize.py`、`bin/match_taxonomy.py` 无网络、无文件写入。
- 没有 docs-service 路径写入、索引重建或 HTML 生成逻辑；`index` 命中仅为局部变量与文档中的 `/srv/docs` 说明。
- 隐藏入口检查：进程环境只读取 `HOME`、`DOCS_API_KEY`、`DOCS_ORIGIN`；无 base-url 参数；`origin_from` 只放行生产 origin 或 `127.0.0.1`/`localhost` 回环；无其他后门旗标。
- `--force-confirm` 不绕过确认：只把计划置为 explicit 并暂停；仍需 `accepted:true`、`client_upload_id`、`plan_sha256` 绑定的确认文件。
- `--dry-run` 在 `save_plan`/`prepare`/POST 之前返回，不写 pending、确认快照或 manifest。

结论：第 4 项在其列出的“命令/参数、退出码、触发边界”范围内通过。凭证策略文字偏差单独列在第 6 节。

## 5. 供应链与痕迹

### 5.1 地址清单

非测试交付文件（`bin/`、`references/`、`templates/`、`SKILL.md`）中的全部 URL：

```text
bin/upload.py:26,32                         https://docs.zh0uyuan.com
                                            https://docs.zh0uyuan.com/console/keys
references/credentials-posix.md:3           https://docs.zh0uyuan.com
                                            https://docs.zh0uyuan.com/console/keys
references/protocol.md:35                   https://docs.zh0uyuan.com（示例）
SKILL.md:4,18,66                            https://docs.zh0uyuan.com
                                            https://docs.zh0uyuan.com/console/keys
references/external-links.md:41             https://example.invalid/a.js（文档示例，非请求）
templates/*                                 无 URL
```

`bin/upload.py` 的请求地址只由 `PRODUCTION = 'https://docs.zh0uyuan.com'` 或进程环境 `DOCS_ORIGIN` 的回环测试地址构造；没有第二套上传域名、遥测地址、CDN 或第三方 API。测试夹具中的 `.invalid`、`evil.*`、`127.0.0.1` 均为本地断言用，不构成生产硬编码。

### 5.2 密钥形态

全仓扫描 `sk-`、`AKIA`、`ghp_`、`BEGIN * PRIVATE KEY`、`api_key = "..."` 等形态：只命中测试假值 `test-key-not-secret` 与文档假值 `dummy-key-not-real`；没有真实密钥形态残留。`templates/` 无 Key 占位符、无 `Authorization`、无 `DOCS_API_KEY`。

### 5.3 调试输出与脱敏

- `bin/upload.py:148,155` 两个连接类 `debuglevel = 0`；`print` 只用于报告/JSON 输出，没有请求头、异常或调试日志。
- `emit()` 对 token 做子串替换；`finally: del token`；`valid_success`、请求体、identity、taxonomy、422 details 均有“含凭证即停止且不回显”检查。
- 实测 401 路径：`exit=2 gets=['/api/v1/me'] posts=0`，stdout/stderr 均不含 `test-key-not-secret`，含“来源是环境变量”。
- 实测成功路径：stdout/stderr 均不含假 Key。
- `references/credentials-posix.md` 含 `IFS= read -rs`，不含 `read -rsp`；`references/credentials-windows.md` 为 `verified: no`，不含 `icacls`。
- 依赖仅 Python 标准库；无 `subprocess`、`requests`、`curl`；TLS 使用 `ssl.create_default_context()`，未见 `CERT_NONE`/`unverified`/`check_hostname=False`。

### 5.4 docs-service 未改动

```text
$ git -C /home/ryan/work/docs-service status --porcelain
（空，0 行）
$ grep -n "external_warning\|warnings" /home/ryan/work/docs-service/app/uploads.py
NO_MATCH
```

未修改 docs-service；未新增/修改上传警告字段。

## 6. 发现列表

### P3（非阻断）凭证策略文字与实现/PLAN §3.4 漂移

- `SKILL.md:60` 写“最多剥一个末尾 LF，其余空白、空内容、超长值拒绝”。
- `references/credentials-posix.md:7` 写“仅剥一个尾 LF；空值、其余空白、Bearer 加 token 超过 512 均拒绝”。
- `PLAN.md` §3.4 写“读取侧只剥一个末尾 LF”。
- 实现 `bin/upload.py:136` 为 `token.rstrip('\r\n')`，会剥掉任意多个尾部 CR/LF；`bin/upload.py:137` 还要求 token 最短 16 字符。
- 交付测试 `tests/test_adversarial_agy.py:164` 明确断言双 LF 被接受；独立探针显示 `b'test-key-not-secret\r\r\n\n'` 会剥掉 4 个字符后通过。

影响与定性：

- 没有密钥泄漏、注入或确认绕过；多剥的只是尾部空行，最短 16 是更严格的限制。
- 但文档承诺比实现更严格，且 PLAN §3.4 的“只剥一个 LF”与 D2/D3/F-3 修复后的行为不一致；修复报告“未改 SKILL.md 的承诺”的说法不准确。
- PLAN §12 A3 只检查来源顺序、0600/0700、`IFS= read -rs`、Windows 与输出不含假 Key，不检查“一个 LF”或最短长度，故不构成 §12 的阻断项。
- 建议发布前把两处文档改为“剥尾部 CR/LF；token 至少 16 字符”，或把实现收紧为只剥一个行结束符并同步测试。若坚持 PLAN §3.4 逐字为准则需先改基线；这是文字/口径问题，不是本次功能验收的失败点。

### 注意事项（非产品缺陷）

- 本次默认环境允许 loopback socket，因此 3 项 transport-stub 测试跳过；强制禁 socket 的对照运行 0 skip，3 项均通过。修复报告的“0 skip”是其环境结果，不能作为环境无关结论引用。
- `artifacts/` 目录对当前 sandbox 只读（touch 被拒绝），`/tmp` 与 runtime tmp 也只读；独立探针与日志放在工作区新增的 `.verify-p6/`，未修改任何被验文件。报告由 wrapper 从 stdout 落盘。
- 早期参数错误报告使用“外链提示：无（尚未完成扫描）”，而不是固定“未发现”文案；这是因为尚未扫描，语义上更准确，未列为缺陷。

## 7. 验收范围声明

已验：

- 全量 112 项测试、14 项定向修复测试、12 项退出码/核心路径测试。
- 6 项修复抽查；100k 层嵌套 JSON 独立复测。
- 临时 HOME + 假 Key + 本地 loopback 桩的 dry-run、缺 meta 暂停、成功路径三件套。
- `SKILL.md` 参数/命令/退出码/边界静态与动态一致性。
- 非测试交付文件的 URL、密钥形态、调试输出、依赖与 docs-service 状态。

未验及原因：

- 未请求 `https://docs.zh0uyuan.com`、任何外网或 docs-service 运行时；任务纪律禁止外网，且验收要求本地假服务器。
- 未做真实 TCP/TLS 握手、真实 API Key、真实服务端 201/200/422/409 响应联调；沙箱/纪律不允许。交付测试用 `test_transport` 检查 opener/TLS/重定向构成，本次未重复实现真实握手。
- 未在 Windows/PowerShell 上运行；本机无 PowerShell，文档已诚实标 `verified: no`。
- 未在真实控制终端复跑 `/dev/tty` 引导；本机无控制终端，文档已标“待终端复跑”。
- 未做 HTML 解析器 fuzz、1M 层以上嵌套、并发多进程 manifest 的独立复现；其中并发测试在 112 项全量中执行，但未单独构造外部复现。
- 未验证 publish 后的外部位置；报告由 wrapper 落盘到调用方 artifacts。

只读与写入边界：

- 未修改 `SKILL.md`、`bin/`、`references/`、`templates/`、`tests/` 中任何交付文件；仅新增工作区辅助目录 `.verify-p6/`（探针、日志、socket 对照用 `sitecustomize.py`）。
- docs-service 全程只读：`git status --porcelain` 为空，`app/uploads.py` 无警告字段。

## 8. 复现命令索引

```bash
# 全量
python3 -m unittest discover -s tests -v

# socket 被禁对照（复现 0 skip）
PYTHONPATH=.verify-p6 python3 -m unittest discover -s tests -v

# 6 项修复独立探针（9 项断言）
python3 .verify-p6/verify_fixes.py

# 端到端 dry-run / 缺 meta / 成功路径（8 项断言）
python3 .verify-p6/verify_e2e.py

# 定向修复测试
cd tests && python3 -m unittest -v \
  test_adversarial_agy.CredentialsAdversarial.test_cred_file_crlf_trailing_newline_defect \
  test_adversarial_agy.HTMLValidationAdversarial.test_html_meta_charset_conflict_defect \
  test_adversarial_agy.TaxonomyAdversarial.test_taxonomy_consecutive_422_cascade_and_state_loss \
  test_p6_regressions.P6Client.test_422_preserves_confirmed_title_time_and_project \
  test_p6_regressions.P6Client.test_external_control_characters_never_reach_text_report \
  test_p6_regressions.P6Client.test_deep_confirmation_json_returns_exit_2 \
  test_p6_regressions.P6Client.test_deep_pending_meta_and_request_return_exit_4 \
  test_p6_regressions.P6Client.test_resume_and_dry_run_are_rejected_before_credentials

# docs-service 只读状态
git -C /home/ryan/work/docs-service status --porcelain
```

完整日志在工作区 `.verify-p6/`：`full-tests-final.log`、`full-tests-socket-denied.log`、`spot-tests.log`、`acceptance-tests.log`、`verify-fixes.log`、`verify-e2e.log`。

## 9. 结论

**通过。** 五项验收动作均有可复现证据；功能、退出码、边界、供应链与 docs-service 只读状态符合 PLAN §12 与本任务要求。唯一 P3 发现是凭证策略文档滞后于修复实现，建议发布前同步 `SKILL.md:60` 与 `references/credentials-posix.md:7`；该问题不影响本次功能/安全验收结论。
