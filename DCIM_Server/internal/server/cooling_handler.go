package server

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"time"

	"github.com/faberlabs/dcim-server/internal/models"
)

// handleCoolingMetrics handles incoming cooling system metrics from simulator
func (s *Server) handleCoolingMetrics(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		s.sendError(w, http.StatusMethodNotAllowed, "Method not allowed")
		return
	}

	// Read and parse request
	body, err := io.ReadAll(io.LimitReader(r.Body, s.config.Server.MaxBodySize))
	if err != nil {
		s.sendError(w, http.StatusBadRequest, "Failed to read request body")
		return
	}

	var req models.CoolingMetricsRequest
	if err := json.Unmarshal(body, &req); err != nil {
		s.sendError(w, http.StatusBadRequest, "Invalid JSON: "+err.Error())
		return
	}

	// Validate required fields
	if req.AgentID == "" {
		s.sendError(w, http.StatusBadRequest, "agent_id is required")
		return
	}

	if req.AgentName == "" {
		s.sendError(w, http.StatusBadRequest, "agent_name is required")
		return
	}

	// Validate timestamp
	if req.Timestamp.IsZero() {
		s.sendError(w, http.StatusBadRequest, "timestamp is required")
		return
	}

	// Validate temperature and pressure ranges
	if err := s.validateCoolingMetrics(&req); err != nil {
		s.sendError(w, http.StatusBadRequest, "Validation failed: "+err.Error())
		return
	}

	// Ensure agent exists (reuse existing logic)
	existingAgent, err := s.db.GetAgentByHostname(req.AgentName)
	if err != nil {
		// Agent doesn't exist, register it
		agent := &models.Agent{
			AgentID:  req.AgentID,
			ServerID: s.serverID,
			Hostname: req.AgentName,
			Status:   "online",
			Group:    "cooling_systems",
			Approved: true, // Auto-approve cooling systems
		}

		if err := s.db.RegisterAgent(agent); err != nil {
			s.logger.Printf("Failed to register cooling agent %s: %v", req.AgentID, err)
			s.sendError(w, http.StatusInternalServerError, "Failed to register agent")
			return
		}

		s.logger.Printf("Registered new cooling agent: %s (hostname: %s)", req.AgentID, req.AgentName)
	} else {
		// Update last seen
		s.db.UpdateAgentLastSeen(existingAgent.AgentID)
	}

	// Convert cooling data to metrics
	metrics := s.convertCoolingToMetrics(&req)

	// Insert metrics into database
	if err := s.db.InsertMetrics(s.serverID, metrics); err != nil {
		s.logger.Printf("Failed to insert cooling metrics: %v", err)
		s.sendError(w, http.StatusInternalServerError, "Failed to store metrics")
		return
	}

	s.logger.Printf("Stored %d cooling metrics from agent %s", len(metrics), req.AgentID)

	// Evaluate cooling system conditions and generate alerts
	alerts := s.evaluateCoolingAlerts(&req)

	// Insert alerts if any were generated
	alertCount := 0
	if len(alerts) > 0 {
		if err := s.db.InsertAlerts(s.serverID, alerts); err != nil {
			s.logger.Printf("Failed to insert cooling alerts: %v", err)
			// Don't fail the request, just log the error
		} else {
			alertCount = len(alerts)
			s.logger.Printf("Generated %d cooling alerts from agent %s", alertCount, req.AgentID)
		}
	}

	// Broadcast update to SSE clients
	if len(metrics) > 0 {
		agent, err := s.db.GetAgent(req.AgentID)
		if err == nil {
			s.broadcastEvent("agent_update", agent)
		}
	}

	// Send success response
	response := models.APIResponse{
		Success: true,
		Message: fmt.Sprintf("Received cooling metrics from %s", req.AgentName),
		Data: map[string]interface{}{
			"agent_id":        req.AgentID,
			"timestamp":       req.Timestamp,
			"metrics_stored":  len(metrics),
			"alerts_generated": alertCount,
		},
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(response)
}

