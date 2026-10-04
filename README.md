# codex-usage-bar

Codex 日常桌面实例的额度信息栏，带原创 SVG 角色 Milo。版本 **0.5.0，macOS**；统一仓库为未来 Windows 实现保留平台目录。

> **在日常 Codex 的原生输入框上方显示额度，首页和会话页面都可使用。** 继续使用原有账号、历史、设置和项目；输入文字、输入法组合输入和进入会话不再主动卸载横条。它是原版 Codex 的启动伴侣，不修改官方应用安装包。
>
> **需从本启动器启用。** 已由启动器打开的日常实例可验证身份后重连；直接从官方图标打开的实例需正常退出后再用启动器打开。真实工作事件、当前会话缓存和 Windows 实现尚未接入。新版本的离线与实际验收范围分别记录在 [验证记录](docs/VALIDATION.md)，没有记录为通过的项目仍待验证。

## 安装并在日常 Codex 使用

需要官方 `/Applications/Codex.app`、已有 Python 3.12+，以及已经使用过的默认资料目录：`~/.codex` 和 `~/Library/Application Support/Codex`。本版不支持自定义资料路径；不复制登录、创建替代账号或要求重新登录。

解压源码包，在源码目录执行：

```sh
python3 platforms/macos/install.py
```

随后双击 `~/Applications/codex-usage-bar.app`，或运行：

```sh
open "$HOME/Applications/codex-usage-bar.app"
```

启动器会打开一个终端。如果日常 Codex 本来就是由本启动器打开，信息栏会验证进程身份后重连，无需重启 Codex，也不会再开第二个实例。如果日常 Codex 是直接从官方图标打开，先保存工作、等待运行任务结束，然后 **Cmd-Q 正常退出 Codex**；启动器会等待其退出，再用原资料重新打开同一份官方应用。保持终端运行，即可在兼容的首页或会话输入框上方查看额度，无需清空输入或退出当前会话。

以后通过 **codex-usage-bar.app** 打开日常 Codex。直接从官方 Codex 图标冷启动，不会自动加上信息栏需要的调试参数；此时需先正常退出，再用本启动器打开。不会添加开机自启动，也不替换系统 Dock 项目或官方 Codex 文件。

**退出行为：** 在启动器终端按 `Ctrl-C` 只移除信息栏并停止其额度刷新，日常 Codex 继续运行。要关闭本地调试端口，请正常退出 Codex（Cmd-Q）。从启动器打开的本地调试端口仅监听 `127.0.0.1`；它仍具有调试能力，不能向不可信程序开放。信息栏出错时也不会杀掉日常任务。

这是未签名、未公证的本机源码启动器，需要外部 Python 与 Codex。安装不下载依赖、不改签名、不绕过 Gatekeeper、不添加自启动项或新的系统敏感权限。

## 当前能力与限制

- 在原生首页和会话输入框上方挂载独立的 Shadow DOM 信息栏；打字、输入法组合输入及切换会话时持续跟随输入框，不替换输入框，不读取输入正文或聊天内容。
- 经官方 CLI 的只读 `account/read` 和 `account/rateLimits/read` 获取订阅额度，约每 60 秒刷新。显示**已使用百分比**。
- 5h 或每周窗口缺失、身份不明、数据过期或待重置时明确显示未知/状态；**缺数据不表示无限制**。没有账户绑定的“用户确认无限制”设置。
- 会话缓存显示“当前来源未提供”；真实工作事件显示未接入。Milo 动画不表示模型正在生成。
- hover、点击、键盘聚焦可查看来源说明。动画用 rAF 并尊重减少动态效果；不承诺 GPU 稳定 60 fps。
- 日常模式支持同一已验证实例中的多个 Codex 应用页面，并在页面重载或输入框被替换后重新挂载。仅支持默认资料路径；不主动切换会话、导航页面或发送消息。
- 有兼容的原生输入框时才显示：登录、设置等没有输入框的页面，以及无法确认输入框位置或空间不足的布局会隐藏横条。DOM 是 Codex 内部接口，应用升级后可能需要适配。
- 启动或连接失败会保留日常 Codex；如果它已经带调试参数打开，端口需随本人 Cmd-Q 关闭。不会为修复横条而强退应用。

## 独立测试模式

0.3.0 的独立测试模式仍保留，但不是 `.app` 默认入口。只在需要测试时打开安装资源中的 `platforms/macos/Test.command`。该模式采用独立资料，并保留旧的 `homeOnly` 行为：仅空白首页显示，输入或进入会话后卸载；这不代表日常模式的行为。Ctrl-C 会关闭其专用实例；日常模式只移除信息栏并保留 Codex。

源码下的 `run`、`login`、`status`、`cleanup` 仍仅指独立测试模式。`daily` 与 `daily-status` 指日常模式。例如：

```sh
python3 -B -m codex_bar daily --acknowledge-runtime --wait-for-exit
python3 -B -m codex_bar daily-status
```

`--acknowledge-runtime` 表示操作者已同意本次本机调试接入。迁移安装选项 `--use-approved-project-profile` 只影响独立 Test 模式的已验证旧资料绑定，日常模式使用原有默认资料。

## 升级、卸载与恢复

先在信息栏所属终端按 Ctrl-C，使管理器退出。安装器发现管理器锁被占用时会拒绝覆盖。重新运行安装命令升级；旧 `.app` 会保留在 `~/Applications/.codex-usage-bar-backups/`，原有资料不迁移。

从保留的源码目录卸载：

```sh
python3 platforms/macos/install.py --uninstall
```

卸载移走带有本项目收据的 `.app`，保留备份、私有运行状态与全部登录资料；不会关闭正在运行的日常 Codex，已有调试端口仍需本人退出 Codex 才关闭。重新安装可恢复入口。需要完全回到原来用法时，正常退出 Codex 后从官方图标启动即可，不需要修复官方安装包。

## 数据与开发

额度桥接仅在内存用官方账户元数据生成身份指纹，不输出身份或记录磁盘额度缓存。官方 Codex 自行处理登录和网络；本项目不读取认证文件、聊天历史或输入文本，不发送模型生成请求。生命周期文件只记录验证进程归属所需的本机元数据，留在私有支持目录，不上传。

```sh
python3 scripts/validate.py
python3 -B -m unittest discover -s tests -v
python3 -B -m unittest discover -s platforms/macos -p 'test_*.py' -v
node --test tests/test_frontend.js
python3 platforms/macos/build.py
python3 platforms/macos/build.py --source-output dist/codex-usage-bar-v0.5.0-source.zip
```

构建只复制白名单文件，拒绝覆盖现有产物。测试数据均为人工构造；真实日常验收与离线测试分开报告。`codex_bar/` 包含独立和日常两套生命周期，`web/` 是原创前端，`platforms/macos/` 是当前平台入口，`platforms/windows/` 仅为未来规划。

本项目独立于 OpenAI。自有代码与原创视觉资源采用 [MIT License](LICENSE)，来源范围见 [PROVENANCE](docs/PROVENANCE.md) 和 [NOTICE](NOTICE.md)。不分发或重新授权 Codex、Python、系统组件或早期参考软件。
