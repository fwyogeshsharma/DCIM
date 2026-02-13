package models

import "time"

// CoolingMetricsRequest represents incoming cooling system data from simulator
type CoolingMetricsRequest struct {
	AgentID   string        `json:"agent_id"`
	AgentName string        `json:"agent_name"`
	Timestamp time.Time     `json:"timestamp"`
	Loops     []CoolingLoop `json:"loops"`
}

// CoolingLoop represents a single cooling loop (primary, secondary, etc.)
type CoolingLoop struct {
	LoopID     string         `json:"loop_id"`
	Type       string         `json:"type"` // IT_COOLING, FACILITY_COOLING, etc.
	Components LoopComponents `json:"components"`
}

// LoopComponents contains components, connections, and sensors
type LoopComponents struct {
	Components  []Component  `json:"components"`
	Connections []Connection `json:"connections"`
	Sensors     []Sensor     `json:"sensors"`
}

// Component represents a cooling system component (pump, server, condenser, etc.)
type Component struct {
	ID         string                 `json:"id"`
	Type       string                 `json:"type"` // PUMP, SERVER, CONDENSER, HEAT_EXCHANGER, etc.
	Properties map[string]interface{} `json:"properties"`
}

// Connection represents flow between two components
type Connection struct {
	From string `json:"from"` // Source component ID
	To   string `json:"to"`   // Destination component ID
}

// Sensor represents a sensor attached to a component
type Sensor struct {
	ID         string      `json:"id"`
	Type       string      `json:"type"`        // TEMPERATURE, PRESSURE, FLOW, LEAK
	AttachedTo string      `json:"attached_to"` // Component ID
	Position   string      `json:"position"`    // INLET, OUTLET
	Value      interface{} `json:"value"`       // float64 for numeric, bool for leak
	Unit       string      `json:"unit"`        // celsius, psi, lpm, boolean
	Status     string      `json:"status"`      // normal, alert, critical (ignored by server)
}

// CoolingConfig represents the cooling system configuration
type CoolingConfig struct {
	Cooling CoolingSettings `yaml:"cooling"`
}

// CoolingSettings contains all cooling thresholds and settings
type CoolingSettings struct {
	Temperature TemperatureThresholds `yaml:"temperature"`
	Pressure    PressureThresholds    `yaml:"pressure"`
	Flow        FlowThresholds        `yaml:"flow"`
	Components  ComponentThresholds   `yaml:"components"`
	Alerts      AlertSettings         `yaml:"alerts"`
	Validation  ValidationSettings    `yaml:"validation"`
}

// TemperatureThresholds defines temperature limits
type TemperatureThresholds struct {
	InletMaxCondenserOn  float64 `yaml:"inlet_max_condenser_on"`
	OutletMaxNormal      float64 `yaml:"outlet_max_normal"`
	OutletMaxCritical    float64 `yaml:"outlet_max_critical"`
	TemperatureTolerance float64 `yaml:"temperature_tolerance"`
	DeltaMinCPUOn        float64 `yaml:"delta_min_cpu_on"`
}

// PressureThresholds defines pressure limits
type PressureThresholds struct {
	NormalMin         float64 `yaml:"normal_min"`
	NormalMax         float64 `yaml:"normal_max"`
	NormalOperating   float64 `yaml:"normal_operating"`
	PumpOffMax        float64 `yaml:"pump_off_max"`
	LeakageThreshold  float64 `yaml:"leakage_threshold"`
}

// AlertSettings defines alert configuration
type AlertSettings struct {
	Enabled        bool                   `yaml:"enabled"`
	SeverityLevels map[string]string      `yaml:"severity_levels"`
}

// ValidationSettings defines validation limits
type ValidationSettings struct {
	MaxTemperature            float64 `yaml:"max_temperature"`
	MinTemperature            float64 `yaml:"min_temperature"`
	MaxPressure               float64 `yaml:"max_pressure"`
	MinPressure               float64 `yaml:"min_pressure"`
	MaxFlow                   float64 `yaml:"max_flow"`
	MinFlow                   float64 `yaml:"min_flow"`
	TimestampToleranceSeconds int     `yaml:"timestamp_tolerance_seconds"`
}

// FlowThresholds defines flow rate limits
type FlowThresholds struct {
	MinNormalFlow      float64 `yaml:"min_normal_flow"`       // Minimum normal flow rate
	MaxNormalFlow      float64 `yaml:"max_normal_flow"`       // Maximum normal flow rate
	CriticalLowFlow    float64 `yaml:"critical_low_flow"`     // Critical low flow threshold
	ZeroFlowThreshold  float64 `yaml:"zero_flow_threshold"`   // Considered zero flow
}

// ComponentThresholds defines component-specific thresholds
type ComponentThresholds struct {
	Condenser CondenserThresholds `yaml:"condenser"`
	Pump      PumpThresholds      `yaml:"pump"`
	Server    ServerThresholds    `yaml:"server"`
}

// CondenserThresholds defines condenser-specific rules
type CondenserThresholds struct {
	MaxTempDifference  float64 `yaml:"max_temp_difference"`   // Max temp diff between inlet/outlet
	MinTempDifference  float64 `yaml:"min_temp_difference"`   // Min temp diff when working
	EfficiencyWarning  float64 `yaml:"efficiency_warning"`    // Warn if delta < this
}

// PumpThresholds defines pump-specific rules
type PumpThresholds struct {
	MinRPM             float64 `yaml:"min_rpm"`               // Minimum RPM when on
	MaxRPM             float64 `yaml:"max_rpm"`               // Maximum RPM
	MinFlowRate        float64 `yaml:"min_flow_rate"`         // Min flow rate when on
}

// ServerThresholds defines server-specific rules
type ServerThresholds struct {
	MaxHeatLoad        float64 `yaml:"max_heat_load"`         // Max heat load in kW
	MinTempRise        float64 `yaml:"min_temp_rise"`         // Min temp rise when CPU on
	MaxOutletTemp      float64 `yaml:"max_outlet_temp"`       // Max outlet temperature
}
