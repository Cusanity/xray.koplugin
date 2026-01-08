-- AIHelper - Google Gemini & ChatGPT for X-Ray
local http = require("socket.http")
local https = require("ssl.https")
local ltn12 = require("ltn12")
local socket = require("socket")
local UIManager = require("ui/uimanager")
local json = require("json")
local json = require("json")
local logger = require("logger")
local CacheManager = require("cachemanager")
local md5 = require("ffi/sha2").md5
local util = require("frontend/util")


local AIHelper = {}

-- AI Provider settings (default values)
AIHelper.providers = {
    gemini = {
        name = "Google Gemini",
        enabled = true,
        api_key = nil,
        model = "gemini-flash-lite-latest", -- Default model
    },
    chatgpt = {
        name = "ChatGPT",
        type = "chatgpt",
        enabled = true,
        api_key = nil,
        endpoint = "https://api.openai.com/v1/chat/completions",
        model = "gpt-4o-mini", -- Varsayılan model (uygun maliyet/performans)
    },
    ["local"] = {
        name = "Local AI",
        type = "local",
        enabled = true,
        api_key = "", -- Configure via settings or config.lua
        endpoint = "http://localhost:8080/v1/chat/completions",
        model = "your-model-name",
    }
}

AIHelper.model_override = nil
AIHelper.CHUNK_SIZE = 25000 -- Centralized text chunk size (25k safe for timeouts)

-- Set Gemini model
function AIHelper:setGeminiModel(model_name)
    if not model_name or #model_name == 0 then return false end
    self.providers.gemini.model = model_name
    self:saveModelToConfig(model_name)
    return true
end

-- Set ChatGPT model
function AIHelper:setChatGPTModel(model_name)
    if not model_name or #model_name == 0 then return false end
    self.providers.chatgpt.model = model_name
    self:saveModelToConfig(model_name, "chatgpt")
    return true
end

-- Set Local AI model
function AIHelper:setLocalAIModel(model_name)
    if not model_name or #model_name == 0 then return false end
    self.providers["local"].model = model_name
    self:saveModelToConfig(model_name, "local")
    return true
end

-- Set Local AI Endpoint
function AIHelper:setLocalAIEndpoint(url)
    if not url or #url == 0 then return false end
    self.providers["local"].endpoint = url
    -- Save to file
    local DataStorage = require("datastorage")
    local settings_dir = DataStorage:getSettingsDir()
    local xray_dir = settings_dir .. "/xray"
    local lfs = require("libs/libkoreader-lfs")
    lfs.mkdir(xray_dir)
    
    local file = io.open(xray_dir .. "/local_endpoint.txt", "w")
    if file then
        file:write(url)
        file:close()
        return true
    end
    return false
end

-- Set default provider 
function AIHelper:setDefaultProvider(provider_name)
    if not provider_name or (provider_name ~= "gemini" and provider_name ~= "chatgpt" and provider_name ~= "local") then 
        return false 
    end
    self.default_provider = provider_name
    self:saveProviderToConfig(provider_name)
    logger.info("AIHelper: Default provider changed to:", provider_name)
    return true
end

-- Save model preference to config file
function AIHelper:saveModelToConfig(model_name, provider)
    provider = provider or "gemini"
    local DataStorage = require("datastorage")
    local settings_dir = DataStorage:getSettingsDir()
    local xray_dir = settings_dir .. "/xray"
    local lfs = require("libs/libkoreader-lfs")
    lfs.mkdir(xray_dir)
    
    local model_file = xray_dir .. "/" .. provider .. "_model.txt"
    local file = io.open(model_file, "w")
    if file then
        file:write(model_name)
        file:close()
        return true
    end
    return false
end

-- Save provider preference to config file 
function AIHelper:saveProviderToConfig(provider_name)
    local DataStorage = require("datastorage")
    local settings_dir = DataStorage:getSettingsDir()
    local xray_dir = settings_dir .. "/xray"
    local lfs = require("libs/libkoreader-lfs")
    lfs.mkdir(xray_dir)
    
    local provider_file = xray_dir .. "/default_provider.txt"
    local file = io.open(provider_file, "w")
    if file then
        file:write(provider_name)
        file:close()
        logger.info("AIHelper: Saved default provider:", provider_name)
        return true
    end
    logger.warn("AIHelper: Failed to save provider preference")
    return false
end

-- Initialize AIHelper
function AIHelper:init()
    self:loadConfig()
    self:loadModelFromFile()
    self:loadLanguage()
    logger.info("AIHelper: Initialized with Gemini model:", self.providers.gemini.model)
    logger.info("AIHelper: ChatGPT model:", self.providers.chatgpt.model)
end

-- Load configuration
function AIHelper:loadConfig()
    local success, config = pcall(require, "config")
    if success and config then
        if config.gemini_api_key then self.providers.gemini.api_key = config.gemini_api_key end
        if config.gemini_model then self.providers.gemini.model = config.gemini_model end
        if config.chatgpt_api_key then self.providers.chatgpt.api_key = config.chatgpt_api_key end
        if config.chatgpt_model then self.providers.chatgpt.model = config.chatgpt_model end
        
        if config.local_api_key then self.providers["local"].api_key = config.local_api_key end
        if config.local_endpoint then self.providers["local"].endpoint = config.local_endpoint end
        if config.local_model then self.providers["local"].model = config.local_model end

        if config.default_provider then self.default_provider = config.default_provider end
        if config.settings then self.settings = config.settings end
    end
