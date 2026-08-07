-- X-Ray Sync Module
-- Handles uploading and downloading X-Ray data to/from WebDAV

local logger = require("logger")
local lfs = require("libs/libkoreader-lfs")
local ffiutil = require("ffi/util")

-- We'll use the APIs provided by KOReader
local WebDavApi = require("webdavapi")
-- Dropbox API loading if needed (simplified for now to just check type and load)
local DropboxApi = require("dropboxapi")
local util = require("util")

local Sync = {}

function Sync:new(o)
    o = o or {}
    setmetatable(o, self)
    self.__index = self
    return o
end

-- Custom listFolder that returns ALL files (not just ebooks)
-- The standard WebDavApi:listFolder filters through DocumentRegistry which excludes .json files

-- Custom listFolder that returns ALL files (not just ebooks)
function Sync:listFolderAll(api, server, folder_path)
    local http = require("socket.http")
    local ltn12 = require("ltn12")
    local socket = require("socket")
    local socketutil = require("socketutil")
    
    -- Construct Full URL for listing
    local list_url = api:getJoinedPath(server.address, folder_path)
    -- WebDavApi:listFolder forces a trailing slash, let's do the same for PROPFIND on a collection
    if list_url:sub(-1) ~= "/" then
        list_url = list_url .. "/"
    end
    
    local sink = {}
    local data = [[<?xml version="1.0"?><a:propfind xmlns:a="DAV:"><a:prop><a:resourcetype/><a:getcontentlength/><a:getetag/></a:prop></a:propfind>]]
    
    socketutil:set_timeout()
    local request = {
        url      = list_url,
        method   = "PROPFIND",
        headers  = {
            ["Content-Type"]   = "application/xml",
            ["Depth"]          = "1",
            ["Content-Length"] = #data,
        },
        user     = server.username,
        password = server.password,
        source   = ltn12.source.string(data),
        sink     = ltn12.sink.table(sink),
    }
    
    local code, _, status = socket.skip(1, http.request(request))
    socketutil:reset_timeout()
    
    if not code or code < 200 or code > 299 then
        logger.warn("Sync: listFolderAll failed:", status or code)
        return nil
    end
    
    local res_data = table.concat(sink)
    local items = {}
    
    if res_data ~= "" then
        -- Parse WebDAV PROPFIND response
        for item in res_data:gmatch("<[^:]*:response[^>]*>(.-)</[^:]*:response>") do
            local item_href = item:match("<[^:]*:href[^>]*>(.*)</[^:]*:href>")
            -- Decode URL and get filename
            -- item_href is typically absolute path e.g. /dav/path/to/file
            local decoded_href = util.urlDecode(item_href)
            local display_name = ffiutil.basename(decoded_href)
            
            -- Construct clean relative path: folder_path + / + filename
            -- This mirrors WebDavApi:listFolder logic to avoid duplication
            local clean_path = self:joinPath(folder_path, display_name)
            
            local is_collection = item:find("<[^:]*:collection[^<]*/>")
            local etag = item:match("<[^:]*:getetag[^>]*>(.-)</[^:]*:getetag>")
            
            -- Simple check to skip current directory (if paths match)
            -- Or just check if basename is empty/matches folder name?
            -- WebDavApi checks: self.trim_slashes(item_fullpath) == path
            
            local is_current = (decoded_href == list_url) or (decoded_href .. "/" == list_url) or (display_name == "")
            -- Better check: if constructed clean_path == folder_path (ignoring trailing slash)
            if clean_path:gsub("/$", "") == folder_path:gsub("/$", "") then
                is_current = true
            end

             if item_href and not is_current and not is_collection then
                table.insert(items, {
                    url = clean_path, -- Store relative path, safe for getJoinedPath later
                    display_name = display_name,
                    type = "file",
                    etag = etag
                })
            end
        end
    end
    
    return items
end

-- Get the correct API based on server type
function Sync:getApi(server)
    if server.type == "dropbox" then
        return DropboxApi
    else
        return WebDavApi
    end
end

-- Helper to join paths ensuring single slash
function Sync:joinPath(p1, p2)
    local path = p1
    if path:sub(-1) ~= "/" then
        path = path .. "/"
    end
    if p2:sub(1, 1) == "/" then
        path = path .. p2:sub(2)
    else
        path = path .. p2
    end
    return path
end


-- Helper to ensure remote folder exists (WebDAV only for now)
function Sync:ensureRemoteFolder(api, server, folder_path)
    if server.type == "dropbox" then
        -- Dropbox creates folders implicitly on upload generally, or we might need CreateFolder
        -- For simplicity, skipping explicit create for Dropbox in this pass
        return true
    end

    -- WebDAV: Check if exists, if not create
    -- Using getJoinedPath since folder_path is relative
    
    local full_url = api:getJoinedPath(server.address, folder_path)
    local code = api:createFolder(full_url, server.username, server.password)
    -- 201 Created, 405 Method Not Allowed (likely already exists), 301/302 Redirection
    if code == 201 or code == 405 then
        return true
    end
    logger.warn("Sync: Failed to create remote folder", folder_path, "Code:", code)
    return false
