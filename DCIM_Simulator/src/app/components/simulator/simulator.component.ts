import { Component } from '@angular/core';
import { CoolingApiService } from '../../services/cooling-api.service';

// Digital Twin Architecture: Topology + Telemetry Separation

// SystemComponent (Node in graph) - renamed to avoid conflict with Angular Component
interface SystemComponent {
  id: string;
  type: 'PUMP' | 'SERVER' | 'CONDENSER';
  properties?: {
    isOn?: boolean;
    rpm?: number;
    status?: string;
    cpuOn?: boolean;
    heatLoad_kw?: number;
  };
}

// Connection (Edge in graph) - defines flow direction
interface Connection {
  from: string;
  to: string;
}

// Sensor - attached to components, not abstract locations
interface Sensor {
  id: string;
  type: 'TEMPERATURE' | 'PRESSURE' | 'FLOW' | 'LEAK';
  attached_to: string;  // SystemComponent ID
  position: 'INLET' | 'OUTLET';
  value: number | boolean;
  unit: string;
  status?: string;
}

interface LoopComponents {
  components: SystemComponent[];  // Nodes
  connections: Connection[];  // Edges (topology)
  sensors: Sensor[];  // Telemetry attached to nodes
}

interface CoolingLoop {
  loop_id: string;
  type: string;
  components: LoopComponents;
}

interface SensorData {
  agent_id: string;
  agent_name: string;
  timestamp: string;
  loops: CoolingLoop[];
}

@Component({
  selector: 'app-simulator',
  templateUrl: './simulator.component.html',
  styleUrls: ['./simulator.component.css']
})
export class SimulatorComponent {
  // Agent information
  agentId: string = 'System_Sim_1';
  agentName: string = 'System_Sim_1';

  // System state
  pumpOn: boolean = false;
  cpuOn: boolean = true;
  condenserOn: boolean = true;
  inletLeakOn: boolean = false;
  outletLeakOn: boolean = false;

  // Sensor readings (realistic pressure drop: OUT < IN)
  inletTemperature: number = 8;
  inletPressure: number = 50;
  inletFlowRate: number = 15;  // Liters per minute
  outletTemperature: number = 55;
  outletPressure: number = 45;  // 5 PSI drop due to system resistance
  outletFlowRate: number = 15;  // Liters per minute

  // Pump properties
  pumpRPM: number = 3200;
  pumpStatus: string = 'stopped';

  // Server properties
  serverHeatLoad: number = 15;  // kW

  // System status
  statusMessage: string = 'Coolant liquid is stopped';
  alerts: string[] = [];
  jsonOutput: string = '';

  // API response message
  apiMessage: string = '';
  apiMessageType: 'success' | 'error' | '' = '';
  isSending: boolean = false;

  constructor(private coolingApiService: CoolingApiService) {}

  ngOnInit() {
    this.updateSystem();
  }

  togglePump() {
    this.pumpOn = !this.pumpOn;
    this.updateSystem();
  }

  toggleCPU() {
    this.cpuOn = !this.cpuOn;
    this.updateSystem();
  }

  toggleCondenser() {
    this.condenserOn = !this.condenserOn;
    this.updateSystem();
  }

  toggleInletLeak() {
    this.inletLeakOn = !this.inletLeakOn;
    this.updateSystem();
  }

  toggleOutletLeak() {
    this.outletLeakOn = !this.outletLeakOn;
    this.updateSystem();
  }

