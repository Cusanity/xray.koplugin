-- X-Ray Plugin for KOReader v2.0.0

local UIManager = require("ui/uimanager")
local InfoMessage = require("ui/widget/infomessage")
local Menu = require("ui/widget/menu")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local logger = require("logger")
local _ = require("gettext")
local Device = require("device")
local Screen = Device.screen
local PluginShare = require("pluginshare")
local SyncService = require("frontend/apps/cloudstorage/syncservice")
local Sync = require("sync")
local Dispatcher = require("dispatcher")
local Event = require("ui/event")


local XRayPlugin = WidgetContainer:new{
    name = "xray",
    is_doc_only = true,
}

function XRayPlugin:preventSleep(enable)
    if Device:isKindle() then
        if enable then
            os.execute("lipc-set-prop com.lab126.powerd preventScreenSaver 1")
        else
            os.execute("lipc-set-prop com.lab126.powerd preventScreenSaver 0")
        end
    elseif Device:isCervantes() or Device:isKobo() then
        PluginShare.pause_auto_suspend = enable
    end
end

function XRayPlugin:showNativeDetails(title, main_text, metadata_items, extra_buttons)
    local FrameContainer = require("ui/widget/container/framecontainer")
    local ScrollTextWidget = require("ui/widget/scrolltextwidget")
    local VerticalGroup = require("ui/widget/verticalgroup")
    local WidgetContainer = require("ui/widget/container/widgetcontainer")
    local MovableContainer = require("ui/widget/container/movablecontainer")
    local TitleBar = require("ui/widget/titlebar")
    local ButtonTable = require("ui/widget/buttontable")
    local VerticalSpan = require("ui/widget/verticalspan")
    local CenterContainer = require("ui/widget/container/centercontainer")
    local Geom = require("ui/geometry")
    local Screen = require("device").screen
    local Size = require("ui/size")
    local UIManager = require("ui/uimanager")
    local Blitbuffer = require("ffi/blitbuffer")
    local Font = require("ui/font")

    -- 1. Create Title Bar
    local title_bar = TitleBar:new{
        width = Screen:getWidth() * 0.85,
        title = title,
        with_bottom_line = true,
        close_callback = function()
            if self.native_dialog then
                UIManager:close(self.native_dialog)
                self.native_dialog = nil
                UIManager:setDirty(nil, "full")
            end
        end,
        show_parent = self,
    }

    local inner_width = title_bar.width
    local content_padding = Size.padding.large

    -- 2. Create Scroll Text Widget for Description
    -- We use a fixed percentage of screen height for the scroll area
    local scroll_height = math.floor(Screen:getHeight() * 0.4)
    local scroll_widget = ScrollTextWidget:new{
        text = main_text or "",
        width = inner_width - 2 * content_padding,
        height = scroll_height,
        face = Font:getFace("cfont", 20),
        alignment = "left",
        justified = false,
    }

    -- 3. Metadata Section (Dynamic)
    local metadata_widgets = {}
    if metadata_items and #metadata_items > 0 then
        -- Add a bit of space before metadata
        table.insert(metadata_widgets, VerticalSpan:new{ width = Size.padding.default })
        for _, item in ipairs(metadata_items) do
            table.insert(metadata_widgets, require("ui/widget/textwidget"):new{
                text = item,
                face = Font:getFace("xx_smallinfofont", 18),
                width = inner_width - 2 * content_padding,
                padding_left = content_padding,
                padding_right = content_padding,
            })
        end
    end

    -- 4. Actions Section (ButtonTable)
    local buttons = {}
    
    -- Extra buttons row(s)
    if extra_buttons and #extra_buttons > 0 then
        local row = {}
        for _, btn_def in ipairs(extra_buttons) do
            table.insert(row, {
                text = btn_def.text,
                callback = function()
                    if self.native_dialog then
                        UIManager:close(self.native_dialog)
                        self.native_dialog = nil
                    end
                    if btn_def.callback then
                        btn_def.callback()
                    end
                end,
            })
        end
        table.insert(buttons, row)
    end

    -- Close button row
    table.insert(buttons, {
        {
            text = self.loc:t("close") or "Close",
            callback = function()
                if self.native_dialog then
                    UIManager:close(self.native_dialog)
                    self.native_dialog = nil
                    UIManager:setDirty(nil, "full")
                end
            end,
        }
    })

    local button_table = ButtonTable:new{
        width = inner_width,
        buttons = buttons,
        zero_sep = true,
        show_parent = self,
    }

    -- 5. Layout Container (Frame)
    local frame = FrameContainer:new{
        padding = 0,
        margin = 0,
        background = Blitbuffer.COLOR_WHITE,
        radius = Size.radius.window,
        bordersize = Size.border.window,
        VerticalGroup:new{
            align = "center",
            title_bar,
            VerticalSpan:new{ width = Size.padding.medium },
            CenterContainer:new{
                dimen = Geom:new{ w = inner_width, h = scroll_widget:getSize().h },
                scroll_widget
            },
            VerticalGroup:new{
                align = "left",
                table.unpack(metadata_widgets)
            },
            VerticalSpan:new{ width = Size.padding.medium },
            button_table,
        }
    }

    -- 6. Movable and Widget Container
    self.native_dialog = WidgetContainer:new{
        align = "center",
        dimen = Screen:getSize(),
        MovableContainer:new{
            frame,
        }
    }

    -- Close on tap outside
    self.native_dialog.onTapClose = function(w, ges_ev)
         if ges_ev.pos:notIntersectWith(frame.dimen) then
             UIManager:close(self.native_dialog)
             self.native_dialog = nil
             return true
         end
    end

    -- Link scroll widget to dialog for refreshes
    scroll_widget.dialog = self.native_dialog
    
    UIManager:show(self.native_dialog)
    UIManager:setDirty(nil, "full")
end

-- Deprecated: Kept as alias if needed, but redirects to native
function XRayPlugin:showHtmlDialog(title, content)
    -- clean html tags for fallback
    local clean_text = content:gsub("<[^>]+>", "")
    self:showNativeDetails(title, clean_text)
end

function XRayPlugin:init()

    
    -- CSS for HTML viewer
    self.css = [[
        @page { margin: 0; }
        body { margin: 0; padding: 0.5em; font-family: "Noto Sans CJK SC", "Noto Sans SC", "Noto Sans", sans-serif; font-style: normal; font-weight: normal; }
        p { margin-top: 0.5em; margin-bottom: 0.5em; font-family: "Noto Sans CJK SC", "Noto Sans SC", "Noto Sans", sans-serif; font-style: normal; font-weight: normal; }
        h1, h2, h3 { margin-top: 0.5em; margin-bottom: 0.3em; font-family: "Noto Sans CJK SC", "Noto Sans SC", "Noto Sans", sans-serif; font-weight: normal; font-style: normal; }
        h1 { font-size: 1.4em; }
        h2 { font-size: 1.25em; }
        h3 { font-size: 1.1em; }
        b, strong { font-family: "Noto Sans CJK SC", "Noto Sans SC", "Noto Sans", sans-serif; font-weight: normal; font-style: normal; }
        em, i, cite, var, address, dfn { font-family: "Noto Sans CJK SC", "Noto Sans SC", "Noto Sans", sans-serif; font-style: normal; font-weight: normal; }
    ]]
    self.ui.menu:registerToMainMenu(self)
    
    -- Load localization module
    local Localization = require("localization_xray")
    self.loc = Localization
    self.loc:init() -- Load saved language preference
    
    self:onDispatcherRegisterActions()
    
    logger.info("XRayPlugin v1.0.0: Initialized with language:", self.loc:getLanguage())
end

function XRayPlugin:onReaderReady()
    -- Initialize simple sync
    self.sync = Sync:new()
    
    -- Register to Highlight Menu (using addToHighlightDialog)
    -- This is the correct way for plugins to add items to text selection menu
    local ReaderHighlight = require("apps/reader/modules/readerhighlight")
    if ReaderHighlight.addToHighlightDialog then
        ReaderHighlight:addToHighlightDialog(function(highlight_instance)
            return self:show_in_highlight_dialog_func(highlight_instance)
        end)
        logger.info("XRayPlugin: Registered to highlight dialog")
    else
        logger.warn("XRayPlugin: ReaderHighlight.addToHighlightDialog not found")
    end
end

-- Called when page changes
function XRayPlugin:onPageUpdate(pageno)
    -- Auto-load partial cache if available for this new position
    self:syncCacheFromPartials()
end


function XRayPlugin:onDispatcherRegisterActions()
    
    local Dispatcher = require("dispatcher")
    
    -- X-Ray Quick Menu action
    Dispatcher:registerAction("xray_quick_menu", {
        category = "none",
        event = "ShowXRayQuickMenu",
        title = self.loc:t("quick_menu_title") or "X-Ray Quick Menu",
        general = true,
        separator = true,
    })
    
    -- X-Ray Characters action
    Dispatcher:registerAction("xray_characters", {
        category = "none",
        event = "ShowXRayCharacters",
        title = self.loc:t("menu_characters") or "Characters",
        general = true,
    })
    
    -- X-Ray Chapter Characters action
    Dispatcher:registerAction("xray_chapter_characters", {
        category = "none",
        event = "ShowXRayChapterCharacters",
        title = self.loc:t("menu_chapter_characters") or "Chapter Characters",
        general = true,
    })
    
    -- X-Ray Timeline action
    Dispatcher:registerAction("xray_timeline", {
        category = "none",
        event = "ShowXRayTimeline",
        title = self.loc:t("menu_timeline") or "Timeline",
        general = true,
    })
    
    -- X-Ray Historical Figures action
    Dispatcher:registerAction("xray_historical", {
        category = "none",
        event = "ShowXRayHistorical",
        title = self.loc:t("menu_historical_figures") or "Historical Figures",
        general = true,
    })

    -- X-Ray Themes action
    Dispatcher:registerAction("xray_themes", {
        category = "none",
        event = "ShowXRayThemes",
        title = self.loc:t("menu_themes") or "Themes",
        general = true,
    })    
    
    -- X-Ray Locations action
    Dispatcher:registerAction("xray_locations", {
        category = "none",
        event = "ShowXRayLocations",
        title = self.loc:t("menu_locations") or "Locations",
        general = true,
    }) 
end

-- Event handlers for Dispatcher actions
function XRayPlugin:onShowXRayQuickMenu()
    self:showQuickXRayMenu()
    return true
end

function XRayPlugin:onShowXRayFullMenu()
    self:showFullXRayMenu()
    return true
end

-- Register X-Ray button in the highlight selection menu
function XRayPlugin:onReaderReady()
    logger.info("XRayPlugin: onReaderReady - registering X-Ray button in highlight menu")
    
    -- Add X-Ray button to the highlight dialog
    self.ui.highlight:addToHighlightDialog("08_xray", function(highlight_instance)
        return {
            text = "X-Ray",
            show_in_highlight_dialog_func = function()
                self:autoLoadCache()
                -- Only show if X-Ray mode is enabled and we have a match
                if not self.xray_mode_enabled then
                    return false
                end
                
                if not highlight_instance.selected_text or not highlight_instance.selected_text.text then
                    return false
                end

                local matched_entity, entity_type = self:matchXRayEntity(highlight_instance.selected_text.text)
                if matched_entity then
                    -- Cache the match so we don't have to search again in callback
                    self._cached_xray_match = {entity = matched_entity, type = entity_type}
                    return true
                end
                
                return false
            end,
            callback = function()
                logger.info("XRayPlugin: X-Ray button clicked")
                if self._cached_xray_match then
                    highlight_instance:onClose()
                    self:showXRayPopup(self._cached_xray_match.entity, self._cached_xray_match.type)
                    self._cached_xray_match = nil
                end
            end,
        }
    end)
    
    logger.info("XRayPlugin: X-Ray button registered successfully")
