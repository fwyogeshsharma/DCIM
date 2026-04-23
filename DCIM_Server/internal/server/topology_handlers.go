package server

import (
	"encoding/json"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/faberlabs/dcim-server/internal/models"
	"github.com/faberlabs/dcim-server/internal/snmpwalker"
	"github.com/gosnmp/gosnmp"
)

// saveSNMPWalkerMetrics converts walker-discovered metrics to models.SNMPMetric and persists them.
func (s *Server) saveSNMPWalkerMetrics(metrics []snmpwalker.DiscoveredMetric) error {
	rows := make([]models.SNMPMetric, 0, len(metrics))
	for _, m := range metrics {
		meta := models.JSONMap{}
		for k, v := range m.Metadata {
			meta[k] = v
		}
		rows = append(rows, models.SNMPMetric{
			AgentID:    m.AgentID,
			Timestamp:  m.Timestamp,
			DeviceName: m.DeviceName,
			DeviceHost: m.DeviceHost,
			OID:        m.OID,
			MetricName: m.MetricName,
			Value:      m.Value,
			ValueType:  m.ValueType,
			Metadata:   meta,
		})
	}
	return s.db.InsertSNMPMetrics(s.serverID, rows)
}

// waitForSNMP probes the seed IP every 15 seconds until SNMP responds, then returns.
// This handles the case where the SNMP simulator is open but not yet started.
func (s *Server) waitForSNMP(ip string, port uint16, community string, timeout time.Duration) {
	s.logger.Printf("[WALKER] Waiting for SNMP agent at %s:%d to become available...", ip, port)
	for {
		g := &gosnmp.GoSNMP{
			Target:    ip,
			Port:      port,
			Community: community,
			Version:   gosnmp.Version2c,
			Timeout:   timeout,
			Retries:   0,
		}
		if err := g.Connect(); err == nil {
			result, err := g.Get([]string{"1.3.6.1.2.1.1.1.0"}) // sysDescr.0
			g.Conn.Close()
			if err == nil && result != nil && len(result.Variables) > 0 {
				s.logger.Printf("[WALKER] SNMP agent at %s is ready — starting walk", ip)
				return
			}
		}
		s.logger.Printf("[WALKER] SNMP at %s not responding yet — retrying in 15s (click Start in the simulator UI)", ip)
		time.Sleep(15 * time.Second)
	}
}

// runWalkerLoop starts a walk on boot, then repeats on the configured interval.
func (s *Server) runWalkerLoop() {
	cfg := s.config.SNMPWalker

	// Wait until the SNMP simulator is actually started (responds to SNMP).
	// Use the same community string that will be used during the walk.
	community := cfg.Community
	if cfg.UseIPAsCommunity {
		community = cfg.SeedIP
	}
	s.waitForSNMP(cfg.SeedIP, cfg.Port, community, cfg.Timeout)

	// Build the server node identity for the synthetic server→seed link.
	// Use the configured server ID as the source so it's always unique and
	// never collides with a real device IP (even when simulator runs locally).
	hostname, _ := os.Hostname()
	serverNodeID := s.serverID
	serverNodeName := hostname
	if serverNodeName == "" {
		serverNodeName = serverNodeID
	}

	run := func() {
		s.logger.Printf("[WALKER] Auto-starting walk from seed %s (depth %d)", cfg.SeedIP, cfg.MaxDepth)
		id, err := s.walker.StartWalk(snmpwalker.WalkConfig{
			SeedIP:           cfg.SeedIP,
			Community:        cfg.Community,
			Version:          cfg.Version,
			Port:             cfg.Port,
			MaxDepth:         cfg.MaxDepth,
			Timeout:          cfg.Timeout,
			Retries:          cfg.Retries,
			UseIPAsCommunity: cfg.UseIPAsCommunity,
			Subnets:          cfg.Subnets,
			CombineDiscovery: cfg.CombineDiscovery,
		})
		if err != nil {
			s.logger.Printf("[WALKER] Auto-start failed: %v", err)
			return
		}
		s.logger.Printf("[WALKER] Auto-started session %s", id)

		// Store a synthetic server→seed link so the UI can render the server
		// as the entry point above the discovered network.
		seedLink := []snmpwalker.TopologyLink{{
			SourceIP:   serverNodeID,
			SourceName: serverNodeName,
			TargetIP:   cfg.SeedIP,
			TargetName: cfg.SeedIP,
		}}
		if err := s.db.UpsertTopologyLinks(s.serverID, seedLink); err != nil {
			s.logger.Printf("[WALKER] Failed to store server→seed link: %v", err)
		}
	}

	run()

	if cfg.Interval <= 0 {
		return // run once only
	}

	ticker := time.NewTicker(cfg.Interval)
	defer ticker.Stop()
	for range ticker.C {
		run()
	}
}

// ── Request / Response structs ────────────────────────────────────────────────

type walkRequest struct {
	SeedIP    string `json:"seed_ip"`
	Community string `json:"community"`
	Version   string `json:"version"`
	Port      uint16 `json:"port"`
	MaxDepth  int    `json:"max_depth"`
}

type walkResponse struct {
	Success   bool   `json:"success"`
	SessionID string `json:"session_id"`
	Message   string `json:"message"`
}

