package server

import (
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"fmt"
	"io"
	"io/ioutil"
	"log"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/faberlabs/dcim-server/internal/config"
	"github.com/faberlabs/dcim-server/internal/database"
	"github.com/faberlabs/dcim-server/internal/license"
	"github.com/faberlabs/dcim-server/internal/models"
)

// Server represents the DCIM server
type Server struct {
	config          *config.Config
	db              *database.Database
	licenseManager  *license.Manager
	httpServer      *http.Server
	startTime       time.Time
	mu              sync.RWMutex
	logger          *log.Logger

	// SSE client management
	clients         map[string]chan string
	clientsMu       sync.RWMutex
	clientIDCounter atomic.Uint64
}

// New creates a new DCIM server
func New(cfg *config.Config, db *database.Database, licMgr *license.Manager) (*Server, error) {
	server := &Server{
		config:         cfg,
		db:             db,
		licenseManager: licMgr,
		startTime:      time.Now(),
		logger:         log.New(log.Writer(), "[SERVER] ", log.LstdFlags),
		clients:        make(map[string]chan string),
	}

	// Setup HTTP server
	mux := http.NewServeMux()

	// Register API routes
	basePath := cfg.API.BasePath
	mux.HandleFunc(basePath+"/metrics", server.handleMetrics)            // POST: submit metrics, GET: query metrics
	mux.HandleFunc(basePath+"/alerts", server.handleAlerts)              // POST: submit alerts, GET: query alerts
	mux.HandleFunc(basePath+"/snmp-metrics", server.handleSNMPMetrics)   // POST: submit SNMP metrics, GET: query SNMP metrics
	mux.HandleFunc(basePath+"/agent-status-history", server.handleAgentStatusHistory) // GET: query agent status history
	mux.HandleFunc(basePath+"/register", server.handleRegister)
	mux.HandleFunc(basePath+"/agents/", server.handleGetAgentMetrics)    // Trailing slash for path params
	mux.HandleFunc(basePath+"/agents", server.handleGetAgents)
	mux.HandleFunc(basePath+"/events", server.handleSSEEvents)           // SSE endpoint for real-time updates
	mux.HandleFunc(basePath+"/ai/query", server.handleAIQuery)           // POST: AI-powered query processing

	// Health check endpoint
	if cfg.Health.Enabled {
		mux.HandleFunc(cfg.Health.Path, server.handleHealth)
	}

	// Apply middleware
	handler := server.loggingMiddleware(mux)
	handler = server.corsMiddleware(handler)
	handler = server.authMiddleware(handler)

	server.httpServer = &http.Server{
		Addr:         cfg.GetServerAddress(),
		Handler:      handler,
		ReadTimeout:  cfg.Server.ReadTimeout,
		WriteTimeout: cfg.Server.WriteTimeout,
		IdleTimeout:  cfg.Server.IdleTimeout,
	}

	// Configure TLS if enabled
	if cfg.TLS.Enabled {
		tlsConfig, err := server.setupTLS()
		if err != nil {
			return nil, fmt.Errorf("failed to setup TLS: %w", err)
		}
		server.httpServer.TLSConfig = tlsConfig
	}

	return server, nil
}

// setupTLS configures TLS with mTLS support
func (s *Server) setupTLS() (*tls.Config, error) {
	cfg := s.config.TLS

	// Load CA certificate for client verification
	caCert, err := ioutil.ReadFile(cfg.CACertPath)
	if err != nil {
		return nil, fmt.Errorf("failed to read CA certificate: %w", err)
	}

	caCertPool := x509.NewCertPool()
	if !caCertPool.AppendCertsFromPEM(caCert) {
		return nil, fmt.Errorf("failed to parse CA certificate")
	}

	// Determine client auth mode
	var clientAuth tls.ClientAuthType
	switch cfg.ClientAuth {
	case "none":
		clientAuth = tls.NoClientCert
	case "request":
		clientAuth = tls.RequestClientCert
	case "require":
		clientAuth = tls.RequireAnyClientCert
	case "verify_if_given":
		clientAuth = tls.VerifyClientCertIfGiven
	case "require_and_verify":
		clientAuth = tls.RequireAndVerifyClientCert
	default:
		clientAuth = tls.RequireAndVerifyClientCert
	}

	// Determine minimum TLS version
	var minVersion uint16
	switch cfg.MinTLSVersion {
	case "1.3":
		minVersion = tls.VersionTLS13
	case "1.2":
		minVersion = tls.VersionTLS12
	default:
		minVersion = tls.VersionTLS12
	}

	tlsConfig := &tls.Config{
		ClientCAs:  caCertPool,
		ClientAuth: clientAuth,
		MinVersion: minVersion,
	}

	s.logger.Printf("TLS configured: ClientAuth=%s, MinVersion=%s", cfg.ClientAuth, cfg.MinTLSVersion)

	return tlsConfig, nil
}

