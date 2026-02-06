package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"time"
)

// Simple test server for Network Monitor Agent
// This is a minimal implementation for testing purposes

type MetricsRequest struct {
	AgentID   string   `json:"agent_id"`
	Timestamp string   `json:"timestamp"`
	Metrics   []Metric `json:"metrics"`
}

type Metric struct {
	ID         int64                  `json:"id"`
	Timestamp  string                 `json:"timestamp"`
	MetricType string                 `json:"metric_type"`
	Value      float64                `json:"value"`
	Unit       string                 `json:"unit"`
	Metadata   map[string]interface{} `json:"metadata"`
	CreatedAt  string                 `json:"created_at"`
}

type AlertsRequest struct {
	AgentID   string  `json:"agent_id"`
	Timestamp string  `json:"timestamp"`
	Alerts    []Alert `json:"alerts"`
}

type Alert struct {
	ID         int64   `json:"id"`
	Timestamp  string  `json:"timestamp"`
	Severity   string  `json:"severity"`
	MetricType string  `json:"metric_type"`
	Value      float64 `json:"value"`
	Threshold  float64 `json:"threshold"`
	Message    string  `json:"message"`
	RetryCount int     `json:"retry_count"`
	CreatedAt  string  `json:"created_at"`
}

type SNMPMetricsRequest struct {
	AgentID     string       `json:"agent_id"`
	Timestamp   string       `json:"timestamp"`
	SNMPMetrics []SNMPMetric `json:"snmp_metrics"`
}

type SNMPMetric struct {
	ID         int64                  `json:"id"`
	Timestamp  string                 `json:"timestamp"`
	DeviceName string                 `json:"device_name"`
	DeviceHost string                 `json:"device_host"`
	OID        string                 `json:"oid"`
	MetricName string                 `json:"metric_name"`
	Value      float64                `json:"value"`
	ValueType  string                 `json:"value_type"`
	Metadata   map[string]interface{} `json:"metadata"`
	CreatedAt  string                 `json:"created_at"`
}

type Response struct {
	Success  bool   `json:"success"`
	Message  string `json:"message"`
	Error    string `json:"error,omitempty"`
	Accepted int    `json:"accepted,omitempty"`
	Rejected int    `json:"rejected,omitempty"`
}

func main() {
	http.HandleFunc("/api/v1/metrics", handleMetrics)
	http.HandleFunc("/api/v1/alerts", handleAlerts)
	http.HandleFunc("/api/v1/snmp-metrics", handleSNMPMetrics)
	http.HandleFunc("/health", handleHealth)

	addr := ":8080"
	log.Printf("Test monitoring server starting on %s", addr)
	log.Printf("Endpoints:")
	log.Printf("  POST http://localhost:8080/api/v1/metrics")
	log.Printf("  POST http://localhost:8080/api/v1/alerts")
	log.Printf("  POST http://localhost:8080/api/v1/snmp-metrics")
	log.Printf("  GET  http://localhost:8080/health")
	log.Printf("")
	log.Printf("Configure agent with: server.url: \"http://localhost:8080/api/v1\"")
	log.Printf("")

	if err := http.ListenAndServe(addr, logMiddleware(http.DefaultServeMux)); err != nil {
		log.Fatalf("Server failed: %v", err)
	}
}

func handleMetrics(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		sendError(w, http.StatusMethodNotAllowed, "Method not allowed")
		return
	}

	var req MetricsRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		sendError(w, http.StatusBadRequest, "Invalid JSON: "+err.Error())
		return
	}

	// Log received metrics
	log.Printf("📊 Received %d metrics from agent %s", len(req.Metrics), req.AgentID)
	for _, m := range req.Metrics {
		log.Printf("   - %s: %.2f %s (at %s)", m.MetricType, m.Value, m.Unit, m.Timestamp)
		if m.Metadata != nil && len(m.Metadata) > 0 {
			metadata, _ := json.Marshal(m.Metadata)
			log.Printf("     Metadata: %s", string(metadata))
		}
	}

	// Send success response
	sendSuccess(w, fmt.Sprintf("Received %d metrics", len(req.Metrics)), len(req.Metrics), 0)
}

func handleAlerts(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		sendError(w, http.StatusMethodNotAllowed, "Method not allowed")
		return
	}

	var req AlertsRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		sendError(w, http.StatusBadRequest, "Invalid JSON: "+err.Error())
		return
	}

	// Log received alerts
	log.Printf("🚨 Received %d alerts from agent %s", len(req.Alerts), req.AgentID)
	for _, a := range req.Alerts {
		emoji := "ℹ️"
		if a.Severity == "WARNING" {
			emoji = "⚠️"
		} else if a.Severity == "CRITICAL" {
			emoji = "🔥"
		}
		log.Printf("   %s [%s] %s", emoji, a.Severity, a.Message)
		log.Printf("     Metric: %s = %.2f (threshold: %.2f)", a.MetricType, a.Value, a.Threshold)
		log.Printf("     Time: %s, Retries: %d", a.Timestamp, a.RetryCount)
	}

	// Send success response
	sendSuccess(w, fmt.Sprintf("Received %d alerts", len(req.Alerts)), len(req.Alerts), 0)
}

func handleSNMPMetrics(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		sendError(w, http.StatusMethodNotAllowed, "Method not allowed")
		return
	}

	var req SNMPMetricsRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		sendError(w, http.StatusBadRequest, "Invalid JSON: "+err.Error())
		return
	}

	// Log received SNMP metrics
	log.Printf("📡 Received %d SNMP metrics from agent %s", len(req.SNMPMetrics), req.AgentID)
	for _, m := range req.SNMPMetrics {
		log.Printf("   - %s.%s: %.2f (%s)", m.DeviceName, m.MetricName, m.Value, m.ValueType)
		log.Printf("     Device: %s, OID: %s", m.DeviceHost, m.OID)
		if m.Metadata != nil && len(m.Metadata) > 0 {
			metadata, _ := json.Marshal(m.Metadata)
			log.Printf("     Metadata: %s", string(metadata))
		}
	}

	// Send success response
	sendSuccess(w, fmt.Sprintf("Received %d SNMP metrics", len(req.SNMPMetrics)), len(req.SNMPMetrics), 0)
}

func handleHealth(w http.ResponseWriter, r *http.Request) {
	response := map[string]interface{}{
		"status":    "ok",
		"timestamp": time.Now().Format(time.RFC3339),
		"service":   "Network Monitor Test Server",
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

func sendSuccess(w http.ResponseWriter, message string, accepted, rejected int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(Response{
		Success:  true,
		Message:  message,
		Accepted: accepted,
		Rejected: rejected,
	})
}

func sendError(w http.ResponseWriter, status int, errorMsg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(Response{
		Success: false,
		Error:   errorMsg,
		Message: "Request failed",
	})
}

func logMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		agentID := r.Header.Get("X-Agent-ID")
		log.Printf("➡️  %s %s (Agent: %s)", r.Method, r.URL.Path, agentID)
		next.ServeHTTP(w, r)
		log.Printf("⬅️  %s %s completed in %v", r.Method, r.URL.Path, time.Since(start))
		log.Println()
	})
}
