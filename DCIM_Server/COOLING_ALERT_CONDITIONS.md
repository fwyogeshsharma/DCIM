# Water Cooling System - Alert Conditions

## All Alert Conditions for Cooling System Monitoring

---

### 🌡️ TEMPERATURE ALERTS

#### ALERT-TEMP-001: Condenser Running but Inlet Too Hot
**Condition:** `condenser.isOn == true AND inlet.temperature > 10°C`
**Severity:** CRITICAL
**Metric Type:** `cooling.inlet.temperature`
**Message:** "Condenser failure: Inlet temperature {temp}°C exceeds maximum 10°C with condenser ON"
**Action:** Check condenser operation, verify refrigerant levels

---

#### ALERT-TEMP-002: CPU Overheating (High Outlet Temperature)
**Condition:** `outlet.temperature > 50°C`
**Severity:** CRITICAL
**Metric Type:** `cooling.outlet.temperature`
**Message:** "CPU overheating: Outlet temperature {temp}°C exceeds normal maximum 50°C"
**Action:** Check CPU thermal paste, verify pump flow, increase cooling capacity

---

#### ALERT-TEMP-003: Emergency Shutdown Temperature
**Condition:** `outlet.temperature > 60°C`
**Severity:** CRITICAL
**Metric Type:** `cooling.outlet.temperature`
**Message:** "EMERGENCY: Outlet temperature {temp}°C exceeds critical threshold 60°C - immediate shutdown recommended"
**Action:** Emergency shutdown may be required

---

#### ALERT-TEMP-004: No Cooling Detected (CPU Not Working)
**Condition:** `cpu.isOn == true AND abs(outlet.temperature - inlet.temperature) <= 2°C`
**Severity:** CRITICAL
**Metric Type:** `cooling.delta_temperature`
**Message:** "CPU not cooling: Temperature difference only {delta}°C (inlet: {inlet}°C, outlet: {outlet}°C) - CPU may not be working or thermal contact lost"
**Action:** Check CPU operation, verify thermal interface, check for air gaps

---

#### ALERT-TEMP-005: Insufficient Cooling Performance
**Condition:** `cpu.isOn == true AND (outlet.temperature - inlet.temperature) < 3°C AND outlet.temperature > 45°C`
**Severity:** WARNING
**Metric Type:** `cooling.delta_temperature`
**Message:** "Insufficient cooling: Temperature delta {delta}°C is below expected minimum 3°C"
**Action:** Verify pump flow rate, check for blockages

---

### 🔧 PRESSURE ALERTS

#### ALERT-PRES-001: Low Pressure (Leakage Suspected)
**Condition:** `inlet.pressure < 30 PSI OR outlet.pressure < 30 PSI` (i.e., below min - leakage_threshold)
**Severity:** CRITICAL
**Metric Type:** `cooling.pressure`
**Message:** "Critical low pressure detected: {location} pressure {pressure} PSI is below leakage threshold 30 PSI - leak suspected"
**Action:** Inspect system for leaks, check fittings and connections

---

#### ALERT-PRES-002: Pressure Out of Normal Range
**Condition:** `(inlet.pressure < 40 OR inlet.pressure > 60) OR (outlet.pressure < 40 OR outlet.pressure > 60)`
**Severity:** WARNING
**Metric Type:** `cooling.pressure`
**Message:** "Abnormal pressure: {location} pressure {pressure} PSI outside normal range 40-60 PSI"
**Action:** Monitor system, check pump operation

---

#### ALERT-PRES-003: High Pressure
**Condition:** `inlet.pressure > 60 PSI OR outlet.pressure > 60 PSI`
**Severity:** WARNING
**Metric Type:** `cooling.pressure`
**Message:** "High pressure detected: {location} pressure {pressure} PSI exceeds maximum 60 PSI"
**Action:** Check for blockages, verify pressure relief valve

---

#### ALERT-PRES-004: Pressure Drop Between Inlet/Outlet
**Condition:** `abs(inlet.pressure - outlet.pressure) > 10 PSI`
**Severity:** WARNING
**Metric Type:** `cooling.pressure_drop`
**Message:** "Excessive pressure drop: {drop} PSI between inlet and outlet - check for blockages or leaks"
**Action:** Inspect water block, check for restrictions

---

### 💧 LEAK DETECTION ALERTS

#### ALERT-LEAK-001: Leak Detected at Inlet
**Condition:** `inlet.leak == true`
**Severity:** CRITICAL
**Metric Type:** `cooling.leak.inlet`
**Message:** "LEAK DETECTED at inlet - immediate inspection required"
**Action:** Shutdown system, inspect inlet connections

---