// Start starts the server
func (s *Server) Start() error {
	s.logger.Println("================================================================================")
	s.logger.Println("  DCIM Server - Data Center Infrastructure Monitor")
	s.logger.Println("================================================================================")
	s.logger.Println()

	// Log license information
	lic := s.licenseManager.GetLicense()
	s.logger.Printf("License Information:")
	s.logger.Printf("  Company: %s", lic.CompanyName)
	s.logger.Printf("  Max Agents: %d", lic.MaxAgents)
	s.logger.Printf("  Max SNMP Devices: %d", lic.MaxSNMPDevices)
	s.logger.Printf("  Expires: %s (%d days)", lic.ExpiresAt.Format("2006-01-02"), s.licenseManager.GetExpiryDays())
	if s.licenseManager.IsInGracePeriod() {
		s.logger.Printf("  WARNING: License is in grace period!")
	}
	s.logger.Println()

	// Log server configuration
	s.logger.Printf("Server Configuration:")
	s.logger.Printf("  Address: %s", s.config.GetServerAddress())
	s.logger.Printf("  TLS Enabled: %v", s.config.TLS.Enabled)
	if s.config.TLS.Enabled {
		s.logger.Printf("  Client Authentication: %s", s.config.TLS.ClientAuth)
	}
	s.logger.Printf("  Database: %s", s.config.Database.Type)
	s.logger.Println()

	// Log API endpoints
	s.logger.Println("API Endpoints:")
	basePath := s.config.API.BasePath
	s.logger.Printf("  POST %s/metrics", basePath)
	s.logger.Printf("  GET  %s/metrics", basePath)
	s.logger.Printf("  POST %s/alerts", basePath)
	s.logger.Printf("  GET  %s/alerts", basePath)
	s.logger.Printf("  POST %s/snmp-metrics", basePath)
	s.logger.Printf("  GET  %s/snmp-metrics", basePath)
	s.logger.Printf("  GET  %s/agent-status-history", basePath)
	s.logger.Printf("  POST %s/register", basePath)
	s.logger.Printf("  GET  %s/agents", basePath)
	s.logger.Printf("  GET  %s/agents/{id}/metrics", basePath)
	s.logger.Printf("  GET  %s/events", basePath)
	if s.config.Health.Enabled {
		s.logger.Printf("  GET  %s", s.config.Health.Path)
	}
	s.logger.Println()
	s.logger.Println("================================================================================")
	s.logger.Println()

	// Start server
	if s.config.TLS.Enabled {
		s.logger.Printf("Server starting with mTLS on https://%s", s.config.GetServerAddress())
		return s.httpServer.ListenAndServeTLS(s.config.TLS.ServerCertPath, s.config.TLS.ServerKeyPath)
	} else {
		s.logger.Printf("Server starting on http://%s", s.config.GetServerAddress())
		return s.httpServer.ListenAndServe()
	}
}

// Stop gracefully stops the server
func (s *Server) Stop() error {
	s.logger.Println("Shutting down server...")
	return s.httpServer.Close()
}

// Middleware

// corsMiddleware handles CORS headers for browser requests
func (s *Server) corsMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Check if CORS is enabled
		if s.config.API.CORS.Enabled {
			// Set CORS headers
			origin := r.Header.Get("Origin")
			if origin != "" {
				// Check if origin is allowed
				allowed := false
				for _, allowedOrigin := range s.config.API.CORS.AllowedOrigins {
					if allowedOrigin == "*" || allowedOrigin == origin {
						allowed = true
						break
					}
				}

				if allowed {
					w.Header().Set("Access-Control-Allow-Origin", origin)
					w.Header().Set("Access-Control-Allow-Methods", strings.Join(s.config.API.CORS.AllowedMethods, ", "))
					w.Header().Set("Access-Control-Allow-Headers", strings.Join(s.config.API.CORS.AllowedHeaders, ", "))
				}
			}

			// Handle preflight requests
			if r.Method == "OPTIONS" {
				w.WriteHeader(http.StatusOK)
				return
			}
		}

		next.ServeHTTP(w, r)
	})
}

