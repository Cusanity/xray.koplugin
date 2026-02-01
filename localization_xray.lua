-- Localization Manager for X-Ray Plugin (with .po support)

local logger = require("logger")
local lfs = require("libs/libkoreader-lfs")

-- Dynamically discover plugin directory from this script's location
local function getPluginDir()
    local info = debug.getinfo(1, "S")
    local script_path = info.source
    -- Remove leading @ if present
    if script_path:sub(1, 1) == "@" then
        script_path = script_path:sub(2)
    end
    -- Get directory from path (works for both / and \ separators)
    local dir = script_path:match("^(.*)[/\\]")
    if dir then
        logger.info("Localization: Plugin directory discovered:", dir)
        return dir
    end
    -- Fallback to relative path (for Kindle)
    logger.warn("Localization: Could not discover plugin path, using fallback")
    return "plugins/xray.koplugin"
end

local PLUGIN_DIR = getPluginDir()

local Localization = {
    current_language = "zh",
    translations = {},
    available_languages = {},
    plugin_dir = PLUGIN_DIR,
}

-- Simple .po file parser
function Localization:parsePO(filepath)
    local translations = {}
    local file = io.open(filepath, "r")
    
    if not file then
        logger.warn("Localization: Cannot open .po file:", filepath)
        return nil
    end
    
    local msgid = nil
    local msgstr = nil
    local in_msgid = false
    local in_msgstr = false
    
    for line in file:lines() do
        -- Skip comments and empty lines
        if line:match("^#") or line:match("^%s*$") then
            goto continue
        end
        
        -- Start of msgid
        if line:match('^msgid%s+"') then
            -- Save previous translation
            if msgid and msgstr then
                translations[msgid] = msgstr
            end
            
            msgid = line:match('^msgid%s+"(.-)"')
            msgstr = nil
            in_msgid = true
            in_msgstr = false
        
        -- Start of msgstr
        elseif line:match('^msgstr%s+"') then
            msgstr = line:match('^msgstr%s+"(.-)"')
            in_msgid = false
            in_msgstr = true
        
        -- Continuation line
        elseif line:match('^"') then
            local continuation = line:match('^"(.-)"')
            if in_msgid and msgid then
                msgid = msgid .. continuation
            elseif in_msgstr and msgstr then
                msgstr = msgstr .. continuation
            end
        end
        
        ::continue::
    end
    
    -- Save last translation
    if msgid and msgstr then
        translations[msgid] = msgstr
    end
    
    file:close()
    
    -- Process escape sequences
    for key, value in pairs(translations) do
        value = value:gsub("\\n", "\n")
        value = value:gsub("\\t", "\t")
        value = value:gsub('\\"', '"')
        value = value:gsub("\\\\", "\\")
        translations[key] = value
    end
    
    return translations
end

-- Initialize localization system
function Localization:init()
    logger.info("Localization: Initializing...")
    
    -- Discover available language files
    self:discoverLanguages()
    
    -- Load saved language preference
    self:loadLanguage()
    
    -- Load translation file
    self:loadTranslations()
    
    logger.info("Localization: Initialized with language:", self.current_language)
end

