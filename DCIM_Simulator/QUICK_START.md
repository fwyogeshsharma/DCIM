# Quick Start Guide - AI Data Center Simulator

## Installation Steps

### 1. Install Node.js and npm

If you don't have Node.js installed:

**Windows:**
- Download from https://nodejs.org/ (LTS version recommended)
- Run the installer and follow the prompts
- Verify installation:
  ```bash
  node --version
  npm --version
  ```

### 2. Install Angular CLI

Open a terminal/command prompt and run:

```bash
npm install -g @angular/cli
```

Verify installation:
```bash
ng version
```

### 3. Install Project Dependencies

Navigate to the project directory:

```bash
cd C:\Users\sjain\github\ai-datacenter-simulator
```

Install all required packages:

```bash
npm install
```

This will install all dependencies listed in `package.json`.

### 4. Start the Development Server

Run the application:

```bash
npm start
```

Or:

```bash
ng serve
```

The application will compile and start. You should see output like:
```
✔ Browser application bundle generation complete.
✔ Compiled successfully.
** Angular Live Development Server is listening on localhost:4200 **
```

### 5. Open in Browser

Navigate to:
```
http://localhost:4200
```

The simulator should load and be ready to use!

## Using the Simulator

### Initial State
- Pump: OFF
- CPU: ON
- Condenser: ON
- All Leaks: OFF (SAFE)

### Test Scenarios

#### Scenario 1: Normal Operation
1. Turn **Pump ON** - Coolant starts flowing
2. Observe:
   - Inlet temp: 8°C (green, normal)
   - Inlet pressure: 50 PSI (green, normal)
   - Outlet temp: 55°C (green, normal)
   - Outlet pressure: 50 PSI (green, normal)
   - Status: "Coolant liquid is flowing" ✅

#### Scenario 2: Inlet Leak
1. Ensure Pump is ON
2. Toggle **Inlet Leak ON**
3. Observe:
   - Inlet pressure drops to 20 PSI (red, alert)
   - Alert appears: "🚨 CRITICAL: INLET LEAK DETECTED!"
   - Alert appears: "⚠️ INLET Pressure OUT OF RANGE"

#### Scenario 3: Outlet Leak
1. Ensure Pump is ON
2. Toggle **Outlet Leak ON**
3. Observe:
   - Outlet pressure drops to 20 PSI (red, alert)
   - Alert appears: "🚨 CRITICAL: OUTLET LEAK DETECTED!"

#### Scenario 4: CPU Off
1. Ensure Pump is ON
2. Toggle **CPU OFF**
3. Observe:
   - Outlet temperature drops to match inlet temp (8°C)
   - Alert appears: "⚠️ OUTLET Temperature LOW"
   - Info message: "ℹ️ CPU is OFF: No heat generation"

#### Scenario 5: Condenser Off
1. Ensure Pump is ON
2. Toggle **Condenser OFF**
3. Observe:
   - Inlet temperature rises (no cooling effect)
   - Alert appears: "⚠️ CONDENSER is OFF: No cooling effect"

#### Scenario 6: System Shutdown
1. Toggle **Pump OFF**
2. Observe:
   - All pressure readings drop to 0
   - Status: "Coolant liquid is stopped"
   - Alert: "ℹ️ SYSTEM IDLE: Pump is OFF"

### JSON Output

The JSON output section shows all current sensor readings in a format ready for API transmission:

```json
{
  "pump": {
    "isOn": true,
    "status": "Coolant liquid is flowing"
  },
  "cpu": {
    "isOn": true
  },
  "condenser": {
    "isOn": true
  },
  "inlet": {
    "temperature": 8,
    "pressure": 50,
    "leak": false
  },
  "outlet": {
    "temperature": 55,
    "pressure": 50,
    "leak": false
  },
  "alerts": [],
  "timestamp": "2024-01-15T10:30:00.000Z"
}
```

**Actions:**
- **Send to API**: Currently logs to browser console (API endpoint to be implemented)
- **Copy JSON**: Copies the JSON to clipboard for testing/documentation

## Troubleshooting

### Port Already in Use
If port 4200 is already in use:
```bash
ng serve --port 4300
```
Then navigate to `http://localhost:4300`

### Module Not Found Errors
If you see module errors:
```bash
npm install
```

### Angular CLI Not Found
If `ng` command is not recognized:
```bash
npm install -g @angular/cli
```

### Browser Doesn't Auto-Open
Manually navigate to: `http://localhost:4200`

## Next Steps

1. Test all control scenarios
2. Monitor the JSON output format
3. Plan API endpoint integration
4. Consider adding data logging features
5. Implement historical data tracking

## Development Commands

```bash
# Start development server
npm start

# Build for production
npm run build

# Run tests
npm test

# Build and watch for changes
npm run watch
```

## Need Help?

Refer to:
- `README.md` - Full documentation
- Angular documentation: https://angular.io/docs
- TypeScript documentation: https://www.typescriptlang.org/docs

Happy simulating!
