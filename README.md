# Echo

Echo 是 Sublime Text 开源 AI 编程助手，支持 Codex app-server 和本地 Pi Agent。通过 `echo.sublime-settings` 的 `provider` 选择默认 agent：

```json
{
    "provider": "pi",
    "providers": {
        "pi": {
            "enabled": true,
            "cli_path": ""
        }
    }
}
```

Pi 需要用户自行安装并登录 [Pi Agent](https://pi.dev)，Echo 会在本地启动
`pi --mode rpc`，不会把源码发送到 Echo 服务。

Pi 直接作为本地子进程运行，因此使用 Pi 时由 Pi 自己管理项目文件访问；Echo 的
`local_workspace` 路径限制、敏感文件规则、写入审批和未保存 Buffer 同步仅适用于
Codex app-server。Pi RPC 返回会话 ID 时，Echo 会将其保存在本地以便重启后继续使用；
Echo 当前不提供 Pi 会话列表或 rewind。

echo 是面向 Sublime Text 的 Codex app-server 客户端。它通过 WebSocket 连接用户已经启动的 Codex app-server，并把当前 Sublime 工程以受控的 `local_workspace` 工具提供给 Codex。

## 隐私与数据处理

echo 不收集遥测数据，也不会把项目上传到 echo 自有服务器。数据只会在以下情况下离开本机：

- 用户主动提交到聊天框中的文字；
- 用户使用 `@file` 或类似方式明确引用的文件内容；
- Codex 或 Pi 通过工具请求读取的目录、文件和命令输出；
- 用户配置了远程 Codex app-server 时，发送到该 app-server 的请求和工具结果。
- 用户配置的 `request_fields`（例如员工标识）会随每个 Codex app-server 请求发送。

echo 本身不会管理 Codex 或 Pi 的账户、模型服务和数据保留策略。实际数据处理还受用户使用的 Codex/Pi 服务、远程 app-server 和其服务条款约束。认证令牌只从配置的环境变量读取，不写入设置文件；日志不会主动记录令牌。

app-server 可以运行在本机或远端；两种部署使用完全相同的协议、工具、审批、会话和重连逻辑，差别只有配置的地址：

- 本机：`ws://127.0.0.1:4500`
- 远端：`wss://codex.example.com`

echo 不会启动、停止或管理 app-server 进程，也不要求把工程同步到 app-server 所在机器。

## 工作方式

```text
Sublime Text / echo
  ├─ 当前 Buffer 和本地 workspace
  ├─ 路径、敏感文件和写入审批策略
  └─ local_workspace dynamicTools
             │ WebSocket JSON-RPC
             ▼
       Codex app-server
```

源码按职责划分：`domain/` 保存纯消息和会话规则，`providers/` 实现 Codex 与
Pi，`transport/` 负责 RPC/WebSocket，`workspace/` 提供受保护的项目文件能力，
`runtime/` 管理活动会话，`sublime_adapter/` 包含编辑器集成与聊天界面，
`application/` 只负责插件入口和命令编排。

`echo.py` 只负责显式加载和导出 Sublime 插件类型。窗口会话、编辑保护、pane
布局和 prompt checkpoint 分别由 `sublime_adapter/window_context.py`、
`sublime_adapter/editor_policy.py`、`sublime_adapter/layout.py` 和
`domain/conversation/checkpoints.py` 管理；Codex 通知与 server request 通过
独立路由表分发。

Sublime view/window 状态统一使用 `echo_*` 键，不读取其他插件或旧版本的状态键。

Codex 的内置文件系统被配置为只读。项目文件的列举、搜索、读取和修改都必须通过 echo 提供的本地工具完成。

## 安装

将仓库放入 Sublime Text 的 `Packages/echo` 目录，或使用 Package Control 安装发布包。兼容 Python 3.8 的纯 Python `websockets==10.4` 已经固定在 `vendor/`，不依赖系统 Python 环境。

echo 不负责安装 Codex CLI，也不会启动、停止或管理 app-server；请由用户自行完成安装、登录和服务启动。

当前开发验证覆盖 Sublime Text 内嵌的 Python 3.8 语法兼容性；发布前应同时在
支持的 Sublime Text 版本与目标操作系统上做一次手工冒烟测试。

## 启动 app-server

本机示例：

```bash
codex app-server --listen ws://127.0.0.1:4500
```

然后在 `echo.sublime-settings` 中配置：

```json
{
    "providers": {
        "codex": {
            "app_server": {
                "url": "ws://127.0.0.1:4500"
            }
        }
    }
}
```

非回环地址必须使用 WSS。推荐在 app-server 前部署经过认证的 TLS 反向代理，或通过 SSH 隧道映射到本机 loopback 地址。

无论地址指向本机还是远端，插件都会为新线程传入空 `environments` 并使用只读 sandbox，禁用 app-server 内置的 shell/apply_patch；工程访问统一由插件的 `local_workspace` 工具完成。

```json
{
    "providers": {
        "codex": {
            "app_server": {
                "url": "wss://codex.example.com",
                "bearer_token_env": "ECHO_GATEWAY_TOKEN",
                "allow_insecure_ws": false,
                "tls_verify": true
            }
        }
    }
}
```

echo 不负责 Codex/ChatGPT 登录；本地和远程 app-server 均由用户自行完成登录后再启动。如果 app-server 前的网关另有 Bearer 认证，可通过环境变量提供握手令牌。

## 主要配置

```json
{
    "provider": "codex",
    "providers": {
        "codex": {
            "app_server": {
                "url": "ws://127.0.0.1:4500",
                "bearer_token_env": "",
                "allow_insecure_ws": false,
                "tls_verify": true,
                "connect_timeout_seconds": 10,
                "request_timeout_seconds": 60,
                "ping_interval_seconds": 25,
                "max_message_bytes": 8388608,
                "minimum_codex_version": "0.141.0",
                "reconnect_max_attempts": 5,
                "reconnect_base_delay_seconds": 1.0,
                "request_fields": {
                    "employeeId": "A10001"
                }
            }
        },
        "pi": {
            "enabled": true,
            "cli_path": ""
        }
    },
    "local_tools": {
        "enabled": [
            "pwd", "list", "stat", "read", "search",
            "write", "create"
        ],
        "auto_approve": ["pwd", "list", "stat", "read", "search"],
        "always_confirm": ["write", "create"],
        "max_read_bytes": 1048576,
        "max_output_bytes": 1048576
    },
    "share_workspace_folders": true
}
```

`auto`、`local`、`remote` 等连接模式设置已经废弃。连接位置由
`providers.codex.app_server.url` 唯一决定。

`providers.codex.app_server.bearer_token_env` 是可选的环境变量名，不是令牌本身。配置后，echo 从 Sublime Text 进程环境读取该变量，并在 WebSocket 握手中发送 `Authorization: Bearer <token>`；变量缺失时连接会失败并提示变量名。留空时不发送认证头。请在启动 Sublime Text 前设置环境变量，不要把令牌写入配置文件。

`providers.codex.app_server.request_fields` 可配置附加字段，例如
`{"employeeId": "A10001"}`。echo 会将其合并到每一个发往 Codex app-server
的 JSON-RPC 请求和通知参数中，包括会话列表和读取请求；同名协议字段（如
`threadId`）优先，避免配置误改协议行为。该配置不用于 Pi，也不应用到对
app-server 主动请求的响应。修改配置后，当前 Codex 会话的后续请求会读取最新值。
这些字段可能包含个人标识信息；不要在其中存放密码、访问令牌或其他密钥。

远程地址应优先使用 `wss://`。仅当服务位于可信内网、VPN 或等效隧道中且只能提供明文 WebSocket 时，设置 `providers.codex.app_server.allow_insecure_ws` 为 `true`。Bearer 令牌在明文 `ws://` 上也会明文传输，因此带认证的远程连接尤其应使用 `wss://` 或 SSH 隧道。

## 本地工作区工具

echo 注册以下 dynamicTools：

- `pwd`：列出 Sublime workspace roots 及其 `root-N` 标识。
- `list`：列出目录内容。
- `stat`：返回文件类型、大小、hash 和 Buffer 状态。
- `read`：读取文件或指定行范围。
- `search`：在工程内进行字面量搜索。
- `write`：基于预期 SHA-256 执行精确替换。
- `create`：创建不存在的新文件。

读取和搜索优先使用 Sublime 中未保存的 Buffer。对已打开文件的写入通过Sublime `TextCommand` 完成，因此可以使用编辑器 Undo；关闭文件使用同目录临时文件和原子替换。写入完成后会显示本轮文件变更 Artifact。

## 安全边界

- 只接受 workspace-relative 路径。
- 多根目录必须显式使用 `root-1`、`root-2` 等标识。
- 禁止通过 `..`、绝对路径、symlink 或 junction 逃逸 workspace。
- 默认拒绝 `.env`、`.git`、SSH/AWS 配置和疑似密钥文件。
- 读取和工具输出有大小限制。
- 写入要求匹配最近读取的 SHA-256。
- `write` 和 `create` 默认每次确认。
- 重连缓存 `callId` 结果，不会自动重放写操作。
- 非回环明文 `ws://` 地址会被拒绝。

## 项目指令

echo 只读取主工程根目录下的：

- `AGENTS.md`
- `rules.md`

不会读取嵌套指令、用户全局指令或项目 `.codex` 目录。打开且未保存的根指令文件优先于磁盘内容，变更在下一轮对话生效。单个文件上限为 64 KiB。

## `@file`

发送消息前，本地文件引用会转换为不泄露绝对路径的形式：

```text
@root-1:src/main.py#L20
```

workspace 外部路径会转换为 `@unavailable-local-path`。app-server 不会收到本机绝对路径。

## 会话和重连

会话由以下信息共同绑定：

- 规范化后的 app-server URL
- 当前 workspace roots fingerprint
- Codex thread ID

切换服务器或工程不会错误恢复旧 thread。连接中断时使用指数退避重连，成功后通过 `thread/resume` 恢复原会话。

## 常用命令

- `echo: Start Chat`
- `echo: Resume Session`
- `echo: Model`
- `echo: Plan Mode`

## 故障排查

连接本机失败时确认：

```bash
codex app-server --listen ws://127.0.0.1:4500
```

并检查端口是否与 `providers.codex.app_server.url` 一致。

远端连接失败时检查 TLS 证书、WSS 反向代理、认证环境变量和防火墙。服务端不支持 `experimentalApi` 或 `dynamicTools` 时，echo 会返回明确的兼容性错误。

## 开发验证

```bash
PYTHONPATH=. python3 -m unittest discover -s test/providers -t . -p 'test_*.py'
PYTHONPATH=. python3 -m unittest discover -s test/sublime_adapter -t . -p 'test_*.py'
PYTHONPATH=. python3 -m unittest discover -s test/scripts -t . -p 'test_*.py'
python3 -m compileall -q application domain providers runtime shared \
  sublime_adapter transport workspace
python3 scripts/release_check.py
python3 scripts/build_package.py /tmp/echo.sublime-package
git diff --check
```

## License

echo 采用 [Apache License, Version 2.0](LICENSE)。

你可以使用、修改、复制、分发和商业化本项目，但分发时必须遵守 Apache 2.0 的版权、许可证和 NOTICE 要求。完整条款请参见 [LICENSE](LICENSE)。

本项目随包分发的第三方组件及其许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## Community and releases

- 贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。
- 安全漏洞请按 [SECURITY.md](SECURITY.md) 私下报告，不要提交公开 Issue。
- 社区协作遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
- 发布记录见 [CHANGELOG.md](CHANGELOG.md)。