// loggingMiddleware logs HTTP requests
func (s *Server) loggingMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !s.config.Logging.LogRequests {
			next.ServeHTTP(w, r)
			return
		}

		start := time.Now()
		agentID := s.getAgentID(r)

		s.logger.Printf("-> %s %s (Agent: %s, IP: %s)", r.Method, r.URL.Path, agentID, r.RemoteAddr)

		next.ServeHTTP(w, r)

		s.logger.Printf("<- %s %s completed in %v", r.Method, r.URL.Path, time.Since(start))
	})
}

// authMiddleware handles agent authentication
func (s *Server) authMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Skip auth for health check
		if r.URL.Path == s.config.Health.Path {
			next.ServeHTTP(w, r)
			return
		}

		// Skip auth for GET requests to UI endpoints (read-only API for dashboard)
		if r.Method == "GET" && (r.URL.Path == s.config.API.BasePath+"/agents" ||
			strings.HasPrefix(r.URL.Path, s.config.API.BasePath+"/agents/") ||
			r.URL.Path == s.config.API.BasePath+"/events") {
			next.ServeHTTP(w, r)
			return
		}

		// Get agent ID from certificate or request
		agentID := s.getAgentID(r)
		if agentID == "" {
			s.sendError(w, http.StatusUnauthorized, "Agent identification required")
			return
		}

		// Check if agent is registered and approved
		agent, err := s.db.GetAgent(agentID)
		if err != nil {
			// Agent not found - check if auto-registration is enabled
			if s.config.Agents.Registration.AutoRegister && r.URL.Path != s.config.API.BasePath+"/register" {
				// Auto-register agent
				if err := s.autoRegisterAgent(r, agentID); err != nil {
					s.logger.Printf("Failed to auto-register agent %s: %v", agentID, err)
					s.sendError(w, http.StatusForbidden, "Agent registration failed")
					return
				}
			} else {
				s.sendError(w, http.StatusForbidden, "Agent not registered")
				return
			}
		} else {
			// Check if agent requires approval
			if s.config.Agents.Registration.RequireApproval && !agent.Approved {
				s.sendError(w, http.StatusForbidden, "Agent pending approval")
				return
			}

			// Update last seen
			s.db.UpdateAgentLastSeen(agentID)
		}

		next.ServeHTTP(w, r)
	})
}

// getAgentID extracts agent ID from request
func (s *Server) getAgentID(r *http.Request) string {
	method := s.config.Agents.Connection.IdentificationMethod

	// Try to get from client certificate
	certCN := ""
	if r.TLS != nil && len(r.TLS.PeerCertificates) > 0 {
		certCN = r.TLS.PeerCertificates[0].Subject.CommonName
	}

	// Try to get from request header
	headerAgentID := r.Header.Get("X-Agent-ID")

	switch method {
	case "certificate_cn":
		return certCN
	case "agent_id":
		return headerAgentID
	case "both":
		if certCN != "" && headerAgentID != "" && certCN == headerAgentID {
			return certCN
		}
		return ""
	default:
		if certCN != "" {
			return certCN
		}
		return headerAgentID
	}
}

// autoRegisterAgent automatically registers a new agent
func (s *Server) autoRegisterAgent(r *http.Request, agentID string) error {
	// Check license limits
	agentCount, err := s.db.GetAgentCount()
	if err != nil {
		return err
	}

	if err := s.licenseManager.CanAddAgent(agentCount); err != nil {
		return err
	}

	// Get certificate CN
	certCN := ""
	if r.TLS != nil && len(r.TLS.PeerCertificates) > 0 {
		certCN = r.TLS.PeerCertificates[0].Subject.CommonName
	}

	// Extract IP address
	ip := r.RemoteAddr
	if idx := strings.LastIndex(ip, ":"); idx != -1 {
		ip = ip[:idx]
	}

	agent := &models.Agent{
		AgentID:       agentID,
		CertificateCN: certCN,
		Hostname:      agentID,
		IPAddress:     ip,
		Status:        "online",
		Group:         s.config.Agents.Registration.DefaultGroup,
		Approved:      !s.config.Agents.Registration.RequireApproval,
	}

	s.logger.Printf("Auto-registering agent: %s (CN: %s, IP: %s)", agentID, certCN, ip)

	return s.db.RegisterAgent(agent)
}