end

function XRayPlugin:onShowXRayCharacters()
    self:showCharacters()
    return true
end

function XRayPlugin:onShowXRayChapterCharacters()
    self:showChapterCharacters()
    return true
end

function XRayPlugin:onShowXRayTimeline()
    self:showTimeline()
    return true
end

function XRayPlugin:onShowXRayHistorical()
    self:showHistoricalFigures()
    return true
end

function XRayPlugin:onShowXRayThemes()
    self:showThemes()
    return true
end

function XRayPlugin:onShowXRayLocations()
    self:showLocations()
    return true
end

function XRayPlugin:autoLoadCache()
    if not self.cache_manager then
        local CacheManager = require("cachemanager")
        self.cache_manager = CacheManager:new()
    end
    
    
    local book_path = self:getBookPath()
    if not book_path then return end
    
    logger.info("XRayPlugin: Auto-loading cache for:", book_path)
    local cached_data = self.cache_manager:loadCache(book_path)
    
    if cached_data then
        self.xray_data = cached_data
        self.book_data = cached_data
        self.characters = cached_data.characters or {}
        self.locations = cached_data.locations or {}
        self.themes = cached_data.themes or {}
        self.summary = cached_data.summary
        self.timeline = cached_data.timeline or {}
        self.historical_figures = cached_data.historical_figures or {}
        if cached_data.author_info then
            self.author_info = cached_data.author_info
        else
            -- Eğer yapı düz ise (author_bio varsa)
            self.author_info = {
                name = cached_data.author,
                description = cached_data.author_bio,
                birthDate = cached_data.author_birth,
                deathDate = cached_data.author_death
            }
        end
        local cache_age = math.floor((os.time() - cached_data.cached_at) / 86400)
        
        logger.info("XRayPlugin: Auto-loaded from cache -", #self.characters, "characters,", 
                    cache_age, "days old")
        
        if #self.characters > 0 then
            self.xray_mode_enabled = true
            logger.info("XRayPlugin: X-Ray mode auto-enabled")
        end
    else
        logger.info("XRayPlugin: No cache found for auto-load")
        return nil
    end
end

-- Toggle X-Ray mode (character name tap detection)
function XRayPlugin:toggleXRayMode()
    self.xray_mode_enabled = not self.xray_mode_enabled
    
    local UIManager = require("ui/uimanager")
    local InfoMessage = require("ui/widget/infomessage")
    
    if self.xray_mode_enabled then
        logger.info("XRayPlugin: X-Ray mode enabled")
        UIManager:show(InfoMessage:new{
            text = self.loc:t("xray_mode_enabled"),
            timeout = 2
        })
    else
        logger.info("XRayPlugin: X-Ray mode disabled")
        UIManager:show(InfoMessage:new{
            text = self.loc:t("xray_mode_disabled"),
            timeout = 2
        })
    end
    
    -- Force menu refresh to update toggle display
    self.ui.menu:refreshMenuTitleBar()
end

