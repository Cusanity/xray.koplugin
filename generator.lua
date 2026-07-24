-- generator.lua
-- Device-side X-Ray Generator
--
-- Mirrors the pipeline of generator.py:
--   extract EPUB text (via KOReader xpointer API)
--   → build chunks
--   → call AI per chunk using the shared chunk_summary prompt
--   → merge results with MasterData
--   → write xray_data.json (same format as the Python generator)
--
-- Output is fully compatible with the progressive loading system in
-- cachemanager.lua and the entity-visibility logic in main.lua.

local logger      = require("logger")
local json        = require("json")
local UIManager   = require("ui/uimanager")
local lfs         = require("libs/libkoreader-lfs")
local DocSettings = require("docsettings")

-- ============================================================
-- MasterData: accumulate analysis results across all chunks
--
-- Output format mirrors master_data.py::MasterData.to_output_json so that
-- device-side generation is byte-compatible with generator.py results:
--   • character events carry {event, xref, anchor} (no percent)
--   • timeline entries carry {sequence, event, character, xref, anchor}
--   • descriptions are progressive [{percent, text}] entries
--   • themes are filtered against META_THEMES and capped at 8
--   • characters/locations are ordered by an importance score
-- ============================================================

-- Structural themes the AI sometimes emits — excluded from output to match
-- text_utils.py::META_THEMES.
local META_THEMES = {
    ["文本过渡"] = true, ["多重视角"] = true, ["叙事结构"] = true,
    ["文本结构"] = true, ["视角转换"] = true, ["章节划分"] = true,
    ["结构特征"] = true, ["叙事视角"] = true, ["文本特点"] = true,
    ["行文风格"] = true, ["写作手法"] = true, ["叙述方式"] = true,
}

-- Name affixes stripped to mirror text_utils.py::normalize_character_name.
local NAME_PREFIXES = {
    "后妈", "继母", "生母", "亲妈", "外婆", "奶奶", "爷爷", "外公",
}
local NAME_SUFFIXES = {
    "先生", "太太", "小姐", "女士", "夫人", "阁下", "律师", "医生",
    "教授", "老师", "博士", "神父", "牧师", "爸爸", "妈妈", "父亲",
    "母亲", "舅舅", "姨父", "姨妈", "叔叔", "阿姨", "姑姑", "姑父",
    "伯父", "伯母", "哥哥", "弟弟", "姐姐", "妹妹", "表哥", "表弟",
    "表姐", "表妹", "堂哥", "堂弟", "堂姐", "堂妹",
}

local function trim(s)
    if type(s) ~= "string" then return "" end
    return s:match("^%s*(.-)%s*$")
end