// validateCoolingMetrics validates cooling metric values
func (s *Server) validateCoolingMetrics(req *models.CoolingMetricsRequest) error {
	// Load cooling config
	coolingCfg, err := s.loadCoolingConfig()
	if err != nil {
		return fmt.Errorf("failed to load cooling config: %w", err)
	}

	// Validate at least one loop exists
	if len(req.Loops) == 0 {
		return fmt.Errorf("at least one cooling loop is required")
	}

	// Validate each loop
	for loopIdx, loop := range req.Loops {
		// Validate loop_id
		if loop.LoopID == "" {
			return fmt.Errorf("loop[%d]: loop_id is required", loopIdx)
		}

		// Validate components exist
		if len(loop.Components.Components) == 0 {
			return fmt.Errorf("loop '%s': at least one component is required", loop.LoopID)
		}

		// Build component ID map for validation
		componentIDs := make(map[string]bool)
		for _, comp := range loop.Components.Components {
			if comp.ID == "" {
				return fmt.Errorf("loop '%s': component ID is required", loop.LoopID)
			}
			if comp.Type == "" {
				return fmt.Errorf("loop '%s': component '%s' type is required", loop.LoopID, comp.ID)
			}
			componentIDs[comp.ID] = true
		}

		// Validate connections reference valid components
		for connIdx, conn := range loop.Components.Connections {
			if !componentIDs[conn.From] {
				return fmt.Errorf("loop '%s': connection[%d] references unknown component '%s'", loop.LoopID, connIdx, conn.From)
			}
			if !componentIDs[conn.To] {
				return fmt.Errorf("loop '%s': connection[%d] references unknown component '%s'", loop.LoopID, connIdx, conn.To)
			}
		}

		// Validate sensors
		for sensorIdx, sensor := range loop.Components.Sensors {
			// Validate sensor has required fields
			if sensor.ID == "" {
				return fmt.Errorf("loop '%s': sensor[%d] ID is required", loop.LoopID, sensorIdx)
			}
			if sensor.Type == "" {
				return fmt.Errorf("loop '%s': sensor '%s' type is required", loop.LoopID, sensor.ID)
			}
			if sensor.AttachedTo == "" {
				return fmt.Errorf("loop '%s': sensor '%s' must be attached to a component", loop.LoopID, sensor.ID)
			}

			// Validate sensor attached to valid component
			if !componentIDs[sensor.AttachedTo] {
				return fmt.Errorf("loop '%s': sensor '%s' attached to unknown component '%s'", loop.LoopID, sensor.ID, sensor.AttachedTo)
			}

			// Validate sensor value based on type
			if err := s.validateSensorValue(sensor, coolingCfg, loop.LoopID); err != nil {
				return err
			}
		}

		// Validate component properties
		for _, comp := range loop.Components.Components {
			if err := s.validateComponentProperties(comp, coolingCfg, loop.LoopID); err != nil {
				return err
			}
		}
	}

	return nil
}

// validateSensorValue validates sensor value based on sensor type
func (s *Server) validateSensorValue(sensor models.Sensor, cfg *models.CoolingConfig, loopID string) error {
	switch sensor.Type {
	case "TEMPERATURE":
		temp, ok := sensor.Value.(float64)
		if !ok {
			return fmt.Errorf("loop '%s': sensor '%s' temperature must be numeric", loopID, sensor.ID)
		}
		if temp < cfg.Cooling.Validation.MinTemperature || temp > cfg.Cooling.Validation.MaxTemperature {
			return fmt.Errorf("loop '%s': sensor '%s' temperature %.1f°C out of valid range (%.1f to %.1f°C)",
				loopID, sensor.ID, temp, cfg.Cooling.Validation.MinTemperature, cfg.Cooling.Validation.MaxTemperature)
		}

	case "PRESSURE":
		pressure, ok := sensor.Value.(float64)
		if !ok {
			return fmt.Errorf("loop '%s': sensor '%s' pressure must be numeric", loopID, sensor.ID)
		}
		if pressure < cfg.Cooling.Validation.MinPressure || pressure > cfg.Cooling.Validation.MaxPressure {
			return fmt.Errorf("loop '%s': sensor '%s' pressure %.1f PSI out of valid range (%.1f to %.1f PSI)",
				loopID, sensor.ID, pressure, cfg.Cooling.Validation.MinPressure, cfg.Cooling.Validation.MaxPressure)
		}

	case "FLOW":
		flow, ok := sensor.Value.(float64)
		if !ok {
			return fmt.Errorf("loop '%s': sensor '%s' flow must be numeric", loopID, sensor.ID)
		}
		if flow < cfg.Cooling.Validation.MinFlow || flow > cfg.Cooling.Validation.MaxFlow {
			return fmt.Errorf("loop '%s': sensor '%s' flow %.1f LPM out of valid range (%.1f to %.1f LPM)",
				loopID, sensor.ID, flow, cfg.Cooling.Validation.MinFlow, cfg.Cooling.Validation.MaxFlow)
		}

	case "LEAK":
		_, ok := sensor.Value.(bool)
		if !ok {
			return fmt.Errorf("loop '%s': sensor '%s' leak value must be boolean", loopID, sensor.ID)
		}

	default:
		return fmt.Errorf("loop '%s': sensor '%s' has unknown type '%s'", loopID, sensor.ID, sensor.Type)
	}

	return nil
}

