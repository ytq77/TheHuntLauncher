# 从源码构建

在 Windows x64 上使用 MSYS2 **CLANG64**，需要 Windows PowerShell 5.1 和系统 .NET Framework 编译器。GitHub Actions 的完整环境列表见 `.github/workflows/windows.yml`。

```bash
python scripts/fetch_sources.py
python scripts/prepare_sources.py .build/bbsc
python scripts/localize.py .build/bbsc
cd .build/bbport
bash build.sh
bash tools/fetch_fsr4_assets.sh
cd ../..
bash scripts/build-mod.sh
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-entry.ps1
python -m unittest discover -s tests -v
python scripts/package.py --toolchain "$(cygpath -m /clang64)"
python scripts/source_archive.py
```

`fetch_sources.py` 只接受新构建目录，防止覆盖修改过的上游源码。准备脚本对预期代码进行精确匹配；上游变化时停止，而不会猜测替换。

产物位于 `dist/`：

- `BBSC-Launcher-<version>-windows-x64.zip`：干净的首次安装包。
- `BBSC-Launcher-sources.zip`：对应应用源码、补丁及构建脚本。
- `SHA256SUMS.txt`：两个压缩包的校验值。

构建依赖来自 MSYS2；Actions 另保存 `toolchain-packages.txt`。核心源码固定版本，但工具链尚未固定到完整离线快照，不能将产物描述为逐字节可重现。

## 发布

普通 push / PR 上传 Actions artifact，不创建正式版本。要发布预览版：

1. 更新 `upstream.lock.json` 的版本号及界面版本标签。
2. 完成检查和人工游戏测试，记录验证范围。
3. 推送对应的 `v<version>` 标签。

标签工作流校验版本号与 lock 文件一致，构建完成后发布为 prerelease，附带源码和校验文件。上游版本升级不会自动进入玩家安装。