-- Normalize a character name: drop parenthetical content and known
-- title/relation affixes. Byte-safe for UTF-8 CJK. Mirrors the Python
-- normalize_character_name (t2s conversion is not available on-device).
local function normalizeCharName(name)
    name = trim(name)
    if name == "" then return "" end
    local original = name
    -- Remove parenthetical content: full-width （…） and half-width (…)
    name = trim(name:gsub("（.-）", ""):gsub("%b()", ""))
    if name == "" then name = original end

    for _, prefix in ipairs(NAME_PREFIXES) do
        if #name > #prefix and name:sub(1, #prefix) == prefix then
            local test = trim(name:sub(#prefix + 1))
            if test ~= "" then name = test end
            break
        end
    end
    for _, suffix in ipairs(NAME_SUFFIXES) do
        if #name > #suffix and name:sub(-#suffix) == suffix then
            local test = trim(name:sub(1, #name - #suffix))
            -- Do not strip if the remainder ends with 的 (possessive)
            if test ~= "" and test:sub(-3) ~= "的" then name = test end
            break
        end
    end
    return name ~= "" and name or original
end

-- Normalize a location name: unify hyphen variants. Mirrors
-- text_utils.py::normalize_location_name (minus t2s conversion).
local function normalizeLocName(name)
    name = trim(name)
    return (name:gsub("－", "-"):gsub("—", "-"):gsub("–", "-"))
end

-- Sort key for events: spine * 10_000_000 + offset, mirroring
-- master_data.py::_xref_sort_key. Falls back to 0 for legacy data.
local function xrefSortKey(evt)
    local xref = evt.xref
    if xref then
        return (xref.spine or 0) * 10000000 + (xref.offset or 0)
    end
    return math.floor((evt.percent or 0) * 1000)
end

-- Strip a trailing "(NN%)" / "（NN%）" marker the AI sometimes appends.
local function stripPercentMarker(text)
    return (text:gsub("%s*[%(（]%d+%%[%)）]%s*$", ""))
end

local MasterData = {}
MasterData.__index = MasterData

function MasterData:new(book_title, author)
    return setmetatable({
        book_title    = book_title or "",
        author        = author or "",
        author_bio    = "",
        -- [normalised_name] → {display_name, descriptions=[], events=[]}
        characters    = {},
        locations     = {},
        themes        = {},         -- {[theme_string] = true}
        summary_parts = {},
    }, self)
end

function MasterData:mergeChunk(chunk_data, end_pct)
    -- ── Characters ──────────────────────────────────────────
    for _, char in ipairs(chunk_data.characters or {}) do
        local name = normalizeCharName(char.name)
        if name ~= "" then
            local key = name   -- dedup key == display name (matches Python)
            if not self.characters[key] then
                self.characters[key] = { display_name = name, descriptions = {}, events = {} }
            end
            local desc = trim(char.description)
            if desc ~= "" then
                table.insert(self.characters[key].descriptions,
                             { percent = end_pct, text = desc })
            end
            for _, evt in ipairs(char.events or {}) do
                local evt_text = stripPercentMarker(trim(evt.event))
                if evt_text ~= "" then
                    local entry = { event = evt_text }
                    -- xref/anchor were populated by Generator:annotateEvents
                    if evt.xref then entry.xref = evt.xref end
                    local anchor = trim(evt.anchor)
                    if anchor ~= "" then entry.anchor = anchor end
                    table.insert(self.characters[key].events, entry)
                end
            end
        end
    end

    -- ── Locations ───────────────────────────────────────────
    for _, loc in ipairs(chunk_data.locations or {}) do
        local name = normalizeLocName(loc.name)
        if name ~= "" then
            local key = name
            if not self.locations[key] then
                self.locations[key] = { display_name = name, descriptions = {} }
            end
            local desc = trim(loc.description)
            if desc ~= "" then
                table.insert(self.locations[key].descriptions,
                             { percent = end_pct, text = desc })
            end
        end
    end

    -- ── Themes ──────────────────────────────────────────────
    for _, t in ipairs(chunk_data.themes or {}) do
        local theme = t
        if type(theme) == "table" then theme = theme.theme or theme.name or "" end
        theme = trim(theme)
        if theme ~= "" and not META_THEMES[theme] then self.themes[theme] = true end
    end

    -- ── Summary ─────────────────────────────────────────────
    local summary = chunk_data.summary or ""
    if type(summary) == "table" then
        summary = summary.description or summary.text or summary.summary or ""
    end
    summary = trim(summary)
    if summary ~= "" then
        table.insert(self.summary_parts, summary)
    end

    -- ── Metadata ────────────────────────────────────────────
    if type(chunk_data.book_title) == "string" and chunk_data.book_title ~= "" then
        self.book_title = chunk_data.book_title
    end
    if type(chunk_data.author) == "string" and chunk_data.author ~= "" then
        self.author = chunk_data.author
    end
    if type(chunk_data.author_bio) == "string" and chunk_data.author_bio ~= "" then
        self.author_bio = chunk_data.author_bio
    end
end

-- Restore master state from an existing xray_data.json checkpoint
function MasterData:restoreFromCheckpoint(data)
    for _, char in ipairs(data.characters or {}) do
        local name = type(char.name) == "string" and char.name or ""
        if name ~= "" then
            self.characters[name] = {
                display_name = name,
                descriptions = char.descriptions or {},
                events       = char.events or {},
            }
        end
    end
    for _, loc in ipairs(data.locations or {}) do
        local name = type(loc.name) == "string" and loc.name or ""
        if name ~= "" then
            self.locations[name] = {
                display_name = name,
                descriptions = loc.descriptions or {},
            }
        end
    end
    for _, theme in ipairs(data.themes or {}) do
        if type(theme) == "string" and theme ~= "" and not META_THEMES[theme] then
            self.themes[theme] = true
        end
    end
    if data.summary and data.summary ~= "" then
        table.insert(self.summary_parts, data.summary)
    end
    if data.book_title and data.book_title ~= "" then self.book_title = data.book_title end
    if data.author    and data.author    ~= "" then self.author     = data.author    end
    if data.author_bio and data.author_bio ~= "" then self.author_bio = data.author_bio end
end

-- Importance score mirroring master_data.py::score_importance: total
-- description text length plus a bonus per historic entry.
local function scoreImportance(descriptions)
    local total = 0
    for _, d in ipairs(descriptions) do total = total + #(d.text or "") end
    return total + #descriptions * 50
end

function MasterData:toOutputJSON(progress_pct)
    -- ── Characters ──────────────────────────────────────────
    local characters = {}
    for _, data in pairs(self.characters) do
        -- Deduplicate description entries by exact text (matches Python)
        local seen, deduped = {}, {}
        for _, d in ipairs(data.descriptions) do
            local k = d.text or ""
            if not seen[k] then seen[k] = true; table.insert(deduped, d) end
        end
        local events = {}
        for _, e in ipairs(data.events) do table.insert(events, e) end
        table.sort(events, function(a, b) return xrefSortKey(a) < xrefSortKey(b) end)
        table.insert(characters, {
            name         = data.display_name,
            descriptions = deduped,
            events       = events,
            _score       = scoreImportance(deduped),
        })
    end
    table.sort(characters, function(a, b) return a._score > b._score end)
    for _, c in ipairs(characters) do c._score = nil end

    -- ── Locations ───────────────────────────────────────────
    local locations = {}
    for _, data in pairs(self.locations) do
        local seen, deduped = {}, {}
        for _, d in ipairs(data.descriptions) do
            local k = d.text or ""
            if not seen[k] then seen[k] = true; table.insert(deduped, d) end
        end
        table.insert(locations, {
            name = data.display_name, descriptions = deduped, _score = scoreImportance(deduped),
        })
    end
    table.sort(locations, function(a, b) return a._score > b._score end)
    for _, l in ipairs(locations) do l._score = nil end

    -- ── Themes (filtered + capped at 8) ─────────────────────
    local themes = {}
    for theme, _ in pairs(self.themes) do
        if not META_THEMES[theme] then table.insert(themes, theme) end
    end
    table.sort(themes)
    while #themes > 8 do table.remove(themes) end

    -- ── Timeline (built from character events, sequenced) ────
    local all_events = {}
    for _, data in pairs(self.characters) do
        for _, evt in ipairs(data.events) do
            local text = trim(evt.event)
            if text ~= "" then
                local entry = { event = text, character = data.display_name }
                if evt.xref then entry.xref = evt.xref end
                if evt.anchor then entry.anchor = evt.anchor end
                table.insert(all_events, entry)
            end
        end
    end
    table.sort(all_events, function(a, b) return xrefSortKey(a) < xrefSortKey(b) end)

    local timeline = {}
    for i, evt in ipairs(all_events) do
        local entry = { sequence = i, event = evt.event, character = evt.character }
        if evt.xref then entry.xref = evt.xref end
        if evt.anchor then entry.anchor = evt.anchor end
        table.insert(timeline, entry)
    end

    -- ── Summary ─────────────────────────────────────────────
    local summary = table.concat(self.summary_parts, " ")

    return {
        book_title        = self.book_title,
        author            = self.author,
        author_bio        = self.author_bio or "",
        summary           = summary,
        characters        = characters,
        locations         = locations,
        themes            = themes,
        timeline          = timeline,
        analysis_progress = progress_pct,
    }
end

-- Current accumulation stats, mirroring master_data.py::get_stats — used to
-- surface live counts (characters / locations / events / themes) in the UI.
function MasterData:getStats()
    local n_chars, n_events = 0, 0
    for _, data in pairs(self.characters) do
        n_chars = n_chars + 1
        n_events = n_events + #data.events
    end
    local n_locs = 0
    for _ in pairs(self.locations) do n_locs = n_locs + 1 end
    local n_themes = 0
    for _ in pairs(self.themes) do n_themes = n_themes + 1 end
    return {
        characters = n_chars,
        locations  = n_locs,
        events     = n_events,
        themes     = n_themes,
    }
end

-- ============================================================
-- Generator
-- ============================================================

local Generator = {}

Generator.CHUNK_SIZE = 12000   -- conservative for on-device (≈12k chars ≈ 6–8 pages)
Generator.MAX_RETRIES = 2

-- ── Helpers ─────────────────────────────────────────────────

-- XPointer for CREngine spine fragment N (0-based).
-- Always uses explicit [index] — the bare "/body/DocFragment" (no [1]) is not
-- accepted by all CREngine builds as a valid xpointer.
local function fragXP(n)
    return "/body/DocFragment[" .. (n + 1) .. "]"
end

-- pcall wrapper around getPageFromXPointer so non-CRE documents (PDF, DjVu)
-- or CRE API mismatches don't crash the scheduled callback silently.
local function safeGetPage(doc, xp)
    local ok, result = pcall(function() return doc:getPageFromXPointer(xp) end)
    if ok and type(result) == "number" then return result end
    return nil
end

-- ── Text Extraction ──────────────────────────────────────────

-- Read one file from inside the EPUB archive via CREngine's built-in zip reader.
local function readEpubFile(doc, path)
    local ok, content = pcall(function()
        return doc:getDocumentFileContent(path)
    end)
    return (ok and type(content) == "string" and #content > 0) and content or nil
end

-- Parse the OPF spine once and return an array (1-based) of archive-relative
-- HTML paths in spine order. Returns nil when the document is not an EPUB.
local function parseEpubSpine(doc)
    local container = readEpubFile(doc, "META-INF/container.xml")
    if not container then return nil end

    local opf_path = container:match('full%-path%s*=%s*"([^"]+)"')
                  or container:match("full%-path%s*=%s*'([^']+)'")
    if not opf_path then
        logger.warn("Generator: cannot find OPF path in container.xml")
        return nil
    end
    logger.info("Generator: OPF path:", opf_path)

    local opf = readEpubFile(doc, opf_path)
    if not opf then
        logger.warn("Generator: cannot read OPF:", opf_path)
        return nil
    end

    -- Base directory for resolving relative hrefs inside the OPF
    local opf_dir = opf_path:match("^(.*/)") or ""

    -- Build manifest id → absolute-in-archive href map
    local manifest = {}
    for attrs in opf:gmatch("<item%s+([^>]+)>") do
        local id   = attrs:match('id%s*=%s*"([^"]*)"') or attrs:match("id%s*=%s*'([^']*)'")
        local href = attrs:match('href%s*=%s*"([^"]*)"') or attrs:match("href%s*=%s*'([^']*)'")
        if id and href then
            href = (href:match("^([^#]+)") or href)  -- strip URL fragment
            href = href:gsub("%%(%x%x)", function(h) return string.char(tonumber(h, 16)) end)
            manifest[id] = opf_dir .. href
        end
    end

    local spine_section = opf:match("<spine[^>]*>(.-)</spine>")
    if not spine_section then
        logger.warn("Generator: no <spine> in OPF")
        return nil
    end

    local items = {}
    for idref in spine_section:gmatch('idref%s*=%s*"([^"]*)"') do
        -- false = known spine slot with no resolvable href (keeps index aligned)
        table.insert(items, manifest[idref] or false)
    end
    logger.info("Generator: EPUB spine has", #items, "items")
    return items
end

-- Strip HTML tags and decode common entities; UTF-8 safe (all ops are ASCII).
local function htmlToText(html)
    if not html then return "" end
    html = html:gsub("<%s*[Hh][Ee][Aa][Dd]%s*>(.-)<%s*/%s*[Hh][Ee][Aa][Dd]%s*>", " ")
    html = html:gsub("<%s*[Ss][Cc][Rr][Ii][Pp][Tt][^>]*>(.-)<%s*/%s*[Ss][Cc][Rr][Ii][Pp][Tt]%s*>", " ")
    html = html:gsub("<%s*[Ss][Tt][Yy][Ll][Ee][^>]*>(.-)<%s*/%s*[Ss][Tt][Yy][Ll][Ee]%s*>", " ")
    html = html:gsub("</?[Pp][^>]*>",      "\n")
    html = html:gsub("</?[Hh][1-6][^>]*>", "\n")
    html = html:gsub("<[Bb][Rr][^>]*>",    "\n")
    html = html:gsub("</?[Ll][Ii][^>]*>",  "\n")
    html = html:gsub("</?[Dd][Ii][Vv][^>]*>", "\n")
    html = html:gsub("<[^>]*>", "")
    html = html:gsub("&nbsp;",  " "):gsub("&lt;", "<"):gsub("&gt;", ">")
               :gsub("&amp;",  "&"):gsub("&quot;", '"'):gsub("&apos;", "'")
    html = html:gsub("&#(%d+);",  function(n) local c = tonumber(n)
        return (c and c >= 32 and c <= 126) and string.char(c) or "" end)
    html = html:gsub("&#x(%x+);", function(h) local c = tonumber(h, 16)
        return (c and c >= 32 and c <= 126) and string.char(c) or "" end)
    html = html:gsub("\r\n", "\n"):gsub("\r", "\n"):gsub("[ \t]+", " "):gsub("\n\n\n+", "\n\n")
    return html:match("^%s*(.-)%s*$") or ""
end

-- Last-resort CRE xpointer extraction (works for FB2 and some EPUB builds).
local function getSpineTextCRE(doc, spine_idx)
    local xp1 = fragXP(spine_idx)
    local xp2 = fragXP(spine_idx + 1)
    local ok, text = pcall(function()
        return doc:getTextFromXPointers(xp1, xp2)
    end)
    if ok and type(text) == "string" and #text > 20 then
        text = text:gsub("\r\n", "\n"):gsub("\r", "\n"):gsub("\n\n\n+", "\n\n")
        return text
    end
    return nil
end

-- Primary strategy: direct EPUB HTML reading; fallback: CRE xpointer API.
function Generator:extractChapters(ui)
    local doc         = ui.document
    local total_pages = doc:getPageCount() or 0
    local toc         = doc:getToc() or {}
    logger.info("Generator: extractChapters total_pages=", total_pages, "toc_entries=", #toc)

    -- Parse the EPUB OPF once to get an ordered array of HTML file paths.
    -- Returns nil for non-EPUB formats (FB2, etc.), falling back to CRE API.
    local epub_spine = parseEpubSpine(doc)

    -- Map page → TOC title for chapter name lookup
    local page_title = {}
    for _, entry in ipairs(toc) do
        if entry.page then page_title[entry.page] = entry.title or "" end
    end

    local chapters   = {}
    local spine_idx  = 0

    while spine_idx < 1000 do
        local xp_cur  = fragXP(spine_idx)
        local xp_next = fragXP(spine_idx + 1)

        local cur_page  = safeGetPage(doc, xp_cur)
        if not cur_page or cur_page <= 0 then
            logger.warn("Generator: spine", spine_idx, "xpointer", xp_cur,
                        "returned", tostring(cur_page))
            if spine_idx < 3 then
                -- Some CRE builds return 0/nil for the first few fragments;
                -- try a few more before giving up.
                spine_idx = spine_idx + 1
                goto continue
            else
                break
            end
        end

        local next_page = safeGetPage(doc, xp_next)
        local is_last   = (not next_page) or (next_page <= cur_page)
        local end_page  = is_last and total_pages or (next_page - 1)

        -- Determine chapter title (nearest TOC entry in this spine range)
        local title = ""
        for pg = cur_page, end_page do
            if page_title[pg] and page_title[pg] ~= "" then
                title = page_title[pg]
                break
            end
        end
        if title == "" then title = "Chapter " .. (spine_idx + 1) end

        -- Strategy 1: read HTML directly from the EPUB archive and strip tags.
        -- This is the most reliable approach and works regardless of CRE rendering.
        local text = nil
        local epub_href = epub_spine and epub_spine[spine_idx + 1]
        if epub_href then
            local html = readEpubFile(doc, epub_href)
            if html then
                local t = htmlToText(html)
                if t and #t > 20 then text = t end
            end
            if not text then
                logger.warn("Generator: spine", spine_idx, "direct HTML read empty for", epub_href)
            end
        end

        -- Strategy 2: CRE xpointer API (FB2 and other non-EPUB CRE formats).
        if not text then
            text = getSpineTextCRE(doc, spine_idx)
        end

        if text and #text > 50 then
            logger.info("Generator: spine", spine_idx, "'", title, "' text_len=", #text)
            table.insert(chapters, { title = title, text = text, spine = spine_idx })
        else
            logger.warn("Generator: spine", spine_idx, "'", title,
                        "' skipped — text_len=", text and #text or 0)
        end

        if is_last then break end
        spine_idx = spine_idx + 1

        ::continue::
    end

    return chapters
end

-- ── Chunk Building ───────────────────────────────────────────

-- Build chunks from chapters, each ≤ max_size chars.
-- Mirrors generator.py::build_chunks: every chunk is annotated with the
-- spine_ranges needed to map an in-chunk text offset back to an exact EPUB
-- spine item and character offset (for xref generation), plus abs_start/
-- abs_end (absolute char positions in the concatenated book) and end_pct.
function Generator:buildChunks(chapters, total_chars, max_size)
    max_size = max_size or self.CHUNK_SIZE
    local chunks      = {}
    local cur_titles  = {}
    local cur_text    = ""
    local cur_ranges  = {}
    local chars_done  = 0

    local function flush()
        if cur_text ~= "" then
            table.insert(chunks, {
                titles       = cur_titles,
                text         = cur_text:match("^%s*(.-)%s*$"),
                end_pos      = chars_done,
                spine_ranges = cur_ranges,
            })
            cur_titles = {}
            cur_text   = ""
            cur_ranges = {}
        end
    end

    for _, ch in ipairs(chapters) do
        local spine = ch.spine or 0
        local body  = ch.text
        local clen  = #body
        local abs_chapter_start = chars_done

        if clen > max_size then
            flush()
            local seg = 0
            local pos = 1
            while pos <= clen do
                local fin = math.min(pos + max_size - 1, clen)
                -- Try to break on a newline near the cut point
                if fin < clen then
                    local nl = body:find("\n", math.max(pos, fin - 300), true)
                    if nl and nl < fin then fin = nl end
                end
                local seg_title = seg == 0 and ch.title
                                  or (ch.title .. "（续" .. seg .. "）")
                local header    = "【" .. seg_title .. "】"
                local seg_body  = body:sub(pos, fin)
                local seg_abs_start = abs_chapter_start + (pos - 1)
                local seg_abs_end   = abs_chapter_start + fin
                chars_done      = chars_done + (fin - pos + 1)
                table.insert(chunks, {
                    titles       = { seg_title },
                    text         = (header .. "\n" .. seg_body .. "\n\n"):match("^%s*(.-)%s*$"),
                    end_pos      = chars_done,
                    spine_ranges = { {
                        spine             = spine,
                        abs_start         = seg_abs_start,
                        abs_end           = seg_abs_end,
                        chapter_abs_start = abs_chapter_start,
                        chapter_len       = clen,
                        -- 0-based offset in chunk text where seg_body begins
                        chunk_text_start  = #header + 1,
                    } },
                })
                seg = seg + 1
                pos = fin + 1
            end
        else
            local hdr      = "【" .. ch.title .. "】\n"
            local with_hdr = hdr .. body .. "\n\n"
            if cur_text ~= "" and #cur_text + #with_hdr > max_size then
                flush()
            end
            table.insert(cur_titles, ch.title)
            cur_text   = cur_text .. with_hdr
            table.insert(cur_ranges, {
                spine             = spine,
                abs_start         = abs_chapter_start,
                abs_end           = abs_chapter_start + clen,
                chapter_abs_start = abs_chapter_start,
                chapter_len       = clen,
                -- 0-based offset in the accumulating chunk where body begins
                chunk_text_start  = #cur_text - clen - 2,
            })
            chars_done = chars_done + clen
        end
    end
    flush()

    -- Annotate with abs_start (previous chunk's end) and end percentage.
    local prev_end = 0
    for _, chunk in ipairs(chunks) do
        chunk.abs_start = prev_end
        chunk.abs_end   = chunk.end_pos
        chunk.end_pct = total_chars > 0
            and math.min(100, math.ceil(chunk.end_pos * 100 / total_chars))
            or 100
        prev_end = chunk.end_pos
    end

    return chunks
end

-- ── Event xref annotation ────────────────────────────────────

-- Port of generator.py::_compute_xref — map a relative-percent position
-- within a chunk to a spine xref (mid-chunk fallback when no anchor matches).
local function computeXref(rel_pct, abs_start, abs_end, spine_ranges)
    local chunk_len = abs_end - abs_start
    if chunk_len <= 0 or not spine_ranges or #spine_ranges == 0 then return nil end
    local target_abs = abs_start + (rel_pct / 100.0) * chunk_len
    for i, sr in ipairs(spine_ranges) do
        if target_abs <= sr.abs_end or i == #spine_ranges then
            local chapter_origin = sr.chapter_abs_start or sr.abs_start
            local raw_offset = target_abs - chapter_origin
            local offset = math.max(0, math.min(math.floor(raw_offset + 0.5), sr.chapter_len))
            return { spine = sr.spine, offset = offset, chapter_len = sr.chapter_len }
        end
    end
    return nil
end

-- Port of generator.py::_annotate_events. Locates each event's verbatim
-- anchor inside the chunk text to derive an exact {spine, offset, chapter_len}
-- xref; falls back to a mid-chunk xref when the anchor is missing or not found.
function Generator:annotateEvents(chunk_data, chunk)
    local text         = chunk.text or ""
    local spine_ranges = chunk.spine_ranges
    local abs_start    = chunk.abs_start or 0
    local abs_end      = chunk.abs_end or 0

    for _, char in ipairs(chunk_data.characters or {}) do
        for _, evt in ipairs(char.events or {}) do
            local anchor = type(evt.anchor) == "string"
                           and evt.anchor:match("^%s*(.-)%s*$") or ""
            local found  = (anchor ~= "" and text:find(anchor, 1, true)) or nil

            if found and spine_ranges and #spine_ranges > 0 then
                local pos0 = found - 1   -- 0-based offset into chunk text
                local sr   = spine_ranges[#spine_ranges]
                for _, cand in ipairs(spine_ranges) do
                    local ct_start = cand.chunk_text_start or 0
                    local ct_end   = ct_start + (cand.abs_end - cand.abs_start)
                    if pos0 < ct_end then sr = cand; break end
                end
                local ct_start     = sr.chunk_text_start or 0
                local within_slice = math.max(0, pos0 - ct_start)
                local chapter_off  = (sr.abs_start - (sr.chapter_abs_start or sr.abs_start))
                                     + within_slice
                if chapter_off > sr.chapter_len then chapter_off = sr.chapter_len end
                evt.xref = { spine = sr.spine, offset = chapter_off, chapter_len = sr.chapter_len }
            elseif spine_ranges and abs_end > abs_start then
                evt.xref = computeXref(50.0, abs_start, abs_end, spine_ranges)
            end
        end
    end
end

-- ── AI Call ──────────────────────────────────────────────────

-- Call AI and return parsed JSON table for a chunk.
-- Uses AIHelper:callAIRaw to get raw text, then parses it here.
function Generator:callAIForChunk(prompt, system_prompt, ai_config)
    local AIHelper = require("aihelper")

    local raw, err_code, err_msg = AIHelper:callAIRaw(prompt, system_prompt, ai_config)
    if not raw then
        return nil, err_code or "error_call", err_msg
    end

    -- Strip markdown fences and whitespace
    local clean = raw:gsub("```json", ""):gsub("```", ""):match("^%s*(.-)%s*$")

    -- First parse attempt
    local ok, data = pcall(json.decode, clean)
    if ok and type(data) == "table" then return data end

    -- Second attempt: extract the first complete JSON object
    local s = clean:find("{")
    local e = nil
    for i = #clean, 1, -1 do
        if clean:sub(i, i) == "}" then e = i; break end
    end
    if s and e and e > s then
        local ok2, data2 = pcall(json.decode, clean:sub(s, e))
        if ok2 and type(data2) == "table" then return data2 end
    end

    return nil, "error_json", "JSON parse failed: " .. clean:sub(1, 120)
end

-- ── File I/O ─────────────────────────────────────────────────

function Generator:getOutputDir(book_path)
    return DocSettings:getSidecarDir(book_path) .. "/xray_analysis"
end

function Generator:saveProgress(master, output_dir, progress_pct, chunk_info)
    if not lfs.attributes(output_dir) then
        lfs.mkdir(output_dir)
    end
    local data = master:toOutputJSON(progress_pct)
    if chunk_info then
        data.completed_chunks = chunk_info.completed
        data.total_chunks     = chunk_info.total
    end
    local encoded = json.encode(data)
    local path    = output_dir .. "/xray_data.json"
    local f       = io.open(path, "w")
    if not f then
        logger.warn("Generator: Cannot write to", path)
        return false
    end
    f:write(encoded)
    f:close()
    return true
end

function Generator:loadCheckpoint(output_dir)
    local path = output_dir .. "/xray_data.json"
    local f    = io.open(path, "r")
    if not f then return nil end
    local content = f:read("*a")
    f:close()
    local ok, data = pcall(json.decode, content)
    return (ok and type(data) == "table") and data or nil
end

-- ── Main Entry Point ─────────────────────────────────────────

-- generate(ui, ai_config, callbacks)
--
-- ai_config: {
--   type    = "gemini"|"chatgpt"|"local",
--   api_key = "...",
--   model   = "...",
--   endpoint= "..."   (for chatgpt/local)
-- }
--
-- callbacks: {
--   on_progress = function(chunk_idx, total_chunks, pct, chapter_label)
--   on_complete = function(success, err_msg)
--   on_abort    = function()  →  return true to cancel
--   on_pause    = function()  →  return true to pause (reschedule without advancing)
-- }
function Generator:generate(ui, ai_config, callbacks)
    callbacks = callbacks or {}
    local on_progress = callbacks.on_progress or function() end
    local on_complete = callbacks.on_complete or function() end
    local on_abort    = callbacks.on_abort    or function() return false end
    local on_pause    = callbacks.on_pause

    -- ── Validate ────────────────────────────────────────────
    if not ui or not ui.document then
        on_complete(false, "No document open")
        return
    end
    local book_path = ui.document.file
    if not book_path then
        on_complete(false, "Cannot determine book path")
        return
    end

    -- ── Prompts ─────────────────────────────────────────────
    local ok_p, prompts = pcall(require, "prompts/zh")
    if not ok_p or not prompts or not prompts.chunk_summary then
        on_complete(false, "Cannot load chunk_summary prompt from prompts/zh")
        return
    end
    local chunk_prompt  = prompts.chunk_summary
    local system_prompt = prompts.system_instruction or ""

    -- ── Book metadata ────────────────────────────────────────
    local title, author = "", ""
    local ok_m, props = pcall(function() return ui.document:getDocumentProps() end)
    if ok_m and props then
        title  = props.title   or props.Title  or ""
        author = props.authors or props.author or props.Author or ""
    end
    if title == "" then
        title = book_path:match("([^/\\]+)%.[^.]+$") or "Unknown Title"
    end

    -- ── Output dir ───────────────────────────────────────────
    local output_dir = self:getOutputDir(book_path)

    -- Check for already-complete data
    local checkpoint = self:loadCheckpoint(output_dir)
    if checkpoint and (checkpoint.analysis_progress or 0) >= 100 then
        on_complete(true, "Already complete")
        return
    end

    on_progress(0, 1, 0, "Extracting text…")

    -- Defer heavy extraction to next tick so UI can paint the message
    UIManager:scheduleIn(0.15, function()
        if on_abort() then on_complete(false, "Aborted"); return end

        -- ── Extract ─────────────────────────────────────────
        local chapters = self:extractChapters(ui)
        if not chapters or #chapters == 0 then
            on_complete(false, "Could not extract text from this book")
            return
        end

        local total_chars = 0
        for _, ch in ipairs(chapters) do total_chars = total_chars + #ch.text end
        if total_chars == 0 then
            on_complete(false, "Book has no extractable text")
            return
        end

        logger.info("Generator: Extracted", #chapters, "chapters,", total_chars, "chars")

        -- ── Chunk ────────────────────────────────────────────
        local chunks       = self:buildChunks(chapters, total_chars)
        local total_chunks = #chunks
        logger.info("Generator: Built", total_chunks, "chunks")

        if total_chunks == 0 then
            on_complete(false, "No text chunks to process")
            return
        end

        -- ── Master data (restore checkpoint if available) ────
        local master      = MasterData:new(title, author)
        local start_chunk = 1

        if checkpoint and (checkpoint.analysis_progress or 0) > 0 then
            master:restoreFromCheckpoint(checkpoint)
            local resume_pct = checkpoint.analysis_progress
            -- Advance past already-completed chunks
            for i, chunk in ipairs(chunks) do
                if (chunk.end_pct or 0) > resume_pct then
                    start_chunk = i
                    break
                end
            end
            -- If all chunks already done per percent
            if start_chunk == 1 and (chunks[1] and (chunks[1].end_pct or 0) <= resume_pct) then
                start_chunk = total_chunks + 1
            end
            logger.info("Generator: Resuming from chunk", start_chunk, "/", total_chunks)
        end

        -- Ensure output dir exists
        if not lfs.attributes(output_dir) then lfs.mkdir(output_dir) end

        -- ── Sequential async processing ──────────────────────
        local chunk_idx = start_chunk

        local function processNext()
            if on_abort() then
                -- Save whatever we have before exiting
                if chunk_idx > start_chunk then
                    local prev_pct = chunk_idx > 1
                        and (chunks[chunk_idx - 1] and chunks[chunk_idx - 1].end_pct or 0)
                        or 0
                    self:saveProgress(master, output_dir, prev_pct,
                        { completed = chunk_idx - 1, total = total_chunks })
                end
                on_complete(false, "Aborted by user")
                return
            end

            -- Pause: reschedule without advancing chunk_idx
            if on_pause and on_pause() then
                UIManager:scheduleIn(0.5, processNext)
                return
            end

            if chunk_idx > total_chunks then
                self:saveProgress(master, output_dir, 100,
                    { completed = total_chunks, total = total_chunks })
                on_complete(true, nil)
                return
            end

            local chunk       = chunks[chunk_idx]
            local end_pct     = chunk.end_pct or math.ceil(chunk_idx * 100 / total_chunks)
            local chapter_lbl = table.concat(chunk.titles, " → ")

            -- Report "analyzing" state with the stats accumulated so far.
            on_progress(chunk_idx, total_chunks, end_pct, chapter_lbl, {
                op    = "analyzing",
                stats = master:getStats(),
            })

            -- Build prompt: chunk_summary = (title, author, pct%, text)
            local prompt = string.format(chunk_prompt, title, author, end_pct, chunk.text)

            local data, err_code, err_msg = self:callAIForChunk(prompt, system_prompt, ai_config)

            if data then
                -- Locate event anchors → xref before merging (parity w/ Python)
                self:annotateEvents(data, chunk)
                master:mergeChunk(data, end_pct)
                self:saveProgress(master, output_dir, end_pct,
                    { completed = chunk_idx, total = total_chunks })
                -- Report "merged" state with the freshly updated stats.
                on_progress(chunk_idx, total_chunks, end_pct, chapter_lbl, {
                    op    = "merged",
                    stats = master:getStats(),
                })
            else
                logger.warn("Generator: Chunk", chunk_idx, "failed:",
                            err_code or "?", err_msg or "")
                -- Non-fatal: skip this chunk and continue
            end

            chunk_idx = chunk_idx + 1
            UIManager:scheduleIn(0.05, processNext)
        end

        UIManager:scheduleIn(0.05, processNext)
    end)
end

return Generator