end

-- Get local paths
function Sync:getLocalPaths(cache_manager, book_path)
    local cache_file = cache_manager:getCachePath(book_path)
    local analysis_dir = cache_manager:getAnalysisCacheDir(book_path)

    -- Actually, let's trust CacheManager's paths.
    -- We need to know the folder name to use on the server.
    -- Typically it's 'bookfile.sdr'.
    local filename = book_path:match("([^/]+)$")
    local remote_subdir = filename .. ".sdr"

    return {
        cache_file = cache_file,
        analysis_dir = analysis_dir,
        remote_subdir = remote_subdir
    }
end
-- Helper to find remote item by name
function Sync:findRemoteItem(items, name)
    if not items then return nil end
    for _, item in ipairs(items) do
        local display_name = item.display_name
        if not display_name and item.url then
             display_name = ffiutil.basename(item.url)
        end
        if display_name == name then
            return item
        end
    end
    return nil
end

function Sync:upload(cache_manager, server, book_path, callback)
    if not server or not book_path then return end
    
    local api = self:getApi(server)
    local paths = self:getLocalPaths(cache_manager, book_path)
    
    -- 1. Base Structure
    -- Server Root / remote_subdir /
    local remote_base = server.url -- User selected folder
    local remote_book_dir = self:joinPath(remote_base, paths.remote_subdir)
    local remote_analysis_dir = self:joinPath(remote_book_dir, "xray_analysis")
    
    -- Ensure remote dirs exist
    self:ensureRemoteFolder(api, server, remote_book_dir)
    self:ensureRemoteFolder(api, server, remote_analysis_dir)
    
    local success_count = 0
    local fail_count = 0
    local errors = {}
    
    -- Helper for uploading single file
    local function upload_file(local_path, remote_dir)
        if not lfs.attributes(local_path) then return end
        
        local filename = ffiutil.basename(local_path)
        local remote_file_path = self:joinPath(remote_dir, filename)
        
        local full_url = remote_file_path -- Default
        if server.type == "webdav" then
             full_url = api:getJoinedPath(server.address, remote_file_path)
        end
        
        local code
        if server.type == "webdav" then
            code = api:uploadFile(full_url, server.username, server.password, local_path)
        else
             code = 400 -- Not implemented
        end
        
        if type(code) == "number" and code >= 200 and code < 300 then
            success_count = success_count + 1
            logger.info("Sync: Uploaded", filename)
        else
            fail_count = fail_count + 1
            logger.warn("Sync: Failed to upload", filename, code)
            table.insert(errors, filename .. " (" .. tostring(code) .. ") -> " .. full_url)
        end
    end
    
    -- 2. Upload xray_cache.lua (Check parent dir items?)
    -- Ideally we list parent dir too, but for now let's just force upload cache.lua as it changes often
    -- Or implement listing for it. Let's force it for safety as it's small.
    if lfs.attributes(paths.cache_file) then
        upload_file(paths.cache_file, remote_book_dir)
    end
    
    
    -- 3. Upload Analysis Files
    if lfs.attributes(paths.analysis_dir) and lfs.attributes(paths.analysis_dir, "mode") == "directory" then
        -- Now upload local analysis files
        for file in lfs.dir(paths.analysis_dir) do
            -- Handle both legacy X%.json files and new xray_data.json
            if file:match("^%d+%%%.json$") or file == "xray_data.json" then
                local local_file = paths.analysis_dir .. "/" .. file
                upload_file(local_file, remote_analysis_dir)
            end
        end
    end
    
    if callback then callback(success_count, fail_count, errors) end
end

-- List all .sdr subdirectories in the server's base folder (WebDAV only)
function Sync:listRemoteSdrFolders(api, server, base_path)
    local http = require("socket.http")
    local ltn12 = require("ltn12")
    local socket = require("socket")
    local socketutil = require("socketutil")

    local list_url = api:getJoinedPath(server.address, base_path)
    if list_url:sub(-1) ~= "/" then list_url = list_url .. "/" end

    local sink = {}
    local data = [[<?xml version="1.0"?><a:propfind xmlns:a="DAV:"><a:prop><a:resourcetype/></a:prop></a:propfind>]]

    socketutil:set_timeout()
    local request = {
        url      = list_url,
        method   = "PROPFIND",
        headers  = {
            ["Content-Type"]   = "application/xml",
            ["Depth"]          = "1",
            ["Content-Length"] = #data,
        },
        user     = server.username,
        password = server.password,
        source   = ltn12.source.string(data),
        sink     = ltn12.sink.table(sink),
    }
    local code, _, status = socket.skip(1, http.request(request))
    socketutil:reset_timeout()

    if not code or code < 200 or code > 299 then
        logger.warn("Sync: listRemoteSdrFolders failed:", status or code)
        return nil
    end

    local res_data = table.concat(sink)
    local folders = {}

    for item in res_data:gmatch("<[^:]*:response[^>]*>(.-)</[^:]*:response>") do
        local item_href = item:match("<[^:]*:href[^>]*>(.*)</[^:]*:href>")
        local is_collection = item:find("<[^:]*:collection[^<]*/>")

        if item_href and is_collection then
            local decoded = util.urlDecode(item_href):gsub("/$", "")
            local name = ffiutil.basename(decoded)
            -- Only include .sdr directories, skip the base folder itself
            if name:match("%.sdr$") and name ~= "" then
                table.insert(folders, name)
            end
        end
    end

    return folders