// validateComponentProperties validates component-specific properties
func (s *Server) validateComponentProperties(comp models.Component, cfg *models.CoolingConfig, loopID string) error {
	switch comp.Type {
	case "PUMP":
		// Validate RPM if present
		if rpmVal, ok := comp.Properties["rpm"]; ok {
			rpm, ok := rpmVal.(float64)
			if !ok {
				return fmt.Errorf("loop '%s': component '%s' rpm must be numeric", loopID, comp.ID)
			}
			if rpm < 0 || rpm > cfg.Cooling.Components.Pump.MaxRPM {
				return fmt.Errorf("loop '%s': component '%s' rpm %.0f out of range (0 to %.0f)",
					loopID, comp.ID, rpm, cfg.Cooling.Components.Pump.MaxRPM)
			}
		}

	case "SERVER":
		// Validate heat load if present
		if heatLoadVal, ok := comp.Properties["heatLoad_kw"]; ok {
			heatLoad, ok := heatLoadVal.(float64)
			if !ok {
				return fmt.Errorf("loop '%s': component '%s' heatLoad_kw must be numeric", loopID, comp.ID)
			}
			if heatLoad < 0 || heatLoad > cfg.Cooling.Components.Server.MaxHeatLoad {
				return fmt.Errorf("loop '%s': component '%s' heat load %.1f kW out of range (0 to %.1f kW)",
					loopID, comp.ID, heatLoad, cfg.Cooling.Components.Server.MaxHeatLoad)
			}
		}

	case "CONDENSER", "HEAT_EXCHANGER":
		// No specific property validation for now

	default:
		// Unknown component types are allowed (extensible design)
	}

	return nil
}

// convertCoolingToMetrics converts cooling data into individual metrics
func (s *Server) convertCoolingToMetrics(req *models.CoolingMetricsRequest) []models.Metric {
	metrics := []models.Metric{}

	timestamp := req.Timestamp
	if timestamp.IsZero() {
		timestamp = time.Now()
	}

	// Iterate through all cooling loops
	for _, loop := range req.Loops {
		// 1. Store all sensor readings
		for _, sensor := range loop.Components.Sensors {
			value := sensorValueToFloat64(sensor.Value)

			// Metric naming: cooling.{loop_id}.{component_id}.{position}.{sensor_type}
			metricType := fmt.Sprintf("cooling.%s.%s.%s.%s",
				loop.LoopID,
				sensor.AttachedTo,
				sensor.Position,
				sensor.Type)

			metrics = append(metrics, models.Metric{
				AgentID:    req.AgentID,
				Timestamp:  timestamp,
				MetricType: metricType,
				Value:      value,
				Unit:       sensor.Unit,
				CreatedAt:  time.Now(),
			})
		}

		// 2. Store component properties as metrics
		for _, comp := range loop.Components.Components {
			for propName, propValue := range comp.Properties {
				value := propertyValueToFloat64(propValue)

				// Metric naming: cooling.{loop_id}.{component_id}.{property_name}
				metricType := fmt.Sprintf("cooling.%s.%s.%s",
					loop.LoopID,
					comp.ID,
					propName)

				metrics = append(metrics, models.Metric{
					AgentID:    req.AgentID,
					Timestamp:  timestamp,
					MetricType: metricType,
					Value:      value,
					Unit:       inferUnitFromProperty(propName),
					CreatedAt:  time.Now(),
				})
			}
		}

		// 3. Calculate derived metrics for this loop
		derivedMetrics := s.calculateDerivedMetrics(loop, req.AgentID, timestamp)
		metrics = append(metrics, derivedMetrics...)
	}

	return metrics
}

// sensorValueToFloat64 converts sensor value to float64
func sensorValueToFloat64(value interface{}) float64 {
	switch v := value.(type) {
	case float64:
		return v
	case bool:
		return boolToFloat(v)
	case int:
		return float64(v)
	default:
		return 0.0
	}
}

// propertyValueToFloat64 converts property value to float64
func propertyValueToFloat64(value interface{}) float64 {
	switch v := value.(type) {
	case float64:
		return v
	case bool:
		return boolToFloat(v)
	case int:
		return float64(v)
	case string:
		// For string properties, return 0 (can't convert)
		return 0.0
	default:
		return 0.0
	}
}

// inferUnitFromProperty infers unit from property name
func inferUnitFromProperty(propName string) string {
	switch propName {
	case "rpm":
		return "rpm"
	case "heatLoad_kw":
		return "kw"
	case "isOn", "cpuOn":
		return "boolean"
	default:
		return "unknown"
	}
}

