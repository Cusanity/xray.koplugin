-- Minimal stand-in for KOReader's removed frontend/apps/cloudstorage/dropboxapi module.
--
-- xray.koplugin's sync.lua only ever selects this API for server.type == "dropbox",
-- but its upload/download paths are currently only implemented for WebDAV (see
-- sync.lua's "Not implemented" branches), so these methods are never actually
-- invoked. This stub exists so require() succeeds and Sync:getApi() keeps working.
-- (Note: even old KOReader's real dropboxapi.lua used a different, token-based
-- API with no getJoinedPath method, so there's no compatible upstream to prefer here.)

local logger = require("logger")

local DropboxApi = {}

function DropboxApi:getJoinedPath(address, path)
    local sane_address = address:gsub("/+$", "")
    local sane_path = path:gsub("^/+", ""):gsub("/+$", "")
    return sane_address .. "/" .. sane_path
end

function DropboxApi:downloadFile(file_url, user, pass, local_path, progress_callback) -- luacheck: no unused
    logger.warn("DropboxApi: Dropbox sync is not implemented, cannot download", file_url)
    return 400
end

function DropboxApi:uploadFile(file_url, user, pass, local_path, etag) -- luacheck: no unused
    logger.warn("DropboxApi: Dropbox sync is not implemented, cannot upload", file_url)
    return 400
end

function DropboxApi:createFolder(folder_url, user, pass) -- luacheck: no unused
    logger.warn("DropboxApi: Dropbox sync is not implemented, cannot create folder", folder_url)
    return false
end

return DropboxApi