  updateSystem() {
    this.alerts = [];

    // Logic: If PUMP OFF -> IN/OUT Pressure will be 0, flow stops
    if (!this.pumpOn) {
      this.inletPressure = 0;
      this.outletPressure = 0;
      this.inletFlowRate = 0;
      this.outletFlowRate = 0;
      this.pumpRPM = 0;
      this.pumpStatus = 'stopped';
      this.statusMessage = 'Coolant liquid is stopped';
    } else {
      this.statusMessage = 'Coolant liquid is flowing';
      this.pumpRPM = 3200;
      this.pumpStatus = 'running';

      // Default pressure when pump is on (REALISTIC: OUT < IN due to system resistance)
      // Pressure drop occurs across CPU block, tubing, fittings (typical: 5 PSI)
      this.inletPressure = 50;
      this.outletPressure = 45;  // 5 PSI drop - engineering-grade realism

      // Default flow rate (normal operation)
      this.inletFlowRate = 15;  // 15 LPM normal flow
      this.outletFlowRate = 15;  // Same as inlet in normal conditions

      // Logic: If Condenser ON -> Temperature IN to 8
      if (this.condenserOn) {
        this.inletTemperature = 8;
      } else {
        // Logic: If Condenser OFF -> send same Temperature value as coming
        this.inletTemperature = this.outletTemperature;

        // Cap at 100°C (API validation limit)
        if (this.inletTemperature > 100) {
          this.inletTemperature = 100;
        }
      }

      // Logic: Leaks affect pressure based on location in the cooling loop
      // Different pressure behavior depending on where coolant escapes
      // IMPORTANT: OUT < IN always maintained (flow requires pressure differential)

      if (this.inletLeakOn && this.outletLeakOn) {
        // Both leaks: Critical system failure - severe pressure drop everywhere
        // Minimal pressure gradient maintained, flow severely reduced
        this.inletPressure = 15;
        this.outletPressure = 10;  // Still lower than inlet
        this.inletFlowRate = 5;  // Severely reduced flow
        this.outletFlowRate = 3;  // Even less at outlet (fluid escaping)
      } else if (this.inletLeakOn) {
        // Inlet leak: Fluid escapes BEFORE reaching server/CPU
        // - Flow rate drops, less coolant reaches CPU
        // - Outlet sees even lower pressure due to reduced flow
        this.inletPressure = 20;  // Pressure at inlet leak point
        this.outletPressure = 15;  // Lower downstream due to reduced flow
        this.inletFlowRate = 10;  // Reduced flow at inlet (fluid escaping)
        this.outletFlowRate = 9;  // Slightly less reaches outlet
      } else if (this.outletLeakOn) {
        // Outlet leak: Fluid escapes AFTER leaving server/CPU
        // - Pump still pushes coolant through inlet and CPU
        // - Inlet maintains higher pressure, outlet drops at leak point
        // - Pressure differential: 10 PSI (higher than normal due to leak)
        this.inletPressure = 30;  // Pump still pushing, higher upstream
        this.outletPressure = 20;  // Pressure at outlet leak point
        this.inletFlowRate = 15;  // Normal flow through inlet
        this.outletFlowRate = 11;  // Reduced at outlet (fluid escaping)
      }

      // Logic: If SERVER (CPU) OFF -> OUT Temperature same as IN Temperature
      if (!this.cpuOn) {
        this.outletTemperature = this.inletTemperature;
      } else {
        // When SERVER (CPU) is on, it heats up the coolant
        this.outletTemperature = this.inletTemperature + 47; // Results in ~55 when inlet is 8

        // Cap at 100°C (API validation limit)
        if (this.outletTemperature > 100) {
          this.outletTemperature = 100;
        }
      }
    }

    // Check alerts
    this.checkAlerts();

    // Generate JSON output
    this.generateJSON();
  }