// calculateDerivedMetrics calculates derived metrics for a cooling loop
func (s *Server) calculateDerivedMetrics(loop models.CoolingLoop, agentID string, timestamp time.Time) []models.Metric {
	metrics := []models.Metric{}

	// Build sensor map for easy lookup
	sensorMap := make(map[string]map[string]map[string]models.Sensor) // [component_id][position][type]sensor
	for _, sensor := range loop.Components.Sensors {
		if sensorMap[sensor.AttachedTo] == nil {
			sensorMap[sensor.AttachedTo] = make(map[string]map[string]models.Sensor)
		}
		if sensorMap[sensor.AttachedTo][sensor.Position] == nil {
			sensorMap[sensor.AttachedTo][sensor.Position] = make(map[string]models.Sensor)
		}
		sensorMap[sensor.AttachedTo][sensor.Position][sensor.Type] = sensor
	}

	// Calculate temperature delta across each component
	for _, comp := range loop.Components.Components {
		if inletSensors, hasInlet := sensorMap[comp.ID]["INLET"]; hasInlet {
			if outletSensors, hasOutlet := sensorMap[comp.ID]["OUTLET"]; hasOutlet {
				if inletTemp, hasInletTemp := inletSensors["TEMPERATURE"]; hasInletTemp {
					if outletTemp, hasOutletTemp := outletSensors["TEMPERATURE"]; hasOutletTemp {
						inletTempVal := sensorValueToFloat64(inletTemp.Value)
						outletTempVal := sensorValueToFloat64(outletTemp.Value)
						deltaTemp := outletTempVal - inletTempVal

						metrics = append(metrics, models.Metric{
							AgentID:    agentID,
							Timestamp:  timestamp,
							MetricType: fmt.Sprintf("cooling.%s.%s.delta_temperature", loop.LoopID, comp.ID),
							Value:      deltaTemp,
							Unit:       "celsius",
							CreatedAt:  time.Now(),
						})
					}
				}

				// Calculate pressure drop across component
				if inletPress, hasInletPress := inletSensors["PRESSURE"]; hasInletPress {
					if outletPress, hasOutletPress := outletSensors["PRESSURE"]; hasOutletPress {
						inletPressVal := sensorValueToFloat64(inletPress.Value)
						outletPressVal := sensorValueToFloat64(outletPress.Value)
						pressureDrop := inletPressVal - outletPressVal

						metrics = append(metrics, models.Metric{
							AgentID:    agentID,
							Timestamp:  timestamp,
							MetricType: fmt.Sprintf("cooling.%s.%s.pressure_drop", loop.LoopID, comp.ID),
							Value:      pressureDrop,
							Unit:       "psi",
							CreatedAt:  time.Now(),
						})
					}
				}
			}
		}
	}

	return metrics
}

// boolToFloat converts boolean to float64 (1.0 for true, 0.0 for false)
func boolToFloat(b bool) float64 {
	if b {
		return 1.0
	}
	return 0.0
}

// evaluateCoolingAlerts evaluates all cooling alert conditions based on graph structure
func (s *Server) evaluateCoolingAlerts(req *models.CoolingMetricsRequest) []models.Alert {
	alerts := []models.Alert{}

	// Load cooling config
	coolingCfg, err := s.loadCoolingConfig()
	if err != nil {
		s.logger.Printf("Failed to load cooling config for alert evaluation: %v", err)
		return alerts
	}

	if !coolingCfg.Cooling.Alerts.Enabled {
		return alerts
	}

	timestamp := req.Timestamp
	if timestamp.IsZero() {
		timestamp = time.Now()
	}

	// Evaluate alerts for each loop
	for _, loop := range req.Loops {
		loopAlerts := s.evaluateLoopAlerts(loop, req.AgentID, timestamp, coolingCfg)
		alerts = append(alerts, loopAlerts...)
	}

	return alerts
}