// ── Handlers ─────────────────────────────────────────────────────────────────

// handleTopologyWalk  POST  /api/v1/topology/walk  — start a new walk
// handleTopologyWalk  GET   /api/v1/topology/walk  — list all sessions
func (s *Server) handleTopologyWalk(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodPost:
		s.startWalk(w, r)
	case http.MethodGet:
		s.listWalkSessions(w, r)
	default:
		s.sendError(w, http.StatusMethodNotAllowed, "Method not allowed")
	}
}

// handleTopologyWalkStatus  GET  /api/v1/topology/walk/<sessionID>
func (s *Server) handleTopologyWalkStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		s.sendError(w, http.StatusMethodNotAllowed, "Method not allowed")
		return
	}

	// Extract session ID from path: /api/v1/topology/walk/<id>
	base := s.config.API.BasePath + "/topology/walk/"
	sessionID := strings.TrimPrefix(r.URL.Path, base)
	if sessionID == "" {
		s.sendError(w, http.StatusBadRequest, "Session ID required")
		return
	}

	session, ok := s.walker.GetSession(sessionID)
	if !ok {
		s.sendError(w, http.StatusNotFound, "Session not found")
		return
	}

	snap := session.Snapshot()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(models.APIResponse{
		Success: true,
		Data:    snap,
	})
}

// handleTopologyNodes  GET  /api/v1/topology/nodes  — list discovered devices with online/offline status
func (s *Server) handleTopologyNodes(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		s.sendError(w, http.StatusMethodNotAllowed, "Method not allowed")
		return
	}

	// Re-use existing snmp_metrics query; walker rows use agent_id = "snmp-walker"
	metrics, err := s.db.GetAllSNMPMetrics(
		snmpwalker.WalkerAgentID,
		"", // any device
		"reachable",
		30*24*time.Hour, // last 30 days
		10000,
	)
	if err != nil {
		s.logger.Printf("Failed to query topology nodes: %v", err)
		s.sendError(w, http.StatusInternalServerError, "Failed to query nodes")
		return
	}

	// Deduplicate by device_host — keep latest record per IP
	seen := make(map[string]models.SNMPMetric)
	for _, m := range metrics {
		existing, ok := seen[m.DeviceHost]
		if !ok || m.Timestamp.After(existing.Timestamp) {
			seen[m.DeviceHost] = m
		}
	}

	// staleness threshold: 2× the walker interval (default 30 min if not set)
	walkerInterval := s.config.SNMPWalker.Interval
	if walkerInterval <= 0 {
		walkerInterval = 30 * time.Minute
	}
	staleThreshold := time.Now().Add(-2 * walkerInterval)

	type TopologyNode struct {
		models.SNMPMetric
		Status string `json:"status"`
	}

	nodes := make([]TopologyNode, 0, len(seen))
	for _, m := range seen {
		status := "offline"
		// online only if walker probed it recently AND it responded
		if m.Timestamp.After(staleThreshold) && m.Value == 1 {
			status = "online"
		}
		nodes = append(nodes, TopologyNode{SNMPMetric: m, Status: status})
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(models.APIResponse{
		Success: true,
		Data:    nodes,
	})
}

// handleTopologyLinks  GET  /api/v1/topology/links  — return discovered edges
func (s *Server) handleTopologyLinks(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		s.sendError(w, http.StatusMethodNotAllowed, "Method not allowed")
		return
	}

	links, err := s.db.GetTopologyLinks(s.serverID)
	if err != nil {
		s.logger.Printf("Failed to query topology links: %v", err)
		s.sendError(w, http.StatusInternalServerError, "Failed to query topology links")
		return
	}
	if links == nil {
		links = []map[string]interface{}{}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(models.APIResponse{Success: true, Data: links})
}

// ── Internal helpers ──────────────────────────────────────────────────────────

func (s *Server) startWalk(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, s.config.Server.MaxBodySize))
	if err != nil {
		s.sendError(w, http.StatusBadRequest, "Failed to read request body")
		return
	}

	var req walkRequest
	if err := json.Unmarshal(body, &req); err != nil {
		s.sendError(w, http.StatusBadRequest, "Invalid JSON: "+err.Error())
		return
	}

	if req.SeedIP == "" {
		s.sendError(w, http.StatusBadRequest, "seed_ip is required")
		return
	}

	sessionID, err := s.walker.StartWalk(snmpwalker.WalkConfig{
		SeedIP:    req.SeedIP,
		Community: req.Community,
		Version:   req.Version,
		Port:      req.Port,
		MaxDepth:  req.MaxDepth,
	})
	if err != nil {
		s.sendError(w, http.StatusBadRequest, err.Error())
		return
	}

	s.logger.Printf("SNMP walk started: session=%s seed=%s", sessionID, req.SeedIP)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	json.NewEncoder(w).Encode(walkResponse{
		Success:   true,
		SessionID: sessionID,
		Message:   "Walk started",
	})
}

func (s *Server) listWalkSessions(w http.ResponseWriter, r *http.Request) {
	raw := s.walker.GetAllSessions()
	snaps := make([]snmpwalker.SessionSnapshot, len(raw))
	for i, s := range raw {
		snaps[i] = s.Snapshot()
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(models.APIResponse{
		Success: true,
		Data:    snaps,
	})
}