  checkAlerts() {
    // IN Temperature alert: above 10
    if (this.inletTemperature > 10) {
      this.alerts.push(`⚠️ INLET Temperature HIGH: ${this.inletTemperature}°C (Alert threshold: >10°C)`);
    }

    // IN Pressure alert: out of 40-60 range
    if (this.pumpOn && (this.inletPressure < 40 || this.inletPressure > 60)) {
      this.alerts.push(`⚠️ INLET Pressure OUT OF RANGE: ${this.inletPressure} PSI (Normal range: 40-60 PSI)`);
    }

    // IN Leak alert
    if (this.inletLeakOn) {
      this.alerts.push(`🚨 CRITICAL: INLET LEAK DETECTED!`);
    }

    // OUT Temperature alert: below 50
    if (this.pumpOn && this.outletTemperature < 50) {
      this.alerts.push(`⚠️ OUTLET Temperature LOW: ${this.outletTemperature}°C (Alert threshold: <50°C)`);
    }

    // OUT Pressure alert: out of normal range
    if (this.pumpOn && (this.outletPressure < 40 || this.outletPressure > 60)) {
      this.alerts.push(`⚠️ OUTLET Pressure OUT OF RANGE: ${this.outletPressure} PSI (Normal range: 40-60 PSI)`);
    }

    // OUT Leak alert
    if (this.outletLeakOn) {
      this.alerts.push(`🚨 CRITICAL: OUTLET LEAK DETECTED!`);
    }

    // System warnings
    if (!this.pumpOn) {
      this.alerts.push(`ℹ️ SYSTEM IDLE: Pump is OFF`);
    }

    if (!this.cpuOn && this.pumpOn) {
      this.alerts.push(`ℹ️ SERVER is OFF: No heat generation`);
    }

    if (!this.condenserOn && this.pumpOn) {
      this.alerts.push(`⚠️ CONDENSER is OFF: No cooling effect`);
    }
  }

  generateJSON() {
    // Digital Twin Architecture: Topology + Telemetry separated
    const data: SensorData = {
      agent_id: this.agentId,
      agent_name: this.agentName,
      timestamp: new Date().toISOString(),
      loops: [
        {
          loop_id: 'primary_loop',
          type: 'IT_COOLING',
          components: {
            // Topology: Components (Nodes in graph)
            components: [
              {
                id: 'pump_1',
                type: 'PUMP',
                properties: {
                  isOn: this.pumpOn,
                  rpm: this.pumpRPM,
                  status: this.pumpStatus
                }
              },
              {
                id: 'server_1',
                type: 'SERVER',
                properties: {
                  cpuOn: this.cpuOn,
                  heatLoad_kw: this.serverHeatLoad
                }
              },
              {
                id: 'condenser_1',
                type: 'CONDENSER',
                properties: {
                  isOn: this.condenserOn
                }
              }
            ],

            // Topology: Connections (Edges - flow direction)
            connections: [
              { from: 'pump_1', to: 'server_1' },
              { from: 'server_1', to: 'condenser_1' },
              { from: 'condenser_1', to: 'pump_1' }
            ],

            // Telemetry: Sensors attached to components
            sensors: [
              // PUMP OUTLET = INLET for server (what we call "inlet")
              {
                id: 'sensor_temp_pump_out',
                type: 'TEMPERATURE',
                attached_to: 'pump_1',
                position: 'OUTLET',
                value: this.inletTemperature,
                unit: 'celsius',
                status: this.inletTemperature <= 10 ? 'normal' : 'alert'
              },
              {
                id: 'sensor_pressure_pump_out',
                type: 'PRESSURE',
                attached_to: 'pump_1',
                position: 'OUTLET',
                value: this.inletPressure,
                unit: 'psi',
                status: (this.inletPressure >= 40 && this.inletPressure <= 60) ? 'normal' : 'alert'
              },
              {
                id: 'sensor_flow_pump_out',
                type: 'FLOW',
                attached_to: 'pump_1',
                position: 'OUTLET',
                value: this.inletFlowRate,
                unit: 'lpm',
                status: (this.inletFlowRate >= 12 && this.inletFlowRate <= 18) ? 'normal' : 'alert'
              },
              {
                id: 'sensor_leak_pump_out',
                type: 'LEAK',
                attached_to: 'pump_1',
                position: 'OUTLET',
                value: this.inletLeakOn,
                unit: 'boolean',
                status: this.inletLeakOn ? 'critical' : 'normal'
              },

              // SERVER OUTLET = INLET for condenser (what we call "outlet")
              {
                id: 'sensor_temp_server_out',
                type: 'TEMPERATURE',
                attached_to: 'server_1',
                position: 'OUTLET',
                value: this.outletTemperature,
                unit: 'celsius',
                status: this.outletTemperature >= 50 ? 'normal' : 'alert'
              },
              {
                id: 'sensor_pressure_server_out',
                type: 'PRESSURE',
                attached_to: 'server_1',
                position: 'OUTLET',
                value: this.outletPressure,
                unit: 'psi',
                status: (this.outletPressure >= 40 && this.outletPressure <= 60) ? 'normal' : 'alert'
              },
              {
                id: 'sensor_flow_server_out',
                type: 'FLOW',
                attached_to: 'server_1',
                position: 'OUTLET',
                value: this.outletFlowRate,
                unit: 'lpm',
                status: (this.outletFlowRate >= 12 && this.outletFlowRate <= 18) ? 'normal' : 'alert'
              },
              {
                id: 'sensor_leak_server_out',
                type: 'LEAK',
                attached_to: 'server_1',
                position: 'OUTLET',
                value: this.outletLeakOn,
                unit: 'boolean',
                status: this.outletLeakOn ? 'critical' : 'normal'
              }
            ]
          }
        }
      ]
    };

    this.jsonOutput = JSON.stringify(data, null, 2);
  }