// evaluateLoopAlerts evaluates alerts for a single cooling loop
func (s *Server) evaluateLoopAlerts(loop models.CoolingLoop, agentID string, timestamp time.Time, cfg *models.CoolingConfig) []models.Alert {
	alerts := []models.Alert{}

	// Build sensor map for easy lookup: [component_id][position][type]sensor
	sensorMap := make(map[string]map[string]map[string]models.Sensor)
	for _, sensor := range loop.Components.Sensors {
		if sensorMap[sensor.AttachedTo] == nil {
			sensorMap[sensor.AttachedTo] = make(map[string]map[string]models.Sensor)
		}
		if sensorMap[sensor.AttachedTo][sensor.Position] == nil {
			sensorMap[sensor.AttachedTo][sensor.Position] = make(map[string]models.Sensor)
		}
		sensorMap[sensor.AttachedTo][sensor.Position][sensor.Type] = sensor
	}

	// Build component map for easy lookup
	componentMap := make(map[string]models.Component)
	for _, comp := range loop.Components.Components {
		componentMap[comp.ID] = comp
	}

	// Check each component for alert conditions
	for _, comp := range loop.Components.Components {
		compAlerts := s.evaluateComponentAlerts(comp, sensorMap, loop.LoopID, agentID, timestamp, cfg)
		alerts = append(alerts, compAlerts...)
	}

	// Check leak sensors
	for _, sensor := range loop.Components.Sensors {
		if sensor.Type == "LEAK" {
			leakDetected, ok := sensor.Value.(bool)
			if ok && leakDetected {
				alerts = append(alerts, models.Alert{
					AgentID:    agentID,
					Timestamp:  timestamp,
					Severity:   "CRITICAL",
					MetricType: fmt.Sprintf("cooling.%s.%s.%s.leak", loop.LoopID, sensor.AttachedTo, sensor.Position),
					Value:      1.0,
					Threshold:  0.0,
					Message: fmt.Sprintf("LEAK DETECTED at %s %s on component %s - immediate inspection required",
						sensor.AttachedTo, sensor.Position, sensor.AttachedTo),
					CreatedAt: time.Now(),
				})
			}
		}
	}

	// Check global pressure conditions
	pressureAlerts := s.evaluatePressureAlerts(loop, sensorMap, agentID, timestamp, cfg)
	alerts = append(alerts, pressureAlerts...)

	return alerts
}

// evaluateComponentAlerts evaluates alerts for a specific component
func (s *Server) evaluateComponentAlerts(comp models.Component, sensorMap map[string]map[string]map[string]models.Sensor,
	loopID string, agentID string, timestamp time.Time, cfg *models.CoolingConfig) []models.Alert {
	alerts := []models.Alert{}

	switch comp.Type {
	case "CONDENSER", "HEAT_EXCHANGER":
		alerts = append(alerts, s.evaluateCondenserAlerts(comp, sensorMap, loopID, agentID, timestamp, cfg)...)
	case "SERVER":
		alerts = append(alerts, s.evaluateServerAlerts(comp, sensorMap, loopID, agentID, timestamp, cfg)...)
	case "PUMP":
		alerts = append(alerts, s.evaluatePumpAlerts(comp, sensorMap, loopID, agentID, timestamp, cfg)...)
	}

	return alerts
}