// API Handlers

// handleMetrics handles incoming metrics (POST) and metric queries (GET)
func (s *Server) handleMetrics(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet {
		s.handleGetMetrics(w, r)
		return
	}

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

	var req models.MetricsRequest
	if err := json.Unmarshal(body, &req); err != nil {
		s.sendError(w, http.StatusBadRequest, "Invalid JSON: "+err.Error())
		return
	}

	// Validate batch size
	if len(req.Metrics) > s.config.Agents.Validation.MaxMetricsPerBatch {
		s.sendError(w, http.StatusBadRequest, fmt.Sprintf("Too many metrics (max: %d)", s.config.Agents.Validation.MaxMetricsPerBatch))
		return
	}

	// Populate agent_id in each metric from the request
	for i := range req.Metrics {
		req.Metrics[i].AgentID = req.AgentID
	}

	// Insert metrics into database
	if err := s.db.InsertMetrics(req.Metrics); err != nil {
		s.logger.Printf("Failed to insert metrics: %v", err)
		s.sendError(w, http.StatusInternalServerError, "Failed to store metrics")
		return
	}

	s.logger.Printf("Stored %d metrics from agent %s", len(req.Metrics), req.AgentID)

	// Broadcast agent update to SSE clients
	if len(req.Metrics) > 0 {
		// Get fresh agent data from database
		agent, err := s.db.GetAgent(req.AgentID)
		if err == nil {
			s.broadcastEvent("agent_update", agent)
		}
	}

	// Send success response
	s.sendSuccess(w, fmt.Sprintf("Received %d metrics", len(req.Metrics)), len(req.Metrics), 0)
}

// handleAlerts handles incoming alerts (POST) and alert queries (GET)
func (s *Server) handleAlerts(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet {
		s.handleGetAlerts(w, r)
		return
	}

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

	var req models.AlertsRequest
	if err := json.Unmarshal(body, &req); err != nil {
		s.sendError(w, http.StatusBadRequest, "Invalid JSON: "+err.Error())
		return
	}

	// Validate batch size
	if len(req.Alerts) > s.config.Agents.Validation.MaxAlertsPerBatch {
		s.sendError(w, http.StatusBadRequest, fmt.Sprintf("Too many alerts (max: %d)", s.config.Agents.Validation.MaxAlertsPerBatch))
		return
	}

	// Populate agent_id in each alert from the request
	for i := range req.Alerts {
		req.Alerts[i].AgentID = req.AgentID
	}

	// Insert alerts into database
	if err := s.db.InsertAlerts(req.Alerts); err != nil {
		s.logger.Printf("Failed to insert alerts: %v", err)
		s.sendError(w, http.StatusInternalServerError, "Failed to store alerts")
		return
	}

	s.logger.Printf("Stored %d alerts from agent %s", len(req.Alerts), req.AgentID)

	// Send success response
	s.sendSuccess(w, fmt.Sprintf("Received %d alerts", len(req.Alerts)), len(req.Alerts), 0)
}

// handleSNMPMetrics handles incoming SNMP metrics (POST) and SNMP metric queries (GET)
func (s *Server) handleSNMPMetrics(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet {
		s.handleGetSNMPMetrics(w, r)
		return
	}

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

	var req models.SNMPMetricsRequest
	if err := json.Unmarshal(body, &req); err != nil {
		s.sendError(w, http.StatusBadRequest, "Invalid JSON: "+err.Error())
		return
	}

	// Populate agent_id in each SNMP metric from the request
	for i := range req.SNMPMetrics {
		req.SNMPMetrics[i].AgentID = req.AgentID
	}

	// Insert SNMP metrics into database
	if err := s.db.InsertSNMPMetrics(req.SNMPMetrics); err != nil {
		s.logger.Printf("Failed to insert SNMP metrics: %v", err)
		s.sendError(w, http.StatusInternalServerError, "Failed to store SNMP metrics")
		return
	}

	s.logger.Printf("Stored %d SNMP metrics from agent %s", len(req.SNMPMetrics), req.AgentID)

	// Send success response
	s.sendSuccess(w, fmt.Sprintf("Received %d SNMP metrics", len(req.SNMPMetrics)), len(req.SNMPMetrics), 0)
}