end

-- Load model preference
function AIHelper:loadModelFromFile()
    local DataStorage = require("datastorage")
    
    -- Gemini model
    local gemini_file = DataStorage:getSettingsDir() .. "/xray/gemini_model.txt"
    local file = io.open(gemini_file, "r")
    if file then
        local model = file:read("*a"):match("^%s*(.-)%s*$")
        file:close()
        if model and #model > 0 then
            self.providers.gemini.model = model
        end
    end
    
    -- ChatGPT model
    local chatgpt_file = DataStorage:getSettingsDir() .. "/xray/chatgpt_model.txt"
    file = io.open(chatgpt_file, "r")
    if file then
        local model = file:read("*a"):match("^%s*(.-)%s*$")
        file:close()
        if model and #model > 0 then
            self.providers.chatgpt.model = model
        end
        if model and #model > 0 then
            self.providers.chatgpt.model = model
        end
    end

    -- Local AI model
    local local_file = DataStorage:getSettingsDir() .. "/xray/local_model.txt"
    file = io.open(local_file, "r")
    if file then
        local model = file:read("*a"):match("^%s*(.-)%s*$")
        file:close()
        if model and #model > 0 then
            self.providers["local"].model = model
        end
    end
    
    -- Local AI Endpoint
    local local_endpoint_file = DataStorage:getSettingsDir() .. "/xray/local_endpoint.txt"
    file = io.open(local_endpoint_file, "r")
    if file then
        local url = file:read("*a"):match("^%s*(.-)%s*$")
        file:close()
        if url and #url > 0 then
            self.providers["local"].endpoint = url
        end
    end
    
    -- Default provider (YENI)
    local provider_file = DataStorage:getSettingsDir() .. "/xray/default_provider.txt"
    file = io.open(provider_file, "r")
    if file then
        local provider = file:read("*a"):match("^%s*(.-)%s*$")
        file:close()
        if provider and (provider == "gemini" or provider == "chatgpt" or provider == "local") then
            self.default_provider = provider
            logger.info("AIHelper: Loaded default provider from file:", provider)
        end
    end
        -- Gemini API Key
    local gemini_key_file = DataStorage:getSettingsDir() .. "/xray/gemini_api_key.txt"
    file = io.open(gemini_key_file, "r")
    if file then
        local key = file:read("*a"):match("^%s*(.-)%s*$")
        file:close()
        if key and #key > 0 then
            self.providers.gemini.api_key = key
            logger.info("AIHelper: Loaded Gemini API key from file")
        end
    end
    
    -- ChatGPT API Key
    local chatgpt_key_file = DataStorage:getSettingsDir() .. "/xray/chatgpt_api_key.txt"
    file = io.open(chatgpt_key_file, "r")
    if file then
        local key = file:read("*a"):match("^%s*(.-)%s*$")
        file:close()
        if key and #key > 0 then
            self.providers.chatgpt.api_key = key
            logger.info("AIHelper: Loaded ChatGPT API key from file")
        end
        if key and #key > 0 then
            self.providers.chatgpt.api_key = key
            logger.info("AIHelper: Loaded ChatGPT API key from file")
        end
    end
    
    -- Local AI API Key
    local local_key_file = DataStorage:getSettingsDir() .. "/xray/local_api_key.txt"
    file = io.open(local_key_file, "r")
    if file then
        local key = file:read("*a"):match("^%s*(.-)%s*$")
        file:close()
        if key and #key > 0 then
            self.providers["local"].api_key = key
            logger.info("AIHelper: Loaded Local AI API key from file")
        end
    end
end


-- Save API Key preference to file
function AIHelper:saveAPIKeyToFile(provider, api_key)
    local DataStorage = require("datastorage")
    local settings_dir = DataStorage:getSettingsDir()
    local xray_dir = settings_dir .. "/xray"
    local lfs = require("libs/libkoreader-lfs")
    lfs.mkdir(xray_dir)
    
    local key_file = xray_dir .. "/" .. provider .. "_api_key.txt"
    local file = io.open(key_file, "w")
    if file then
        file:write(api_key)
        file:close()
        logger.info("AIHelper: Saved", provider, "API key to file")
        return true
    end
    logger.warn("AIHelper: Failed to save", provider, "API key")
    return false
end

