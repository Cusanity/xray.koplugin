-- Vendored copy of KOReader's WebDAV API.
--
-- KOReader removed the standalone frontend/apps/cloudstorage/webdavapi module
-- (folded into plugins/cloudstorage.koplugin/providers/webdav.lua, which is not
-- requirable from other plugins). This file is a self-contained copy of the
-- subset of that API xray.koplugin depends on directly for custom PROPFIND-based
-- listing, so it keeps working across KOReader versions.

local http = require("socket.http")
local ltn12 = require("ltn12")
local socket = require("socket")
local socketutil = require("socketutil")
local util = require("util")
local logger = require("logger")
local lfs = require("libs/libkoreader-lfs")

local WebDavApi = {}

-- Trim leading & trailing slashes from string `s` (based on util.trim)
function WebDavApi:trim_slashes(s)
    local from = s:match"^/*()"
    return from > #s and "" or s:match(".*[^/]", from)
end

-- Trim trailing slashes from string `s` (based on util.rtrim)
function WebDavApi:rtrim_slashes(s)
    local n = #s
    while n > 0 and s:find("^/", n) do
        n = n - 1
    end
    return s:sub(1, n)
end

-- Append path to address with a slash separator, trimming any unwanted slashes in the process.
function WebDavApi:getJoinedPath(address, path)
    local path_encoded = util.urlEncode(path, "/") or ""
    local sane_path = self.trim_slashes(self, path_encoded)
    local sane_address = self.rtrim_slashes(self, address)
    return sane_address .. "/" .. sane_path
end

function WebDavApi:downloadFile(file_url, user, pass, local_path, progress_callback)
    socketutil:set_timeout(socketutil.FILE_BLOCK_TIMEOUT, socketutil.FILE_TOTAL_TIMEOUT)
    logger.dbg("WebDavApi: downloading file: ", file_url)
    local handle = ltn12.sink.file(io.open(local_path, "w"))
    if progress_callback then
        handle = socketutil.chainSinkWithProgressCallback(handle, progress_callback)
    end
    local code, headers, status = socket.skip(1, http.request {
        url      = file_url,
        method   = "GET",
        sink     = handle,
        user     = user,
        password = pass,
    })
    socketutil:reset_timeout()
    if code ~= 200 then
        logger.warn("WebDavApi: cannot download file:", status or code)
        logger.dbg("WebDavApi: Response headers:", headers)
    end
    return code, headers and headers.etag
end

function WebDavApi:uploadFile(file_url, user, pass, local_path, etag)
    socketutil:set_timeout(socketutil.FILE_BLOCK_TIMEOUT, socketutil.FILE_TOTAL_TIMEOUT)
    -- If-Match uses strong comparison (RFC 7232 §3.1), so a weak validator
    -- (W/"…", returned e.g. for gzip-compressed responses) can never match and
    -- would 412 forever. Strip the weak prefix; proxies keep the same value.
    if type(etag) == "string" then
        etag = etag:gsub("^%s*[Ww]/", "")
    end
    local code, _, status = socket.skip(1, http.request{
        url      = file_url,
        method   = "PUT",
        source   = ltn12.source.file(io.open(local_path, "r")),
        user     = user,
        password = pass,
        headers  = {
            ["Content-Length"] = lfs.attributes(local_path, "size"),
            ["If-Match"] = etag,
        },
    })
    socketutil:reset_timeout()
    if type(code) == "number" and code >= 200 and code <= 299 then
        code = 200
    else
        logger.warn("WebDavApi: cannot upload file:", status or code)
    end
    return code
end

function WebDavApi:deleteFile(file_url, user, pass)
    socketutil:set_timeout(socketutil.FILE_BLOCK_TIMEOUT, socketutil.FILE_TOTAL_TIMEOUT)
    local code, _, status = socket.skip(1, http.request{
        url      = file_url,
        method   = "DELETE",
        user     = user,
        password = pass,
    })
    socketutil:reset_timeout()
    if type(code) == "number" and code >= 200 and code <= 299 then
        return true
    end
    logger.warn("WebDavApi: cannot delete file:", status or code)
end

function WebDavApi:createFolder(folder_url, user, pass)
    socketutil:set_timeout(socketutil.FILE_BLOCK_TIMEOUT, socketutil.FILE_TOTAL_TIMEOUT)
    local code, _, status = socket.skip(1, http.request{
        url      = folder_url,
        method   = "MKCOL",
        user     = user,
        password = pass,
    })
    socketutil:reset_timeout()
    if type(code) == "number" and code >= 200 and code <= 299 then
        return true
    end
    logger.warn("WebDavApi: cannot create folder:", status or code)
end

return WebDavApi
