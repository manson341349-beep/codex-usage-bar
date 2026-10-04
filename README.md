# codex-usage-bar

Codex 日常桌面实例的额度信息栏，带原创 SVG 角色 Milo。版本 **0.7.0，macOS**；统一仓库为未来 Windows 实现保留平台目录。

> **在日常 Codex 的原生输入框上方显示额度，首页和会话页面都可使用。** 继续使用原有账号、历史、设置和项目；输入文字、输入法组合输入和进入会话不再主动卸载横条。它是原版 Codex 的启动伴侣，不修改官方应用安装包。
>
> **需从本启动器启用。** 已由启动器打开的日常实例可验证身份后重连；直接从官方图标打开的实例需正常退出后再用启动器打开。真实工作事件和 Windows 实现尚未接入；缓存命中等待当前会话的真实统计通知。新版本的离线与实际验收范围分别记录在 [验证记录](docs/VALIDATION.md)，没有记录为通过的项目仍待验证。

## 安装并在日常 Codex 使用

运行需要官方 `/Applications/Codex.app`、已有 Python 3.12+，以及已经使用过的默认资料目录：`~/.codex` 和 `~/Library/Application Support/Codex`。本版不支持自定义资料路径；不复制登录、创建替代账号或要求重新登录。

从源码构建还需要已有 Xcode 或 Command Line Tools（`xcrun swiftc`）；不自动下载或安装编译器。解压源码包，在源码目录执行：

```sh
python3 platforms/macos/install.py
```

随后双击 `~/Applications/codex-usage-bar.app`，或运行：

```sh
open "$HOME/Applications/codex-usage-bar.app"
```

启动后静默在后台刷新，**不开终端，不弹控制窗口**，额度条留在日常 Codex 输入框上方。macOS 顶部的 **“额度”** 菜单提供状态、停止、重新启动及退出入口；正常使用无需操作菜单。

如果当前 Codex 已由本启动器打开，会直接重连。若是从官方图标普通启动，菜单会显示等待状态，并提示先保存工作、正常退出 Codex；启动器会用原资料重新打开。不会强制结束日常任务。

以后通过 **codex-usage-bar.app** 打开日常 Codex。普通官方图标冷启动不会自动加上额度条需要的调试参数。应用不添加开机自启，不替换 Dock 图标或官方 Codex 文件。

**停止与退出：** 在菜单栏“额度”选择停止或退出，只会要求自身启动的管理器清理横条，日常 Codex 继续运行。清理失败会明确提示，不会强退 Codex。要关闭本机调试端口，请正常退出 Codex（Cmd-Q）。端口仅监听 `127.0.0.1`，不能向不可信程序开放。

这是未经开发者签名和公证的本机应用，需要外部 Python 与 Codex；构建产物可能有编译器生成的临时签名。安装不下载依赖、不改 Codex 签名、不绕过 Gatekeeper、不添加自启动或新的系统敏感权限。

## 当前能力与限制

- 在原生首页和会话输入框上方挂载独立的 Shadow DOM 信息栏；打字、输入法组合输入及切换会话时持续跟随输入框，不替换输入框，不读取输入正文或聊天内容。
- 经官方 CLI 的只读 `account/read` 和 `account/rateLimits/read` 获取订阅额度，约每 60 秒刷新。显示**已使用百分比**。
- 5h 或每周窗口缺失、身份不明、数据过期或待重置时明确显示未知/状态；**缺数据不表示无限制**。没有账户绑定的“用户确认无限制”设置。
- 缓存命中来自 Codex 已有的会话统计通知，按当前会话累计缓存输入 token ÷ 累计输入 token 计算；折叠行也保留缓存读数。仅匹配当前输入框的会话 UUID 与侧栏主机标识；两者缺失、冲突或计数无效时显示未知/等待，切换时立即清掉旧统计。初次接入、页面刷新或切换到其他会话后，需要下一条真实统计通知才显示；不回读历史日志。超过两分钟无新通知会标为“上次统计”。这不是上下文占用率，也不等于全账户缓存命中率。
- 真实工作事件仍未接入。Milo 动画不表示模型正在生成。
- 箭头可折叠为单行额度摘要，再次点击展开；点击 `i` 查看来源说明，再次点击、点击外部或按 Esc 关闭。鼠标经过或键盘聚焦不再自动展开详情。动画尊重减少动态效果；不承诺 GPU 稳定 60 fps。
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

先在菜单栏“额度”选择退出，使伴侣应用和管理器结束。旧版外部终端入口使用 Ctrl-C 退出。安装器发现管理器锁被占用时会拒绝覆盖。重新运行安装命令升级；旧 `.app` 会保留在 `~/Applications/.codex-usage-bar-backups/`，原有资料不迁移。

从保留的源码目录卸载：

```sh
python3 platforms/macos/install.py --uninstall
```

卸载移走带有本项目收据的 `.app`，保留备份、私有运行状态与全部登录资料；不会关闭正在运行的日常 Codex，已有调试端口仍需本人退出 Codex 才关闭。重新安装可恢复入口。需要完全回到原来用法时，正常退出 Codex 后从官方图标启动即可，不需要修复官方安装包。

## 数据与开发

额度桥接仅在内存用官方账户元数据生成身份指纹，不输出身份或记录磁盘额度缓存。官方 Codex 自行处理登录和网络；本项目不读取认证文件、聊天历史或输入文本，不发送模型生成请求。当前会话 UUID 和主机标识只在页面内存用于匹配通知，不向后端、日志或公开包导出；通知只复制累计输入与累计缓存输入两个数值。生命周期文件只记录验证进程归属所需的本机元数据，留在私有支持目录，不上传。

```sh
python3 scripts/validate.py
python3 -B -m unittest discover -s tests -v
python3 -B -m unittest discover -s platforms/macos -p 'test_*.py' -v
node --test tests/test_frontend.js
python3 platforms/macos/build.py
python3 platforms/macos/build.py --source-output dist/codex-usage-bar-v0.7.0-source.zip
```

构建只使用白名单源文件，编译原生菜单栏启动器，并拒绝覆盖现有产物。测试数据均为人工构造；真实日常验收与离线测试分开报告。`codex_bar/` 包含独立和日常两套生命周期，`web/` 是原创前端，`platforms/macos/` 是当前平台入口，`platforms/windows/` 仅为未来规划。

本项目独立于 OpenAI。自有代码与原创视觉资源采用 [MIT License](LICENSE)，来源范围见 [PROVENANCE](docs/PROVENANCE.md) 和 [NOTICE](NOTICE.md)。不分发或重新授权 Codex、Python、系统组件或早期参考软件。
