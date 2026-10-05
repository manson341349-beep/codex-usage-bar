# Sprig · Remotion 产品短片

[English](README.en.md) | [简体中文](README.md) | [返回项目](../../README.md)

24 秒、1080 × 1350、60 fps 的原创产品短片工程。包含可编辑时间线、角色模型与动作、真实插件组件的人工样例截图、原创配乐和锁定依赖。420 张可重建的透明角色帧没有提交；首次运行会在本机生成。

[观看原成片](https://github.com/manson341349-beep/codex-usage-bar/blob/main/docs/assets/sprig-product-film.mp4) · [下载 MP4](https://raw.githubusercontent.com/manson341349-beep/codex-usage-bar/main/docs/assets/sprig-product-film.mp4)

## 首次运行

需要已安装的 **Node.js 22+、Python 3.12+ 和 Google Chrome**。图片处理依赖固定为 **Pillow 12.0.0**。该工程独立于仓库根目录的插件构建：在 `promos/sprig-remotion/` 中安装依赖、执行命令，不需要启动或连接 Codex。

以下命令已在 macOS 验证，从仓库根目录开始：

```sh
cd promos/sprig-remotion
npm ci --ignore-scripts
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-assets.txt
PYTHON=.venv/bin/python npm run assets
PYTHON=.venv/bin/python npm run studio
```

依赖安装需要访问 npm 和 Python 包索引。若已有 Pillow 12.0.0，可省略虚拟环境，用 `npm run assets` 和 `npm run studio`。素材重建和视频导出可通过 `REMOTION_BROWSER_EXECUTABLE` 指定已安装浏览器的可执行文件路径；macOS 默认检查标准 Google Chrome 安装位置。

`npm run assets` 使用本地 Three.js 模块、角色模型与动作，以 1024 像素母版渲染，再用 Pillow LANCZOS 缩小为 768 像素透明 PNG。输出包括 `public/sprig/performance/` 的 300 帧和 `public/sprig/idle/` 的 120 帧。已有完整素材且源码指纹匹配时自动跳过；需要强制重建时执行 `PYTHON=.venv/bin/python npm run assets -- --force`。

## 编辑与导出

```sh
npm run check
PYTHON=.venv/bin/python npm run stills
PYTHON=.venv/bin/python npm run render
```

`check` 运行 TypeScript 类型检查。`studio`、`stills` 和 `render` 会先检查并按需重建角色素材，因此使用虚拟环境时也保留 `PYTHON` 参数。Studio 提供时间线预览；关键帧 PNG 与 H.264/AAC 成片输出到 `out/`，视频文件名为 `Sprig-Remotion-Product-Film.mp4`。

macOS 已完成素材重建和成片导出验证。其他系统尚未完整验收；命令中的虚拟环境路径需要按系统调整，字体替代、浏览器和 GPU 驱动也可能改变字形、排版或光栅化像素。工程不打包系统字体，不承诺其他机器重建结果逐字节相同。

## 文件结构

- `src/SprigFilm.tsx`：分镜、文案、镜头和动画时间线。
- `src/index.tsx`：`Sprig-Product-Film` 合成入口、画幅、1440 帧和 60 fps 设置。
- `scripts/rebuild-sprig.mjs`、`asset-source/`：角色生成源、浏览器渲染和透明 PNG 处理。
- `scripts/render.mjs`：关键帧与视频导出。
- `public/ui/`：本项目真实组件在中英、明暗、展开和折叠状态下的人工样例截图。
- `public/audio/sprig-score.wav`：原创 24 秒合成配乐与交互音，无配音或外部音乐采样。
- `package-lock.json`、`requirements-assets.txt`：独立动画工程的锁定依赖。

## 演示与许可边界

片中的每周剩余 72%、5 小时已用 18% 均为人工示例，缓存显示未知。界面截图来自插件组件的隔离样例，不是用户账户或聊天记录；影片中的输入框外壳和切换控件用于视觉演示。角色动作按离线时间线渲染，60 fps 是成片编码帧率，不证明插件实时稳定呈现 60 fps，也不表示已接入真实 Codex 工作事件。静音观看仍能理解主要内容。

项目自有动画代码、角色、影片画面与样例截图采用根目录 [MIT License](../../LICENSE)。原创合成音轨以 **CC0-1.0** 提供。Three.js 使用随生成源保留的 [MIT 许可](asset-source/web/vendor/THREE-LICENSE.txt)。Remotion **4.0.532** 及其他依赖分别遵循各自上游许可；[Remotion 自有许可](https://github.com/remotion-dev/remotion/blob/v4.0.532/LICENSE.md)不属于本仓库的 MIT 授权。动画依赖不参与日常插件安装或运行。完整来源范围见 [NOTICE](../../NOTICE.md) 与 [PROVENANCE](../../docs/PROVENANCE.md)。
