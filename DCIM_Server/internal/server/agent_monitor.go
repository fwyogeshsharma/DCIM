package server

import (
	"time"

	"github.com/faberlabs/dcim-server/internal/models"
)

// startAgentMonitor starts a background goroutine that monitors agent heartbeats
// and creates alerts when agents go offline
func (s *Server) startAgentMonitor() {
	// Get heartbeat timeout from config
	heartbeatTimeout := s.config.Agents.Connection.HeartbeatTimeout

	// Check every 1 minute
	checkInterval := 1 * time.Minute

	s.logger.Printf("Agent monitor started (heartbeat timeout: %v, check interval: %v)", heartbeatTimeout, checkInterval)

	go func() {
		// Check immediately on startup
		s.checkAgentHeartbeats(heartbeatTimeout)

		// Then check every interval
		ticker := time.NewTicker(checkInterval)
		defer ticker.Stop()

		for range ticker.C {
			s.checkAgentHeartbeats(heartbeatTimeout)
		}
	}()
}

// checkAgentHeartbeats checks all agents and creates/resolves offline alerts
func (s *Server) checkAgentHeartbeats(timeout time.Duration) {
	// Get all agents
	agents, err := s.db.GetAllAgents()
	if err != nil {
		s.logger.Printf("Failed to get agents for heartbeat check: %v", err)
		return
	}

	// Use UTC for consistent timezone handling
	now := time.Now().UTC()
	offlineThreshold := now.Add(-timeout)

	s.logger.Printf("[Monitor] Checking %d agents (offline threshold: %v)", len(agents), offlineThreshold.Format("15:04:05"))

	for _, agent := range agents {
		// Skip if agent was never seen
		if agent.LastSeen.IsZero() {
			s.logger.Printf("[Monitor] Agent %s (%s) - never seen, skipping", agent.AgentID, agent.Hostname)
			continue
		}

		// Convert last_seen to UTC for consistent comparison
		lastSeenUTC := agent.LastSeen.UTC()

		// Check if agent is offline (last_seen older than threshold)
		isOffline := lastSeenUTC.Before(offlineThreshold)
		wasOffline := agent.Status == "offline"
		timeSinceLastSeen := now.Sub(lastSeenUTC)

		s.logger.Printf("[Monitor] Agent %s (%s) - Status: %s, LastSeen: %v ago, IsOffline: %v, WasOffline: %v",
			agent.AgentID, agent.Hostname, agent.Status, timeSinceLastSeen.Round(time.Second), isOffline, wasOffline)

		if isOffline && !wasOffline {
			// Agent just went offline - create alert and update status
			s.handleAgentOffline(agent, now)
		} else if !isOffline && wasOffline {
			// Agent came back online - resolve alert and update status
			s.handleAgentOnline(agent, now)
		}
	}
}

// handleAgentOffline handles when an agent goes offline
func (s *Server) handleAgentOffline(agent models.Agent, now time.Time) {
	s.logger.Printf("Agent %s (%s) went offline (last seen: %v)", agent.AgentID, agent.Hostname, agent.LastSeen)

	// Update agent status to offline
	_, err := s.db.UpdateAgentStatus(agent.AgentID, "offline")
	if err != nil {
		s.logger.Printf("Failed to update agent status to offline: %v", err)
	}

	// Create offline alert
	alert := models.Alert{
		AgentID:    agent.AgentID,
		Timestamp:  now,
		Severity:   "CRITICAL",
		MetricType: "agent_offline",
		Value:      0,
		Threshold:  0,
		Message:    "Agent is offline - no data received for " + s.config.Agents.Connection.HeartbeatTimeout.String(),
		Resolved:   false,
	}

	// InsertAlerts takes serverID as first parameter
	err = s.db.InsertAlerts(s.serverID, []models.Alert{alert})
	if err != nil {
		s.logger.Printf("Failed to create offline alert for agent %s: %v", agent.AgentID, err)
	}
}

// handleAgentOnline handles when an agent comes back online
func (s *Server) handleAgentOnline(agent models.Agent, now time.Time) {
	s.logger.Printf("Agent %s (%s) came back online", agent.AgentID, agent.Hostname)

	// Update agent status to online
	_, err := s.db.UpdateAgentStatus(agent.AgentID, "online")
	if err != nil {
		s.logger.Printf("Failed to update agent status to online: %v", err)
	}

	// Resolve offline alerts for this agent
	err = s.db.ResolveAgentOfflineAlerts(agent.AgentID)
	if err != nil {
		s.logger.Printf("Failed to resolve offline alerts for agent %s: %v", agent.AgentID, err)
	}
}
