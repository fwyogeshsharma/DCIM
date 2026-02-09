# DCIM UI - Quick Start Guide

Get the DCIM Enterprise Monitoring UI running in under 5 minutes.

## Prerequisites Check

```bash
node --version  # Should be 18+
npm --version   # Should be 9+
python --version  # Should be 3.9+
```

## Step 1: Install Dependencies

```bash
# Frontend
cd E:\Projects\DCIM\DCIM_UI
npm install

# Prediction Service
cd ..\prediction_service
pip install -r requirements.txt
```

## Step 2: Start Services

### Terminal 1: Backend (DCIM_Server)
```bash
cd E:\Projects\DCIM\DCIM_Server
.\dcim-server.exe
```

### Terminal 2: Prediction Service
```bash
cd E:\Projects\DCIM\prediction_service
python app.py
```

### Terminal 3: Frontend
```bash
cd E:\Projects\DCIM\DCIM_UI
npm run dev
```

## Step 3: Access the UI

Open your browser to: **http://localhost:5173**

## What You'll See

### Dashboard Page
- Total agents count
- Online agents count
- Alert statistics
- Recent alerts feed

### Agents Page
- List of all registered agents
- Status indicators (online/offline)
- Click any agent ID to view details

### Alerts Page
- All system alerts
- Severity badges (CRITICAL, WARNING, INFO)
- Filterable by status

### AI Analytics Page
- Placeholder sections for predictive charts
- Ready for metric forecasting integration

### Natural Language Query
- Chat interface for asking questions
- Example queries provided

### Settings
- Theme toggle (dark/light)
- Default time range selection
- License information

## Quick Tests

### Test 1: Verify Backend Connection
```bash
curl -k https://localhost:8443/api/v1/agents
```

Expected: JSON array of agents

### Test 2: Verify Prediction Service
```bash
curl http://localhost:5000/api/health
```

Expected:
```json
{"status": "healthy", "service": "DCIM Prediction Service"}
```

### Test 3: Verify Frontend Build
```bash
cd DCIM_UI
npm run build
```

Expected: Build completes successfully in `dist/` folder

## Common Issues

### Issue: "Cannot find module"
**Solution:**
```bash
cd DCIM_UI
rm -rf node_modules
npm install
```

### Issue: Port 5173 already in use
**Solution:**
```bash
# Edit vite.config.ts and change port
server: {
  port: 3000,  // Change this
}
```

### Issue: Backend not responding
**Solution:**
- Check DCIM_Server is running on port 8443
- Verify firewall allows localhost connections
- Check `config.yaml` CORS settings

### Issue: Dark mode not working
**Solution:**
- Theme is stored in localStorage
- Clear browser cache and reload
- Check browser console for errors

## Development Workflow

### Making Changes
1. Edit files in `src/`
2. Vite will hot-reload automatically
3. Check browser console for errors

### Adding New Pages
1. Create component in `src/pages/`
2. Add route in `src/App.tsx`
3. Add navigation link in `src/components/layout/Sidebar.tsx`

### Adding New API Calls
1. Add function to `src/lib/api.ts`
2. Create hook in `src/hooks/`
3. Use hook in component

## Environment Variables

Create `.env` in DCIM_UI folder:

```env
# Development (default)
VITE_API_URL=/api/v1
VITE_AI_API_URL=/ai

# Production (update these)
# VITE_API_URL=https://dcim.yourdomain.com/api/v1
# VITE_AI_API_URL=https://dcim.yourdomain.com/ai

# Optional: OpenAI API Key for NL queries
# VITE_OPENAI_API_KEY=sk-your-key-here
```

## Project Structure (Key Files)

```
DCIM_UI/
├── src/
│   ├── pages/           # All page components
│   ├── components/      # Reusable UI components
│   ├── lib/             # API clients, utilities
│   ├── hooks/           # React hooks
│   ├── stores/          # State management
│   └── App.tsx          # Main app & routing
├── .env                 # Environment variables
├── vite.config.ts       # Vite configuration
└── package.json         # Dependencies
```

## Useful Commands

```bash
# Start development server
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview

# Type check
npx tsc --noEmit

# Lint (if configured)
npm run lint
```

## Next Steps

1. **Customize Theme**: Edit colors in `src/index.css`
2. **Add Charts**: Install Recharts and create chart components
3. **Connect Real Data**: Ensure DCIM_Server has agents reporting
4. **Deploy**: Follow `DEPLOYMENT_GUIDE.md` for production setup

## Getting Help

- **Documentation**: See `README.md` in DCIM_UI folder
- **Deployment**: See `DEPLOYMENT_GUIDE.md`
- **Implementation**: See `IMPLEMENTATION_SUMMARY.md`
- **Backend API**: Check `DCIM_Server/README.md`

## Keyboard Shortcuts

- `Ctrl + K` / `Cmd + K`: Command palette (when implemented)
- `Tab`: Navigate between interactive elements
- `Escape`: Close modals and dialogs
- `Enter`: Submit forms

## Theme Preview

**Dark Mode** (default)
- Background: Dark slate
- Primary: Blue
- Cards: Slightly lighter slate

**Light Mode**
- Background: White
- Primary: Blue
- Cards: Light gray

Toggle with the sun/moon icon in the header.

## Ready for Production?

Before deploying to production:

1. ✅ Update `.env` with production URLs
2. ✅ Build with `npm run build`
3. ✅ Setup Nginx as reverse proxy
4. ✅ Configure SSL certificates
5. ✅ Setup prediction service as systemd service
6. ✅ Configure firewall rules
7. ✅ Test all endpoints

See `DEPLOYMENT_GUIDE.md` for complete instructions.

---

**You're all set!** The DCIM UI should now be running at http://localhost:5173

Happy monitoring! 🚀
