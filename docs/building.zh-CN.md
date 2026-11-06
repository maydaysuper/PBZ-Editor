# 从源码构建

[English](building.en.md)

需要 Windows x64、Python 3.12 和 Rust `1.99.1-x86_64-pc-windows-gnu`。首次安装依赖需要联网；玩家使用运行包不需要这些工具。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
rustup toolchain install 1.99.1-x86_64-pc-windows-gnu --profile minimal
rustup override set 1.99.1-x86_64-pc-windows-gnu
.\tools\build_codec.ps1
.\.venv\Scripts\python.exe -m pytest -q
.\tools\build_release.ps1 -Zip
```

输出：`dist/v0.3.2/` 下的 Windows ZIP、源码 ZIP 和 SHA256SUMS.txt。运行包内置 Python、Qt 和开放源码解码器，不需要专有 Oodle DLL。

源码启动：`.\.venv\Scripts\python.exe -m pbz_editor.app`。可加 `--language en-US` 或 `--language zh-CN`。设置 `PBZ_EDITOR_DATA_DIR` 可指定独立测试数据目录。

无本机资源的环境会跳过可选真实资源测试。公开测试仅动态生成合成存档；不要加入实际玩家存档或提取的资源。`data/profile.json` 只记录资源路径、指纹、属性布局和密钥定位参考。新增规则需要核对实际字段语义与写入结果，不能只改 build 编号或替换指纹。

目录生成：`python tools/build_catalog.py --game-dir <game-root>`。解析器只读游戏安装，从资源建立中英文目录、地图和图标。运行数据保留在本机私有目录。

GitHub Actions 的 Build Windows preview 工作流可手动运行测试、构建包并检查无游戏启动；不自动发布 Release。源代码归档与仓库发布使用 `tools/release_contents.py` 的同一公开文件清单，排除私人缓存和实测记录。

主要代码：`pbz_editor/core.py` 为存档逻辑，`draft.py` 为内存预览与撤销，`ui.py` 为界面，`i18n.py` 和 `locales/en-US.json` 为语言，`native/` 为解码器包装及固定的上游依赖。
