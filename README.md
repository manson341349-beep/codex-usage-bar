# codex-usage-bar

Codex 桌面端的独立额度信息栏，带原创 SVG 角色 Milo。当前版本 **0.3.0，实验性，仅支持 macOS**。仓库名称统一为 `codex-usage-bar`；Windows 仅预留平台目录，尚未实现或验证。

> **当前仅在专用隔离实例的空白首页显示。开始输入、输入法组合输入、进入对话页面时会卸载横条。真实工作事件和当前会话缓存尚未接入。** Milo 动画不表示模型正在工作；这不是全功能成品，也不是 OpenAI 官方产品。

## 当前能力与边界

- 在原生首页输入框上方的专用 portal 挂载 Shadow DOM 信息栏；不替换输入框。
- 通过官方 Codex CLI 的只读 `account/read` 和 `account/rateLimits/read` 获取订阅额度，约每 60 秒刷新一次。读数表示**已使用百分比**。
- 5 小时或每周窗口缺失、身份不明、超时、过期、到达重置时间时显示未知或明确状态；**缺数据不代表无限制**。本版没有账户绑定的“用户确认无限制”设置。
- 当前会话缓存固定显示“当前来源未提供”；真实工作状态显示“未接入”。hover、点击或键盘聚焦信息按钮可展开来源说明。
- 动画使用 `requestAnimationFrame`，支持减少动态效果。回调频率随显示器和窗口状态变化，不承诺 GPU 稳定 60 fps。
- DOM 结构属于 Codex 内部实现，应用更新可能使信息栏拒绝挂载。只支持唯一、可见且无内容的首页输入框。

## macOS 安装

前提：官方 `/Applications/Codex.app`、已安装的 Python 3.12 或以上。构建和安装不下载依赖，不打包 Codex 或 Python。Node.js 仅用于开发测试。

解压源码发布包，在目录中执行：

```sh
python3 platforms/macos/install.py
```

安装位置为 `~/Applications/codex-usage-bar.app`，私有运行状态位于 `~/Library/Application Support/codex-usage-bar`。这是未签名、未公证的本机源码启动器；不修改 Codex.app 的签名，不绕过 Gatekeeper，不添加自启动项，也不申请新的系统敏感权限。

首次使用在**专用实例**中由本人登录。安装后运行：

```sh
cd "$HOME/Applications/codex-usage-bar.app/Contents/Resources/codex-usage-bar"
python3 -E -s -B -m codex_bar login --acknowledge-runtime
```

登录完成后按 `Ctrl-C` 关闭专用实例并保留其资料。之后在 Finder 双击 `codex-usage-bar.app`，或执行：

```sh
open "$HOME/Applications/codex-usage-bar.app"
```

启动器打开终端并运行专用 Codex。**退出时在该终端按 `Ctrl-C`**，等待清理完成后再关终端。启动器自动寻找常见位置的 Python 3.12+；命令行中的 `python3` 也必须满足这一版本要求。

需要限时运行或查看状态时，在安装资源目录执行：

```sh
python3 -E -s -B -m codex_bar run --acknowledge-runtime --duration 30
python3 -E -s -B -m codex_bar status
```

`--acknowledge-runtime` 表示操作者已同意本次专用实例运行与本地调试访问。源码开发模式使用项目自己的 `.state/`。迁移选项 `--use-approved-project-profile` 只用于此前已批准且已验证关闭的相邻原型专用资料，按固定清单原位复用，不接受任意路径，也不复制认证文件；普通新安装不需要此选项。

## 卸载与恢复

先按 `Ctrl-C` 停止运行，再从保留的源码目录执行：

```sh
python3 platforms/macos/install.py --uninstall
```

卸载仅移走有本项目安装收据的 `.app`，旧应用留在 `~/Applications/.codex-usage-bar-backups/`，登录资料和私有运行状态保留。升级也保留可恢复备份。重新运行安装命令会恢复入口，并沿用已验证的资料绑定；不要手工覆盖安装收据或从其它账户复制登录资料。

## 数据与访问

管理器只连接自己启动并核验过的专用进程，调试端口仅监听 `127.0.0.1`。CDP 可控制应用，因此不要向不可信程序开放调试端口。日常 Codex 实例不作为连接目标。退出会卸载自有 DOM、关闭专用进程和端口，并保留专用资料供下次使用。

不读取聊天正文、历史记录或输入文本；首页是否为空仅检查节点结构。额度桥接短暂处理官方账户元数据以生成内存身份指纹，不对外暴露身份，不把额度存成磁盘缓存。官方 Codex 处理其自身登录和网络访问；本项目不发送模型请求。公开仓库和包不包含认证文件、会话日志、真实额度截图或私人验收证据。

## 开发与验证

```sh
python3 scripts/validate.py
python3 -B -m unittest discover -s tests -v
python3 -B -m unittest discover -s platforms/macos -p 'test_*.py' -v
node --test tests/test_frontend.js
python3 platforms/macos/build.py
python3 platforms/macos/build.py --source-output dist/codex-usage-bar-v0.3.0-source.zip
```

构建输出 `dist/codex-usage-bar.app`，只打包列出的源码和资源；现有目标不会被覆盖。开发测试使用人工构造数据，不等同真实模型工作事件验收。手动修改前端后需重新审查并更新 `web/asset-manifest.json`。

目录：`codex_bar/` 为额度与进程管理，`web/` 为原创前端，`platforms/macos/` 为当前平台入口，`platforms/windows/` 为未来规划。共享逻辑应与平台实现分离。

源码与原创视觉资源采用 [MIT License](LICENSE)，来源范围见 [PROVENANCE](docs/PROVENANCE.md) 与 [NOTICE](NOTICE.md)。Codex、Python 及系统组件分别由其权利人授权，不随本项目分发。
