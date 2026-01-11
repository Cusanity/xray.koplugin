-- Prompt loader for X-Ray plugin
-- Loads prompts from shared JSON file (prompts/zh.json)
-- Returns a table compatible with aihelper.lua's self.prompts pattern

local json = require("rapidjson")

-- Get the path to the prompts JSON file
local function getPromptsPath()
    local source = debug.getinfo(1, "S").source
    local plugin_dir = source:match("@(.*/)")
    if not plugin_dir then
        -- Fallback for different path separator (Windows)
        plugin_dir = source:match("@(.*\\)")
    end
    if not plugin_dir then
        -- Ultimate fallback
        plugin_dir = "./"
    end
    return plugin_dir .. "zh.json"
end

-- Load and parse the JSON file
local function loadFromJSON()
    local prompts_path = getPromptsPath()
    
    local file = io.open(prompts_path, "r")
    if not file then
        -- Return fallback prompts if file not found
        return nil
    end
    
    local content = file:read("*a")
    file:close()
    
    local success, prompts = pcall(json.decode, content)
    if not success or not prompts then
        return nil
    end
    
    return prompts
end

-- Try to load from JSON, otherwise return fallback
local prompts = loadFromJSON()

if not prompts then
    -- Fallback prompts (in case JSON loading fails)
    prompts = {
        system_instruction = "你是一位专业的文学评论家。你的回复必须仅使用有效的JSON格式。",
        text_based = "请分析书籍文本。\n\n书名：《%s》\n作者：%s\n\n<书籍内容>\n%s\n</书籍内容>",
        incremental = "请更新分析。\n\n书名：《%s》\n作者：%s\n\n<已有分析>\n%s\n</已有分析>\n\n<本段文字>\n%s\n</本段文字>",
        timeline_curation = "整理时间线。\n\n书名：《%s》\n\n<待定事件>\n%s\n</待定事件>\n\n<当前时间线>\n%s\n</当前时间线>",
        fallback = {
            unknown_book = "未知书籍",
            unknown_author = "未知作者",
            unnamed_character = "未命名人物",
            not_specified = "未指定",
            no_description = "暂无描述",
            unnamed_person = "未命名人物",
            no_biography = "暂无生平"
        }
    }
end

-- Return the prompts table directly for compatibility with aihelper.lua
return prompts