-- Discover available .po files
function Localization:discoverLanguages()
    local lang_dir = self.plugin_dir .. "/languages"
    
    self.available_languages = {}
    
    local attr = lfs.attributes(lang_dir)
    if not attr or attr.mode ~= "directory" then
        logger.warn("Localization: Languages directory not found:", lang_dir)
        return
    end
    
    for file in lfs.dir(lang_dir) do
        if file:match("%.po$") then
            local lang_code = file:match("^(.+)%.po$")
            if lang_code then
                table.insert(self.available_languages, lang_code)
                logger.info("Localization: Found language:", lang_code)
            end
        end
    end
    
    table.sort(self.available_languages)
    logger.info("Localization: Discovered", #self.available_languages, "languages")
end

-- Load translations from .po file
function Localization:loadTranslations()
    local po_file = self.plugin_dir .. "/languages/" .. self.current_language .. ".po"
    
    logger.info("Localization: Loading translations from:", po_file)
    
    local translations = self:parsePO(po_file)
    
    if translations then
        self.translations = translations
        logger.info("Localization: Loaded", self:tableSize(translations), "translations")
    else
        logger.warn("Localization: Failed to load .po file")
        
        -- Fallback to Chinese
        if self.current_language ~= "zh" then
            logger.info("Localization: Falling back to Chinese")
            self.current_language = "zh"
            po_file = self.plugin_dir .. "/languages/zh.po"
            translations = self:parsePO(po_file)
            if translations then
                self.translations = translations
            else
                self.translations = {}
                logger.error("Localization: Failed to load fallback!")
            end
        end
    end
end

-- Helper: count table size
function Localization:tableSize(t)
    local count = 0
    for _ in pairs(t) do count = count + 1 end
    return count
end

-- Get translated string with better error handling
function Localization:t(key, ...)
    local translation = self.translations[key]
    
    if not translation or translation == "" then
        logger.warn("Localization: Missing translation key:", key)
        -- Return a user-friendly fallback instead of the key
        local fallbacks = {
            cache_saved = "已保存!",
            cache_save_failed = "保存失败",
            ai_fetch_complete = "从%s获取成功\n\n%s\n%s\n\n%d | %d | %d | %d | %d\n\n%s",
            fetching_ai = "正在从%s获取...",
            no_api_key = "未设置API密钥!",
            error_title = "错误",
            button_close = "关闭",
            menu_xray_progress = "X-Ray进度",
            reading_progress = "阅读进度",
            gemini_model_flash_info = "已切换到 Gemini Flash",
            gemini_model_flash_lite_info = "已切换到 Gemini Flash Lite",
            view_events = "View Events",
            go_to_location = "Go to Location",
            menu_cloud_sync = "云端同步",
            menu_manage_server = "管理服务器",
            menu_upload_xray = "上传 X-Ray 数据",
            menu_download_xray = "下载 X-Ray 数据",
            upload_warn = "这将用本地 X-Ray 数据覆盖服务器上的文件。\n确定继续吗？",
            upload = "上传",
            uploading = "正在上传...",
            download_warn = "这将用服务器上的文件覆盖本地 X-Ray 数据。\n确定继续吗？",
            download = "下载",
            downloading = "正在下载...",
            server_saved = "服务器设置已保存",
            cloud_storage = "云端存储",
            delete = "删除",
            edit = "编辑",
            menu_local_ai_settings = "本地AI设置",
            menu_local_ai_url = "本地AI服务地址",
            menu_local_ai_key = "本地AI密钥",
            menu_local_ai_model = "本地AI模型",
            local_url_hint = "例如 http://127.0.0.1:8045/v1",
            local_key_hint = "任意字符串（如果无需验证）",
            local_model_hint = "例如 claude-sonnet-4-5",
            local_url_saved = "本地AI地址已保存！",
            local_key_saved = "本地AI密钥已保存！",
            local_model_saved = "本地AI模型已保存！",
            local_ai_selected = "已选择本地AI",
            
            -- Provider Selection
            menu_provider_select = "选择 AI 提供商",
            provider_select_title = "选择 AI 提供商",
            gemini_selected = "已启用 Google Gemini",
            chatgpt_selected = "已启用 ChatGPT",
            set_key_first = "请先设置 API 密钥！",
            
            -- View Options
            menu_view_options = "显示选项",
            menu_characters = "人物",
            menu_chapter_characters = "本章人物",
            menu_character_notes = "人物笔记",
            menu_timeline = "时间线",
            menu_historical_figures = "历史人物",
            menu_locations = "地点",
            menu_themes = "主题",
            menu_summary = "总结",
            menu_author_info = "作者信息",
            menu_author_info = "作者信息",
            menu_full_analysis = "完整分析",
            listing_files = "正在列出文件...",
        }
        translation = fallbacks[key] or key
    end
    
    -- Format with arguments
    if select('#', ...) > 0 then
        local success, result = pcall(string.format, translation, ...)
        if success then
            return result
        else
            logger.warn("Localization: Format error for key:", key)
            logger.warn("Localization: Error:", result)
            logger.warn("Localization: Args count:", select('#', ...))
            -- Print arguments for debugging
            for i = 1, select('#', ...) do
                local arg = select(i, ...)
                logger.warn("Localization: Arg", i, "type:", type(arg), "value:", tostring(arg))
            end
            return translation
        end
    end
    
    return translation
end

-- Load/save language preference (same as before)
function Localization:loadLanguage()
    -- Only Chinese is available, force it
    self.current_language = "zh"
    logger.info("Localization: Using Chinese (only available language)")
end

function Localization:languageExists(lang_code)
    for _, code in ipairs(self.available_languages) do
        if code == lang_code then return true end
    end
    return false
end

function Localization:getLanguage()
    return self.current_language
end

function Localization:getLanguageName()
    return self.translations["language_name"] or self.current_language
end

function Localization:setLanguage(lang_code)
    if not self:languageExists(lang_code) then
        logger.warn("Localization: Cannot set non-existent language:", lang_code)
        return false
    end
    
    self.current_language = lang_code
    
    local DataStorage = require("datastorage")
    local settings_dir = DataStorage:getSettingsDir()
    local xray_dir = settings_dir .. "/xray"
    lfs.mkdir(xray_dir)
    
    local language_file = xray_dir .. "/language.txt"
    local file = io.open(language_file, "w")
    if file then
        file:write(lang_code)
        file:close()
        logger.info("Localization: Language saved:", lang_code)
    end
    
    self:loadTranslations()
    
    local AIHelper = require("aihelper")
    if AIHelper then
        AIHelper:loadLanguage()
    end
    
    return true
end

-- Reload translations (call this after editing .po files)
function Localization:reload()
    logger.info("Localization: Reloading translations...")
    self:loadTranslations()
    
    -- Clear cached translations in AIHelper if it exists
    local AIHelper = require("aihelper")
    if AIHelper and AIHelper.localization then
        AIHelper.localization = nil
    end
    
    logger.info("Localization: Reload complete")
end

return Localization