// evaluateCondenserAlerts checks condenser efficiency
func (s *Server) evaluateCondenserAlerts(comp models.Component, sensorMap map[string]map[string]map[string]models.Sensor,
	loopID string, agentID string, timestamp time.Time, cfg *models.CoolingConfig) []models.Alert {
	alerts := []models.Alert{}

	// Check if condenser has inlet and outlet temperature sensors
	if inletSensors, hasInlet := sensorMap[comp.ID]["INLET"]; hasInlet {
		if outletSensors, hasOutlet := sensorMap[comp.ID]["OUTLET"]; hasOutlet {
			if inletTempSensor, hasInletTemp := inletSensors["TEMPERATURE"]; hasInletTemp {
				if outletTempSensor, hasOutletTemp := outletSensors["TEMPERATURE"]; hasOutletTemp {
					inletTemp := sensorValueToFloat64(inletTempSensor.Value)
					outletTemp := sensorValueToFloat64(outletTempSensor.Value)
					tempDiff := inletTemp - outletTemp // Condenser should cool, so inlet > outlet

					// Check if condenser is ON
					condenserOn := false
					if isOnVal, hasIsOn := comp.Properties["isOn"]; hasIsOn {
						condenserOn, _ = isOnVal.(bool)
					}

					if condenserOn {
						// ALERT: Condenser not working (inlet temp = outlet temp)
						if math.Abs(tempDiff) < cfg.Cooling.Components.Condenser.MinTempDifference {
							alerts = append(alerts, models.Alert{
								AgentID:    agentID,
								Timestamp:  timestamp,
								Severity:   "CRITICAL",
								MetricType: fmt.Sprintf("cooling.%s.%s.efficiency", loopID, comp.ID),
								Value:      tempDiff,
								Threshold:  cfg.Cooling.Components.Condenser.MinTempDifference,
								Message: fmt.Sprintf("Condenser %s failure: Inlet temp %.1f°C equals outlet temp %.1f°C (diff: %.1f°C) - condenser not cooling",
									comp.ID, inletTemp, outletTemp, tempDiff),
								CreatedAt: time.Now(),
							})
						} else if tempDiff < cfg.Cooling.Components.Condenser.EfficiencyWarning {
							// ALERT: Low condenser efficiency
							alerts = append(alerts, models.Alert{
								AgentID:    agentID,
								Timestamp:  timestamp,
								Severity:   "WARNING",
								MetricType: fmt.Sprintf("cooling.%s.%s.efficiency", loopID, comp.ID),
								Value:      tempDiff,
								Threshold:  cfg.Cooling.Components.Condenser.EfficiencyWarning,
								Message: fmt.Sprintf("Condenser %s low efficiency: Temperature reduction only %.1f°C (inlet: %.1f°C, outlet: %.1f°C)",
									comp.ID, tempDiff, inletTemp, outletTemp),
								CreatedAt: time.Now(),
							})
						}

						// ALERT: Condenser ON but inlet too hot
						if inletTemp > cfg.Cooling.Temperature.InletMaxCondenserOn {
							alerts = append(alerts, models.Alert{
								AgentID:    agentID,
								Timestamp:  timestamp,
								Severity:   "CRITICAL",
								MetricType: fmt.Sprintf("cooling.%s.%s.inlet_temp", loopID, comp.ID),
								Value:      inletTemp,
								Threshold:  cfg.Cooling.Temperature.InletMaxCondenserOn,
								Message: fmt.Sprintf("Condenser %s overload: Inlet temperature %.1f°C exceeds maximum %.1f°C - condenser cannot keep up",
									comp.ID, inletTemp, cfg.Cooling.Temperature.InletMaxCondenserOn),
								CreatedAt: time.Now(),
							})
						}
					}

					// ALERT: Outlet temperature too high (even if condenser off)
					if outletTemp > cfg.Cooling.Temperature.OutletMaxNormal {
						severity := "WARNING"
						if outletTemp > cfg.Cooling.Temperature.OutletMaxCritical {
							severity = "CRITICAL"
						}
						alerts = append(alerts, models.Alert{
							AgentID:    agentID,
							Timestamp:  timestamp,
							Severity:   severity,
							MetricType: fmt.Sprintf("cooling.%s.%s.outlet_temp", loopID, comp.ID),
							Value:      outletTemp,
							Threshold:  cfg.Cooling.Temperature.OutletMaxNormal,
							Message: fmt.Sprintf("Condenser %s outlet temperature %.1f°C exceeds normal maximum %.1f°C",
								comp.ID, outletTemp, cfg.Cooling.Temperature.OutletMaxNormal),
							CreatedAt: time.Now(),
						})
					}
				}
			}
		}
	}

	return alerts
}

