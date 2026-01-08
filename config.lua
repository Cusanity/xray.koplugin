-- X-Ray Plugin Configuration
-- Copy this file to config.lua and fill in your API keys

return {
    -- Google Gemini API Key
    -- Get your free API key at: https://makersuite.google.com/app/apikey
    gemini_api_key = "",
    
    -- Gemini Model Selection
    -- Options: "gemini-flash-lite-latest", "gemini-2.5-pro", "gemini-3.0-preview"
    gemini_model = "gemini-flash-lite-latest",
    
    -- ChatGPT API Key 
    -- Get API key at: https://platform.openai.com/api-keys
    chatgpt_api_key = "",
    
    -- Local AI Settings (OpenAI-compatible API)
    local_endpoint = "http://localhost:8080/v1/chat/completions",
    local_model = "your-model-name",
    local_api_key = "",
    
    -- Default AI Provider: "gemini", "chatgpt", or "local"
    default_provider = "gemini",
}
