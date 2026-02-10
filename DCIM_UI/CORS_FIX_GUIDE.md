# CORS Fix Guide for Infrastructure Generator

## What Changed?

The infrastructure generator now calls the Nvidia AI API **directly from your browser** - no backend server needed!

## CORS Issue & Solutions

### What is CORS?
Browsers block API calls to external domains for security. This is called CORS (Cross-Origin Resource Sharing).

### Quick Fixes

#### Option 1: Install CORS Extension (Easiest)

**Chrome/Edge:**
1. Go to Chrome Web Store
2. Search for "Allow CORS: Access-Control-Allow-Origin"
3. Install the extension
4. Click the extension icon to enable it
5. Refresh the page

**Firefox:**
1. Go to Firefox Add-ons
2. Search for "CORS Everywhere"
3. Install and enable
4. Refresh the page

#### Option 2: Use Brave Browser (Recommended for Developers)
Brave browser has built-in CORS bypass features for localhost development.

#### Option 3: Launch Chrome with Disabled Security (Temporary)

**Windows:**
```bash
"C:\Program Files\Google\Chrome\Application\chrome.exe" --disable-web-security --user-data-dir="C:\chrome-dev-session"
```

**macOS:**
```bash
open -na Google\ Chrome --args --disable-web-security --user-data-dir=/tmp/chrome-dev-session
```

**Linux:**
```bash
google-chrome --disable-web-security --user-data-dir=/tmp/chrome-dev-session
```

⚠️ **Warning**: Only use this Chrome instance for development, not regular browsing!

#### Option 4: Use Firefox Developer Edition
1. Download Firefox Developer Edition
2. Open `about:config`
3. Set `security.fileuri.strict_origin_policy` to `false`
4. Restart Firefox

## How It Works Now

```
Your Browser
    ↓
Nvidia API (Direct Call)
    ↓
AI Response with Terraform/Terragrunt
    ↓
Download Buttons
```

No proxy server needed! ✨

## Testing the Setup

1. Open: `http://localhost:5173/app/nl-query`
2. Enable CORS extension (if using one)
3. Type: "Create AWS S3 bucket with versioning"
4. Hit send
5. See generated configs in side panel!

## Troubleshooting

### "CORS error" appears
- ✅ Install CORS extension
- ✅ Or use Brave browser
- ✅ Or launch Chrome with `--disable-web-security`

### "API Error: 401"
- ❌ Check if API key is valid
- ✅ Should work with the embedded key

### No response from AI
- ✅ Check internet connection
- ✅ Verify Nvidia API is online
- ✅ Check browser console for errors

## Security Note

The API key is embedded in the frontend code. For production:
- Move API key to environment variables
- Implement rate limiting
- Add authentication
- Use a backend proxy

For now, this is fine for development and personal use!

## Benefits

✅ No backend server required
✅ Direct AI responses
✅ Faster (no proxy hop)
✅ Simpler architecture
✅ Easy to understand and modify

## Next Steps

1. Install CORS extension
2. Try generating some infrastructure!
3. Download and use the Terraform files

Happy Infrastructure Coding! 🎉
