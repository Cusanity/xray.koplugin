-- X-Ray PC Receiver
--
-- Tiny LuaSocket HTTP server that accepts a POSTed xray_data.json from
-- the PC-side generator.py and writes it to the correct .sdr location.
--
-- Python usage:
--   XRAY_DEVICE=192.168.1.100 python generator.py book.epub
--   XRAY_DEVICE=192.168.1.100:8763 python generator.py book.epub  (custom port)

local socket      = require("socket")
local lfs         = require("libs/libkoreader-lfs")
local logger      = require("logger")
local UIManager   = require("ui/uimanager")
local InfoMessage = require("ui/widget/infomessage")
local DocSettings = require("docsettings")
local Device      = require("device")

local DEFAULT_PORT = 8763

local XRayReceiver = {
    port    = DEFAULT_PORT,
    server  = nil,
    _poll_fn = nil,
    _plugin  = nil,  -- set to XRayPlugin instance on start()
}

function XRayReceiver:new(o)
    o = o or {}
    setmetatable(o, self)
    self.__index = self
    return o
end

-- Start listening. Returns true, port on success; false, errmsg on failure.
function XRayReceiver:start(plugin, port)
    self._plugin = plugin
    self.port    = port or DEFAULT_PORT

    if self.server then
        return true, self.port  -- already running
    end

    -- On Kindle, open the firewall port (same pattern as httpinspector.koplugin)
    if Device:isKindle() then
        os.execute(string.format(
            "iptables -A INPUT  -p tcp --dport %d -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT",
            self.port))
        os.execute(string.format(
            "iptables -A OUTPUT -p tcp --sport %d -m conntrack --ctstate ESTABLISHED       -j ACCEPT",
            self.port))
    end

    local srv, err = socket.bind("*", self.port)
    if not srv then
        logger.warn("XRayReceiver: bind failed:", err)
        return false, err
    end
    srv:settimeout(0)  -- non-blocking accept
    self.server = srv
    logger.info("XRayReceiver: listening on port", self.port)

    -- Poll every 50 ms via UIManager scheduler (non-blocking, UI stays responsive)
    self._poll_fn = function()
        if not self.server then return end
        local client = self.server:accept()
        if client then
            self:_handleClient(client)
        end
        UIManager:scheduleIn(0.05, self._poll_fn)
    end
    UIManager:scheduleIn(0.05, self._poll_fn)

    return true, self.port
end

function XRayReceiver:stop()
    if self._poll_fn then
        UIManager:unschedule(self._poll_fn)
        self._poll_fn = nil
    end
    if self.server then
        if Device:isKindle() then
            os.execute(string.format(
                "iptables -D INPUT  -p tcp --dport %d -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT",
                self.port))
            os.execute(string.format(
                "iptables -D OUTPUT -p tcp --sport %d -m conntrack --ctstate ESTABLISHED       -j ACCEPT",
                self.port))
        end
        pcall(function() self.server:close() end)
        self.server = nil
    end
    logger.info("XRayReceiver: stopped")
end

function XRayReceiver:isRunning()
    return self.server ~= nil
end

-- Translation helper: delegates to the plugin's localization system.
-- Falls back to returning the key if the plugin is not yet attached.
function XRayReceiver:_t(key, ...)
    if self._plugin and self._plugin.loc then
        return self._plugin.loc:t(key, ...)
    end
    return key
end

-- Best-effort: read device IP for display to the user.
function XRayReceiver:getDeviceIP()
    -- UDP socket trick: connect to an external address (no data sent) and read
    -- getsockname() to discover which local IP the OS would route through.
    local ok, ip = pcall(function()
        local s = socket.udp()
        s:setpeername("8.8.8.8", 53)
        local addr = s:getsockname()
        s:close()
        return addr
    end)
    if ok and ip and ip ~= "" and ip ~= "0.0.0.0" then
        return ip
    end
    -- Fallback: NetworkMgr:getIP() if available
    local ok2, nm = pcall(require, "ui/network/manager")
    if ok2 and nm and nm.getIP then
        local addr = nm:getIP()
        if addr and addr ~= "" then return addr end
    end
    return nil
end

-- Handle one accepted client connection.
function XRayReceiver:_handleClient(client)
    client:settimeout(10, "t")

    -- Read HTTP request headers line by line until blank line
    local content_length = 0
    while true do
        local line, err = client:receive("*l")
        if not line or err then
            client:close()
            return
        end
        if line == "" then break end  -- end of headers
        local cl = line:lower():match("^content%-length:%s*(%d+)")
        if cl then content_length = tonumber(cl) or 0 end
    end

    if content_length <= 0 then
        client:send("HTTP/1.0 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
        client:close()
        return
    end

    -- Read body (the raw JSON)
    local body, err = client:receive(content_length)
    if not body or err then
        client:send("HTTP/1.0 500 Read Error\r\nContent-Length: 0\r\n\r\n")
        client:close()
        return
    end

    -- Write to the correct sdr path and respond
    local ok, msg = self:_writeResult(body)
    if ok then
        client:send("HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nOK")
        client:close()
        logger.info("XRayReceiver: wrote xray_data.json →", msg)
        UIManager:show(InfoMessage:new{
            text    = self:_t("receiver_data_received"),
            timeout = 3,
        })
        UIManager:scheduleIn(1, function()
            if self._plugin then
                self._plugin:autoLoadCache()
                UIManager:show(InfoMessage:new{
                    text    = self:_t("receiver_data_loaded"),
                    timeout = 2,
                })
            end
        end)
    else
        local resp_body = "Error: " .. msg
        client:send(string.format(
            "HTTP/1.0 500 Error\r\nContent-Length: %d\r\n\r\n%s",
            #resp_body, resp_body))
        client:close()
        logger.warn("XRayReceiver: write failed:", msg)
        UIManager:show(InfoMessage:new{
            text    = self:_t("receiver_write_failed", msg),
            timeout = 4,
        })
    end
end

-- Write json_body to the current book's .sdr/xray_analysis/xray_data.json
function XRayReceiver:_writeResult(json_body)
    if not self._plugin then
        return false, "no plugin reference"
    end
    local book_path = self._plugin:getBookPath()
    if not book_path then
        return false, "no book is currently open"
    end

    -- KOReader's own sidecar directory: always resolves to the correct .sdr folder
    local sdr_dir      = DocSettings:getSidecarDir(book_path)
    local analysis_dir = sdr_dir .. "/xray_analysis"

    if not lfs.attributes(analysis_dir, "mode") then
        local ok, err = lfs.mkdir(analysis_dir)
        if not ok then
            return false, "mkdir failed: " .. (err or "unknown")
        end
    end

    local out_path = analysis_dir .. "/xray_data.json"
    local f, err = io.open(out_path, "w")
    if not f then
        return false, "open failed: " .. (err or "unknown")
    end
    f:write(json_body)
    f:close()

    return true, out_path
end

return XRayReceiver