-- Process text range (delta) with percentage-based caching
function AIHelper:processTextRange(title, author, config, book_text, target_percent, on_complete, progress_callback, context)
    local chunk_size = self.CHUNK_SIZE or 25000
    local cache_mgr = CacheManager:new()
    local total_len = #book_text
    local book_path = context and context.book_path or nil
    
    logger.info("AIHelper: processTextRange started")
    logger.info("AIHelper: Config Type:", config.type)
    logger.info("AIHelper: Target Percent:", target_percent)
    logger.info("AIHelper: Total Text Len:", total_len)
    
    local start_percent = 0
    local start_byte = 1
    local current_data = nil
    
    -- Ensure start_byte aligns with UTF-8 char boundary (skip continuation bytes)
    if start_byte > 1 then
        while start_byte <= #book_text do
             local b = string.byte(book_text, start_byte)
             -- If continuation byte (10xxxxxx: 128-191), skip forward
             if b and b >= 128 and b <= 191 then
                 start_byte = start_byte + 1
             else
                 break
             end
        end
    end
    
    -- Check cache filesystem for xx%.json files to determine start_percent
    if book_path then
        local caches = cache_mgr:getAvailableCaches(book_path)
        -- Find nearest cache < target_percent
        for i = #caches, 1, -1 do
            if caches[i].percent < target_percent then
                local content = cache_mgr:getAnalysis(book_path, caches[i].percent)
                if content then
                    -- Parse it to ensure it's valid
                    local success, data = pcall(json.decode, content)
                    if success and data then
                         start_percent = caches[i].percent
                         current_data = data
                         -- Calculate start_byte based on ratio
                         if target_percent > 0 then
                             start_byte = math.floor((start_percent / target_percent) * total_len) + 1
                         end
                         logger.info("AIHelper: Found cache at " .. start_percent .. "% from filesystem")
                         break
                    end
                end
            end
        end
    end
    
    -- If we have existing_data from context but no filesystem cache, use context data
    if not current_data and context and context.existing_data then
        current_data = context.existing_data
        -- Try to get percent from cached_percent field or use 0
        start_percent = context.existing_data.cached_percent or 0
        if target_percent > 0 and start_percent < target_percent then
            start_byte = math.floor(total_len * start_percent / target_percent) + 1
        end
        logger.info("AIHelper: Using existing data from context, start_percent:", start_percent)
    end
    
    if current_data then
        logger.info("AIHelper: Resuming from " .. start_percent .. "% (Byte: " .. start_byte .. ")")
    end
    
    -- Target byte for this request
    -- We assume book_text provided corresponds roughly to 0..target_percent range
    local target_byte = total_len
    
    -- If we are already there (or past)
    if start_byte >= target_byte then
        logger.info("AIHelper: Target already reached or exceeded by cache.")
        if on_complete then on_complete(current_data) end
        return
    end
    
    -- 2. Calculate remaining text to retain context/integrity
    -- We'll process from start_byte to target_byte
    local text_to_process = string.sub(book_text, start_byte, target_byte)
    
    -- Helper to ensure valid UTF-8
    local function sanitizeUTF8(str)
        if not str then return "" end
        local buffer = {}
        for c in str:gmatch(util.UTF8_CHAR_PATTERN) do
            table.insert(buffer, c)
        end
        return table.concat(buffer)
    end

    -- Split into chunks
    local chunks = {}
    local i = 1
    local len = #text_to_process
    
    while i <= len do
        local j = math.min(i + chunk_size, len + 1)
         -- UTF-8 check: Ensure we don't cut a multibyte char in half
        while j > i and j <= len do
             local b = string.byte(text_to_process, j)
             -- 0xc0 is 192. Any byte < 192 is either ASCII (<128) or continuation (128-191).
             -- Start bytes are >= 192.
             -- If we are at a continuation byte (10xxxxxx, 0x80-0xBF i.e. 128-191), go back.
             if b >= 128 and b <= 191 then j = j - 1 else break end
        end
        
        local chunk = string.sub(text_to_process, i, j - 1)
        
        -- SANITIZE CHUNK
        chunk = sanitizeUTF8(chunk)
        -- Keep control char filter
        chunk = chunk:gsub("[%z\1-\8\11-\12\14-\31]", "")
        
        -- DEBUG: Log the hex of the chunk end to check for dangling bytes
        if #chunk > 0 then
             local tail = string.sub(chunk, -10)
             local hex = ""
             for k=1, #tail do
                 hex = hex .. string.format("%02X ", string.byte(tail, k))
             end
             logger.info("AIHelper: Chunk " .. #chunks + 1 .. " sanitized. End hex: " .. hex)
             -- Also searching for potential bad sequences
             if not utf8 then
                 logger.warn("AIHelper: UTF8 library NOT available, using fallback sanitization.")
             end
        end
        
        table.insert(chunks, chunk)
        i = j
    end
    
    logger.info("AIHelper: Processing delta (" .. start_percent .. "% -> " .. target_percent .. "%) in " .. #chunks .. " chunks")
    
    local chunk_idx = 1
    local cumulative_len = start_byte - 1 -- Length processed so far (including cache)
    
    local function process_next_chunk()
        -- Trace entry
        logger.info("AIHelper: process_next_chunk called. chunk_idx:", chunk_idx, "max:", #chunks)
        
        if config.type == "local" then
             logger.info("AIHelper: Processing chunk for LOCAL AI. Endpoint:", config.endpoint)
        end

        local success, err = pcall(function()
            if progress_callback then
                logger.info("AIHelper: Calling progress_callback")
                local signal = progress_callback(chunk_idx, #chunks, "Analysing part " .. chunk_idx .. "/" .. #chunks)
                if signal == "ABORT" or signal == false then
                    logger.info("AIHelper: Aborted by user signal")
                    if on_complete then on_complete(nil, "aborted", "User Cancelled") end
                    return
                end
            end
            
            if chunk_idx > #chunks then
                 logger.info("AIHelper: All chunks processed (idx > max)")
                 if on_complete then on_complete(current_data) end
                 return
            end
            
            local chunk = chunks[chunk_idx]
            -- Update processed length for this chunk
            cumulative_len = cumulative_len + #chunk
            -- Scale percentage based on target_percent (since total_len corresponds to target_percent)
            local current_percent_marker = math.floor((cumulative_len / total_len) * target_percent)
            
            logger.info("AIHelper: Preparing prompt for chunk", chunk_idx)
            
            local prompt
            if not current_data then
                 -- No previous data (starting from 0%)
                 prompt = self:createPrompt(title, author, nil, chunk)
            else
                 -- Has previous data (from cache or previous chunk)
                 logger.info("AIHelper: Encoding current_data for incremental prompt")
                 local json_data = json.encode(current_data)
                 logger.info("AIHelper: Encoded data length:", #json_data)
                 prompt = string.format(self.prompts.incremental, title, author, json_data, chunk)
            end
            
            logger.info("AIHelper: Calling API for chunk", chunk_idx)
            
            -- Call API
            local data, err_code, err_msg
            if config.type == "local" then
                data, err_code, err_msg = self:callLocalAI(prompt, config)
            elseif config.type == "chatgpt" then
                data, err_code, err_msg = self:callChatGPT(prompt, config)
            else
                data, err_code, err_msg = self:callGemini(prompt, config)
            end
            
            if data then
                 logger.info("AIHelper: API Success for chunk", chunk_idx)
                 current_data = data
                 
                 if book_path then
                     -- Align to percentage
                     logger.info("AIHelper: Saving intermediate cache for", current_percent_marker, "%")
                     current_data.analysis_progress = current_percent_marker
                     cache_mgr:saveAnalysis(book_path, current_percent_marker, json.encode(current_data))
                 end
                 
                 chunk_idx = chunk_idx + 1
                 if chunk_idx <= #chunks then
                     logger.info("AIHelper: Scheduling next chunk", chunk_idx)
                     UIManager:scheduleIn(0.1, process_next_chunk)
                 else
                     logger.info("AIHelper: Delta analysis complete")
                     if on_complete then 
                         current_data.analysis_progress = target_percent 
                         on_complete(current_data) 
                     end
                 end
            else
                 logger.warn("AIHelper: Chunk failed:", err_msg)
                 -- Fallback to existing data if available
                 if current_data then
                     logger.info("AIHelper: Falling back to previous valid data")
                     -- Pass data as success, but maybe log the error/warning
                     if on_complete then on_complete(current_data) end
                 else
                     -- No data at all, return error
                     if on_complete then on_complete(nil, err_code, err_msg) end
                 end
            end
        end)

        if not success then
             logger.error("AIHelper: CRITICAL ERROR in process_next_chunk:", err)
             if on_complete then on_complete(nil, "error_crash", tostring(err)) end
        end
    end
    
    UIManager:scheduleIn(0.1, process_next_chunk)
end

-- Get book data from AI
-- reading_percent: optional int (0-100)
function AIHelper:getBookData(title, author, provider_name, context, book_text, on_complete, progress_callback)
    self:loadModelFromFile()
    local provider = provider_name or "gemini"
    local provider_config = self.providers[provider]
    
    if not provider_config or not provider_config.api_key then
        if on_complete then on_complete(nil, "error_no_api_key") end
        return
    end
    
    if not book_text then
         if on_complete then on_complete(nil, "error_no_text", "No book text provided") end
         return
    end
    
    -- Determine target percent
    local target_percent = 100
    if context and context.reading_percent then
        target_percent = context.reading_percent
    end
    
    -- Always use processTextRange for flexibility
    if provider == "gemini" then
        self:processTextRange(title, author, provider_config, book_text, target_percent, on_complete, progress_callback, context)
    else
         -- Fallback for ChatGPT or other providers if not using incremental
         -- (For now, we just support this new flow for Gemini/incremental capable)
         -- If small, maybe just one shot? But user wants percentage cache.
         -- Let's stick to processTextRange logic if possible, but ChatGPT might be expensive for repeated calls.
         -- For now, redirect to processTextRange as well (assuming prompt is compatible).
         self:processTextRange(title, author, provider_config, book_text, target_percent, on_complete, progress_callback, context)
    end
end

-- Check network
function AIHelper:checkNetworkConnectivity()
    local socket = require("socket")
    local success, err = pcall(function()
        local tcp = socket.tcp()
        tcp:settimeout(3)
        local result = tcp:connect("8.8.8.8", 53)
        tcp:close()
        return result
    end)
    return success
end

-- Load language
function AIHelper:loadLanguage()
    local DataStorage = require("datastorage")
    local f = io.open(DataStorage:getSettingsDir() .. "/xray/language.txt", "r")
    self.current_language = f and f:read("*a"):match("^%s*(.-)%s*$") or "zh"
    if f then f:close() end
    self:loadPrompts()
end

-- Load prompts
function AIHelper:loadPrompts()
    local success, prompts = pcall(require, "prompts/" .. self.current_language)
    if not success then 
        success, prompts = pcall(require, "prompts/zh") 
    end
    self.prompts = prompts or {}
end

-- Create prompt
-- book_text: optional full text of the book for text-based analysis
function AIHelper:createPrompt(title, author, context, book_text)
    if not self.prompts then self:loadLanguage() end
    
    -- If book_text is provided, use text_based prompt (most accurate)
    if book_text and #book_text > 100 then
        local template = self.prompts.text_based or self.prompts.main
        -- Truncate if too long (centralized limit)
        local MAX_CHARS = 100000000
        if #book_text > MAX_CHARS then
            book_text = string.sub(book_text, 1, MAX_CHARS)
            logger.info("AIHelper: Truncated book text to", MAX_CHARS, "characters")
        end
        return string.format(template, title, author or "Unknown", book_text)
    -- Spoiler-free mode (legacy, less accurate)
    elseif context and context.spoiler_free then
        local template = self.prompts.spoiler_free or self.prompts.main
        return string.format(template, title, author or "Unknown", context.reading_percent)
    else
        -- Full book mode (legacy, uses only title)
        local template = self.prompts.main
        return string.format(template, title, author or "Unknown")
    end
end

function AIHelper:getFallbackStrings()
    if not self.prompts then self:loadPrompts() end
    return self.prompts.fallback or {}
end

-- Call Google Gemini API (FIXED VERSION)
function AIHelper:callGemini(prompt, config)
    logger.info("AIHelper: Calling Google Gemini API")
    
    if not self:checkNetworkConnectivity() then
        return nil, "error_no_network", "No internet connection"
    end
    
    local model = config.model or "gemini-flash-lite-latest"
    local api_key = config.api_key or ""
    local url = "https://generativelanguage.googleapis.com/v1beta/models/" .. model .. ":generateContent?key=" .. api_key
    
    -- GÜVENLİK FİLTRELERİNİ KAPAT (Dostoyevski vb. için şart)
    local safety_settings = {
        { category = "HARM_CATEGORY_HARASSMENT", threshold = "BLOCK_NONE" },
        { category = "HARM_CATEGORY_HATE_SPEECH", threshold = "BLOCK_NONE" },
        { category = "HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold = "BLOCK_NONE" },
        { category = "HARM_CATEGORY_DANGEROUS_CONTENT", threshold = "BLOCK_NONE" }
    }

    local request_body = json.encode({
        contents = {{ parts = {{ text = prompt }} }},
        safetySettings = safety_settings,
        generationConfig = {
            temperature = 0.4,
            topK = 40,
            topP = 0.95,
            responseMimeType = "application/json" -- JSON Modu
        }
    })
    
    -- RETRY LOGIC
    local max_retries = 3
    
    -- Increase timeout (default is usually 30s)
    if http then http.TIMEOUT = 60 end
    if https then https.TIMEOUT = 60 end
    
    for attempt = 1, max_retries + 1 do
        if attempt > 1 then
             local socket = require("socket")
             socket.sleep(3) 
        end

        local response_body = {}
        
        -- Log FULL request body (for debugging purposes)
        if attempt == 1 then
             logger.info("AIHelper: Full Request Body:", request_body)
        end
        
        local res, code, headers, status = https.request{
            url = url,
            method = "POST",
            headers = {
                ["Content-Type"] = "application/json",
                -- ["Content-Length"] = tostring(#request_body), -- Let ltn12/http handle calculation if possible or ensure precise count
                 ["Content-Length"] = tostring(#request_body),
            },
            source = ltn12.source.string(request_body),
            sink = ltn12.sink.table(response_body)
        }
        
        local response_text = table.concat(response_body)
        local code_num = tonumber(code)
        
        logger.info("AIHelper: API Code:", code_num, "Length:", #response_text)
        
        -- Handle connection failure (nil code)
        if not code_num then
            logger.warn("AIHelper: Connection failed, code is nil. Status:", status or "unknown")
            -- Retry on first attempt
            if attempt < max_retries + 1 then
                logger.info("AIHelper: Retrying after connection failure...")
            else
                return nil, "error_network", "网络连接失败: " .. (status or "无响应")
            end
        elseif code_num == 200 then
            local success, data = pcall(json.decode, response_text)
            if not success then return nil, "error_json_parse" end
            
            -- Check for prompt-level blocking (Prohibited Content)
            if data and data.promptFeedback and data.promptFeedback.blockReason then
                local reason = data.promptFeedback.blockReason
                logger.warn("AIHelper: Prompt blocked by safety filter. Reason:", reason)
                -- Return empty fallback data to continue gracefully
                return { characters = {}, locations = {}, events = {}, terms = {} }
            end

            -- CRASH PROTECTION: Null check
            if data and data.candidates and data.candidates[1] then
                local candidate = data.candidates[1]
                
                -- Check for candidate-level blocking
                if candidate.finishReason == "SAFETY" then
                     logger.warn("AIHelper: Response blocked by safety filter (finishReason=SAFETY). Returning empty result.")
                     return { characters = {}, locations = {}, events = {}, terms = {} }
                end

                if candidate.content and candidate.content.parts and candidate.content.parts[1] then
                    return self:parseAIResponse(candidate.content.parts[1].text)
                else
                    logger.warn("AIHelper: No text in response. Candidate:", json.encode(candidate))
                    -- Also handle this gracefully if possible, or return error if it's truly broken
                    -- Returning empty for now to be safe
                    return { characters = {}, locations = {}, events = {}, terms = {} }
                end
            else
                logger.warn("AIHelper: Invalid response structure. Full response:", response_text)
                return nil, "error_api", "Invalid response format"
            end
        elseif code_num == 503 or code_num == 504 then
             logger.warn("AIHelper: Service Unavailable/Timeout (Retrying...)")
        else
             return nil, "error_" .. tostring(code_num), "Error Code: " .. tostring(code_num)
        end
    end
    
    return nil, "error_timeout", "Zaman aşımı"
end

-- Generic OpenAI-compatible API Call
function AIHelper:callOpenAICompatible(prompt, config, provider_label)
    provider_label = provider_label or "OpenAI Compatible"
    logger.info("AIHelper: Calling " .. provider_label .. " API")
    
    if not self:checkNetworkConnectivity() then
         -- For local, maybe network check is irrelevant if localhost? 
         -- But socket check is 8.8.8.8, which fails if offline.
         -- If local endpoint is 127.0.0.1, we might not need internet.
         -- Also allow 192.168.x.x and 10.x.x.x private ranges
         local is_local = config.endpoint:find("127.0.0.1") or 
                          config.endpoint:find("localhost") or
                          config.endpoint:find("192.168.") or
                          config.endpoint:find("10.")
                          
         if not is_local then
             logger.warn("AIHelper: Network check failed and not a local endpoint. Endpoint:", config.endpoint)
             return nil, "error_no_network", "No internet connection"
         else
             logger.info("AIHelper: Network check failed but local endpoint detected, proceeding. Endpoint:", config.endpoint)
         end
    end
    
    local model = config.model
    local url = config.endpoint
    local api_key = config.api_key or "dummy"
    
    -- System instruction
    local system_instruction = self.prompts and self.prompts.system_instruction or 
        "You are an expert literary critic. Respond ONLY with valid JSON format."
    
    local request_body = json.encode({
        model = model,
        messages = {
            {
                role = "system",
                content = system_instruction
            },
            {
                role = "user",
                content = prompt
            }
        },
        temperature = 0.4,
        max_tokens = 8192,
        top_p = 0.95,
        response_format = { type = "json_object" } -- JSON mode
    })
    
    logger.info("AIHelper:", provider_label, "request size:", #request_body)
    logger.info("AIHelper: Request Body:", request_body)
    
    -- RETRY LOGIC
    local max_retries = 1
    for attempt = 1, max_retries + 1 do
        if attempt > 1 then
             local socket = require("socket")
             socket.sleep(3) 
             logger.info("AIHelper: Retrying " .. provider_label .. " request (attempt " .. attempt .. ")")
        end

        local response_body = {}
        local response_body = {}
        
        -- Select correct transport based on URL scheme
        local request_func = https.request
        if url:find("^http://") then
            request_func = http.request
            logger.info("AIHelper: Using plain HTTP for", url)
        end
        
        local res, code, headers, status = request_func{
            url = url,
            method = "POST",
            headers = {
                ["Content-Type"] = "application/json",
                ["Authorization"] = "Bearer " .. api_key,
                ["Content-Length"] = tostring(#request_body),
            },
            source = ltn12.source.string(request_body),
            sink = ltn12.sink.table(response_body),
            -- timeout only supported by some implementations or handled via socket.http.TIMEOUT
        }
        
        -- Log raw error if code is nil (usually connection refused)
        if not code then
             logger.warn("AIHelper: Connection failed. Error info:", tostring(res), tostring(code), tostring(status))
        end
        
        local response_text = table.concat(response_body)
        local code_num = tonumber(code)
        
        
        logger.info("AIHelper:", provider_label, "API Code:", code_num, "Length:", #response_text)
        logger.info("AIHelper: Response headers:", json.encode(headers))

        if code_num == 200 then
            local success, data = pcall(json.decode, response_text)
            if not success then 
                logger.warn("AIHelper: JSON parse error")
                return nil, "error_json_parse" 
            end
            
            if data and data.choices and data.choices[1] then
                local choice = data.choices[1]
                if choice.message and choice.message.content then
                    local content = choice.message.content
                    logger.info("AIHelper: Response received, parsing...")
                    return self:parseAIResponse(content)
                end
            end
             
             if data and data.error then
                 logger.warn("AIHelper: API Error:", data.error.message or "Unknown")
                 return nil, "error_api", data.error.message or "API Error"
             end
             return nil, "error_api", "Invalid response format"
             
        elseif code_num == 429 then
            logger.warn("AIHelper: 429 Rate Limit")
            if attempt <= max_retries then
                local socket = require("socket")
                socket.sleep(5)
            end
        else
            logger.warn("AIHelper: Unexpected error code:", code_num)
            return nil, "error_" .. tostring(code_num), "Error Code: " .. tostring(code_num)
        end
    end
    
    return nil, "error_timeout", "Timeout"
end

-- Call ChatGPT API (Wrapper)
function AIHelper:callChatGPT(prompt, config)
    return self:callOpenAICompatible(prompt, config, "ChatGPT")
end

-- Call Local AI (Wrapper)
function AIHelper:callLocalAI(prompt, config)
    return self:callOpenAICompatible(prompt, config, "Local AI")
end

function AIHelper:parseAIResponse(response_text)
    logger.info("AIHelper: Received AI Response (JSON), length:", #response_text)
    
    -- Clean markdown code blocks
    -- Temizlik
    local json_text = response_text:gsub("```json", ""):gsub("```", ""):gsub("^%s+", ""):gsub("%s+$", "")
    
    -- Parse
    local success, data = pcall(json.decode, json_text)
    
    logger.info("AIHelper: First parse attempt:", success and "success" or "failed")
    
    -- Eğer başarısızsa, {} arasını bulmaya çalış
    if not success then
        logger.warn("AIHelper: Parse error:", tostring(data))
        local first = json_text:find("{")
        local last_brace = nil
        for i = #json_text, 1, -1 do
            if json_text:sub(i,i) == "}" then last_brace = i; break end
        end
        if first and last_brace then
             json_text = json_text:sub(first, last_brace)
             success, data = pcall(json.decode, json_text)
             logger.info("AIHelper: Second parse attempt:", success and "success" or "failed")
        end
    end

    if success and data then
        logger.info("AIHelper: Parsing successful, validating data...")
        return self:validateAndCleanData(data)
    end
    logger.warn("AIHelper: parseAIResponse returning nil")
    return nil
end

function AIHelper:validateAndCleanData(data)
    if not data then return nil end
    local strings = self:getFallbackStrings()
    
    local function ensureString(v, d)
        return (type(v) == "string" and #v > 0) and v or d or ""
    end
    
    -- Helper: Clean Markdown and normalize text (no truncation - let AI control length via prompt)
    local function cleanText(text)
        if type(text) ~= "string" then return "" end
        -- Remove Markdown formatting
        text = text:gsub("%*%*(.-)%*%*", "%1") -- Remove **bold**
        text = text:gsub("%*(.-)%*", "%1")     -- Remove *italic*
        text = text:gsub("##+ ", "")           -- Remove headers
        text = text:gsub("\\n", " ")           -- Normalize escaped newlines
        text = text:gsub("\n", " ")            -- Normalize actual newlines
        text = text:gsub("  +", " ")           -- Collapse multiple spaces
        text = text:match("^%s*(.-)%s*$") or "" -- Trim
        return text
    end
    
    -- Helper: Parse occupation to array
    local function parseOccupation(occ)
        if type(occ) == "table" then return occ end
        if type(occ) ~= "string" or #occ == 0 then return {} end
        local arr = {}
        -- Split by / or \ or escaped versions
        for part in occ:gmatch("[^/\\]+") do
            local cleaned = part:match("^%s*(.-)%s*$")
            if cleaned and #cleaned > 0 then
                table.insert(arr, cleaned)
            end
        end
        return #arr > 0 and arr or {occ}
    end
    
    -- Helper: Generate stable ID from name
    local function generateId(prefix, name)
        if not name or #name == 0 then return prefix .. "_unknown" end
        local hash = md5(name)
        return prefix .. "_" .. hash:sub(1, 8)
    end
    
    -- Helper: Canonical name for deduplication (lowercase, trimmed, no parens)
    local function canonicalName(name)
        if type(name) ~= "string" then return "" end
        -- Remove content in parenthesis (English and Chinese)
        local clean = name:gsub("%(.-%)", ""):gsub("（.-）", "")
        return clean:lower():gsub("^%s+", ""):gsub("%s+$", ""):gsub("%s+", " ")
    end

    -- 1. AUTHOR & BOOK
    data.book_title = data.book_title or data.title or strings.unknown_book
    data.author = data.author or data.book_author or strings.unknown_author
    data.author_bio = cleanText(data.author_bio or data.AuthorBio or data.bio)
    data.summary = cleanText(data.summary or data.book_summary)
    
    -- Remove empty author_bio
    if #data.author_bio == 0 then data.author_bio = nil end

    -- 2. CHARACTERS with deduplication
    local chars = data.characters or data.Characters or {}
    local char_map = {} -- canonical_name -> merged character
    local char_order = {} -- maintain insertion order
    
    for _, c in ipairs(chars) do
        if type(c) == "table" and c.name then
            local canon = canonicalName(c.name)
            if #canon > 0 then
                if char_map[canon] then
                    -- Merge into existing character
                    local existing = char_map[canon]
                    
                    -- Improved: Update name if new name is cleaner (shorter usually means no extra parens/titles)
                    local new_clean = ensureString(c.name or c.Name, "")
                    if #new_clean > 0 and #new_clean < #existing.name then
                         logger.info("AIHelper: Upgrading name", existing.name, "->", new_clean)
                         existing.name = new_clean
                    end
                    
                    -- Keep the more specific role
                    if c.role and c.role ~= "未指定" and c.role ~= strings.not_specified then
                        if existing.role == "未指定" or existing.role == strings.not_specified then
                             existing.role = c.role
                        end
                    end
                    
                    -- Merge descriptions (consolidate, not concatenate)
                    local new_desc = cleanText(c.description or c.desc)
                    if #new_desc > 0 and not existing.description:find(new_desc:sub(1, 50), 1, true) then
                        -- Only add if substantially new content
                        local combined = existing.description .. " " .. new_desc
                        existing.description = cleanText(combined)
                    end
                    -- Merge occupations
                    local new_occ = parseOccupation(c.occupation)
                    for _, o in ipairs(new_occ) do
                        local found = false
                        for _, eo in ipairs(existing.occupation) do
                             if eo:lower() == o:lower() then found = true; break end
                        end
                        if not found then table.insert(existing.occupation, o) end
                    end
                    logger.info("AIHelper: Merged duplicate character:", c.name)
                else
                    -- New character
                    local char = {
                        id = c.id or generateId("char", c.name),
                        name = ensureString(c.name or c.Name, strings.unnamed_character),
                        role = ensureString(c.role or c.Role, strings.not_specified),
                        description = cleanText(c.description or c.desc),
                        gender = ensureString(c.gender, ""),
                        occupation = parseOccupation(c.occupation)
                    }
                    char_map[canon] = char
                    table.insert(char_order, canon)
                end
            end
        end
    end
    
    -- Rebuild character list in order
    local valid_chars = {}
    for _, canon in ipairs(char_order) do
        table.insert(valid_chars, char_map[canon])
    end
    data.characters = valid_chars

    -- 3. HISTORICAL FIGURES with deduplication
    local hists = data.historical_figures or data.historicalFigures or {}
    local hist_map = {}
    local hist_order = {}
    
    for _, h in ipairs(hists) do
        if type(h) == "table" and h.name then
            local canon = canonicalName(h.name)
            if #canon > 0 and not hist_map[canon] then
                hist_map[canon] = {
                    id = h.id or generateId("hist", h.name),
                    name = ensureString(h.name or h.Name, strings.unnamed_person),
                    biography = cleanText(h.biography or h.bio),
                    role = ensureString(h.role, ""),
                    importance_in_book = cleanText(h.importance_in_book or h.importance),
                    context_in_book = cleanText(h.context_in_book or h.context)
                }
                table.insert(hist_order, canon)
            end
        end
    end
    
    local valid_hists = {}
    for _, canon in ipairs(hist_order) do
        table.insert(valid_hists, hist_map[canon])
    end
    data.historical_figures = #valid_hists > 0 and valid_hists or nil

    -- 4. LOCATIONS - convert dict to array if needed
    local locs = data.locations or {}
    local valid_locs = {}
    
    if type(locs) == "table" then
        -- Check if dict format (string keys with string values)
        local is_dict = false
        for k, v in pairs(locs) do
            if type(k) == "string" and type(v) == "string" then
                is_dict = true
                break
            end
        end
        
        if is_dict then
            -- Convert dict to array
            for name, desc in pairs(locs) do
                if type(name) == "string" and #name > 0 then
                    table.insert(valid_locs, {
                        id = generateId("loc", name),
                        name = name,
                        description = cleanText(desc),
                        importance = ""
                    })
                end
            end
            logger.info("AIHelper: Converted locations dict to array format")
        else
            -- Already array format, just validate
            for _, loc in ipairs(locs) do
                if type(loc) == "table" and loc.name then
                    table.insert(valid_locs, {
                        id = loc.id or generateId("loc", loc.name),
                        name = ensureString(loc.name, ""),
                        description = cleanText(loc.description),
                        importance = cleanText(loc.importance)
                    })
                end
            end
        end
    end
    data.locations = #valid_locs > 0 and valid_locs or nil
    
    -- 5. THEMES
    local themes = data.themes or {}
    local valid_themes = {}
    for _, t in ipairs(themes) do
        if type(t) == "string" and #t > 0 then
            table.insert(valid_themes, t)
        end
    end
    data.themes = #valid_themes > 0 and valid_themes or nil
    
    -- 6. TIMELINE with sequence
    local timeline = data.timeline or {}
    local valid_timeline = {}
    for i, t in ipairs(timeline) do
        if type(t) == "table" and (t.event or t.importance) then
            table.insert(valid_timeline, {
                sequence = t.sequence or i,
                event = cleanText(t.event),
                chapter = ensureString(t.chapter, ""),
                importance = cleanText(t.importance)
            })
        end
    end
    data.timeline = #valid_timeline > 0 and valid_timeline or nil
    
    return data
end

function AIHelper:setAPIKey(provider, api_key)
    if self.providers[provider] then
        self.providers[provider].api_key = api_key:gsub("%s+", "")
        self:saveAPIKeyToFile(provider, api_key)
        return true
    end
    return false
end

function AIHelper:testAPIKey(provider)
    local provider_config = self.providers[provider]
    
    if not provider_config then
        return false, "Unknown provider"
    end
    
    if not provider_config.api_key or #provider_config.api_key == 0 then
        return false, "AI API Key not set"
    end
    
    if not self:checkNetworkConnectivity() then
        return false, "No internet connection!"
    end
    
    logger.info("AIHelper: Testing", provider, "API key")
    
    local test_prompt = "Test: 'OK'"
    
    if provider == "gemini" then
        local result, error_code, error_msg = self:callGemini(test_prompt, provider_config)
        if result then
            return true, "Success"
        else
            return false, error_msg or ("Error: " .. (error_code or "Unknown"))
        end
        
    elseif provider == "chatgpt" then
        local result, error_code, error_msg = self:callChatGPT(test_prompt, provider_config)
        if result then
            return true, "Success"
        else
            return false, error_msg or ("Error: " .. (error_code or "Unknown"))
        end
    end
    
    return false, "Unsupported provider"
end

return AIHelper
