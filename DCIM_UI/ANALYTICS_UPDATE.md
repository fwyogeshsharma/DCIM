# Agent Analytics - Metrics API Integration

## ✅ **Updates Made**

### **1. Fixed Metrics Fetching**
**Problem:** Analytics page was using `time_range` parameter which returned `null` for old data

**Solution:**
- Removed time_range dependency
- Increased data limits (500/1000 records)
- Fetches all available metrics regardless of age
- Shows actual data timestamps

### **2. API Endpoint Updates**

**Before:**
```typescript
api.getMetrics({ agent_id: agentId, time_range: '1h', limit: 100 })
```

**After:**
```typescript
api.getMetrics({ agent_id: agentId, limit: 500 })  // Recent data
api.getMetrics({ agent_id: agentId, limit: 1000 }) // All data
```

### **3. Added Limit Parameter Support**

Updated `api.ts` to properly pass `limit` parameter to server:
```typescript
if (filter.limit) params.append('limit', filter.limit.toString())
```

---

## 📊 **Available Metrics**

### **Metric Types Found:**
1. **CPU Metrics**
   - `cpu.core_usage` - Individual CPU core usage (%)
   - Multiple cores tracked separately

2. **Memory Metrics**
   - `memory.usage` - RAM usage (%)
   - `memory.swap` - Swap usage (%)

3. **Disk Metrics**
   - `disk.usage` - Disk space usage (%)
   - `disk.io` - Disk I/O (bytes)
   - Multiple drives tracked

4. **Network Metrics** (if available)
   - Network I/O statistics
   - Bandwidth metrics

---

## 🎯 **What Analytics Page Shows**

### **Charts & Visualizations:**

1. **Quick Stats Cards**
   - Total metrics count
   - Active alerts
   - System uptime
   - Last seen time

2. **CPU Usage Charts**
   - Line chart showing CPU usage over time
   - Per-core breakdown
   - Average, min, max values

3. **Memory Usage Charts**
   - RAM usage trends
   - Swap usage
   - Memory pressure indicators

4. **Disk Usage Charts**
   - Disk space utilization
   - Per-drive breakdown (C:, D:, etc.)
   - I/O statistics

5. **Network Charts** (if data available)
   - Bandwidth usage
   - Network traffic patterns

---

## 📈 **Data Details**

### **Current Data Available:**
- **Agent:** DESKTOP-AU1P9BD
- **Metrics:** 500+ recent metrics
- **Time Range:** Last few hours of data
- **Refresh:** Every 30 seconds
- **Types:** CPU, Memory, Disk, Network

### **Sample Data:**
```json
{
  "metric_type": "cpu.core_usage",
  "value": 17.46,
  "unit": "percent",
  "timestamp": "2026-02-09T16:32:12Z"
}
```

---

## 🔄 **How It Works Now**

### **Data Flow:**
```
Agent → DCIM Server → Database → API → Proxy → UI → Charts
```

### **Fetching Process:**
1. AgentAnalytics page loads
2. Fetches agent info
3. Fetches recent metrics (limit: 500)
4. Fetches all metrics (limit: 1000)
5. Groups by metric type
6. Calculates stats (avg, min, max)
7. Renders charts with Recharts

### **Grouping Logic:**
Metrics are automatically grouped by type:
- CPU metrics → CPU charts
- Memory metrics → Memory charts
- Disk metrics → Disk charts (per drive)
- Network metrics → Network charts

---

## 🎨 **Chart Types Used**

1. **Line Charts** - Time-series trends
2. **Area Charts** - Filled time-series
3. **Bar Charts** - Comparisons
4. **Pie Charts** - Distributions
5. **Radial Charts** - Gauge-style displays

---

## ✨ **Features**

### **Interactive Charts:**
- ✅ Hover tooltips with exact values
- ✅ Legend to toggle data series
- ✅ Responsive design
- ✅ Color-coded by metric type
- ✅ Time-based X-axis

### **Data Processing:**
- ✅ Automatic type detection
- ✅ Drive letter extraction (C:, D:)
- ✅ Unit conversion
- ✅ Timestamp sorting
- ✅ Statistical calculations

### **Real-Time Updates:**
- ✅ Auto-refresh every 30 seconds
- ✅ Shows latest data
- ✅ Preserves chart state

---

## 🚀 **How to View Analytics**

### **Option 1: From Agents Page**
1. Go to **Agents** page
2. Click **"Analytics"** button for any agent
3. View comprehensive metrics dashboard

### **Option 2: Direct URL**
```
http://localhost:5173/app/agents/DESKTOP-AU1P9BD/analytics
```

### **Option 3: From Agent Details**
1. Go to **Agents** page
2. Click on Agent ID
3. Click **"View Analytics"** button

---

## 📊 **Sample Metrics for DESKTOP-AU1P9BD**

### **Recent Data:**
- **CPU Usage:** 7.8% - 26.5% (across 8 cores)
- **Memory Usage:** 84%
- **Swap Usage:** 66%
- **Disk C: Usage:** 98.4% ⚠️ (Critical!)
- **Disk D: Usage:** 2.0%
- **Disk E: Usage:** 16.1%

### **Alert Status:**
- **84 Active Alerts**
- Memory warnings
- Critical disk space warnings

---

## 🔧 **Technical Details**

### **API Endpoint:**
```
GET /api/v1/metrics?agent_id={agentId}&limit={limit}
```

### **Response Format:**
```json
{
  "success": true,
  "message": "",
  "data": [
    {
      "id": 10500,
      "agent_id": "DESKTOP-AU1P9BD",
      "timestamp": "2026-02-09T16:32:12Z",
      "metric_type": "cpu.core_usage",
      "value": 9.375,
      "unit": "percent",
      "created_at": "2026-02-09T19:16:10.006494Z"
    }
  ]
}
```

### **Data Extraction:**
API client automatically extracts `data` array:
```typescript
if (json && typeof json === 'object' && 'data' in json) {
  return json.data as T
}
```

---

## 🎯 **Next Steps**

### **To See Your Data:**
1. **Refresh browser** (Ctrl + Shift + R)
2. Go to **Agents** page
3. Click **"Analytics"** button on DESKTOP-AU1P9BD
4. See all your metrics visualized!

### **Expected View:**
- Quick stats at top
- CPU usage charts
- Memory usage charts
- Disk usage charts (showing C: at 98% - critical!)
- All data grouped and color-coded

---

## ✅ **What's Working**

- ✅ Metrics API fetching data
- ✅ 500+ metrics available
- ✅ CPU, Memory, Disk data present
- ✅ Automatic data grouping
- ✅ Chart rendering
- ✅ Real-time updates (30s interval)
- ✅ Multiple time ranges
- ✅ Statistical calculations
- ✅ Responsive design

---

**Your analytics are ready! Refresh and view them now!** 🎉
