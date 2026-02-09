# DCIM Enterprise Monitoring UI

Professional, AI-powered web interface for Data Center Infrastructure Management (DCIM) monitoring system.

## Features

### Core Functionality
- **Real-time Monitoring**: Live SSE updates for agent status, metrics, and alerts
- **Agent Management**: View, approve, and manage monitoring agents
- **Alert System**: Comprehensive alert management with filtering and bulk actions
- **Metrics Visualization**: Interactive charts for 100+ metric types

### AI-Powered Features
- **Predictive Analytics**: CPU, memory, and disk usage forecasting
- **Natural Language Queries**: Ask questions in plain English
- **AI Insights Panel**: Automated anomaly detection and recommendations
- **Anomaly Visualization**: Historical anomaly timeline with RCA results

### User Experience
- **Responsive Design**: Works on desktop, tablet, and mobile
- **Dark/Light Theme**: User-selectable with persistence
- **Accessibility**: WCAG 2.1 AA compliant
- **Modern UI**: Built with shadcn/ui and Tailwind CSS

## Tech Stack

- **Framework**: React 18 + TypeScript + Vite
- **UI Components**: shadcn/ui + Tailwind CSS
- **State Management**:
  - TanStack Query v5 (server state)
  - Zustand (client state)
  - Custom SSE hooks (real-time data)
- **Charts**: Recharts + D3.js
- **AI Integration**: OpenAI API / Anthropic Claude API
- **Routing**: React Router v6

## Prerequisites

- Node.js 18+ and npm
- DCIM_Server running on backend
- Python 3.9+ (for prediction service)

## Installation

### 1. Install Frontend Dependencies

```bash
cd DCIM_UI
npm install
```

### 2. Install Prediction Service

```bash
cd ../prediction_service
pip install -r requirements.txt
```

### 3. Configure Environment

Create `.env` file in DCIM_UI directory:

```env
VITE_API_URL=/api/v1
VITE_AI_API_URL=/ai
VITE_OPENAI_API_KEY=sk-your-openai-api-key-here  # Optional
```

## Development

### Start Frontend (Development)

```bash
cd DCIM_UI
npm run dev
```

The UI will be available at `http://localhost:5173`

### Start Prediction Service

```bash
cd prediction_service
python app.py
```

The service will run on `http://localhost:5000`

### Start DCIM_Server Backend

```bash
cd DCIM_Server
./dcim-server.exe  # or ./dcim-server on Linux
```

## Production Build

### 1. Build Frontend

```bash
cd DCIM_UI
npm run build
```

Output will be in `dist/` folder.

### 2. Setup Nginx

Copy the example configuration:

```bash
cp ../nginx.conf.example /etc/nginx/sites-available/dcim-ui
```

Edit the configuration:
- Replace `dcim.example.com` with your domain
- Update backend server IP address
- Configure SSL certificates
- Set mTLS client certificates

Enable the site:

```bash
sudo ln -s /etc/nginx/sites-available/dcim-ui /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 3. Deploy Frontend

```bash
sudo mkdir -p /var/www/dcim-ui
sudo cp -r dist/* /var/www/dcim-ui/
sudo chown -R www-data:www-data /var/www/dcim-ui
```

### 4. Deploy Prediction Service (Production)

```bash
cd prediction_service
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

Or use systemd service:

```bash
sudo cp prediction-service.service /etc/systemd/system/
sudo systemctl enable prediction-service
sudo systemctl start prediction-service
```

## Project Structure

```
DCIM_UI/
├── src/
│   ├── components/
│   │   ├── ui/                   # shadcn/ui components
│   │   ├── layout/               # Layout components (Sidebar, Header)
│   │   ├── dashboard/            # Dashboard-specific components
│   │   ├── agents/               # Agent-related components
│   │   ├── charts/               # Chart components
│   │   ├── ai/                   # AI features components
│   │   └── alerts/               # Alert components
│   │
│   ├── pages/                    # Page components
│   │   ├── Dashboard.tsx
│   │   ├── Agents.tsx
│   │   ├── AgentDetail.tsx
│   │   ├── Alerts.tsx
│   │   ├── AIAnalytics.tsx
│   │   ├── NaturalLanguageQuery.tsx
│   │   └── Settings.tsx
│   │
│   ├── lib/
│   │   ├── api.ts                # Backend API client
│   │   ├── ai-api.ts             # AI service API client
│   │   ├── sse.ts                # SSE client
│   │   ├── types.ts              # TypeScript types
│   │   └── utils.ts              # Utility functions
│   │
│   ├── hooks/
│   │   ├── useAgents.ts          # React Query hooks
│   │   ├── useMetrics.ts
│   │   ├── useAlerts.ts
│   │   ├── useSSE.ts             # SSE hook
│   │   ├── usePredictions.ts     # AI predictions
│   │   └── useNLQuery.ts         # Natural language queries
│   │
│   ├── stores/
│   │   ├── useUIStore.ts         # Zustand UI state
│   │   └── useRealtimeStore.ts   # SSE data store
│   │
│   ├── App.tsx                   # Main app component
│   └── main.tsx                  # Entry point
│
├── public/                       # Static assets
├── index.html
├── package.json
├── tsconfig.json
├── tailwind.config.js
└── vite.config.ts
```

## API Integration

### Backend Endpoints

The UI connects to DCIM_Server endpoints:

- `GET /api/v1/agents` - List all agents
- `GET /api/v1/agents/:id` - Get agent details
- `GET /api/v1/metrics` - Query metrics
- `GET /api/v1/alerts` - List alerts
- `GET /api/v1/events` - SSE stream for real-time updates
- `POST /api/v1/agents/:id/approve` - Approve agent

### Prediction Service Endpoints

- `POST /ai/predict` - Get metric predictions
- `POST /ai/anomaly-score` - Calculate anomaly scores

## Configuration

### Vite Proxy (Development)

In `vite.config.ts`, the proxy is configured to forward requests:

```typescript
server: {
  proxy: {
    '/api': {
      target: 'https://localhost:8443',
      changeOrigin: true,
      secure: false,
    },
  },
}
```

### Time Ranges

Configurable time ranges for metrics:
- 5 minutes
- 1 hour
- 6 hours
- 24 hours
- 7 days
- 30 days
- Custom

## Accessibility

- WCAG 2.1 AA compliant
- Keyboard navigation support
- Screen reader friendly
- Focus indicators
- ARIA labels
- Color contrast ratios meet standards

## Browser Support

- Chrome/Edge 90+
- Firefox 88+
- Safari 14+

## Performance Optimization

- Code splitting with React.lazy()
- Virtual scrolling for large tables
- Optimized chart re-renders with useMemo
- Service worker caching (optional)
- Gzip compression in production

## Troubleshooting

### UI not connecting to backend

1. Check backend is running: `curl https://localhost:8443/api/v1/agents`
2. Verify CORS settings in backend `config.yaml`
3. Check browser console for errors
4. Verify proxy configuration in `vite.config.ts` (dev) or `nginx.conf` (prod)

### SSE not receiving updates

1. Check SSE endpoint: `curl -N https://localhost:8443/api/v1/events`
2. Verify Nginx SSE configuration (production)
3. Check browser Network tab for EventSource connection
4. Ensure `proxy_buffering off` in Nginx

### AI features not working

1. Verify prediction service is running: `curl http://localhost:5000/api/health`
2. Check OPENAI_API_KEY is set in `.env`
3. Review prediction service logs
4. Verify Nginx proxy for `/ai/` path

## License

Enterprise License - See backend LICENSE file

## Support

For issues and feature requests, contact the development team.
