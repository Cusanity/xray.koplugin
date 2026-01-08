-- CacheManager - X-Ray data caching system
local lfs = require("libs/libkoreader-lfs")
local logger = require("logger")
local DocSettings = require("docsettings")
local json = require("json")

local CacheManager = {}

function CacheManager:new(o)
    o = o or {}
    setmetatable(o, self)
    self.__index = self
    return o
end

-- Get cache file path for a book
function CacheManager:getCachePath(book_path)
    if not book_path then
        return nil
    end
    
    -- Use KOReader's sidecar directory
    local cache_dir = DocSettings:getSidecarDir(book_path)
    local cache_file = cache_dir .. "/xray_cache.lua"
    
    logger.info("CacheManager: Cache path:", cache_file)
    return cache_file
end

-- Ensure directory exists
function CacheManager:ensureDirectory(path)
    local dir = path:match("(.+)/[^/]+$")
    if not dir then
        return false
    end
    
    local attr = lfs.attributes(dir)
    if attr and attr.mode == "directory" then
        return true
    end
    
    logger.info("CacheManager: Creating directory:", dir)
    local success, err = lfs.mkdir(dir)
    
    if not success then
        logger.warn("CacheManager: Failed to create directory:", err or "unknown error")
        return false
    end
    
    return true
end

-- Save book data to cache
function CacheManager:saveCache(book_path, data)
    if not book_path or not data then
        logger.warn("CacheManager: Cannot save cache - invalid parameters")
        return false
    end
    
    local cache_file = self:getCachePath(book_path)
    if not cache_file then
        logger.warn("CacheManager: Cannot determine cache path")
        return false
    end
    
    -- Ensure directory exists
    if not self:ensureDirectory(cache_file) then
        logger.warn("CacheManager: Cannot create cache directory")
        return false
    end
    
    -- Add timestamp
    data.cached_at = os.time()
    data.cache_version = "6.0"
    
    -- Serialize data
    local success, err = pcall(function()
        local f, open_err = io.open(cache_file, "w")
        
        if not f then
            logger.warn("CacheManager: Cannot open file for writing:", cache_file)
            logger.warn("CacheManager: Error:", open_err or "unknown")
            return false
        end
        
        local serialized_data = self:serialize(data)
        
        if not serialized_data then
            logger.warn("CacheManager: Failed to serialize data")
            f:close()
            return false
        end
        
        f:write("-- X-Ray Cache v6.0\n")
        f:write("-- Generated: " .. os.date("%Y-%m-%d %H:%M:%S") .. "\n\n")
        f:write("return " .. serialized_data)
        f:close()
        
        logger.info("CacheManager: Saved cache to:", cache_file)
        return true
    end)
    
    if not success then
        logger.warn("CacheManager: Failed to save cache:", err or "unknown error")
        return false
    end
    
    return success
end

-- Load book data from cache
function CacheManager:loadCache(book_path)
    if not book_path then
        return nil
    end
    
    local cache_file = self:getCachePath(book_path)
    if not cache_file then
        logger.warn("CacheManager: Cannot determine cache path")
        return nil
    end
    
    -- Check if cache file exists
    local attr = lfs.attributes(cache_file)
    if not attr then
        logger.info("CacheManager: No cache file found")
        return nil
    end
    
    -- Load cache
    local success, data = pcall(function()
        return dofile(cache_file)
    end)
    
    if not success or not data then
        logger.warn("CacheManager: Failed to load cache:", data or "unknown error")
        return nil
    end
    
    -- Check cache version
    if data.cache_version ~= "6.0" then
        logger.warn("CacheManager: Cache version mismatch, ignoring")
        return nil
    end
    
    logger.info("CacheManager: Loaded cache from:", cache_file)
    return data
end

-- Serialize Lua table to string (with better error handling)
function CacheManager:serialize(obj, indent, seen)
    indent = indent or ""
    seen = seen or {}
    
    local t = type(obj)
    
    if t == "table" then
        -- Prevent infinite recursion
        if seen[obj] then
            return "{--[[circular reference]]}"
        end
        seen[obj] = true
        
        local s = "{\n"
        for k, v in pairs(obj) do
            -- Skip functions and userdata
            if type(v) ~= "function" and type(v) ~= "userdata" and type(v) ~= "thread" then
                s = s .. indent .. "  "
                if type(k) == "string" then
                    -- Check if key needs escaping
                    if k:match("^[%a_][%w_]*$") then
                        s = s .. k .. " = "
                    else
                        s = s .. "[" .. string.format("%q", k) .. "] = "
                    end
                else
                    s = s .. "[" .. tostring(k) .. "] = "
                end
                s = s .. self:serialize(v, indent .. "  ", seen) .. ",\n"
            end
        end
        s = s .. indent .. "}"
        return s
    elseif t == "string" then
        return string.format("%q", obj)
    elseif t == "number" or t == "boolean" then
        return tostring(obj)
    elseif t == "nil" then
        return "nil"
    else
        -- Skip functions, userdata, threads
        return "nil"
    end