end

-- Compute LCS length between two strings (byte-level, capped for performance)
function Sync:lcsLength(s1, s2)
    local m = math.min(#s1, 80)
    local n = math.min(#s2, 80)
    s1, s2 = s1:sub(1, m), s2:sub(1, n)

    local prev = {}
    for j = 0, n do prev[j] = 0 end
    for i = 1, m do
        local curr = { [0] = 0 }
        for j = 1, n do
            if s1:sub(i, i) == s2:sub(j, j) then
                curr[j] = prev[j - 1] + 1
            else
                curr[j] = math.max(prev[j], curr[j - 1])
            end
        end
        prev = curr
    end
    return prev[n]
end

-- Score how well a remote .sdr folder name matches the local book filename (0–100)
function Sync:scoreMatch(remote_sdr_name, local_filename)
    local r = remote_sdr_name:gsub("%.sdr$", ""):lower()
    local l = (local_filename:match("^(.+)%.[^.]+$") or local_filename):lower()
    if r == l then return 100 end
    local lcs = self:lcsLength(r, l)
    return math.floor(lcs / math.max(#r, #l) * 100)
end

function Sync:download(cache_manager, server, book_path, callback, force, progress_callback, remote_subdir_override)
    if not server or not book_path then return end

    local api = self:getApi(server)
    local paths = self:getLocalPaths(cache_manager, book_path)
    local remote_base = server.url
    local remote_book_dir = self:joinPath(remote_base, remote_subdir_override or paths.remote_subdir)
    local remote_analysis_dir = self:joinPath(remote_book_dir, "xray_analysis")
    
    local success_count = 0
    local fail_count = 0
    local skipped_count = 0
    local errors = {}

    -- Ensure local directories exist
    cache_manager:ensureDirectory(paths.cache_file) -- Creates book.sdr if needed

    -- Helper for downloading single file
    local function download_file(remote_item, local_dest)
        local filename = remote_item.display_name or ffiutil.basename(remote_item.url)
        -- remote_item.url is now RELATIVE path from listFolderAll
        
        local full_url = remote_item.url
        if server.type == "webdav" then
             full_url = api:getJoinedPath(server.address, remote_item.url)
        end
        
        local code, _ = api:downloadFile(full_url, server.username, server.password, local_dest)
        
        if type(code) == "number" and code == 200 then
            success_count = success_count + 1
            logger.info("Sync: Downloaded", filename)
            return true
        else
            fail_count = fail_count + 1
            logger.warn("Sync: Failed to download", filename, code)
            table.insert(errors, filename .. " (" .. tostring(code) .. ") <- " .. full_url)
            return false
        end
    end
    
    -- 2. Download Analysis Files
    local items = nil
    if server.type == "webdav" then
        -- Pass relative path 'remote_analysis_dir'
        items = self:listFolderAll(api, server, remote_analysis_dir)
    end
    
    -- Ensure local analysis dir exists
    lfs.mkdir(paths.analysis_dir)
    
    if items then
        -- Check if we have any local analysis files to avoid mixing
        local has_local_files = false
        if lfs.attributes(paths.analysis_dir) then
             for file in lfs.dir(paths.analysis_dir) do
                 -- Handle both legacy X%.json files and new xray_data.json
                 if file:match("^%d+%%%.json$") or file == "xray_data.json" then
                     has_local_files = true
                     break
                 end
             end
        end

        if force then
            if lfs.attributes(paths.analysis_dir) then
                logger.info("Sync: Force download requested. Deleting local analysis files.")
                for file in lfs.dir(paths.analysis_dir) do
                    -- Handle both legacy X%.json files and new xray_data.json
                    if file:match("^%d+%%%.json$") or file == "xray_data.json" then
                        os.remove(paths.analysis_dir .. "/" .. file)
                    end
                end
                has_local_files = false
            end
        end

        if has_local_files then
             logger.info("Sync: Local analysis files found. Skipping download of " .. #items .. " items from cloud to prevent mixing.")
               skipped_count = #items
        else
            logger.info("Sync: Processing", #items, "items from remote analysis folder")
            for i, item in ipairs(items) do
                local filename = item.display_name
                if not filename and item.url then
                    filename = ffiutil.basename(item.url)
                end
                
                if progress_callback then
                    progress_callback(i, #items, filename)
                end
                
                -- Skip directories
                if item.type == "folder" or item.type == "directory" then
                    goto continue
                end
    
                -- Handle both legacy X%.json files and new xray_data.json
                if filename and (filename:match("^%d+%%%.json$") or filename == "xray_data.json") then
                     local local_dest = paths.analysis_dir .. "/" .. filename
                     download_file(item, local_dest)
                end
                
                ::continue::
            end
        end
    end
    
    
    if callback then callback(success_count, fail_count, errors, skipped_count) end
end

return Sync
