# 📖 X-Ray 插件 - KOReader

利用 AI 在 KOReader 上还原 Amazon Kindle 的 X-Ray 功能。

> Fork 自 [koreader-xray-plugin](https://github.com/0zd3m1r/koreader-xray-plugin)，进行了架构重写以解决原版的可靠性问题，并增加了一些新功能。

![Version](https://img.shields.io/badge/版本-2.0.0-blue.svg)
![Platform](https://img.shields.io/badge/平台-KOReader-green.svg)
![License](https://img.shields.io/badge/许可-MIT-yellow.svg)

---

## 核心改进

原版仅告诉 AI "分析《书名》的前 X%"，导致 AI 凭空猜测（幻觉）、剧透、译名不一致等问题。

**本 Fork 发送实际书籍文本给 AI**，消耗更多 Token，但换来 100% 准确的 X-Ray 数据。

| 特性 | 原版 | 本 Fork |
|------|------|---------|
| 数据来源 | 仅发送书名+进度 | 发送实际文本（25k 字/块） |
| 准确性 | 易幻觉/剧透 | 基于真实文本 |
| 剧透控制 | 手动选择 | 自动基于阅读进度 |
| 缓存 | 单一文件 | 多个 `*%.json` 分块缓存 |
| 跳转处理 | 无法回退 | 自动加载历史缓存 |

---

## 新增功能

### 🎯 渐进式分析
- **按阅读进度分析**：AI 只分析已读内容，**零剧透**
- **分块缓存**：保存为 `5%.json`、`10%.json`... 等文件
- **智能回退**：从 40% 跳回 20%？自动加载 20% 的缓存

### 🤖 多 AI 支持
- **本地 AI** ⭐ 推荐（如有 [Antigravity-Manager](https://github.com/lbjlaq/Antigravity-Manager) 访问权限）
- **Google Gemini**
- **ChatGPT**

### 📱 文本选中即查
长按选中文本 → 点击 **X-Ray** → 自动匹配角色/地点

### ☁️ WebDAV 同步
上传/下载 X-Ray 数据到云端，多设备共享

---

## 快速开始

### 安装

```bash
cd ~/.config/koreader/plugins/
git clone https://github.com/Cusanity/xray.koplugin.git
```

### 配置 API 密钥

1. 打开书籍 → **菜单 → X-Ray → AI 设置**
2. 选择 AI 服务商并输入 API 密钥

**或**：复制 `config.lua.example` 为 `config.lua`，填入密钥。

### 使用

1. 阅读书籍至任意位置（如 10%）
2. **菜单 → X-Ray → 获取 AI 数据**
3. AI 分析已读内容，生成角色/地点/时间线
4. 继续阅读，随时查看 X-Ray

---

## 工作原理

```
阅读 35% → 获取数据 → 保存为 35%.json
    ↓
阅读 60% → 增量分析 → 保存为 60%.json
    ↓
跳回 20% → 自动加载 ≤20% 的最新缓存
```

### 文件结构

```
书籍.epub.sdr/
├── xray_cache.lua          # 当前加载的主缓存
└── xray_analysis/          # 渐进式缓存目录
    ├── 5%.json
    ├── 10%.json
    └── 35%.json
```

---

## Python 批量生成器

使用 `xray_generator.py` 在电脑上批量预生成整本书的 X-Ray：

```bash
export XRAY_API_BASE="http://localhost:8080/v1"
export XRAY_API_KEY="your-api-key"
export XRAY_MODEL="gemini-2.5-flash-lite"

pip install openai
python xray_generator.py 书籍.epub
```

将生成的 `*.json` 文件复制到 `书籍.epub.sdr/xray_analysis/`。

---

## 配置文件

`config.lua`（可选）:

```lua
return {
    gemini_api_key = "AIzaSy...",
    chatgpt_api_key = "sk-...",
    gemini_model = "gemini-flash-lite-latest",
    
    -- 本地 AI
    local_endpoint = "http://localhost:8080/v1/chat/completions",
    local_model = "your-model-name",
    local_api_key = "",
    
    default_provider = "gemini",
}
```

---

## 常见问题

**Q: 离线能用吗？**  
A: 首次分析需要网络，之后完全离线可用。

**Q: 分析中断了？**  
A: 下次继续时会从上次位置续传。

**Q: 跳转章节会怎样？**  
A: 自动加载不超过当前进度的最新缓存。

---

## 贡献

欢迎 PR 和 Issue！

- **Bug 报告**：附上 KOReader 版本和 `/koreader/crash.log`
- **功能建议**：描述使用场景

---

## 许可

MIT License - 详见 [LICENSE](LICENSE)

---

**用 ❤️ 为书籍爱好者制作**