// evaluateServerAlerts checks server cooling
func (s *Server) evaluateServerAlerts(comp models.Component, sensorMap map[string]map[string]map[string]models.Sensor,
	loopID string, agentID string, timestamp time.Time, cfg *models.CoolingConfig) []models.Alert {
	alerts := []models.Alert{}

	// Check if server has inlet and outlet temperature sensors
	if inletSensors, hasInlet := sensorMap[comp.ID]["INLET"]; hasInlet {
		if outletSensors, hasOutlet := sensorMap[comp.ID]["OUTLET"]; hasOutlet {
			if inletTempSensor, hasInletTemp := inletSensors["TEMPERATURE"]; hasInletTemp {
				if outletTempSensor, hasOutletTemp := outletSensors["TEMPERATURE"]; hasOutletTemp {
					inletTemp := sensorValueToFloat64(inletTempSensor.Value)
					outletTemp := sensorValueToFloat64(outletTempSensor.Value)
					tempRise := outletTemp - inletTemp // Server should add heat, so outlet > inlet

					// Check if CPU is ON
					cpuOn := false
					if cpuOnVal, hasCpuOn := comp.Properties["cpuOn"]; hasCpuOn {
						cpuOn, _ = cpuOnVal.(bool)
					}

					if cpuOn {
						// ALERT: CPU ON but no temperature rise
						if tempRise < cfg.Cooling.Components.Server.MinTempRise {
							alerts = append(alerts, models.Alert{
								AgentID:    agentID,
								Timestamp:  timestamp,
								Severity:   "CRITICAL",
								MetricType: fmt.Sprintf("cooling.%s.%s.temp_rise", loopID, comp.ID),
								Value:      tempRise,
								Threshold:  cfg.Cooling.Components.Server.MinTempRise,
								Message: fmt.Sprintf("Server %s cooling issue: CPU ON but temperature rise only %.1f°C (inlet: %.1f°C, outlet: %.1f°C) - CPU may not be working or no thermal contact",
									comp.ID, tempRise, inletTemp, outletTemp),
								CreatedAt: time.Now(),
							})
						}

						// ALERT: Server outlet temperature too high
						if outletTemp > cfg.Cooling.Components.Server.MaxOutletTemp {
							alerts = append(alerts, models.Alert{
								AgentID:    agentID,
								Timestamp:  timestamp,
								Severity:   "CRITICAL",
								MetricType: fmt.Sprintf("cooling.%s.%s.outlet_temp", loopID, comp.ID),
								Value:      outletTemp,
								Threshold:  cfg.Cooling.Components.Server.MaxOutletTemp,
								Message: fmt.Sprintf("Server %s overheating: Outlet temperature %.1f°C exceeds maximum %.1f°C - immediate action required",
									comp.ID, outletTemp, cfg.Cooling.Components.Server.MaxOutletTemp),
								CreatedAt: time.Now(),
							})
						}
					} else {
						// ALERT: CPU OFF but temperature rising
						if tempRise > cfg.Cooling.Temperature.TemperatureTolerance {
							alerts = append(alerts, models.Alert{
								AgentID:    agentID,
								Timestamp:  timestamp,
								Severity:   "WARNING",
								MetricType: fmt.Sprintf("cooling.%s.%s.state", loopID, comp.ID),
								Value:      tempRise,
								Threshold:  cfg.Cooling.Temperature.TemperatureTolerance,
								Message: fmt.Sprintf("Server %s inconsistent: CPU reported OFF but outlet %.1f°C is higher than inlet %.1f°C - possible sensor failure or residual heat",
									comp.ID, outletTemp, inletTemp),
								CreatedAt: time.Now(),
							})
						}
					}

					// Check heat load
					if heatLoadVal, hasHeatLoad := comp.Properties["heatLoad_kw"]; hasHeatLoad {
						heatLoad, _ := heatLoadVal.(float64)
						if heatLoad > cfg.Cooling.Components.Server.MaxHeatLoad {
							alerts = append(alerts, models.Alert{
								AgentID:    agentID,
								Timestamp:  timestamp,
								Severity:   "WARNING",
								MetricType: fmt.Sprintf("cooling.%s.%s.heat_load", loopID, comp.ID),
								Value:      heatLoad,
								Threshold:  cfg.Cooling.Components.Server.MaxHeatLoad,
								Message: fmt.Sprintf("Server %s high heat load: %.1f kW exceeds maximum %.1f kW",
									comp.ID, heatLoad, cfg.Cooling.Components.Server.MaxHeatLoad),
								CreatedAt: time.Now(),
							})
						}
					}
				}
			}
		}
	}

	return alerts
}