#### ALERT-LEAK-002: Leak Detected at Outlet
**Condition:** `outlet.leak == true`
**Severity:** CRITICAL
**Metric Type:** `cooling.leak.outlet`
**Message:** "LEAK DETECTED at outlet - immediate inspection required"
**Action:** Shutdown system, inspect outlet connections

---

#### ALERT-LEAK-003: Leak Indicated by Pressure Drop
**Condition:** `(inlet.leak == true OR outlet.leak == true) AND (inlet.pressure < 40 OR outlet.pressure < 40)`
**Severity:** CRITICAL
**Metric Type:** `cooling.leak.pressure_correlation`
**Message:** "Leak confirmed: Leak sensor triggered AND pressure dropped to {pressure} PSI"
**Action:** Emergency shutdown, locate and repair leak

---

### ⚙️ COMPONENT STATE VALIDATION ALERTS

#### ALERT-STATE-001: Pump OFF but Pressure Detected
**Condition:** `pump.isOn == false AND (inlet.pressure > 5 OR outlet.pressure > 5)`
**Severity:** WARNING
**Metric Type:** `cooling.pump.state`
**Message:** "Inconsistent state: Pump reported OFF but pressure detected (inlet: {inlet_p} PSI, outlet: {outlet_p} PSI) - possible pump status sensor failure"
**Action:** Verify pump status sensor, check for valve issues

---

#### ALERT-STATE-002: Condenser OFF but Temperature Difference
**Condition:** `condenser.isOn == false AND abs(inlet.temperature - outlet.temperature) > 2°C`
**Severity:** INFO
**Metric Type:** `cooling.condenser.state`
**Message:** "Condenser OFF but temperature difference detected: {delta}°C (inlet: {inlet}°C, outlet: {outlet}°C)"
**Action:** Verify condenser status, normal if CPU is generating heat

---

#### ALERT-STATE-003: CPU OFF but Temperature Rising
**Condition:** `cpu.isOn == false AND outlet.temperature > (inlet.temperature + 2)`
**Severity:** WARNING
**Metric Type:** `cooling.cpu.state`
**Message:** "CPU reported OFF but outlet temperature {outlet}°C is higher than inlet {inlet}°C - possible CPU status sensor failure or residual heat"
**Action:** Verify CPU status sensor

---

#### ALERT-STATE-004: Pump OFF but Normal Pressure Expected
**Condition:** `pump.isOn == false AND inlet.pressure == 0 AND outlet.pressure == 0`
**Severity:** INFO
**Metric Type:** `cooling.pump.state`
**Message:** "Pump OFF: System pressure at 0 PSI (normal)"
**Action:** None - this is expected behavior

---

### 🔄 LOGICAL CONSISTENCY ALERTS

#### ALERT-LOGIC-001: Impossible Temperature Readings
**Condition:** `outlet.temperature < inlet.temperature AND cpu.isOn == true`
**Severity:** CRITICAL
**Metric Type:** `cooling.sensor.error`
**Message:** "Sensor error: Outlet temperature {outlet}°C is LESS than inlet {inlet}°C with CPU ON - physically impossible"
**Action:** Check temperature sensor calibration, verify sensor placement

---

#### ALERT-LOGIC-002: All Components OFF but System Active
**Condition:** `pump.isOn == false AND cpu.isOn == false AND condenser.isOn == false AND (inlet.pressure > 0 OR outlet.pressure > 0)`
**Severity:** WARNING
**Metric Type:** `cooling.system.state`
**Message:** "All components reported OFF but pressure detected - possible state reporting error"
**Action:** Verify all component status sensors

---

#### ALERT-LOGIC-003: Pressure Without Flow
**Condition:** `pump.isOn == false AND (inlet.pressure > 10 OR outlet.pressure > 10)`
**Severity:** WARNING
**Metric Type:** `cooling.pressure.anomaly`
**Message:** "Abnormal pressure {pressure} PSI detected with pump OFF - check for valve issues or pressure sensor malfunction"
**Action:** Inspect valves, verify pressure sensors

---

## Summary of Alert Counts

- **Temperature Alerts:** 5
- **Pressure Alerts:** 4
- **Leak Detection Alerts:** 3
- **Component State Alerts:** 4
- **Logical Consistency Alerts:** 3

**Total Alert Conditions:** 19

---

## Alert Severity Distribution

- **CRITICAL:** 10 conditions (immediate action required)
- **WARNING:** 7 conditions (monitor closely)
- **INFO:** 2 conditions (informational only)

---

## Configuration Reference

All thresholds referenced in these conditions are defined in:
`DCIM_Server/cooling_config.yaml`

To modify alert thresholds, edit the configuration file and restart the server.
