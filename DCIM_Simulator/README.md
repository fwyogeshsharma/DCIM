# AI Data Center Liquid Cooling System Simulator

An Angular-based web application that simulates IoT-ready devices and sensors used to manage and optimize liquid cooling systems in AI data centers.

## Features

- **Real-time Monitoring**: Track temperature, pressure, and leak detection sensors
- **Interactive Controls**: Toggle Pump, CPU, Condenser, and Leak detectors
- **Visual Flow Diagram**: See the coolant flow through the system
- **Alert System**: Automatic alerts for out-of-range values and critical conditions
- **JSON API Output**: Generate and export sensor data in JSON format
- **Responsive Design**: Works on desktop, tablet, and mobile devices

## System Components

### Flow Diagram
```
Pump → Inlet Sensors → CPU → Outlet Sensors → Condenser → (back to Pump)
```

### Sensors

#### Inlet (IN) Sensors
- **Temperature**: Alert triggered above 10°C
- **Pressure**: Normal range 40-60 PSI (alert if outside range)
- **Leak Detection**: Yes/No (alert on leak detected)

#### Outlet (OUT) Sensors
- **Temperature**: Alert triggered below 50°C
- **Pressure**: Normal range 40-60 PSI (alert if outside range)
- **Leak Detection**: Yes/No (alert on leak detected)

## System Logic

### Control Behaviors

1. **Pump OFF**: All pressure values set to 0, system idle
2. **Pump ON**: Coolant flows, standard values applied
3. **Condenser ON**: Inlet temperature set to 8°C (cooling active)
4. **Condenser OFF**: Inlet temperature equals outlet temperature (no cooling)
5. **CPU ON**: Generates heat, outlet temperature increases
6. **CPU OFF**: No heat generation, outlet temperature equals inlet temperature
7. **Inlet Leak ON**: Inlet pressure drops to 20 PSI
8. **Outlet Leak ON**: Outlet pressure drops to 20 PSI

## Installation

### Prerequisites

- Node.js (v18 or higher)
- npm (v9 or higher)
- Angular CLI (v17 or higher)

### Setup Instructions

1. **Install Node.js and npm** (if not already installed):
   - Download from: https://nodejs.org/

2. **Install Angular CLI globally**:
   ```bash
   npm install -g @angular/cli
   ```

3. **Navigate to the project directory**:
   ```bash
   cd ai-datacenter-simulator
   ```

4. **Install dependencies**:
   ```bash
   npm install
   ```

5. **Start the development server**:
   ```bash
   npm start
   ```
   or
   ```bash
   ng serve
   ```

6. **Open your browser** and navigate to:
   ```
   http://localhost:4200
   ```

## Usage

### Control Panel

Use the control panel buttons to simulate different system states:

- **Pump**: Start/stop coolant flow
- **CPU**: Enable/disable heat generation
- **Condenser**: Enable/disable cooling
- **Inlet Leak**: Simulate inlet leak condition
- **Outlet Leak**: Simulate outlet leak condition

### Monitoring

- **Flow Diagram**: Visual representation of the cooling system with real-time status
- **Sensor Cards**: Display current readings with color-coded status
  - Green: Normal operation
  - Red: Alert condition
  - Gray: System idle

### Alerts

The alerts section displays:
- Critical alerts (leaks)
- Warnings (out-of-range values)
- Info messages (system status)

### JSON Output

- View the generated JSON data for all sensor readings
- **Send to API**: Logs data to console (API endpoint not yet implemented)
- **Copy JSON**: Copy JSON data to clipboard for testing

## Project Structure

```
ai-datacenter-simulator/
├── src/
│   ├── app/
│   │   ├── components/
│   │   │   └── simulator/
│   │   │       ├── simulator.component.ts
│   │   │       ├── simulator.component.html
│   │   │       └── simulator.component.css
│   │   ├── app.component.ts
│   │   ├── app.component.html
│   │   ├── app.component.css
│   │   └── app.module.ts
│   ├── environments/
│   │   ├── environment.ts
│   │   └── environment.prod.ts
│   ├── assets/
│   ├── index.html
│   ├── main.ts
│   └── styles.css
├── angular.json
├── package.json
├── tsconfig.json
└── README.md
```

## Future Enhancements

1. **API Integration**: Connect to backend API for data storage and analysis
2. **Historical Data**: Track and visualize sensor data over time
3. **Advanced Analytics**: Machine learning for predictive maintenance
4. **Multi-System Support**: Monitor multiple cooling loops simultaneously
5. **User Authentication**: Secure access control
6. **Alert Notifications**: Email/SMS notifications for critical events
7. **Data Export**: Export sensor data to CSV/Excel
8. **Customizable Thresholds**: User-defined alert thresholds

## Technology Stack

- **Framework**: Angular 17
- **Language**: TypeScript 5.2
- **Styling**: CSS3 with animations
- **Build Tool**: Angular CLI
- **Package Manager**: npm

## Browser Support

- Chrome (latest)
- Firefox (latest)
- Safari (latest)
- Edge (latest)

## Development

### Build for Production

```bash
npm run build
```

The build artifacts will be stored in the `dist/` directory.

### Running Tests

```bash
npm test
```

## License

This project is created for educational and demonstration purposes.

## Author

Created for AI Data Center simulation and monitoring.

## Support

For issues or questions, please refer to the project documentation or contact the development team.