// handleRegister handles agent registration
func (s *Server) handleRegister(w http.ResponseWriter, r *http.Request) {
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

	var req models.AgentRegistrationRequest
	if err := json.Unmarshal(body, &req); err != nil {
		s.sendError(w, http.StatusBadRequest, "Invalid JSON: "+err.Error())
		return
	}

	// Check license limits
	agentCount, err := s.db.GetAgentCount()
	if err != nil {
		s.sendError(w, http.StatusInternalServerError, "Failed to check agent count")
		return
	}

	if err := s.licenseManager.CanAddAgent(agentCount); err != nil {
		s.sendError(w, http.StatusForbidden, err.Error())
		return
	}

	// Get certificate CN
	certCN := ""
	if r.TLS != nil && len(r.TLS.PeerCertificates) > 0 {
		certCN = r.TLS.PeerCertificates[0].Subject.CommonName
	}

	agent := &models.Agent{
		AgentID:       req.AgentID,
		CertificateCN: certCN,
		Hostname:      req.Hostname,
		IPAddress:     req.IPAddress,
		Status:        "online",
		Group:         s.config.Agents.Registration.DefaultGroup,
		Approved:      !s.config.Agents.Registration.RequireApproval,
	}

	if err := s.db.RegisterAgent(agent); err != nil {
		s.logger.Printf("Failed to register agent: %v", err)
		s.sendError(w, http.StatusInternalServerError, "Failed to register agent")
		return
	}

	s.logger.Printf("Registered agent: %s (CN: %s, Hostname: %s)", req.AgentID, certCN, req.Hostname)

	s.sendSuccess(w, "Agent registered successfully", 0, 0)
}

