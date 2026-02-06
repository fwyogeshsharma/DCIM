package main

import (
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"fmt"
	"io/ioutil"
	"log"
	"net/http"
	"time"
)

// mTLS-enabled test server for Network Monitor Agent
// This version requires client certificates for authentication

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
	// Certificate paths (relative to where you run the command)
	caCertPath := "../../certs/ca.crt"
	serverCertPath := "../../certs/server.crt"
	serverKeyPath := "../../certs/server.key"

	log.Println("================================================================================")
	log.Println("  Network Monitor Test Server with mTLS")
	log.Println("================================================================================")
	log.Println()

	// Load CA certificate for verifying client certificates
	caCert, err := ioutil.ReadFile(caCertPath)
	if err != nil {
		log.Fatalf("❌ Failed to read CA certificate: %v", err)
	}

	caCertPool := x509.NewCertPool()
	if !caCertPool.AppendCertsFromPEM(caCert) {
		log.Fatalf("❌ Failed to parse CA certificate")
	}
	log.Printf("✓ Loaded CA certificate from: %s", caCertPath)

	// Load server certificate and key
	serverCert, err := tls.LoadX509KeyPair(serverCertPath, serverKeyPath)
	if err != nil {
		log.Fatalf("❌ Failed to load server certificate: %v", err)
	}
	log.Printf("✓ Loaded server certificate from: %s", serverCertPath)
	log.Printf("✓ Loaded server key from: %s", serverKeyPath)
	log.Println()

	// Configure TLS with client certificate verification
	tlsConfig := &tls.Config{
		ClientCAs:    caCertPool,
		ClientAuth:   tls.RequireAndVerifyClientCert, // Require client certificates
		Certificates: []tls.Certificate{serverCert},
		MinVersion:   tls.VersionTLS12,
	}

	// Set up HTTP handlers
	http.HandleFunc("/api/v1/metrics", handleMetrics)
	http.HandleFunc("/api/v1/alerts", handleAlerts)
	http.HandleFunc("/api/v1/snmp-metrics", handleSNMPMetrics)
	http.HandleFunc("/health", handleHealth)

	// Create HTTPS server with mTLS
	server := &http.Server{
		Addr:      ":8443",
		TLSConfig: tlsConfig,
		Handler:   logMiddleware(http.DefaultServeMux),
	}

	log.Println("🔐 mTLS Configuration:")
	log.Println("   - Client authentication: REQUIRED")
	log.Println("   - Minimum TLS version: 1.2")
	log.Println("   - Certificate verification: ENABLED")
	log.Println()

	log.Println("🚀 Server starting on https://localhost:8443")
	log.Println()
	log.Println("Endpoints:")
	log.Println("  POST https://localhost:8443/api/v1/metrics")
	log.Println("  POST https://localhost:8443/api/v1/alerts")
	log.Println("  POST https://localhost:8443/api/v1/snmp-metrics")
	log.Println("  GET  https://localhost:8443/health")
	log.Println()
	log.Println("Configure agent with:")
	log.Println("  server:")
	log.Println("    url: \"https://localhost:8443/api/v1\"")
	log.Println("    tls:")
	log.Println("      enabled: true")
	log.Println("      client_cert_path: \"./certs/client.crt\"")
	log.Println("      client_key_path: \"./certs/client.key\"")
	log.Println("      ca_cert_path: \"./certs/ca.crt\"")
	log.Println()
	log.Println("Test with curl:")
	log.Println("  curl.exe -v https://localhost:8443/api/v1/metrics \\")
	log.Println("    --cert ../../certs/client.crt \\")
	log.Println("    --key ../../certs/client.key \\")
	log.Println("    --cacert ../../certs/ca.crt \\")
	log.Println("    -H \"Content-Type: application/json\" \\")
	log.Println("    -d '{\"test\":\"data\"}'")
	log.Println()
	log.Println("================================================================================")
	log.Println()

	// Start HTTPS server
	if err := server.ListenAndServeTLS(serverCertPath, serverKeyPath); err != nil {
		log.Fatalf("❌ Server failed: %v", err)
	}
}

func handleMetrics(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		sendError(w, http.StatusMethodNotAllowed, "Method not allowed")
		return
	}

	// Log client certificate info
	logClientCert(r)

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

	// Log client certificate info
	logClientCert(r)

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

	// Log client certificate info
	logClientCert(r)

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
		"service":   "Network Monitor Test Server (mTLS)",
		"tls":       "enabled",
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

func logClientCert(r *http.Request) {
	if r.TLS != nil {
		log.Printf("🔐 TLS Connection Details:")
		log.Printf("   - Protocol Version: %d", r.TLS.Version)
		log.Printf("   - Cipher Suite: %d", r.TLS.CipherSuite)
		log.Printf("   - Server Name: %s", r.TLS.ServerName)
		log.Printf("   - Handshake Complete: %v", r.TLS.HandshakeComplete)

		if len(r.TLS.PeerCertificates) > 0 {
			clientCert := r.TLS.PeerCertificates[0]
			log.Printf("   - Client Certificate:")
			log.Printf("     - Subject: %s", clientCert.Subject.CommonName)
			log.Printf("     - Issuer: %s", clientCert.Issuer.CommonName)
			log.Printf("     - Valid: %s to %s",
				clientCert.NotBefore.Format("2006-01-02"),
				clientCert.NotAfter.Format("2006-01-02"))
		} else {
			log.Printf("   - No client certificate presented")
		}
	}
}
