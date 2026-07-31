## Assets

- `ASSET_PLACEHOLDER` - KOReader device-side plugin package (Lua only)

## Installation (Quick Start)

1. Download `ASSET_PLACEHOLDER`.
2. Unzip into `koreader/plugins/`.
3. Restart KOReader.

<details>
<summary><strong>Full installation notes (KOReader + desktop generator)</strong></summary>

### 安装说明 / Installation

### KOReader 插件（设备端）
1. 下载 `ASSET_PLACEHOLDER`
2. 解压到 `koreader/plugins/`，解压后目录结构如下：
   ```
   koreader/plugins/
   └── xray.koplugin/
       ├── _meta.lua
       ├── cachemanager.lua
       ├── chapteranalyzer.lua
       ├── characternotes.lua
       ├── localization_xray.lua
       ├── main.lua
       ├── sync.lua
       ├── xray_receiver.lua
       ├── LICENSE
       └── languages/
   ```
3. 重启 KOReader

### 电脑端生成器（从源码运行）

```bash
git clone https://github.com/Cusanity/xray.koplugin.git
cd xray.koplugin
python -m venv .venv
# Windows Git Bash:
source .venv/Scripts/activate
pip install -r requirements.txt
python generator_gui.py
```

Linux/macOS 激活虚拟环境：

```bash
source .venv/bin/activate
```

Windows PowerShell 激活虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

</details>

## Verify After Install

- KOReader menu shows `X-Ray` entry.
- Opening `X-Ray` displays entities for already-read content.
- Optional: push or sync `xray_data.json` from desktop tooling.
