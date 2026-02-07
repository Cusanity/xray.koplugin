// Message Transformation System
// Transforms ALL technical messages into user-friendly notifications

function transformTechnicalMessage(message) {
    let friendly = message;

    // Remove emoji prefixes
    friendly = friendly.replace(/^[✅❌⚠️🔄📝🔌✓✗💬📖]\s*/, '');

    // Character/Location Updates
    // "[Char] 父亲 updated" → "Character '父亲' profile updated"
    const charMatch = friendly.match(/\[Char\]\s*(.+?)\s+updated/i);
    if (charMatch && charMatch[1]) {
        return `Character '${charMatch[1].trim()}' profile updated`;
    }

    const locMatch = friendly.match(/\[Loc\]\s*(.+?)\s+updated/i);
    if (locMatch && locMatch[1]) {
        return `Location '${locMatch[1].trim()}' details updated`;
    }

    // Merge Messages with Chinese Titles
    // "=== Merging Chunk 5/10: 《第二章...》 ===" → "Analyzing chapter 5 of 10"
    const mergeMatch = friendly.match(/===\s*merging chunk (\d+)\/(\d+):\s*《[^》]+》\s*===/i);
    if (mergeMatch) {
        return `Analyzing chapter ${mergeMatch[1]} of ${mergeMatch[2]}`;
    }

    // AI Request Details
    // "[Chunk 5] AI Request sent... (15234 chars)" → "Requesting AI analysis for part 5"
    const aiRequestMatch = friendly.match(/\[Chunk (\d+)\]\s*AI Request sent.*?\((\d+) chars\)/i);
    if (aiRequestMatch) {
        return `Requesting AI analysis for part ${aiRequestMatch[1]}`;
    }

    // Merge Statistics
    // "[Merged] Chars: 25, Locs: 12, Events: 8" → "Discovered 25 characters, 12 locations, 8 events"
    const statsMatch = friendly.match(/\[Merged\]\s*Chars:\s*(\d+),\s*Locs:\s*(\d+),\s*Events:\s*(\d+)/i);
    if (statsMatch) {
        return `Discovered ${statsMatch[1]} characters, ${statsMatch[2]} locations, ${statsMatch[3]} events`;
    }

    // Consolidation Processing
    if (friendly.match(/\[Consolidation\].*processing/i)) {
        return 'Organizing discovered information';
    }

    const parallelMatch = friendly.match(/parallel processing:\s*(\d+)\s*chars?,\s*(\d+)\s*locs?/i);
    if (parallelMatch) {
        return `Processing ${parallelMatch[1]} characters and ${parallelMatch[2]} locations`;
    }

    // Checkpoint Saves
    if (friendly.match(/saved.*checkpoint/i)) {
        const pctMatch = friendly.match(/(\d+)%/);
        return pctMatch ? `Progress saved at ${pctMatch[1]}%` : 'Progress saved';
    }

    if (friendly.match(/saved.*\.json/i)) {
        const pctMatch = friendly.match(/(\d+)%\.json/);
        return pctMatch ? `Progress saved at ${pctMatch[1]}%` : 'Progress saved';
    }

    // Cache Hits
    const cacheMatch = friendly.match(/\[Chunk (\d+)\].*cached/i);
    if (cacheMatch) {
        return `Using previous analysis for part ${cacheMatch[1]}`;
    } else if (friendly.match(/cached/i)) {
        return 'Using previous analysis (faster!)';
    }

    // Simple replacements
    friendly = friendly
        .replace(/processing chunk (\d+)\/(\d+)/gi, 'Analyzing part $1 of $2')
        .replace(/chunk (\d+)\/(\d+)/gi, 'Part $1 of $2')
        .replace(/Extracting text from EPUB/gi, 'Reading book content')
        .replace(/Parsing EPUB structure/gi, 'Understanding book structure')
        .replace(/Reading EPUB/gi, 'Opening book')
        .replace(/Processing EPUB/gi, 'Analyzing book')
        .replace(/AI request failed/gi, 'AI temporarily unavailable')
        .replace(/Retrying AI request/gi, 'Reconnecting to AI')
        .replace(/AI Error/gi, 'AI connection issue')
        .replace(/Received AI response/gi, 'AI analysis complete')
        .replace(/Generating X-Ray data/gi, 'Creating character insights')
        .replace(/Consolidating entities/gi, 'Organizing information')
        .replace(/Merging data/gi, 'Combining results')
        .replace(/WebSocket/gi, 'connection')
        .replace(/HTTP/gi, 'network')
        .replace(/JSON/gi, 'data')
        .replace(/API/gi, 'service')
        .replace(/Fatal error/gi, 'Critical issue')
        .replace(/Process termination/gi, 'Stopping')
        .replace(/\b(\d+)\/(\d+)\b/g, '$1 of $2')
        .replace(/\bpct\b/gi, '%')
        .replace(/\bsecs?\b/gi, 'seconds')
        .replace(/\bmins?\b/gi, 'minutes')
        .replace(/\bchars?\b/gi, 'characters')
        .replace(/===+/g, '')
        .replace(/\[Chunk \d+\]/g, '')
        .replace(/\[System\]/g, '')
        .replace(/\[Info\]/g, '')
        .replace(/\[Consolidation\]/g, '')
        .replace(/\s+/g, ' ')
        .trim();

    // Capitalize first letter
    if (friendly.length > 0 && !friendly.match(/^[🎉✨💫]/)) {
        friendly = friendly.charAt(0).toUpperCase() + friendly.slice(1);
    }

    return friendly;
}

console.log('Message transformation system loaded');