-- Handle long-press/text selection for X-Ray entity detection
function XRayPlugin:onHoldWord(word, word_box, ges_pos)
    logger.info("=== XRayPlugin: onHoldWord CALLED ===")
    logger.info("XRayPlugin: word=", word)
    logger.info("XRayPlugin: xray_mode_enabled=", self.xray_mode_enabled)
    logger.info("XRayPlugin: characters count=", self.characters and #self.characters or "nil")
    logger.info("XRayPlugin: locations count=", self.locations and #self.locations or "nil")
    logger.info("XRayPlugin: themes count=", self.themes and #self.themes or "nil")
    
    -- Only process if X-Ray mode is enabled and we have data
    if not self.xray_mode_enabled then
        logger.info("XRayPlugin: onHoldWord - mode DISABLED, propagating")
        return false  -- Propagate to other handlers (dictionary, highlight, etc.)
    end
    
    if not word or word == "" then
        logger.info("XRayPlugin: onHoldWord - NO WORD, propagating")
        return false
    end
    
    logger.info("XRayPlugin: Checking word against X-Ray entities:", word)
    
    -- Try to match against all X-Ray entities
    local matched_entity, entity_type = self:matchXRayEntity(word)
    if matched_entity then
        logger.info("XRayPlugin: MATCHED entity:", matched_entity.name or matched_entity.term, "type:", entity_type)
        self:showXRayPopup(matched_entity, entity_type)
        return true  -- Consume the event (don't show dictionary)
    end
    
    logger.info("XRayPlugin: ✗ NO X-RAY MATCH for word:", word, "- propagating to other handlers")
    return false  -- No match, allow dictionary/highlight to work
end

-- Match selected text against all X-Ray entities
function XRayPlugin:matchXRayEntity(selected_text)
    if not selected_text or selected_text == "" then
        logger.info("XRayPlugin: matchXRayEntity - empty text")
        return nil, nil
    end
    
    -- Normalize the selected text
    local normalized = selected_text:lower():gsub("^%s*(.-)%s*$", "%1")  -- trim
    logger.info("XRayPlugin: Normalized text:", normalized)
    
    -- Check characters
    logger.info("XRayPlugin: Checking against characters...")
    if self.characters and #self.characters > 0 then
        logger.info("XRayPlugin: Have", #self.characters, "characters to check")
        for i, character in ipairs(self.characters) do
            logger.dbg("XRayPlugin: Checking character", i, ":", character.name)
            if self:matchesEntity(normalized, character.name, character.aliases) then
                logger.info("XRayPlugin: MATCHED character:", character.name)
                return character, "character"
            end
        end
    else
        logger.info("XRayPlugin: No characters data")
    end
    
    -- Check locations
    logger.info("XRayPlugin: Checking against locations...")
    if self.locations and #self.locations > 0 then
        logger.info("XRayPlugin: Have", #self.locations, "locations to check")
        for _, location in ipairs(self.locations) do
            if self:matchesEntity(normalized, location.name) then
                logger.info("XRayPlugin: MATCHED location:", location.name)
                return location, "location"
            end
        end
    else
        logger.info("XRayPlugin: No locations data")
    end
    
    -- Check themes/terms
    logger.info("XRayPlugin: Checking against themes...")
    if self.themes and #self.themes > 0 then
        logger.info("XRayPlugin: Have", #self.themes, "themes to check")
        for _, theme in ipairs(self.themes) do
            if self:matchesEntity(normalized, theme.term or theme.name) then
                logger.info("XRayPlugin: MATCHED theme:", theme.term or theme.name)
                return theme, "theme"
            end
        end
    else
        logger.info("XRayPlugin: No themes data")
    end
    
    -- No match in loaded cache - search forward caches for first appearance
    logger.info("XRayPlugin: No match in loaded cache, searching forward caches...")
    
    if not self.cache_manager then
        local CacheManager = require("cachemanager")
        self.cache_manager = CacheManager:new()
    end
    
    local book_path = self:getBookPath()
    if book_path then
        -- Get current reading progress
        local _, _, current_progress = self:getReadingProgress()
        current_progress = current_progress or 0
        
        -- Also check the loaded xray_data progress as fallback
        if self.xray_data and self.xray_data.analysis_progress then
            current_progress = math.max(current_progress, self.xray_data.analysis_progress)
        end
        
        -- Create a wrapper function that matches the expected signature
        local matchesEntityFunc = function(norm_text, entity_name, aliases)
            return self:matchesEntity(norm_text, entity_name, aliases)
        end
        
        local entity, entity_type = self.cache_manager:searchEntityInForwardCaches(
            book_path, 
            current_progress, 
            normalized, 
            matchesEntityFunc
        )
        
        if entity then
            logger.info("XRayPlugin: Found entity in forward cache:", entity.name or entity.term or "unknown")
            return entity, entity_type
        end
    end
    
    logger.info("XRayPlugin: No match found for:", normalized)
    return nil, nil  -- No match found
end

-- Helper to check if text matches an entity name
function XRayPlugin:matchesEntity(normalized_text, entity_name, aliases)
    if not entity_name then return false end
    
    local name_lower = entity_name:lower()
    
    -- Exact match
    if name_lower == normalized_text then
        return true
    end
    
    -- Only match if selected text is a substring of entity name
    -- This prevents "保" (from "保护") matching "保拉"
    if name_lower:find(normalized_text, 1, true) then
        return true
    end
    
    -- Check aliases if provided
    if aliases then
        for _, alias in ipairs(aliases) do
            local alias_lower = alias:lower()
            if alias_lower == normalized_text or alias_lower:find(normalized_text, 1, true) then
                return true
            end
        end
    end
    
    return false
end

-- Show X-Ray entity details with consistent UI for all types
function XRayPlugin:showXRayPopup(entity, entity_type)
    logger.info("XRayPlugin: showXRayPopup - entity_type:", entity_type)
    
    -- Call the appropriate detail view based on entity type
    if entity_type == "character" then
        self:showCharacterDetails(entity)
    elseif entity_type == "location" then
        self:showLocationDetails(entity)
    elseif entity_type == "theme" then
        self:showThemeDetails(entity)
    else
        logger.warn("XRayPlugin: Unknown entity type:", entity_type)
    end
end

function XRayPlugin:getMenuCounts()
    return {
        characters = self.characters and #self.characters or 0,
        locations = self.locations and #self.locations or 0,
        themes = self.themes and #self.themes or 0,
        timeline = self.timeline and #self.timeline or 0,
        historical_figures = self.historical_figures and #self.historical_figures or 0,
    }
end

-- Safely get current book path
function XRayPlugin:getBookPath()
    if self.ui and self.ui.document and self.ui.document.file then
        return self.ui.document.file
    end
    logger.warn("XRayPlugin: Could not get book path - ui or document missing")
    return nil
end

-- Get current reading progress (works for EPUB, PDF, MOBI, etc.)
function XRayPlugin:getReadingProgress()
    -- Default values
    local current_page = 0
    local total_pages = 0
    local progress = 0
    
    if not self.ui or not self.ui.document then
        logger.warn("XRayPlugin: No document or UI available")
        return current_page, total_pages, progress
    end
    
    local doc = self.ui.document
    
    -- Get total pages
    local success_pages, pages = pcall(function() return doc:getPageCount() end)
    if success_pages and pages and pages > 0 then
        total_pages = pages
    else
        logger.warn("XRayPlugin: Could not get page count")
        return current_page, total_pages, progress
    end
    
    -- Try multiple methods to get current page
    local methods = {
        -- Method 1: Paging (for PDF, DjVu)
        function()
            if self.ui.paging and type(self.ui.paging.getCurrentPage) == "function" then
                return self.ui.paging:getCurrentPage()
            end
        end,
        -- Method 2: Rolling (for EPUB, MOBI)
        function()
            if self.ui.rolling and type(self.ui.rolling.getCurrentPage) == "function" then
                return self.ui.rolling:getCurrentPage()
            end
        end,
        -- Method 3: Document direct
        function()
            if type(doc.getCurrentPage) == "function" then
                return doc:getCurrentPage()
            end
        end,
        -- Method 4: View state
        function()
            if self.view and self.view.state and self.view.state.page then
                return self.view.state.page
            end
        end,
        -- Method 5: Document settings
        function()
            if self.ui.doc_settings then
                local settings = self.ui.doc_settings
                return settings:readSetting("last_page") or settings:readSetting("page")
            end
        end,
    }
    
    -- Try each method
    for i, method in ipairs(methods) do
        local success_method, page = pcall(method)
        if success_method and page and tonumber(page) then
            current_page = tonumber(page)
            logger.info("XRayPlugin: Got current page using method", i, ":", current_page)
            break
        end
    end
    
    -- If still no page, try one more fallback
    if current_page == 0 and self.ui.document then
        local success_fallback, fallback_page = pcall(function()
            -- Try to get from bookmark or last position
            if self.ui.bookmark and self.ui.bookmark.getCurrentPageNumber then
                return self.ui.bookmark:getCurrentPageNumber()
            end
        end)
        
        if success_fallback and fallback_page then
            current_page = tonumber(fallback_page) or 0
            logger.info("XRayPlugin: Got current page from fallback:", current_page)
        end
    end
    
    -- Calculate progress
    if total_pages > 0 and current_page > 0 then
        progress = math.floor((current_page / total_pages) * 100)
    end
    
    logger.info("XRayPlugin: Reading progress -", current_page, "/", total_pages, "=", progress .. "%")
    
    return current_page, total_pages, progress
end


function XRayPlugin:getXRaySubMenuItems()
    local counts = self:getMenuCounts()
    local function safe_t(key)
        if self.loc and self.loc.t then
            return self.loc:t(key) or key
        end
        return key
    end
    
    local percentage = 0
    if self.xray_data and self.xray_data.analysis_progress then
        percentage = self.xray_data.analysis_progress
    end
    
    local _, _, reading_progress = self:getReadingProgress()
    reading_progress = reading_progress or 0
    
    local info_text = string.format("%s: %d%%  %s: %d%%", 
        self.loc:t("menu_xray_progress"), percentage,
        self.loc:t("reading_progress"), reading_progress)
    
    return {
        {
            text = info_text,
            enabled = false, -- Info only
        },
        {
            text = self.loc:t("menu_characters") .. (counts.characters > 0 and " (" .. counts.characters .. ")" or ""),
            keep_menu_open = true,
            callback = function()
                self:showCharacters()
            end,
        },
        {
            text = self.loc:t("menu_chapter_characters"),
            keep_menu_open = true,
            callback = function()
                self:showChapterCharacters()
            end,
        },
        {
            text = self.loc:t("menu_character_notes"),
            keep_menu_open = true,
            callback = function()
                self:showCharacterNotes()
            end,
        },
        {
            text = self.loc:t("menu_timeline") .. (counts.timeline > 0 and " (" .. counts.timeline .. " " .. self.loc:t("events") .. ")" or ""),
            keep_menu_open = true,
            callback = function()
                self:showTimeline()
            end,
        },
        {
            text = self.loc:t("menu_historical_figures") .. (counts.historical_figures > 0 and " (" .. counts.historical_figures .. ")" or ""),
            keep_menu_open = true,
            callback = function()
                self:showHistoricalFigures()
            end,
        },
        {
            text = self.loc:t("menu_locations") .. (counts.locations > 0 and " (" .. counts.locations .. ")" or ""),
            keep_menu_open = true,
            callback = function()
                self:showLocations()
            end,
        },
        {
            text = self.loc:t("menu_author_info"),
            keep_menu_open = true,
            callback = function()
                self:showAuthorInfo()
            end,
        },
        {
            text = self.loc:t("menu_summary"),
            keep_menu_open = true,
            callback = function()
                self:showSummary()
            end,
        },
        {
            text = self.loc:t("menu_themes") .. (counts.themes > 0 and " (" .. counts.themes .. ")" or ""),
            keep_menu_open = true,
            callback = function()
                self:showThemes()
            end,
        },
        {
            text = self.loc:t("menu_fetch_ai"),
            keep_menu_open = true,
            callback = function()
                self:fetchFromAI()
            end,
        },
        {
            text = self.loc:t("menu_ai_settings"),
            keep_menu_open = true,
            sub_item_table = {
                {
                    text = self.loc:t("menu_gemini_key"), 
                    keep_menu_open = true,
                    callback = function()
                        self:setGeminiAPIKey()
                    end,
                },
                {
                    text = self.loc:t("menu_gemini_model"), 
                    keep_menu_open = true,
                    callback = function()
                        self:selectGeminiModel()
                    end,
                },
                {
                    text = self.loc:t("menu_chatgpt_key"), 
                    keep_menu_open = true,
                    callback = function()
                        self:setChatGPTAPIKey()
                    end,
                },
                {
                    text = self.loc:t("menu_provider_select"), 
                    keep_menu_open = true,
                    callback = function()
                        self:selectAIProvider()
                    end,
                },
                {
                    text = self.loc:t("menu_local_ai_settings"), 
                    keep_menu_open = true,
                    callback = nil, -- Submenu
                    sub_item_table = {
                        {
                            text = self.loc:t("menu_local_ai_url"),
                            keep_menu_open = true,
                            callback = function()
                                self:setLocalAIEndpoint()
                            end,
                        },
                        {
                            text = self.loc:t("menu_local_ai_key"),
                            keep_menu_open = true,
                            callback = function()
                                self:setLocalAIKey()
                            end,
                        },
                        {
                            text = self.loc:t("menu_local_ai_model"),
                            keep_menu_open = true,
                            callback = function()
                                self:setLocalAIModel()
                            end,
                        },
                    }
                },
            }
        },
        {
            text = self.loc:t("menu_clear_cache"),
            keep_menu_open = true,
            callback = function()
                self:clearCache()
            end,
        },
        {
            text = self.loc:t("menu_xray_mode") .. " " .. (self.xray_mode_enabled and self.loc:t("xray_mode_active") or self.loc:t("xray_mode_inactive")),
            keep_menu_open = true,
            callback = function()
                self:toggleXRayMode()
            end,
        },
        {
            text = self.loc:t("menu_cloud_sync") or "Cloud Sync",
            keep_menu_open = true,
            sub_item_table = {
                {
                    text = self.loc:t("menu_manage_server") or "Manage Server",
                    keep_menu_open = true,
                    callback = function(touchmenu_instance)
                         self:manageSyncServer(touchmenu_instance)
                    end,
                },
                {
                   text = self.loc:t("menu_upload_xray") or "Upload X-Ray Data",
                   enabled_func = function() return self.settings.sync_server ~= nil end,
                   callback = function()
                       self:uploadXRayData()
                   end,
                },
                {
                   text = self.loc:t("menu_download_xray") or "Download X-Ray Data",
                   enabled_func = function() return self.settings.sync_server ~= nil end,
                   callback = function()
                       self:downloadXRayData()
                   end,
                },
            }
        },
        {
            text = self.loc:t("menu_about"),
            keep_menu_open = true,
            callback = function()
                self:showAbout()
            end,
        },
    }
end

function XRayPlugin:addToMainMenu(menu_items)
    logger.info("XRayPlugin: addToMainMenu called")
    
    -- Load settings
    if not self.settings then self.settings = {} end
    local saved_server = G_reader_settings:readSetting("xray_sync_server")
    if saved_server then
        self.settings.sync_server = saved_server
    end

    
    self.ui:registerKeyEvents({
        ShowXRayMenu = {
            { "Alt", "X" },
            event = "ShowXRayMenu",
        },
        XRayUploadSync = {
            event = "XRayUploadSync",
        },
        XRayDownloadSync = {
            event = "XRayDownloadSync",
        },
    })
    
    local counts = self:getMenuCounts()
    local function safe_t(key)
        if self.loc and self.loc.t then
            return self.loc:t(key) or key
        end
        return key
    end
    
    -- Use sub_item_table_func for lazy evaluation (sync on open) and native styling
    menu_items.xray = {
        text_func = function()
            local percent_str = ""
            if self.xray_data and self.xray_data.analysis_progress then
                percent_str = " (" .. self.xray_data.analysis_progress .. "%)"
            end
            return self.loc:t("menu_xray") .. percent_str
        end,
        sorting_hint = "tools",
        sub_item_table_func = function()
            self:syncCacheFromPartials()
            return self:getXRaySubMenuItems()
        end,
    }

    logger.info("XRayPlugin: Menu item 'xray' added successfully with Gemini Model option")
    
    self:registerActions()
end

function XRayPlugin:registerActions()
    Dispatcher:registerAction("xray_upload_sync", {
        category = "none",
        event = "XRayUploadSync",
        title = self.loc:t("menu_upload_xray") or "Upload X-Ray Data",
        reader = true,
    })
    Dispatcher:registerAction("xray_download_sync", {
        category = "none",
        event = "XRayDownloadSync",
        title = self.loc:t("menu_download_xray") or "Download X-Ray Data",
        reader = true,
    })
end

function XRayPlugin:showLanguageSelection()
    local ButtonDialog = require("ui/widget/buttondialog")
    local InfoMessage = require("ui/widget/infomessage")
    
    local current_lang = "tr" -- Varsayılan
    if self.loc then
        current_lang = self.loc:getLanguage()
    end
    
    local function changeLang(lang_code, lang_name)
        UIManager:close(self.ldlg)
        
        if self.loc then 
            self.loc:setLanguage(lang_code) 
        end
        
        UIManager:show(InfoMessage:new{
            text = self.loc:t("language_changed") .. "\n\n" .. self.loc:t("please_restart"),
            timeout = 4 
        })
    end
    
    local buttons = {
        {
            {
                text = "简体中文 " .. (self.loc:getLanguage() == "zh" and "*" or ""), 
                callback = function() changeLang("zh", "简体中文") end
            }
        },
    }
    
    self.ldlg = ButtonDialog:new{title = "语言", buttons = buttons}
    UIManager:show(self.ldlg)
end

function XRayPlugin:showCharacters()
    if not self.characters or #self.characters == 0 then
        UIManager:show(InfoMessage:new{
            text = self.loc:t("no_character_data") or "No character data",
            timeout = 3,
        })
        return
    end
    
    local items = {}
    
    -- Add search option
    table.insert(items, {
        text = self.loc:t("search_character") or "Search Character",
        callback = function()
            self:showCharacterSearch()
        end
    })
    
    -- Add characters
    for i, char in ipairs(self.characters) do
        -- CRITICAL: Ensure char and char.name exist
        if char and type(char) == "table" then
            local name = char.name
            
            -- Ensure name is a string
            if type(name) ~= "string" or name == "" then
                name = self.loc:t("unknown_character") or "Unknown Character"
            end
            
            local text = name
            
            -- CRITICAL: Ensure text is not nil
            if text and type(text) == "string" and #text > 0 then
                table.insert(items, {
                    text = text,
                    callback = function()
                        self:showCharacterDetails(char)
                    end
                })
            else
                logger.warn("XRayPlugin: Skipping character with invalid text at index", i)
            end
        else
            logger.warn("XRayPlugin: Skipping invalid character at index", i)
        end
    end
    
    -- Ensure we have items to display
    if #items <= 2 then
        -- Only search and separator
        UIManager:show(InfoMessage:new{
            text = self.loc:t("no_character_data") or "No valid character data",
            timeout = 3,
        })
        return
    end
    
    local character_menu = Menu:new{
        title = (self.loc:t("menu_characters") or "Characters") .. " (" .. #self.characters .. ")",
        item_table = items,
        -- is_borderless = true,
        is_popout = false,
        title_bar_fm_style = true,
        -- width = Screen:getWidth(),
        -- height = Screen:getHeight(),
    }
    
    UIManager:show(character_menu)
end

-- showCharacterDetails consolidated to implementation at line 2022

function XRayPlugin:selectGeminiModel()
    if not self.ai_helper then
        local AIHelper = require("aihelper")
        self.ai_helper = AIHelper
        self.ai_helper:init()
    end

    local current_model = "gemini-flash-lite-latest"
    if self.ai_helper.providers and self.ai_helper.providers.gemini then
        current_model = self.ai_helper.providers.gemini.model or "gemini-flash-lite-latest"
    end

    local ButtonDialog = require("ui/widget/buttondialog")
    local InfoMessage = require("ui/widget/infomessage")
    
    local models = {
        { id = "gemini-flash-latest", name = "Gemini Flash", info = "gemini_model_flash_info" },
        { id = "gemini-flash-lite-latest", name = "Gemini Flash Lite", info = "gemini_model_flash_lite_info" },
    }
    
    local buttons = {}
    for _, model in ipairs(models) do
        table.insert(buttons, {
            {
                text = model.name .. (current_model == model.id and " *" or ""),
                callback = function()
                    self.ai_helper:setGeminiModel(model.id)
                    UIManager:close(self.dlg)
                    UIManager:show(InfoMessage:new{
                        text = self.loc:t(model.info), 
                        timeout = 2
                    })
                end
            }
        })
    end

    self.dlg = ButtonDialog:new{
        title = self.loc:t("gemini_model_title"),
        buttons = buttons,
    }
    UIManager:show(self.dlg)
end

function XRayPlugin:fetchFromAI()
    logger.info("XRayPlugin: Fetching AI data")
    
    -- 1. WİRELESS KONTROL
    local NetworkMgr = require("ui/network/manager")
    
    if not NetworkMgr:isOnline() then
        logger.info("XRayPlugin: Network is offline, asking user...")
        
        local UIManager = require("ui/uimanager")
        local ConfirmBox = require("ui/widget/confirmbox")
        
        UIManager:show(ConfirmBox:new{
            text = self.loc:t("network_offline_prompt"),
            ok_text = self.loc:t("turn_on_wifi"),
            cancel_text = self.loc:t("cancel"),
            ok_callback = function()

                logger.info("XRayPlugin: User chose to turn on WiFi")
                
                -- WiFi'yi aç
                NetworkMgr:turnOnWifi(function()
                    logger.info("XRayPlugin: WiFi turned on, proceeding with fetch")
                    -- WiFi açıldıktan sonra spoiler tercihini sor
                    self:askSpoilerPreference()
                end)
            end,
            cancel_callback = function()
                logger.info("XRayPlugin: User cancelled WiFi activation")
                local InfoMessage = require("ui/widget/infomessage")
                UIManager:show(InfoMessage:new{
                    text = self.loc:t("fetch_cancelled"),
                    timeout = 3,
                })
            end,
        })
        return
    end
    
    -- WiFi zaten açıksa spoiler tercihini sor
    self:askSpoilerPreference()
end

function XRayPlugin:manageSyncServer(touchmenu_instance)
    local server = self.settings.sync_server
    local edit_cb = function()
        local sync_settings = SyncService:new{}
        sync_settings.onClose = function(this)
            UIManager:close(this)
        end
        sync_settings.onConfirm = function(sv)
            self.settings.sync_server = sv
            G_reader_settings:saveSetting("xray_sync_server", sv)
            if touchmenu_instance then touchmenu_instance:updateItems() end
            UIManager:show(InfoMessage:new{
                text = self.loc:t("server_saved") or "Server settings saved",
                timeout = 2
            })
        end
        UIManager:show(sync_settings)
    end
    
    if not server then
        edit_cb()
        return
    end
    
    -- If server exists, ask to edit or delete
    local ButtonDialog = require("ui/widget/buttondialog")
    local type = server.type == "dropbox" and " (DropBox)" or " (WebDAV)"
    
    local dialogue = ButtonDialog:new{
        title = (self.loc:t("cloud_storage") or "Cloud Storage") .. ":\n" .. server.name .. type,
        buttons = {
            {
                {
                    text = self.loc:t("delete") or "Delete",
                    callback = function()
                        UIManager:close(dialogue)
                        self.settings.sync_server = nil
                        G_reader_settings:delSetting("xray_sync_server")
                        if touchmenu_instance then touchmenu_instance:updateItems() end
                    end
                },
                {
                    text = self.loc:t("edit") or "Edit",
                    callback = function()
                        UIManager:close(dialogue)
                        edit_cb()
                    end
                },
                {
                    text = self.loc:t("close") or "Close",
                    callback = function()
                        UIManager:close(dialogue)
                    end
                }
            }
        }
    }
    UIManager:show(dialogue)
end

function XRayPlugin:uploadXRayData()
    if not self.settings.sync_server then return end
    
    local InfoMessage = require("ui/widget/infomessage")
    local msg = InfoMessage:new{ text = self.loc:t("uploading") or "Uploading...", timeout = nil }
    UIManager:show(msg)
    
    -- Ensure Cache Manager is loaded
    self:autoLoadCache() -- Ensures self.cache_manager exists
    
    if not self.sync then
        self.sync = Sync:new()
    end

    local book_path = self:getBookPath()
    if book_path then
        self.sync:upload(self.cache_manager, self.settings.sync_server, book_path, function(success, fail, errors)
            UIManager:close(msg)
            
            local result_msg = string.format(self.loc:t("upload_complete"), success, fail)
            
            if errors and #errors > 0 then
                result_msg = result_msg .. "\n\nError details:\n"
                -- Limit to first 3 errors to ensure it fits on screen
                for i = 1, math.min(#errors, 3) do
                    result_msg = result_msg .. errors[i] .. "\n"
                end
                if #errors > 3 then
                    result_msg = result_msg .. "...and " .. (#errors - 3) .. " more."
                end
            end
            
            UIManager:show(InfoMessage:new{
                text = result_msg,
                timeout = 10 -- Longer timeout to read errors
            })
        end)
    else
        UIManager:close(msg)
    end
end

function XRayPlugin:downloadXRayData()
    if not self.settings.sync_server then return end
    
    local InfoMessage = require("ui/widget/infomessage")
    local msg = InfoMessage:new{ text = self.loc:t("downloading") or "Downloading...", timeout = nil }
    UIManager:show(msg)
    
     -- Ensure Cache Manager is loaded
    self:autoLoadCache()
    
    if not self.sync then
        self.sync = Sync:new()
    end
    
    local book_path = self:getBookPath()
    if book_path then
        self.sync:download(self.cache_manager, self.settings.sync_server, book_path, function(success, fail, errors)
            UIManager:close(msg)
            -- Reload cache
            self:autoLoadCache()
            
            local result_msg = string.format(self.loc:t("download_complete"), success, fail)
            
            if errors and #errors > 0 then
                result_msg = result_msg .. "\n\nError details:\n"
                -- Limit to first 3 errors to ensure it fits on screen
                for i = 1, math.min(#errors, 3) do
                    result_msg = result_msg .. errors[i] .. "\n"
                end
                if #errors > 3 then
                    result_msg = result_msg .. "...and " .. (#errors - 3) .. " more."
                end
            end
            
            UIManager:show(InfoMessage:new{
                text = result_msg,
                timeout = 10 -- Longer timeout to read errors
            })
        end)
    else
        UIManager:close(msg)
    end
end

function XRayPlugin:askSpoilerPreference()
    logger.info("XRayPlugin: Asking spoiler preference")
    
    local UIManager = require("ui/uimanager")
    local Menu = require("ui/widget/menu")
    local Screen = require("device").screen
    
    -- Calculate reading percentage
    local current_page = self.ui:getCurrentPage()
    local total_pages = self.ui.document:getPageCount()
    local reading_percent = math.floor((current_page / total_pages) * 100)
    
    -- Check for nearest cache to show in UI
    local nearest_cache_percent = 0
    if not self.cache_manager then
        local CacheManager = require("cachemanager")
        self.cache_manager = CacheManager:new()
    end
    
    local book_path = self.ui.document.file
    local nearest_cache = self.cache_manager:getNearestPartialCache(book_path, reading_percent)
    if nearest_cache and nearest_cache.percent then
        nearest_cache_percent = nearest_cache.percent
    end
    
    -- Build the spoiler-free option text
    local spoiler_free_text
    if nearest_cache_percent > 0 then
        spoiler_free_text = string.format(
            self.loc:t("spoiler_free_option_with_cache"),
            nearest_cache_percent,
            reading_percent
        )
    else
        spoiler_free_text = string.format(
            self.loc:t("spoiler_free_option"),
            reading_percent
        )
    end
    
    local spoiler_menu = Menu:new{
        title = self.loc:t("spoiler_preference_title"),
        item_table = {
            {
                text = spoiler_free_text,
                callback = function()
                    logger.info("XRayPlugin: User chose spoiler-free mode")
                    UIManager:close(spoiler_menu)
                    self:continueWithFetch(reading_percent)
                end,
            },
            {
                text = self.loc:t("full_book_option"),
                callback = function()
                    logger.info("XRayPlugin: User chose full book mode")
                    UIManager:close(spoiler_menu)
                    self:continueWithFetch(100)
                end,
            },
            {
                text = self.loc:t("cancel"),
                callback = function()
                    logger.info("XRayPlugin: User cancelled fetch")
                    UIManager:close(spoiler_menu)
                    local InfoMessage = require("ui/widget/infomessage")
                    UIManager:show(InfoMessage:new{
                        text = self.loc:t("fetch_cancelled"),
                        timeout = 3,
                    })
                end,
            },
        },
        is_borderless = true,
        is_popout = false,
        title_bar_fm_style = true,
        width = Screen:getWidth(),
        height = Screen:getHeight(),
    }
    
    UIManager:show(spoiler_menu)
end

function XRayPlugin:continueWithFetch(reading_percent)
    logger.info("XRayPlugin: Continuing with fetch process (reading_percent:", reading_percent, ")")
    
    -- 1. Cache Manager Başlat (Kontrol için gerekli)
    if not self.cache_manager then
        local CacheManager = require("cachemanager")
        self.cache_manager = CacheManager:new()
    end
    
    -- 2. CACHE CHECK - Load existing data for incremental update if present
    local book_path = self:getBookPath()
    if not book_path then 
        logger.warn("XRayPlugin: Cannot fetch AI data, no book path")
        UIManager:show(InfoMessage:new{text = "Error: No book open", timeout = 3})
        return 
    end
    local cache_path = self.cache_manager:getCachePath(book_path)
    local lfs = require("libs/libkoreader-lfs")
    local existing_data = nil
    
    -- Load existing cache for incremental update
    if cache_path and lfs.attributes(cache_path) then
        existing_data = self.cache_manager:loadCache(book_path)
        if existing_data then
            logger.info("XRayPlugin: Loaded existing cache for incremental update")
        end
    end

    -- 3. AI Helper Başlat (Eğer cache yoksa devam et)
    if not self.ai_helper then
        local AIHelper = require("aihelper")
        self.ai_helper = AIHelper
        self.ai_helper:init()
    end
    
    -- Seçili provider'ı al (varsayılan: gemini)
    local selected_provider = self.ai_provider or self.ai_helper.default_provider or "gemini"
    local provider_config = self.ai_helper.providers[selected_provider]
    
    local title = self.ui.document:getProps().title or "Unknown"
    local author = self.ui.document:getProps().authors or ""
    
    -- Model adını seçili provider'a göre al
    local current_model = self.loc:t("unknown_model")
    if provider_config and provider_config.model then
        current_model = provider_config.model
    end
    
    -- Provider adını al
    local provider_name = provider_config and provider_config.name or "AI"
    
    -- Spoiler durumunu hazırla
    local spoiler_status = reading_percent < 100 and 
        string.format(self.loc:t("spoiler_free_mode"), reading_percent) or 
        self.loc:t("full_book_mode_active")
    
    -- 4. Bekleme Mesajı Göster
    local InfoMessage = require("ui/widget/infomessage")
    local wait_msg = InfoMessage:new{
        text = string.format(
            self.loc:t("fetching_ai") ..
            self.loc:t("fetching_model") .. "%s\n" ..
            self.loc:t("book_title") .. "%s\n" ..
            "%s\n\n" ..
            self.loc:t("fetching_wait") ..
            self.loc:t("dont_touch"), 
            current_model,
            title,
            spoiler_status
        ),
        timeout = 60,
    }
    UIManager:show(wait_msg)
    
    -- 5. Extract book text for accurate analysis using ChapterAnalyzer methods
    local book_text = nil
    local extraction_mode = "title_only"
    logger.info("XRayPlugin: Extracting book text for text-based analysis...")
    
    -- Use extraction method from assistant.koplugin (proven to work)
    local doc = self.ui.document
    local is_paged = doc.info.has_pages
    
    if not is_paged or (doc.info.doc_format and doc.info.doc_format ~= "pdf" and doc.info.doc_format ~= "djvu" and doc.info.doc_format ~= "cbz") then
        -- Reflowable document (EPUB, FB2, MOBI etc) OR Scroll Mode: use getTextFromXPointers
        -- (Even if paged, Crengine handles XPointers better than getPageText)
        logger.info("XRayPlugin: EPUB detected, using getTextFromXPointers...")
            local current_xp = doc:getXPointer()
        local success, result = pcall(function()
            local current_xp = doc:getXPointer()
            logger.info("XRayPlugin: Current XP:", tostring(current_xp))
            
            doc:gotoPos(0)
            local start_xp = doc:getXPointer()
            logger.info("XRayPlugin: Start XP:", tostring(start_xp))
            if not start_xp then error("Failed to get Start XPointer") end
            
            doc:gotoXPointer(current_xp)
            
            -- For full book, go to end first
            local end_xp = current_xp
            if reading_percent >= 100 then
                -- Force go to end of document
                -- gotoPos takes a Y coordinate (pixel offset) in scroll mode, or potentially page index in some contexts.
                -- For Crengine, it seems to be Y coordinate.
                local height = 100000000 -- Fallback large value
                if doc.info and doc.info.doc_height and doc.info.doc_height > 0 then
                    height = doc.info.doc_height
                elseif doc.getDocHeight then
                    height = doc:getDocHeight()
                end
                
                logger.info("XRayPlugin: Jumping to end of book with height:", height)
                doc:gotoPos(height)
                end_xp = doc:getXPointer()
                logger.info("XRayPlugin: End XP (at 100%):", tostring(end_xp))
                
                if not end_xp then 
                     -- Try getting doc height if gotoPos(1) failed to give a valid XPointer
                     local h = doc:getDocHeight()
                     logger.info("XRayPlugin: Retrying with doc height:", h)
                     if h then 
                        doc:gotoPos(1) -- Try again
                        end_xp = doc:getXPointer() 
                     end
                end
                
                if not end_xp then error("Failed to get End XPointer") end
                doc:gotoXPointer(current_xp) -- Restore position
            end
            
            local extracted = doc:getTextFromXPointers(start_xp, end_xp)
            if not extracted then error("getTextFromXPointers returned nil") end
            logger.info("XRayPlugin: Extracted text length:", #extracted)
            return extracted
        end)
        
        if success and result and #result > 100 then
            book_text = result
            extraction_mode = "xpointers"
            logger.info("XRayPlugin: Got text via XPointers:", #book_text, "characters")
        else
            logger.warn("XRayPlugin: getTextFromXPointers failed or empty. Error:", tostring(result))
            -- Fallback?
        end
    else
        -- PDF/paged document: extract page by page with proper table handling
        logger.info("XRayPlugin: Paged document detected, extracting pages...")
        local total_pages = doc:getPageCount() or 0
        local max_pages = math.min(total_pages, 500)
        
        -- Limit pages for spoiler-free mode
        if reading_percent < 100 then
            max_pages = math.floor(total_pages * reading_percent / 100)
        end
        
        local text_parts = {}
        for page = 1, max_pages do
            local success, page_text = pcall(function()
                return doc:getPageText(page)
            end)
            
            if success and page_text then
                -- Handle table format (common in PDF)
                if type(page_text) == "table" then
                    local texts = {}
                    for _, block in ipairs(page_text) do
                        if type(block) == "table" then
                            for i = 1, #block do
                                local span = block[i]
                                if type(span) == "table" and span.word then
                                    table.insert(texts, span.word)
                                end
                            end
                        end
                    end
                    page_text = table.concat(texts, " ")
                end
                
                if type(page_text) == "string" and #page_text > 0 then
                    table.insert(text_parts, page_text)
                end
            end
        end
        
        if #text_parts > 0 then
            book_text = table.concat(text_parts, "\n")
            extraction_mode = "page_by_page"
            logger.info("XRayPlugin: Extracted", #text_parts, "pages,", #book_text, "characters")
        end
    end
    
    -- Truncation and prompt selection handled in AIHelper (centralized)
    
    -- Log extraction result
    if book_text and #book_text > 100 then
        logger.info("XRayPlugin: Text extraction successful, mode:", extraction_mode, "length:", #book_text)
    else
        logger.warn("XRayPlugin: Could not extract book text, falling back to title-only mode")
        extraction_mode = "title_only"
    end
    
    -- 6. Start AI request (Async)
    UIManager:scheduleIn(0.5, function()
        local ConfirmBox = require("ui/widget/confirmbox")
        local InfoMessage = require("ui/widget/infomessage")
        

        local context = {
            reading_percent = reading_percent,
            spoiler_free = true, -- User requested to always use spoiler-free prompt logic (even for 100%)
            book_path = book_path,
            existing_data = existing_data  -- Pass cached data for incremental merge
        }
        

        local abort_flag = false
        local wait_popup
         
        -- Progress callback
        local progress_callback = function(current, total)
             if abort_flag then return false end
             
             if wait_popup then
                 local percent = math.floor(((current - 1) / total) * 100)
                 local status_text = string.format("模型: %s\nAI分析进行中...\n正在处理: %d / %d\n(%d%%)", 
                    current_model, current, total, percent)
                 
                 -- Recreate to update text (safest way)
                 UIManager:close(wait_popup)
                 wait_popup = ConfirmBox:new{
                     text = status_text,
                     title = title,
                     ok_text = "", -- Hide OK
                     cancel_text = "停止 (Stop)",
                     cancel_callback = function()
                         abort_flag = true
                     end,
                 }
                 UIManager:show(wait_popup)
                 UIManager:forceRePaint()
             end
        end
        
        -- Initial Popup
        wait_popup = ConfirmBox:new{
            text = "AI正在准备分析...",
            title = title,
            ok_text = "",
            cancel_text = "停止",
            cancel_callback = function()
                abort_flag = true
            end,
        }
        UIManager:show(wait_popup)
        
        -- Completion Callback
        local on_complete = function(book_data, error_code, error_msg)

            if wait_popup then UIManager:close(wait_popup) end
            
            if error_code == "aborted" then
                UIManager:show(InfoMessage:new{text = "AI分析已取消", timeout = 2})
                return
            end

            if not book_data then
                local error_text = self.loc:t("error_info") .. "\n\n"
                if error_code == "error_safety" then
                    error_text = error_text .. self.loc:t("error_filtered")
                elseif error_code == "error_503" then
                    error_text = error_text .. self.loc:t("error_network_timeout")
                elseif error_msg then
                    error_text = error_text .. error_msg
                else
                    error_text = error_text .. self.loc:t("ai_fetch_failed")
                end
                
                local ConfirmBox = require("ui/widget/confirmbox")
                UIManager:show(ConfirmBox:new{
                    title = self.loc:t("error_title"),
                    text = error_text,
                    ok_text = self.loc:t("button_close"),
                    cancel_text = "",
                })
                return
            end
            
            -- Success: Show Result
            -- Success: Save Data
            self.book_title = book_data.book_title
            self.author = book_data.author
            self.author_bio = book_data.author_bio
            self.author_birth = book_data.author_birth
            self.author_death = book_data.author_death
            self.summary = book_data.summary
            self.characters = book_data.characters or {}
            self.themes = book_data.themes or {}
            self.locations = book_data.locations or {}
            self.timeline = book_data.timeline or {}
            self.historical_figures = book_data.historical_figures or {}
            
            logger.info("XRayPlugin: Found", #self.characters, "characters")
            
            -- Save to cache with the percentage marker
            book_data.cached_percent = reading_percent
            self.cache_manager:saveCache(book_path, book_data)
            
            -- Show Success Message
            local mode_str = extraction_mode == "title_only" and "[仅标题]" or "[全文]"
            local success_msg = string.format("分析完成!\n%s\n人物: %d, 地点: %d, 主题: %d", mode_str, #self.characters, #self.locations, #self.themes)
            
            UIManager:show(InfoMessage:new{
                text = success_msg,
                timeout = 4,
            })
            
            -- Reload document to apply X-Ray data
            self.ui:reloadDocument()
        end
        
        -- Call AI Async
        self.ai_helper:getBookData(title, author, selected_provider, context, book_text, on_complete, progress_callback)
    end)
        

end

function XRayPlugin:showLocations()
    if not self.locations or #self.locations == 0 then
        UIManager:show(InfoMessage:new{
            text = self.loc:t("no_location_data"),
            timeout = 3,
        })
        return
    end
    
    local items = {}
    for i, loc in ipairs(self.locations) do
        local text = loc.name or "Unknown Location"
        
        table.insert(items, {
            text = text,
            callback = function()
                self:showLocationDetails(loc)
            end,
        })
    end
    
    local location_menu = Menu:new{
        title = self.loc:t("menu_locations") .. " (" .. #self.locations .. ")",
        item_table = items,
        -- is_borderless = true,
        is_popout = false,
        title_bar_fm_style = true,
        -- width = Screen:getWidth(),
        -- height = Screen:getHeight(),
    }
    
    UIManager:show(location_menu)
end

function XRayPlugin:showLocationDetails(location)
    if not location then return end
    
    local name = location.name or "Unknown"
    local description = location.description or ""
    local metadata = {}
    
    if location.importance then
        table.insert(metadata, (self.loc:t("importance") or "Importance") .. ": " .. location.importance)
    end
    
    if location.count then
        table.insert(metadata, self.loc:t("mention_count"):format(location.count))
    elseif location.pages and #location.pages > 0 then
        table.insert(metadata, self.loc:t("mention_count"):format(#location.pages))
    end
    
    self:showNativeDetails(name, description, metadata)
end

function XRayPlugin:showAuthorInfo()
    if not self.author_info or not self.author_info.description or #self.author_info.description == 0 then
        UIManager:show(InfoMessage:new{
            text = self.loc:t("no_author_data"),
            timeout = 3,
        })
        return
    end
    
    local title = self.author_info.name or self.loc:t("menu_author_info")
    local description = self.author_info.description or ""
    local metadata = {}
    
    if self.author_info.birthDate and #self.author_info.birthDate > 0 then
        table.insert(metadata, self.loc:t("author_birth") .. ": " .. self.author_info.birthDate)
    end
    if self.author_info.deathDate and #self.author_info.deathDate > 0 then
        table.insert(metadata, self.loc:t("author_death") .. ": " .. self.author_info.deathDate)
    end
    
    self:showNativeDetails(title, description, metadata)
end

function XRayPlugin:showAbout()
    local TextViewer = require("ui/widget/textviewer")
    
    local about_viewer = TextViewer:new{
        title = self.loc:t("about_title"),
        text = self.loc:t("about_text"),
        justified = false,
    }
    
    UIManager:show(about_viewer)
end

function XRayPlugin:clearCache()
    if not self.cache_manager then
        local CacheManager = require("cachemanager")
        self.cache_manager = CacheManager:new()
    end
    
    local ConfirmBox = require("ui/widget/confirmbox")
    UIManager:show(ConfirmBox:new{
        text = self.loc:t("cache_clear_confirm"),
        ok_text = self.loc:t("yes_clear"),
        cancel_text = self.loc:t("cancel"),
        ok_callback = function()
            local book_path = self:getBookPath()
            if not book_path then return end
            
            local success = self.cache_manager:clearCache(book_path)
            
            if success then
                self.book_data = nil
                self.characters = {}
                self.locations = {}
                self.themes = {}
                self.summary = nil
                self.author_info = nil
                self.timeline = {}
                self.historical_figures = {}
                
                UIManager:show(InfoMessage:new{
                    text = self.loc:t("cache_cleared"),
                    timeout = 5,
                })
            else
                UIManager:show(InfoMessage:new{
                    text = self.loc:t("cache_not_found"),
                    timeout = 3,
                })
            end
        end,
    })
end

function XRayPlugin:toggleXRayMode()
    if not self.characters or #self.characters == 0 then
        UIManager:show(InfoMessage:new{
            text = self.loc:t("xray_mode_no_data"),
            timeout = 5,
        })
        return
    end
    
    self.xray_mode_enabled = not self.xray_mode_enabled
    
    if self.xray_mode_enabled then
        UIManager:show(InfoMessage:new{
            text = self.loc:t("xray_mode_enabled"),
            timeout = 7,
        })
    else
        UIManager:show(InfoMessage:new{
            text = self.loc:t("xray_mode_disabled"),
            timeout = 3,
        })
    end
    
    logger.info("XRayPlugin: X-Ray mode:", self.xray_mode_enabled and "enabled" or "disabled")
end

function XRayPlugin:findCharacterByName(word)
    if not self.characters or not word then
        return nil
    end
    
    local word_lower = string.lower(word)
    
    for _, char in ipairs(self.characters) do
        local name_lower = string.lower(char.name or "")
        
        if name_lower == word_lower then
            return char
        end
        
        if string.find(name_lower, word_lower, 1, true) or
           string.find(word_lower, name_lower, 1, true) then
            return char
        end
        
        local first_name = string.match(name_lower, "^(%S+)")
        if first_name and first_name == word_lower then
            return char
        end
    end
    
    return nil
end

function XRayPlugin:showCharacterInfo(char)
    self:showCharacterDetails(char)
end

function XRayPlugin:showCharacterDetails(character)
    if not character then return end
    
    local name = character.name or "Unknown"
    local description = character.description or ""
    local metadata = {}
    
    if character.role then
        table.insert(metadata, (self.loc:t("role") or "Role") .. ": " .. character.role)
    end

    if character.count then
        table.insert(metadata, self.loc:t("mention_count"):format(character.count))
    elseif character.pages and #character.pages > 0 then
        table.insert(metadata, self.loc:t("mention_count"):format(#character.pages))
    end
    
    local extra_buttons = {}
    
    if character.events and #character.events > 0 then
        table.insert(extra_buttons, {
            text = (self.loc:t("view_events") or "View Events") .. " (" .. #character.events .. ")",
            callback = function()
                self:showCharacterEventsList(character, character.events)
            end
        })
    end
    
    self:showNativeDetails(name, description, metadata, extra_buttons)
end

function XRayPlugin:showCharacterEventsList(character, events)
    if not events or #events == 0 then return end
    
    local menu -- Declare upvalue for closure
    local items = {}
    
    for i, event in ipairs(events) do
        local percent = event.percent or 0
        local text = (event.event or "Event") .. string.format(" (%.1f%%)", percent)
        
        table.insert(items, {
            text = text,
            callback = function()
                 if menu then UIManager:close(menu) end
                 self.ui.link:addCurrentLocationToStack()
                 self.ui:handleEvent(Event:new("GotoPercent", percent))
            end,
        })
    end
    
    menu = Menu:new{
        title = (character.name or "Character") .. " - " .. (self.loc:t("events") or "Events"),
        item_table = items,
        is_popout = false,
        title_bar_fm_style = true,
        width = Screen:getWidth(),
        height = Screen:getHeight(),
    }
    
    UIManager:show(menu)
end

function XRayPlugin:setGeminiAPIKey()
    local InputDialog = require("ui/widget/inputdialog")
    
    if not self.ai_helper then
        local AIHelper = require("aihelper")
        self.ai_helper = AIHelper
        self.ai_helper:init()
    end
    
    local current_key = self.ai_helper.providers.gemini.api_key or ""
    
    local input_dialog
    input_dialog = InputDialog:new{
        title = self.loc:t("gemini_key_title"), 
        input = current_key,
        input_hint = self.loc:t("gemini_key_hint"), 
        description = self.loc:t("gemini_key_desc"), 
        buttons = {
            {
                {
                    text = self.loc:t("cancel"),
                    callback = function()
                        UIManager:close(input_dialog)
                    end,
                },
                {
                    text = self.loc:t("save"),
                    is_enter_default = true,
                    callback = function()
                        local api_key = input_dialog:getInputText()
                        if api_key and #api_key > 0 then
                            if not self.ai_helper then
                                local AIHelper = require("aihelper")
                                self.ai_helper = AIHelper
                            end
                            
                            self.ai_helper:setAPIKey("gemini", api_key)
                            self.ai_provider = "gemini"
                            
                            UIManager:show(InfoMessage:new{
                                text = self.loc:t("gemini_key_saved"), 
                                timeout = 3,
                            })                            
                        end
                        UIManager:close(input_dialog)
                    end,
                },
            }
        },
    }
    UIManager:show(input_dialog)
    input_dialog:onShowKeyboard()
end

function XRayPlugin:setChatGPTAPIKey()
    local InputDialog = require("ui/widget/inputdialog")
    
    if not self.ai_helper then
        local AIHelper = require("aihelper")
        self.ai_helper = AIHelper
        self.ai_helper:init()
    end
    
    local current_key = self.ai_helper.providers.chatgpt.api_key or ""
    
    local input_dialog
    input_dialog = InputDialog:new{
        title = self.loc:t("chatgpt_key_title"), 
        input = current_key,
        input_hint = self.loc:t("chatgpt_key_hint"), 
        description = self.loc:t("chatgpt_key_desc"), 
        buttons = {
            {
                {
                    text = self.loc:t("cancel"),
                    callback = function()
                        UIManager:close(input_dialog)
                    end,
                },
                {
                    text = self.loc:t("save"),
                    is_enter_default = true,
                    callback = function()
                        local api_key = input_dialog:getInputText()
                        if api_key and #api_key > 0 then
                            if not self.ai_helper then
                                local AIHelper = require("aihelper")
                                self.ai_helper = AIHelper
                            end
                            self.ai_helper:setAPIKey("chatgpt", api_key)
                            self.ai_provider = "chatgpt"
                            
                            UIManager:show(InfoMessage:new{
                                text = self.loc:t("chatgpt_key_saved"), 
                                timeout = 3,
                            })
                        end
                        UIManager:close(input_dialog)
                    end,
                },
            }
        },
    }
    UIManager:show(input_dialog)
    input_dialog:onShowKeyboard()
end

function XRayPlugin:setLocalAIEndpoint()
    local InputDialog = require("ui/widget/inputdialog")
    
    if not self.ai_helper then
        local AIHelper = require("aihelper")
        self.ai_helper = AIHelper
        self.ai_helper:init()
    end
    
    local current_url = self.ai_helper.providers["local"].endpoint or "http://localhost:8080/v1/chat/completions"
    
    local input_dialog
    input_dialog = InputDialog:new{
        title = self.loc:t("menu_local_ai_url"), 
        input = current_url,
        input_hint = self.loc:t("local_url_hint"), 
        buttons = {
            {
                {
                    text = self.loc:t("cancel"),
                    callback = function()
                        UIManager:close(input_dialog)
                    end,
                },
                {
                    text = self.loc:t("save"),
                    is_enter_default = true,
                    callback = function()
                        local url = input_dialog:getInputText()
                        if url and #url > 0 then
                            self.ai_helper:setLocalAIEndpoint(url)
                            UIManager:show(InfoMessage:new{
                                text = self.loc:t("local_url_saved"), 
                                timeout = 3,
                            })
                        end
                        UIManager:close(input_dialog)
                    end,
                },
            }
        },
    }
    UIManager:show(input_dialog)
    input_dialog:onShowKeyboard()
end

function XRayPlugin:setLocalAIKey()
    local InputDialog = require("ui/widget/inputdialog")
    
    if not self.ai_helper then
        local AIHelper = require("aihelper")
        self.ai_helper = AIHelper
        self.ai_helper:init()
    end
    
    local current_key = self.ai_helper.providers["local"].api_key or ""
    
    local input_dialog
    input_dialog = InputDialog:new{
        title = self.loc:t("menu_local_ai_key"), 
        input = current_key,
        input_hint = self.loc:t("local_key_hint"), 
        buttons = {
            {
                {
                    text = self.loc:t("cancel"),
                    callback = function()
                        UIManager:close(input_dialog)
                    end,
                },
                {
                    text = self.loc:t("save"),
                    is_enter_default = true,
                    callback = function()
                        local key = input_dialog:getInputText()
                        if key and #key > 0 then
                            -- Reuse generic logic if possible, or manual update
                            self.ai_helper.providers["local"].api_key = key
                            self.ai_helper:saveAPIKeyToFile("local", key)
                            
                            UIManager:show(InfoMessage:new{
                                text = self.loc:t("local_key_saved"), 
                                timeout = 3,
                            })
                        end
                        UIManager:close(input_dialog)
                    end,
                },
            }
        },
    }
    UIManager:show(input_dialog)
    input_dialog:onShowKeyboard()
end

function XRayPlugin:setLocalAIModel()
    local InputDialog = require("ui/widget/inputdialog")
    
    if not self.ai_helper then
        local AIHelper = require("aihelper")
        self.ai_helper = AIHelper
        self.ai_helper:init()
    end
    
    local current_model = self.ai_helper.providers["local"].model or ""
    
    local input_dialog
    input_dialog = InputDialog:new{
        title = self.loc:t("menu_local_ai_model"), 
        input = current_model,
        input_hint = self.loc:t("local_model_hint"), 
        buttons = {
            {
                {
                    text = self.loc:t("cancel"),
                    callback = function()
                        UIManager:close(input_dialog)
                    end,
                },
                {
                    text = self.loc:t("save"),
                    is_enter_default = true,
                    callback = function()
                        local model = input_dialog:getInputText()
                        if model and #model > 0 then
                            self.ai_helper:setLocalAIModel(model)
                            UIManager:show(InfoMessage:new{
                                text = self.loc:t("local_model_saved"), 
                                timeout = 3,
                            })
                        end
                        UIManager:close(input_dialog)
                    end,
                },
            }
        },
    }
    UIManager:show(input_dialog)
    input_dialog:onShowKeyboard()
end

function XRayPlugin:selectAIProvider()
    if not self.ai_helper then
        local AIHelper = require("aihelper")
        self.ai_helper = AIHelper
        self.ai_helper:init()
    end
    
    if not self.ai_provider and self.ai_helper.default_provider then
        self.ai_provider = self.ai_helper.default_provider
    end
    
    -- 1. ADIM: Değişkeni burada önceden tanımlıyoruz (henüz boş)
    local provider_menu 

    local providers = {}
    
    local gemini_key = self.ai_helper.providers.gemini and self.ai_helper.providers.gemini.api_key
    if gemini_key and gemini_key ~= "" then
        table.insert(providers, {
            text = "[OK] Google Gemini (" .. (self.ai_provider == "gemini" and "已启用" or "未启用") .. ")",
            callback = function()
                self.ai_provider = "gemini"
                self.ai_helper:setDefaultProvider("gemini")
                UIManager:show(InfoMessage:new{
                    text = self.loc:t("gemini_selected"), 
                    timeout = 2,
                })
                
                -- 3. ADIM: Artık provider_menu dolu olduğu için bu satır çalışır
                if provider_menu then
                    UIManager:close(provider_menu)
                end
            end,
        })
    else
        table.insert(providers, {
            text = "[!] Google Gemini (未设置API密钥)",
            callback = function()
                UIManager:show(InfoMessage:new{
                    text = self.loc:t("set_key_first"), 
                    timeout = 3,
                })
            end,
        })
    end
    
    local chatgpt_key = self.ai_helper.providers.chatgpt and self.ai_helper.providers.chatgpt.api_key
    if chatgpt_key and chatgpt_key ~= "" then
        table.insert(providers, {
            text = "[OK] ChatGPT (" .. (self.ai_provider == "chatgpt" and "已启用" or "未启用") .. ")",
            callback = function()
                self.ai_provider = "chatgpt"
                self.ai_helper:setDefaultProvider("chatgpt")
                UIManager:show(InfoMessage:new{
                    text = self.loc:t("chatgpt_selected"), 
                    timeout = 2,
                })
                
                -- 3. ADIM: Burada da menüyü kapatıyoruz
                if provider_menu then
                    UIManager:close(provider_menu)
                end
            end,
        })
    else
        table.insert(providers, {
            text = "[!] ChatGPT (未设置API密钥)",
            callback = function()
                UIManager:show(InfoMessage:new{
                    text = self.loc:t("set_key_first"), 
                    timeout = 3,
                })
            end,
        })
    end

    -- Local AI Option
    table.insert(providers, {
        text = "[OK] Local AI (" .. (self.ai_provider == "local" and "已启用" or "未启用") .. ")",
        callback = function()
            self.ai_provider = "local"
            self.ai_helper:setDefaultProvider("local")
            UIManager:show(InfoMessage:new{
                text = self.loc:t("local_ai_selected"), 
                timeout = 2,
            })
            
            if provider_menu then
                UIManager:close(provider_menu)
            end
        end,
    })
    
    -- 2. ADIM: Daha önce tanımladığımız değişkene atama yapıyoruz (başındaki 'local' ifadesini kaldırdık)
    provider_menu = Menu:new{
        title = self.loc:t("provider_select_title"), 
        item_table = providers,
        is_borderless = true,
        is_popout = false,
        title_bar_fm_style = true,
        width = Screen:getWidth(),
        height = Screen:getHeight(),
    }
    
    UIManager:show(provider_menu)
end



function XRayPlugin:showSummary()
    if not self.summary or #self.summary == 0 then
        UIManager:show(InfoMessage:new{
            text = self.loc:t("no_summary_data"),
            timeout = 3,
        })
        return
    end
    
    local title = self.loc:t("summary_title") or "Summary"
    local description = self.summary
    local metadata = { "(Spoiler-free)" }
    
    self:showNativeDetails(title, description, metadata)
end

function XRayPlugin:showThemes()
    if not self.themes or #self.themes == 0 then
        UIManager:show(InfoMessage:new{
            text = self.loc:t("no_theme_data"),
            timeout = 3,
        })
        return
    end
    
    local items = {}
    for i, theme in ipairs(self.themes) do
        -- Support both string themes and object themes
        local theme_name = type(theme) == "table" and (theme.term or theme.name) or theme
        local theme_obj = type(theme) == "table" and theme or {name = theme}
        
        table.insert(items, {
            text = theme_name,
            callback = function()
                self:showThemeDetails(theme_obj)
            end,
        })
    end
    
    local theme_menu = Menu:new{
        title = self.loc:t("menu_themes") .. " (" .. #self.themes .. ")",
        item_table = items,
        is_popout = false,
        title_bar_fm_style = true,
    }
    
    UIManager:show(theme_menu)
end

function XRayPlugin:showThemeDetails(theme)
    if not theme then return end
    
    local name = theme.term or theme.name or "Unknown"
    local description = ""
    local metadata = {}
    
    if theme.description then
        description = theme.description
    elseif type(theme) == "string" then
        description = theme
    end
    
    if theme.count then
        table.insert(metadata, self.loc:t("mention_count"):format(theme.count))
    elseif theme.pages and #theme.pages > 0 then
        table.insert(metadata, self.loc:t("mention_count"):format(#theme.pages))
    end
    
    self:showNativeDetails(name, description, metadata)
end


function XRayPlugin:showTimeline()
    if not self.timeline or #self.timeline == 0 then
        UIManager:show(InfoMessage:new{
            text = self.loc:t("no_timeline_data"),
            timeout = 5,
        })
        return
    end
    
    local items = {}
    for i, event in ipairs(self.timeline) do
        local text = event.event or "Event"
        if event.chapter then
             text = text .. " (" .. self.loc:t("chapter") .. " " .. event.chapter .. ")"
        end
        
        table.insert(items, {
            text = text,
            callback = function()
                local title = string.format(self.loc:t("timeline_event"), i)
                local description = event.event or ""
                local metadata = {}
                
                if event.chapter then
                    table.insert(metadata, self.loc:t("chapter") .. ": " .. event.chapter)
                end
                
                if event.importance then
                    table.insert(metadata, (self.loc:t("importance") or "Importance") .. ": " .. event.importance)
                end
                
                if event.characters and #event.characters > 0 then
                    local chars_str = table.concat(event.characters, ", ")
                    table.insert(metadata, self.loc:t("characters_involved") .. ": " .. chars_str)
                end
                
                local extra_buttons = {}
                if event.percent then
                    table.insert(extra_buttons, {
                        text = (self.loc:t("go_to_location") or "Go to Location") .. string.format(" (%.1f%%)", event.percent),
                        callback = function()
                            self.ui.link:addCurrentLocationToStack()
                            self.ui:handleEvent(Event:new("GotoPercent", event.percent))
                        end
                    })
                end
                
                self:showNativeDetails(title, description, metadata, extra_buttons)
            end,
        })
    end
    
    local timeline_menu = Menu:new{
        title = self.loc:t("menu_timeline") .. " (" .. #self.timeline .. " " .. self.loc:t("events") .. ")",
        item_table = items,
        -- is_borderless = true,
        is_popout = false,
        title_bar_fm_style = true,
        -- width = Screen:getWidth(),
        -- height = Screen:getHeight(),
    }
    
    UIManager:show(timeline_menu)
end

function XRayPlugin:showHistoricalFigures()
    if not self.historical_figures or #self.historical_figures == 0 then
        UIManager:show(InfoMessage:new{
            text = self.loc:t("no_historical_data"),
            timeout = 5,
        })
        return
    end
    
    local items = {}
    for i, figure in ipairs(self.historical_figures) do
        local text = figure.name or "Unknown"
        if figure.role then
            text = text .. " (" .. figure.role .. ")"
        end
        
        table.insert(items, {
            text = text,
            callback = function()
                self:showHistoricalFigureDetails(figure)
            end,
        })
    end
    
    local figures_menu = Menu:new{
        title = self.loc:t("menu_historical_figures") .. " (" .. #self.historical_figures .. ")",
        item_table = items,
        -- is_borderless = true,
        is_popout = false,
        title_bar_fm_style = true,
        -- width = Screen:getWidth(),
        -- height = Screen:getHeight(),
    }
    
    UIManager:show(figures_menu)
end

function XRayPlugin:showHistoricalFigureDetails(figure)
    local name = figure.name or "Unknown"
    local description = figure.biography or ""
    local metadata = {}
    
    -- Life span
    if figure.birth_year or figure.death_year then
        local life = ""
        if figure.birth_year then life = life .. figure.birth_year end
        if figure.death_year then 
            life = life .. " - " .. figure.death_year 
        elseif figure.birth_year then
            life = life .. " - ?"
        end
        table.insert(metadata, life)
    end
    
    if figure.role then
        table.insert(metadata, (self.loc:t("role") or "Role") .. ": " .. figure.role)
    end
    
    if figure.importance_in_book then
        table.insert(metadata, (self.loc:t("hist_importance") or "Importance") .. ":\n" .. figure.importance_in_book)
    end
    
    if figure.context_in_book then
        table.insert(metadata, (self.loc:t("hist_context") or "Context") .. ":\n" .. figure.context_in_book)
    end
    
    self:showNativeDetails(name, description, metadata)
end

function XRayPlugin:showChapterCharacters()
    if not self.characters or #self.characters == 0 then
        UIManager:show(InfoMessage:new{
            text = self.loc:t("no_char_data_fetch"), 
            timeout = 3,
        })
        return
    end
    
    if not self.chapter_analyzer then
        local ChapterAnalyzer = require("chapteranalyzer")
        self.chapter_analyzer = ChapterAnalyzer:new()
    end
    
    UIManager:show(InfoMessage:new{
        text = self.loc:t("analyzing_chapter"),
        timeout = 1,
    })
    
    local chapter_text, chapter_title = self.chapter_analyzer:getCurrentChapterText(self.ui)
    
    if not chapter_text or #chapter_text == 0 then
        UIManager:show(InfoMessage:new{
            text = self.loc:t("chapter_text_error"),
            timeout = 3,
        })
        return
    end
    
    local found_chars = self.chapter_analyzer:findCharactersInText(chapter_text, self.characters)
    
    if #found_chars == 0 then
        UIManager:show(InfoMessage:new{
            text = string.format(self.loc:t("no_characters_in_chapter"), chapter_title or self.loc:t("this_chapter")),
            timeout = 5,
        })
        return
    end
    
    local items = {}
    for _, char_info in ipairs(found_chars) do
        local char = char_info.character
        local count = char_info.count
        
        local gender_icon = ""
        if char.gender == "male" or char.gender == "erkek" then
            gender_icon = ""
        elseif char.gender == "female" or char.gender == "kadın" then
            gender_icon = ""
        else
            gender_icon = ""
        end
        
        table.insert(items, {
            text = string.format("%s%s (%dx)", gender_icon, char.name, count),
            callback = function()
                self:showCharacterInfo(char)
            end,
        })
    end
    
    local menu = Menu:new{
        title = string.format("%s\n%d %s", 
                             chapter_title or self.loc:t("this_chapter"), 
                             #found_chars,
                             self.loc:t("chapter_chars_title")), 
        item_table = items,
        -- is_borderless = true,
        is_popout = false,
        title_bar_fm_style = true,
        -- width = Screen:getWidth(),
        -- height = Screen:getHeight(),
    }
    
    UIManager:show(menu)
    
    logger.info("XRayPlugin: Showed chapter characters -", #found_chars, "found")
end

function XRayPlugin:showCharacterNotes()
    if not self.characters or #self.characters == 0 then
        UIManager:show(InfoMessage:new{
            text = self.loc:t("no_char_data_fetch"), 
            timeout = 3,
        })
        return
    end
    
    if not self.notes_manager then
        local CharacterNotes = require("characternotes")
        self.notes_manager = CharacterNotes:new()
    end
    
    local book_path = self:getBookPath()
    if not book_path then return end
    
    self.character_notes = self.notes_manager:loadNotes(book_path)
    
    local items = {}
    local notes_count = 0
    
    for _, char in ipairs(self.characters) do
        local char_name = char.name or self.loc:t("unknown_character")
        local note = self.notes_manager:getNote(self.character_notes, char.name)
        if note then
            notes_count = notes_count + 1
            
            local note_preview = note.text or ""
            if #note_preview > 50 then
                note_preview = string.sub(note_preview, 1, 50) .. "..."
            end
            
            table.insert(items, {
                text = char_name .. "\n   " .. note_preview,
                callback = function()
                    self:showCharacterWithNote(char, note)
                end,
            })
        end
    end
    
    if notes_count > 0 then
        table.insert(items, {
            text = "---",
            separator = true,
        })
    end
    
    for _, char in ipairs(self.characters) do
        local char_name = char.name or self.loc:t("unknown_character")
        local note = self.notes_manager:getNote(self.character_notes, char.name)
        if not note then
            table.insert(items, {
                text = "➕ " .. char_name .. " (" .. self.loc:t("add_note") .. ")",
                callback = function()
                    self:addCharacterNote(char)
                end,
            })
        end
    end
    
    local menu = Menu:new{
        title = string.format(self.loc:t("character_notes_title"), notes_count),
        item_table = items,
        is_borderless = true,
        is_popout = false,
        title_bar_fm_style = true,
        width = Screen:getWidth(),
        height = Screen:getHeight(),
    }
    
    UIManager:show(menu)
end

function XRayPlugin:showCharacterWithNote(char, note)
    local InputDialog = require("ui/widget/inputdialog")
    
    local input_dialog
    input_dialog = InputDialog:new{
        title = char.name,
        input = note.text,
        input_hint = self.loc:t("note_hint"),
        buttons = {
            {
                {
                    text = self.loc:t("cancel"),
                    callback = function()
                        UIManager:close(input_dialog)
                    end,
                },
                {
                    text = self.loc:t("delete"),
                    callback = function()
                        self:deleteCharacterNote(char)
                        UIManager:close(input_dialog)
                    end,
                },
                {
                    text = self.loc:t("save"),
                    is_enter_default = true,
                    callback = function()
                        local new_note = input_dialog:getInputText()
                        self:updateCharacterNote(char, new_note)
                        UIManager:close(input_dialog)
                    end,
                },
            },
        },
    }
    
    UIManager:show(input_dialog)
    input_dialog:onShowKeyboard()
end

function XRayPlugin:addCharacterNote(char)
    local InputDialog = require("ui/widget/inputdialog")
    
    local input_dialog
    input_dialog = InputDialog:new{
        title = string.format(self.loc:t("add_note_title"), char.name),
        input = "",
        input_hint = self.loc:t("note_hint"),
        buttons = {
            {
                {
                    text = self.loc:t("cancel"),
                    callback = function()
                        UIManager:close(input_dialog)
                    end,
                },
                {
                    text = self.loc:t("save"),
                    is_enter_default = true,
                    callback = function()
                        local note_text = input_dialog:getInputText()
                        if note_text and #note_text > 0 then
                            self:updateCharacterNote(char, note_text)
                        end
                        UIManager:close(input_dialog)
                    end,
                },
            },
        },
    }
    
    UIManager:show(input_dialog)
    input_dialog:onShowKeyboard()
end

function XRayPlugin:updateCharacterNote(char, note_text)
    if not self.notes_manager then
        return
    end
    
    self.notes_manager:setNote(self.character_notes, char.name, note_text)
    
    local book_path = self:getBookPath()
    if not book_path then return end
    
    self.notes_manager:saveNotes(book_path, self.character_notes)
    
    UIManager:show(InfoMessage:new{
        text = string.format(self.loc:t("note_saved"), char.name),
        timeout = 2,
    })
end

function XRayPlugin:deleteCharacterNote(char)
    if not self.notes_manager then
        return
    end
    
    self.notes_manager:deleteNote(self.character_notes, char.name)
    
    local book_path = self:getBookPath()
    if not book_path then return end
    
    self.notes_manager:saveNotes(book_path, self.character_notes)
    
    UIManager:show(InfoMessage:new{
        text = string.format(self.loc:t("note_deleted"), char.name),
        timeout = 2,
    })
    
    self:showCharacterNotes()
end

function XRayPlugin:showQuickXRayMenu()
    self:syncCacheFromPartials()
    logger.info("XRayPlugin: showQuickXRayMenu called")
    
    local ButtonDialog = require("ui/widget/buttondialog")
    
    local buttons = {
        {
            {
                text = self.loc:t("menu_characters"),
                callback = function()
                    UIManager:close(self.quick_dialog)
                    self:showCharacters()
                end,
            },
        },
        {
            {
                text = self.loc:t("menu_chapter_characters"),
                callback = function()
                    UIManager:close(self.quick_dialog)
                    self:showChapterCharacters()
                end,
            },
        },
        {
            {
                text = self.loc:t("menu_timeline"),
                callback = function()
                    UIManager:close(self.quick_dialog)
                    self:showTimeline()
                end,
            },
        },
        {
            {
                text = self.loc:t("menu_historical_figures"),
                callback = function()
                    UIManager:close(self.quick_dialog)
                    self:showHistoricalFigures()
                end,
            },
        },
        {
            {
                text = self.loc:t("menu_character_notes"),
                callback = function()
                    UIManager:close(self.quick_dialog)
                    self:showCharacterNotes()
                end,
            },
        },
        {
            {
                text = self.loc:t("fetch_data"),
                callback = function()
                    UIManager:close(self.quick_dialog)
                    self:fetchFromAI()
                end,
            },
        },
    }
    
    self.quick_dialog = ButtonDialog:new{
        title = self.loc:t("quick_menu_title") .. (self.xray_data and self.xray_data.analysis_progress and " (" .. self.xray_data.analysis_progress .. "%)" or ""),
        buttons = buttons,
    }
    
    UIManager:show(self.quick_dialog)
end

function XRayPlugin:showCharacterSearch()
    if not self.characters or #self.characters == 0 then
        UIManager:show(InfoMessage:new{
            text = self.loc:t("no_character_data"),
            timeout = 3,
        })
        return
    end
    
    local InputDialog = require("ui/widget/inputdialog")
    local plugin = self
    
    local input_dialog
    input_dialog = InputDialog:new{
        title = self.loc:t("search_character_title"),
        input = "",
        input_hint = self.loc:t("search_hint"),
        buttons = {
            {
                {
                    text = self.loc:t("cancel"),
                    callback = function()
                        UIManager:close(input_dialog)
                    end,
                },
                {
                    text = self.loc:t("search_button"),
                    is_enter_default = true,
                    callback = function()
                        local search_text = input_dialog:getInputText()
                        UIManager:close(input_dialog)
                        
                        if search_text and #search_text > 0 then
                            local found_char = plugin:findCharacterByName(search_text)
                            if found_char then
                                plugin:showCharacterInfo(found_char)
                            else
                                UIManager:show(InfoMessage:new{
                                    text = string.format(self.loc:t("character_not_found"), search_text),
                                    timeout = 3,
                                })
                            end
                        end
                    end,
                },
            },
        },
    }
    
    UIManager:show(input_dialog)
    input_dialog:onShowKeyboard()
end

function XRayPlugin:showFullXRayMenu()
    local menu_items = {}
    self:addToMainMenu(menu_items)
    
    if menu_items.xray and menu_items.xray.sub_item_table then
        self.full_menu = Menu:new{
            title = self.loc:t("menu_xray"),
            item_table = menu_items.xray.sub_item_table,
            is_borderless = true,
            is_popout = false,
            title_bar_fm_style = true,
            width = Screen:getWidth(),
            height = Screen:getHeight(),
        }
        UIManager:show(self.full_menu)
    end
end




function XRayPlugin:syncCacheFromPartials()
    if not self.cache_manager then
        local CacheManager = require("cachemanager")
        self.cache_manager = CacheManager:new()
    end
    
    -- 1. Get current reading progress
    local current_page, total_pages, progress = self:getReadingProgress()
    if not progress then progress = 100 end -- Fallback
    
    local book_path = self:getBookPath()
    if not book_path then return end
    
    -- 2. Find nearest partial cache <= progress
    local partial = self.cache_manager:getNearestPartialCache(book_path, progress)
    
    if partial then
        -- OPTIMIZATION: If we are already running this version, do nothing
        if self.xray_data and self.xray_data.analysis_progress == partial.percent then
             return
        end

        local main_cache_time = 0
        local main_cache = self.cache_manager:getCachePath(book_path)
        local attr = require("libs/libkoreader-lfs").attributes(main_cache)
        if attr then
            main_cache_time = attr.modification
        end
        
        -- Logic Change: We ALWAYS overwrite if we found a "better" context-aware partial
        -- because the user might have jumped BACK. Even if main cache is "newer" (timestamp),
        -- it might contain spoilers (100% data) when we are at 20%.
        -- So we prioritize the partial if it matches our context better?
        -- Actually, if main cache exists and covers 100%, and we are at 20%, maybe we SHOULD show 100% data?
        -- User request: "without any ai request, the plugin should be able to load the nearest *%.json and rebuild xray_cache.lua"
        -- "scenario: read 40%, go back 20%, clicked X-ray... load nearest *%.json"
        -- This implies strictly loading the partial corresponding to current location, ignoring main cache's "completeness".
        -- Yes, "rebuild xray_cache.lua".
        
        logger.info("XRayPlugin: Syncing cache. Current:", progress, "%, Found partial:", partial.percent, "%")
        
        -- Parse content to ensure valid JSON before saving
        local json = require("json")
        local success, data = pcall(json.decode, partial.content)
        
        if success and data then
             -- Force analysis_progress to match the partial we found
             -- This ensures the data is consistent with the filename (e.g. 22%.json -> 22%)
             data.analysis_progress = partial.percent
             
             -- Save as main cache (Rewind/Contextualize)
             -- Only save if the data inside is actually different or if we want to force the view
             -- We just overwrite `xray_cache.lua` to be the 20% version.
             if self.cache_manager:saveCache(book_path, data) then
                 -- Reload
                 self:autoLoadCache()
             end
        end
    end
end

function XRayPlugin:onShowXRayMenu()
    self:syncCacheFromPartials()
    self:showQuickXRayMenu()
    self:showQuickXRayMenu()
    return true
end

function XRayPlugin:onXRayUploadSync()
    self:uploadXRayData()
    return true
end

function XRayPlugin:onXRayDownloadSync()
    self:downloadXRayData()
    return true
end

function XRayPlugin:showFullXRayMenu()
    self:syncCacheFromPartials()
    
    local sub_items = self:getXRaySubMenuItems()
    
    self.full_menu = Menu:new{
        title = self.loc:t("menu_xray"),
        item_table = sub_items,
        -- Remove explicit fullscreen dimensions to let it behave like a standard submenu
        -- is_borderless = true, -- Usually for full screen readers
        -- width = Screen:getWidth(),
        -- height = Screen:getHeight(),
    }
    UIManager:show(self.full_menu)
end

return XRayPlugin