end

-- Clear cache for a book (includes main cache and all percentage-based caches)
function CacheManager:clearCache(book_path)
    local cleared = false
    
    -- Clear main cache file
    local cache_file = self:getCachePath(book_path)
    if cache_file then
        local success, err = os.remove(cache_file)
        if success then
            logger.info("CacheManager: Cleared main cache:", cache_file)
            cleared = true
        else
            logger.warn("CacheManager: Failed to clear main cache:", err or "unknown")
        end
    end
    
    -- Clear all percentage-based caches (xx%.json files)
    local analysis_dir = self:getAnalysisCacheDir(book_path)
    if analysis_dir and lfs.attributes(analysis_dir) then
        for file in lfs.dir(analysis_dir) do
            if file:match("^%d+%%%.json$") then
                local filepath = analysis_dir .. "/" .. file
                local success, err = os.remove(filepath)
                if success then
                    logger.info("CacheManager: Cleared analysis cache:", file)
                    cleared = true
                else
                    logger.warn("CacheManager: Failed to clear:", file, err or "")
                end
            end
        end
        -- Try to remove the directory if empty
        pcall(function() lfs.rmdir(analysis_dir) end)
    end
    
    return cleared
end

-- Percentage-Based Analysis Cache Methods

function CacheManager:getAnalysisCacheDir(book_path)
    if not book_path then return nil end
    local sdr = DocSettings:getSidecarDir(book_path)
    return sdr .. "/xray_analysis"
end

-- Returns sorted list of {percent, filepath}
function CacheManager:getAvailableCaches(book_path)
    local dir = self:getAnalysisCacheDir(book_path)
    if not dir then return {} end
    
    -- Check if directory exists before iterating
    if not lfs.attributes(dir) then
        return {}
    end
    
    local caches = {}
    for file in lfs.dir(dir) do
        local percent = file:match("^(%d+)%%%.json$")
        if percent then
            table.insert(caches, {
                percent = tonumber(percent),
                path = dir .. "/" .. file
            })
        end
    end
    
    -- Sort by percentage ascending
    table.sort(caches, function(a, b) return a.percent < b.percent end)
    
    return caches
end

-- Validate if analysis data is empty
function CacheManager:isValidAnalysis(content)
    if not content or content == "" then return false end
    
    local success, data = pcall(json.decode, content)
    if not success or not data then return false end
    
    -- Check for meaningful data
    local has_data = false
    
    local keys_to_check = {"characters", "locations", "themes", "events"}
    for _, key in ipairs(keys_to_check) do
        if data[key] and next(data[key]) then
            has_data = true
            break
        end
    end
    
    return has_data
end



function CacheManager:getNearestPartialCache(book_path, target_percent)
    local caches = self:getAvailableCaches(book_path)
    if #caches == 0 then return nil end
    
    -- Filter candidates <= target_percent
    local candidates = {}
    for _, cache in ipairs(caches) do
         if cache.percent <= target_percent then
             table.insert(candidates, cache)
         end
    end
    
    -- Sort candidates descending (best fit first)
    table.sort(candidates, function(a, b) return a.percent > b.percent end)
    
    -- Iterate candidates to find first non-empty one
    for _, candidate in ipairs(candidates) do
        local content = self:getAnalysis(book_path, candidate.percent)
        if self:isValidAnalysis(content) then
            -- Load content meta
            local attr = lfs.attributes(candidate.path)
            if attr then
                candidate.mtime = attr.modification
            end
            candidate.content = content
            logger.info("CacheManager: Found valid partial cache at " .. candidate.percent .. "%")
            return candidate
        else
            logger.info("CacheManager: Skipping empty/invalid partial cache at " .. candidate.percent .. "%")
        end
    end
    
    return nil
end

function CacheManager:getAnalysis(book_path, percent)
    local dir = self:getAnalysisCacheDir(book_path)
    if not dir then return nil end
    
    local file = string.format("%s/%d%%.json", dir, percent)
    local f = io.open(file, "r")
    if not f then return nil end
    
    local content = f:read("*a")
    f:close()
    
    -- It's stored as raw JSON, so just return it
    if content and #content > 0 then
        return content
    end
    return nil
end

function CacheManager:saveAnalysis(book_path, percent, json_content)
    local dir = self:getAnalysisCacheDir(book_path)
    if not dir then return false end
    
    -- Ensure dir exists
    if not self:ensureDirectory(dir .. "/dummy_file") then
        return false
    end
    
    local file = string.format("%s/%d%%.json", dir, percent)
    local f = io.open(file, "w")
    if f then
        f:write(json_content)
        f:close()
        logger.info("CacheManager: Saved analysis for " .. percent .. "%")
        return true
    end
    
    logger.warn("CacheManager: Failed to save analysis for " .. percent .. "%")
    return false
end

return CacheManager