// handleHealth handles health check requests
func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	totalAgents, _ := s.db.GetAgentCount()
	onlineAgents, _ := s.db.GetOnlineAgentCount(s.config.Agents.Connection.HeartbeatTimeout)

	response := models.HealthResponse{
		Status:       "ok",
		Timestamp:    time.Now(),
		Service:      "DCIM Server",
		Version:      "1.0.0",
		Uptime:       time.Since(s.startTime),
		TotalAgents:  totalAgents,
		OnlineAgents: onlineAgents,
	}

	if s.config.Health.Detailed {
		lic := s.licenseManager.GetLicense()
		response.Details = map[string]interface{}{
			"license": map[string]interface{}{
				"company":          lic.CompanyName,
				"max_agents":       lic.MaxAgents,
				"max_snmp_devices": lic.MaxSNMPDevices,
				"expires_at":       lic.ExpiresAt,
				"expires_in_days":  s.licenseManager.GetExpiryDays(),
			},
			"database": map[string]interface{}{
				"type": s.config.Database.Type,
			},
		}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// handleGetAgents returns all agents
func (s *Server) handleGetAgents(w http.ResponseWriter, r *http.Request) {
	// Only allow GET requests
	if r.Method != http.MethodGet {
		s.sendError(w, http.StatusMethodNotAllowed, "Method not allowed")
		return
	}

	// Get all agents from database
	agents, err := s.db.GetAllAgents()
	if err != nil {
		s.logger.Printf("Failed to get agents: %v", err)
		s.sendError(w, http.StatusInternalServerError, "Failed to retrieve agents")
		return
	}

	// Return agents in APIResponse format
	response := models.APIResponse{
		Success: true,
		Data:    agents,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// handleGetAgentMetrics returns metrics for a specific agent
func (s *Server) handleGetAgentMetrics(w http.ResponseWriter, r *http.Request) {
	// Only allow GET requests
	if r.Method != http.MethodGet {
		s.sendError(w, http.StatusMethodNotAllowed, "Method not allowed")
		return
	}

	// Extract agent ID from path: /api/v1/agents/{id}/metrics
	// Path format: /api/v1/agents/agent-001/metrics
	basePath := s.config.API.BasePath + "/agents/"
	if !strings.HasPrefix(r.URL.Path, basePath) {
		s.sendError(w, http.StatusBadRequest, "Invalid path")
		return
	}

	pathAfterBase := strings.TrimPrefix(r.URL.Path, basePath)
	parts := strings.Split(pathAfterBase, "/")
	if len(parts) < 2 || parts[0] == "" || parts[1] != "metrics" {
		s.sendError(w, http.StatusBadRequest, "Invalid path format. Expected: /agents/{id}/metrics")
		return
	}

	agentID := parts[0]

	// Parse query parameters
	query := r.URL.Query()

	// Parse time_range (default 24h)
	timeRangeStr := query.Get("time_range")
	var timeRange time.Duration
	switch timeRangeStr {
	case "1h":
		timeRange = 1 * time.Hour
	case "7d":
		timeRange = 7 * 24 * time.Hour
	case "30d":
		timeRange = 30 * 24 * time.Hour
	case "", "24h":
		timeRange = 24 * time.Hour
	default:
		s.sendError(w, http.StatusBadRequest, "Invalid time_range. Valid values: 1h, 24h, 7d, 30d")
		return
	}

	// Parse metric_type (optional)
	metricType := query.Get("metric_type")

	// Parse limit (default 100, max 100000)
	limitStr := query.Get("limit")
	limit := 100
	if limitStr != "" {
		var err error
		fmt.Sscanf(limitStr, "%d", &limit)
		if err != nil || limit < 1 {
			s.sendError(w, http.StatusBadRequest, "Invalid limit")
			return
		}
		if limit > 100000 {
			limit = 100000
		}
	}

	// Get metrics from database
	metrics, err := s.db.GetAgentMetrics(agentID, timeRange, metricType, limit)
	if err != nil {
		s.logger.Printf("Failed to get metrics for agent %s: %v", agentID, err)
		s.sendError(w, http.StatusInternalServerError, "Failed to retrieve metrics")
		return
	}

	// Return metrics in APIResponse format
	response := models.APIResponse{
		Success: true,
		Data:    metrics,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// handleSSEEvents handles Server-Sent Events connections for real-time updates
func (s *Server) handleSSEEvents(w http.ResponseWriter, r *http.Request) {
	// Only allow GET requests
	if r.Method != http.MethodGet {
		s.sendError(w, http.StatusMethodNotAllowed, "Method not allowed")
		return
	}

	// Check if ResponseWriter supports flushing (required for SSE)
	flusher, ok := w.(http.Flusher)
	if !ok {
		s.sendError(w, http.StatusInternalServerError, "Streaming not supported")
		return
	}

	// Set SSE headers
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")

	// Add CORS header for browser access
	if s.config.API.CORS.Enabled {
		origin := r.Header.Get("Origin")
		for _, allowedOrigin := range s.config.API.CORS.AllowedOrigins {
			if allowedOrigin == "*" || allowedOrigin == origin {
				w.Header().Set("Access-Control-Allow-Origin", origin)
				break
			}
		}
	}

	// Flush headers immediately
	flusher.Flush()

	// Create unique client ID
	clientID := fmt.Sprintf("client-%d", s.clientIDCounter.Add(1))

	// Create event channel for this client (buffered to prevent blocking)
	eventChan := make(chan string, 10)

	// Register client
	s.clientsMu.Lock()
	s.clients[clientID] = eventChan
	s.clientsMu.Unlock()

	s.logger.Printf("SSE client connected: %s (Total clients: %d)", clientID, len(s.clients))

	// Cleanup on disconnect
	defer func() {
		s.clientsMu.Lock()
		delete(s.clients, clientID)
		clientCount := len(s.clients)
		s.clientsMu.Unlock()
		close(eventChan)
		s.logger.Printf("SSE client disconnected: %s (Total clients: %d)", clientID, clientCount)
	}()

	// Keepalive ticker (15 seconds)
	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()

	// Event loop
	for {
		select {
		case <-r.Context().Done():
			// Client disconnected
			return

		case <-ticker.C:
			// Send keepalive comment
			fmt.Fprintf(w, ": keepalive\n\n")
			flusher.Flush()

		case event := <-eventChan:
			// Send event to client
			fmt.Fprintf(w, "%s", event)
			flusher.Flush()
		}
	}
}

// handleGetMetrics handles GET requests for querying metrics
func (s *Server) handleGetMetrics(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query()

	// Parse query parameters
	agentID := query.Get("agent_id")
	metricType := query.Get("metric_type")
	timeRangeStr := query.Get("time_range")
	limitStr := query.Get("limit")

	// Parse time_range (default 24h)
	var timeRange time.Duration
	switch timeRangeStr {
	case "1h":
		timeRange = 1 * time.Hour
	case "7d":
		timeRange = 7 * 24 * time.Hour
	case "30d":
		timeRange = 30 * 24 * time.Hour
	case "", "24h":
		timeRange = 24 * time.Hour
	default:
		s.sendError(w, http.StatusBadRequest, "Invalid time_range. Valid values: 1h, 24h, 7d, 30d")
		return
	}

	// Parse limit (default 100, max 1000)
	limit := 100
	if limitStr != "" {
		fmt.Sscanf(limitStr, "%d", &limit)
		if limit < 1 || limit > 1000 {
			limit = 100
		}
	}

	// Get metrics from database
	metrics, err := s.db.GetAllMetrics(agentID, metricType, timeRange, limit)
	if err != nil {
		s.logger.Printf("Failed to get metrics: %v", err)
		s.sendError(w, http.StatusInternalServerError, "Failed to retrieve metrics")
		return
	}

	// Return metrics in APIResponse format
	response := models.APIResponse{
		Success: true,
		Data:    metrics,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// handleGetAlerts handles GET requests for querying alerts
func (s *Server) handleGetAlerts(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query()

	// Parse query parameters
	agentID := query.Get("agent_id")
	severity := query.Get("severity")
	resolvedStr := query.Get("resolved")
	timeRangeStr := query.Get("time_range")
	limitStr := query.Get("limit")

	// Parse resolved filter
	var resolved *bool
	if resolvedStr != "" {
		val := resolvedStr == "true"
		resolved = &val
	}

	// Parse time_range (default 24h)
	var timeRange time.Duration
	switch timeRangeStr {
	case "1h":
		timeRange = 1 * time.Hour
	case "7d":
		timeRange = 7 * 24 * time.Hour
	case "30d":
		timeRange = 30 * 24 * time.Hour
	case "", "24h":
		timeRange = 24 * time.Hour
	default:
		s.sendError(w, http.StatusBadRequest, "Invalid time_range. Valid values: 1h, 24h, 7d, 30d")
		return
	}

	// Parse limit (default 100, max 1000)
	limit := 100
	if limitStr != "" {
		fmt.Sscanf(limitStr, "%d", &limit)
		if limit < 1 || limit > 1000 {
			limit = 100
		}
	}

	// Get alerts from database
	alerts, err := s.db.GetAllAlerts(agentID, severity, resolved, timeRange, limit)
	if err != nil {
		s.logger.Printf("Failed to get alerts: %v", err)
		s.sendError(w, http.StatusInternalServerError, "Failed to retrieve alerts")
		return
	}

	// Return alerts in APIResponse format
	response := models.APIResponse{
		Success: true,
		Data:    alerts,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// handleGetSNMPMetrics handles GET requests for querying SNMP metrics
func (s *Server) handleGetSNMPMetrics(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query()

	// Parse query parameters
	agentID := query.Get("agent_id")
	deviceName := query.Get("device_name")
	metricName := query.Get("metric_name")
	timeRangeStr := query.Get("time_range")
	limitStr := query.Get("limit")

	// Parse time_range (default 24h)
	var timeRange time.Duration
	switch timeRangeStr {
	case "1h":
		timeRange = 1 * time.Hour
	case "7d":
		timeRange = 7 * 24 * time.Hour
	case "30d":
		timeRange = 30 * 24 * time.Hour
	case "", "24h":
		timeRange = 24 * time.Hour
	default:
		s.sendError(w, http.StatusBadRequest, "Invalid time_range. Valid values: 1h, 24h, 7d, 30d")
		return
	}

	// Parse limit (default 100, max 1000)
	limit := 100
	if limitStr != "" {
		fmt.Sscanf(limitStr, "%d", &limit)
		if limit < 1 || limit > 1000 {
			limit = 100
		}
	}

	// Get SNMP metrics from database
	metrics, err := s.db.GetAllSNMPMetrics(agentID, deviceName, metricName, timeRange, limit)
	if err != nil {
		s.logger.Printf("Failed to get SNMP metrics: %v", err)
		s.sendError(w, http.StatusInternalServerError, "Failed to retrieve SNMP metrics")
		return
	}

	// Return SNMP metrics in APIResponse format
	response := models.APIResponse{
		Success: true,
		Data:    metrics,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// handleAgentStatusHistory handles GET requests for agent status history
func (s *Server) handleAgentStatusHistory(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		s.sendError(w, http.StatusMethodNotAllowed, "Method not allowed")
		return
	}

	query := r.URL.Query()

	// Parse query parameters
	agentID := query.Get("agent_id")
	timeRangeStr := query.Get("time_range")
	limitStr := query.Get("limit")

	// Parse time_range (default 7d)
	var timeRange time.Duration
	switch timeRangeStr {
	case "1h":
		timeRange = 1 * time.Hour
	case "24h":
		timeRange = 24 * time.Hour
	case "30d":
		timeRange = 30 * 24 * time.Hour
	case "", "7d":
		timeRange = 7 * 24 * time.Hour
	default:
		s.sendError(w, http.StatusBadRequest, "Invalid time_range. Valid values: 1h, 24h, 7d, 30d")
		return
	}

	// Parse limit (default 100, max 1000)
	limit := 100
	if limitStr != "" {
		fmt.Sscanf(limitStr, "%d", &limit)
		if limit < 1 || limit > 1000 {
			limit = 100
		}
	}

	// Get status history from database
	history, err := s.db.GetAgentStatusHistory(agentID, timeRange, limit)
	if err != nil {
		s.logger.Printf("Failed to get agent status history: %v", err)
		s.sendError(w, http.StatusInternalServerError, "Failed to retrieve status history")
		return
	}

	// Return history in APIResponse format
	response := models.APIResponse{
		Success: true,
		Data:    history,
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(response)
}

// SSE Broadcasting

// broadcastEvent sends an event to all connected SSE clients
func (s *Server) broadcastEvent(eventType string, data interface{}) {
	event := s.formatSSEEvent(eventType, data)

	s.clientsMu.RLock()
	defer s.clientsMu.RUnlock()

	// Send to all connected clients (non-blocking)
	for clientID, ch := range s.clients {
		select {
		case ch <- event:
			// Event sent successfully
		default:
			// Channel full, drop event for this slow client
			s.logger.Printf("Dropped event for slow client: %s", clientID)
		}
	}
}

// formatSSEEvent formats data as an SSE message
func (s *Server) formatSSEEvent(eventType string, data interface{}) string {
	eventData := map[string]interface{}{
		"type": eventType,
		"data": data,
	}

	jsonData, err := json.Marshal(eventData)
	if err != nil {
		s.logger.Printf("Failed to marshal SSE event: %v", err)
		return ""
	}

	return fmt.Sprintf("data: %s\n\n", jsonData)
}

// Helper functions

func (s *Server) sendSuccess(w http.ResponseWriter, message string, accepted, rejected int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(models.APIResponse{
		Success:  true,
		Message:  message,
		Accepted: accepted,
		Rejected: rejected,
	})
}

// handleAIQuery proxies AI requests to Nvidia API
func (s *Server) handleAIQuery(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		s.sendError(w, http.StatusMethodNotAllowed, "Only POST requests are allowed")
		return
	}

	// Read request body
	body, err := io.ReadAll(r.Body)
	if err != nil {
		s.sendError(w, http.StatusBadRequest, "Failed to read request body")
		return
	}
	defer r.Body.Close()

	// Create request to Nvidia API
	nvidiaURL := "https://integrate.api.nvidia.com/v1/chat/completions"
	nvidiaAPIKey := "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"

	req, err := http.NewRequest("POST", nvidiaURL, strings.NewReader(string(body)))
	if err != nil {
		s.sendError(w, http.StatusInternalServerError, "Failed to create request")
		return
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+nvidiaAPIKey)

	// Send request to Nvidia
	client := &http.Client{Timeout: 60 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		s.logger.Printf("Nvidia API error: %v", err)
		s.sendError(w, http.StatusBadGateway, "Failed to connect to AI service")
		return
	}
	defer resp.Body.Close()

	// Read response
	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		s.sendError(w, http.StatusInternalServerError, "Failed to read AI response")
		return
	}

	// Forward response to client
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(resp.StatusCode)
	w.Write(respBody)
}

func (s *Server) sendError(w http.ResponseWriter, status int, errorMsg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(models.APIResponse{
		Success: false,
		Error:   errorMsg,
		Message: "Request failed",
	})
}