// evaluatePumpAlerts checks pump operation
func (s *Server) evaluatePumpAlerts(comp models.Component, sensorMap map[string]map[string]map[string]models.Sensor,
	loopID string, agentID string, timestamp time.Time, cfg *models.CoolingConfig) []models.Alert {
	alerts := []models.Alert{}

	// Check if pump is ON
	pumpOn := false
	if isOnVal, hasIsOn := comp.Properties["isOn"]; hasIsOn {
		pumpOn, _ = isOnVal.(bool)
	}

	// Check RPM
	if rpmVal, hasRPM := comp.Properties["rpm"]; hasRPM {
		rpm, _ := rpmVal.(float64)

		if pumpOn {
			// ALERT: Pump ON but RPM too low
			if rpm < cfg.Cooling.Components.Pump.MinRPM {
				alerts = append(alerts, models.Alert{
					AgentID:    agentID,
					Timestamp:  timestamp,
					Severity:   "CRITICAL",
					MetricType: fmt.Sprintf("cooling.%s.%s.rpm", loopID, comp.ID),
					Value:      rpm,
					Threshold:  cfg.Cooling.Components.Pump.MinRPM,
					Message: fmt.Sprintf("Pump %s failure: RPM %.0f is below minimum %.0f - pump not functioning",
						comp.ID, rpm, cfg.Cooling.Components.Pump.MinRPM),
					CreatedAt: time.Now(),
				})
			} else if rpm > cfg.Cooling.Components.Pump.MaxRPM {
				// ALERT: RPM too high
				alerts = append(alerts, models.Alert{
					AgentID:    agentID,
					Timestamp:  timestamp,
					Severity:   "WARNING",
					MetricType: fmt.Sprintf("cooling.%s.%s.rpm", loopID, comp.ID),
					Value:      rpm,
					Threshold:  cfg.Cooling.Components.Pump.MaxRPM,
					Message: fmt.Sprintf("Pump %s high RPM: %.0f exceeds maximum %.0f - possible pump issue",
						comp.ID, rpm, cfg.Cooling.Components.Pump.MaxRPM),
					CreatedAt: time.Now(),
				})
			}
		} else {
			// ALERT: Pump OFF but RPM detected
			if rpm > 0 {
				alerts = append(alerts, models.Alert{
					AgentID:    agentID,
					Timestamp:  timestamp,
					Severity:   "WARNING",
					MetricType: fmt.Sprintf("cooling.%s.%s.state", loopID, comp.ID),
					Value:      rpm,
					Threshold:  0.0,
					Message: fmt.Sprintf("Pump %s inconsistent: Reported OFF but RPM is %.0f - possible sensor failure",
						comp.ID, rpm),
					CreatedAt: time.Now(),
				})
			}
		}
	}

	// Check flow rate
	if outletSensors, hasOutlet := sensorMap[comp.ID]["OUTLET"]; hasOutlet {
		if flowSensor, hasFlow := outletSensors["FLOW"]; hasFlow {
			flow := sensorValueToFloat64(flowSensor.Value)

			if pumpOn {
				// ALERT: Pump ON but no flow
				if flow < cfg.Cooling.Flow.ZeroFlowThreshold {
					alerts = append(alerts, models.Alert{
						AgentID:    agentID,
						Timestamp:  timestamp,
						Severity:   "CRITICAL",
						MetricType: fmt.Sprintf("cooling.%s.%s.flow", loopID, comp.ID),
						Value:      flow,
						Threshold:  cfg.Cooling.Components.Pump.MinFlowRate,
						Message: fmt.Sprintf("Pump %s failure: Flow %.1f LPM near zero - pump not moving fluid",
							comp.ID, flow),
						CreatedAt: time.Now(),
					})
				} else if flow < cfg.Cooling.Components.Pump.MinFlowRate {
					// ALERT: Low flow
					alerts = append(alerts, models.Alert{
						AgentID:    agentID,
						Timestamp:  timestamp,
						Severity:   "WARNING",
						MetricType: fmt.Sprintf("cooling.%s.%s.flow", loopID, comp.ID),
						Value:      flow,
						Threshold:  cfg.Cooling.Components.Pump.MinFlowRate,
						Message: fmt.Sprintf("Pump %s low flow: %.1f LPM below minimum %.1f LPM - possible blockage or pump issue",
							comp.ID, flow, cfg.Cooling.Components.Pump.MinFlowRate),
						CreatedAt: time.Now(),
					})
				}
			}
		}
	}

	return alerts
}

// evaluatePressureAlerts checks global pressure conditions
func (s *Server) evaluatePressureAlerts(loop models.CoolingLoop, sensorMap map[string]map[string]map[string]models.Sensor,
	agentID string, timestamp time.Time, cfg *models.CoolingConfig) []models.Alert {
	alerts := []models.Alert{}

	leakageThreshold := cfg.Cooling.Pressure.NormalMin - cfg.Cooling.Pressure.LeakageThreshold

	// Check all pressure sensors
	for _, sensor := range loop.Components.Sensors {
		if sensor.Type == "PRESSURE" {
			pressure := sensorValueToFloat64(sensor.Value)

			// ALERT: Critical low pressure (leak suspected)
			if pressure < leakageThreshold {
				alerts = append(alerts, models.Alert{
					AgentID:    agentID,
					Timestamp:  timestamp,
					Severity:   "CRITICAL",
					MetricType: fmt.Sprintf("cooling.%s.%s.%s.pressure", loop.LoopID, sensor.AttachedTo, sensor.Position),
					Value:      pressure,
					Threshold:  leakageThreshold,
					Message: fmt.Sprintf("Critical low pressure at %s %s: %.1f PSI below leakage threshold %.1f PSI - leak suspected",
						sensor.AttachedTo, sensor.Position, pressure, leakageThreshold),
					CreatedAt: time.Now(),
				})
			} else if pressure < cfg.Cooling.Pressure.NormalMin || pressure > cfg.Cooling.Pressure.NormalMax {
				// ALERT: Pressure out of normal range
				alerts = append(alerts, models.Alert{
					AgentID:    agentID,
					Timestamp:  timestamp,
					Severity:   "WARNING",
					MetricType: fmt.Sprintf("cooling.%s.%s.%s.pressure", loop.LoopID, sensor.AttachedTo, sensor.Position),
					Value:      pressure,
					Threshold:  cfg.Cooling.Pressure.NormalOperating,
					Message: fmt.Sprintf("Abnormal pressure at %s %s: %.1f PSI outside normal range %.1f-%.1f PSI",
						sensor.AttachedTo, sensor.Position, pressure, cfg.Cooling.Pressure.NormalMin, cfg.Cooling.Pressure.NormalMax),
					CreatedAt: time.Now(),
				})
			}
		}
	}

	return alerts
}