  // Simulate sending to API (placeholder)
  sendToAPI() {
    console.log('Sending data to API:', this.jsonOutput);

    const data = JSON.parse(this.jsonOutput);

    // Clear previous message and show loading
    this.apiMessage = '';
    this.apiMessageType = '';
    this.isSending = true;

    this.coolingApiService.sendMetrics(data).subscribe({
      next: (response) => {
        console.log('✅ API Response:', response);
        this.isSending = false;
        this.apiMessage = response.message || 'Data sent successfully to server!';
        this.apiMessageType = 'success';

        // Auto-hide message after 5 seconds
        setTimeout(() => {
          this.apiMessage = '';
          this.apiMessageType = '';
        }, 5000);
      },
      error: (error) => {
        console.error('❌ API Error:', error);
        this.isSending = false;

        if (error.status === 0) {
          this.apiMessage = 'Cannot connect to server. Please check: 1) Server is running 2) Proxy is running 3) Network connectivity';
          this.apiMessageType = 'error';
        } else if (error.error && error.error.error) {
          // Server validation error
          this.apiMessage = `Server Error: ${error.error.error}`;
          this.apiMessageType = 'error';
        } else {
          this.apiMessage = `API Error: ${error.status} - ${error.message}`;
          this.apiMessageType = 'error';
        }

        // Auto-hide error message after 8 seconds
        setTimeout(() => {
          this.apiMessage = '';
          this.apiMessageType = '';
        }, 8000);
      }
    });
  }

  copyJSON() {
    navigator.clipboard.writeText(this.jsonOutput).then(() => {
      this.apiMessage = 'JSON copied to clipboard!';
      this.apiMessageType = 'success';

      // Auto-hide message after 3 seconds
      setTimeout(() => {
        this.apiMessage = '';
        this.apiMessageType = '';
      }, 3000);
    });
  }

  getInletPressureColor(): string {
    if (!this.pumpOn) return '#6c757d';
    if (this.inletPressure >= 40 && this.inletPressure <= 60) return '#28a745';
    return '#dc3545';
  }

  getInletTemperatureColor(): string {
    if (this.inletTemperature <= 10) return '#28a745';
    return '#dc3545';
  }

  getOutletPressureColor(): string {
    if (!this.pumpOn) return '#6c757d';
    if (this.outletPressure >= 40 && this.outletPressure <= 60) return '#28a745';
    return '#dc3545';
  }

  getOutletTemperatureColor(): string {
    if (!this.pumpOn) return '#6c757d';
    if (this.outletTemperature >= 50) return '#28a745';
    return '#dc3545';
  }

  getInletFlowRateColor(): string {
    if (!this.pumpOn) return '#6c757d';
    if (this.inletFlowRate >= 12 && this.inletFlowRate <= 18) return '#28a745';
    if (this.inletFlowRate >= 8 && this.inletFlowRate < 12) return '#ffc107'; // Warning
    return '#dc3545';
  }

  getOutletFlowRateColor(): string {
    if (!this.pumpOn) return '#6c757d';
    if (this.outletFlowRate >= 12 && this.outletFlowRate <= 18) return '#28a745';
    if (this.outletFlowRate >= 8 && this.outletFlowRate < 12) return '#ffc107'; // Warning
    return '#dc3545';
  }
}
