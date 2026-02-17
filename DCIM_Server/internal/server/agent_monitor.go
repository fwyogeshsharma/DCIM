package server

import (
	"fmt"
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
	// Expected interval is heartbeat timeout / 2 (agents should send more frequently than timeout)
	expectedInterval := timeout / 2

	s.logger.Printf("[Monitor] Checking %d agents (offline threshold: %v, expected interval: %v)", len(agents), offlineThreshold.Format("15:04:05"), expectedInterval)

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

		// Check for degraded/hanging status (slow responses)
		isDegraded := false
		isHanging := false

		if !isOffline && agent.LastResponseTime != nil && expectedInterval > 0 {
			// Consider "slow" if response time > 1.5x expected interval
			slowThreshold := time.Duration(float64(expectedInterval) * 1.5)
			isSlow := *agent.LastResponseTime > slowThreshold

			if isSlow {
				// Check for hanging (consistent slowness)
				if agent.ConsecutiveSlowCount >= 5 {
					isHanging = true
				} else if agent.ConsecutiveSlowCount >= 3 {
					isDegraded = true
				}
			}
		}

		s.logger.Printf("[Monitor] Agent %s (%s) - Status: %s, LastSeen: %v ago, ResponseTime: %v, SlowCount: %d, IsOffline: %v, IsDegraded: %v, IsHanging: %v",
			agent.AgentID, agent.Hostname, agent.Status,
			timeSinceLastSeen.Round(time.Second),
			formatDuration(agent.LastResponseTime),
			agent.ConsecutiveSlowCount,
			isOffline, isDegraded, isHanging)

		// Handle state transitions
		if isOffline && !wasOffline {
			// Agent just went offline - create alert and update status
			s.handleAgentOffline(agent, now)
		} else if !isOffline && wasOffline {
			// Agent came back online - resolve alert and update status
			s.handleAgentOnline(agent, now)
		} else if !isOffline {
			// Agent is online - check for degraded/hanging status
			if isHanging && agent.Status != "degraded" {
				s.handleAgentHanging(agent, now)
			} else if isDegraded && agent.Status == "online" {
				s.handleAgentDegraded(agent, now)
			} else if !isDegraded && !isHanging && agent.Status == "degraded" {
				s.handleAgentRecovered(agent, now)
			}
		}
	}
}

// formatDuration formats a duration pointer for logging
func formatDuration(d *time.Duration) string {
	if d == nil {
		return "N/A"
	}
	return d.Round(time.Second).String()
}

// handleAgentOffline handles when an agent goes offline
func (s *Server) handleAgentOffline(agent models.Agent, now time.Time) {
	s.logger.Printf("Agent %s (%s) went offline (last seen: %v)", agent.AgentID, agent.Hostname, agent.LastSeen)

	// Update agent status to offline
	_, err := s.db.UpdateAgentStatus(agent.AgentID, "offline")
	if err != nil {
		s.logger.Printf("Failed to update agent status to offline: %v", err)
	}

	// Check if there was a recent graceful shutdown notification
	// Look for shutdown events within heartbeat timeout + 1 minute grace period
	shutdownCheckWindow := s.config.Agents.Connection.HeartbeatTimeout + (1 * time.Minute)
	recentShutdown, err := s.db.GetRecentShutdownEvent(agent.AgentID, shutdownCheckWindow)
	if err != nil {
		s.logger.Printf("Failed to check for recent shutdown event: %v", err)
	}

	// Determine alert severity and message based on shutdown type
	var severity string
	var message string
	var metricType string

	if recentShutdown != nil {
		// Agent notified us about shutdown
		if recentShutdown.ShutdownType == "graceful" {
			severity = "INFO"
			message = "Agent shutdown gracefully"
			metricType = "agent_shutdown_graceful"
			if recentShutdown.Reason != "" {
				message = message + ": " + recentShutdown.Reason
			}
			s.logger.Printf("Agent %s had graceful shutdown notification at %v", agent.AgentID, recentShutdown.ShutdownTime)
		} else {
			// Error shutdown
			severity = "CRITICAL"
			message = "Agent shutdown with error"
			metricType = "agent_shutdown_error"
			if recentShutdown.Reason != "" {
				message = message + ": " + recentShutdown.Reason
			}
			s.logger.Printf("Agent %s had error shutdown notification at %v", agent.AgentID, recentShutdown.ShutdownTime)
		}
	} else {
		// No shutdown notification - unexpected offline (crash/hang/network)
		severity = "CRITICAL"
		message = "Agent went offline unexpectedly - no data received for " + s.config.Agents.Connection.HeartbeatTimeout.String()
		metricType = "agent_offline"
		s.logger.Printf("Agent %s went offline unexpectedly (no shutdown notification)", agent.AgentID)
	}

	// Create alert with appropriate severity
	alert := models.Alert{
		AgentID:    agent.AgentID,
		Timestamp:  now,
		Severity:   severity,
		MetricType: metricType,
		Value:      0,
		Threshold:  0,
		Message:    message,
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

	// Reset consecutive slow count
	s.db.ResetAgentSlowCount(agent.AgentID)

	// Resolve offline alerts for this agent
	err = s.db.ResolveAgentOfflineAlerts(agent.AgentID)
	if err != nil {
		s.logger.Printf("Failed to resolve offline alerts for agent %s: %v", agent.AgentID, err)
	}
}

// handleAgentDegraded handles when an agent becomes degraded (slow responses)
func (s *Server) handleAgentDegraded(agent models.Agent, now time.Time) {
	s.logger.Printf("Agent %s (%s) is degraded - slow responses detected (count: %d, response time: %v)",
		agent.AgentID, agent.Hostname, agent.ConsecutiveSlowCount, formatDuration(agent.LastResponseTime))

	// Update agent status to degraded
	_, err := s.db.UpdateAgentStatus(agent.AgentID, "degraded")
	if err != nil {
		s.logger.Printf("Failed to update agent status to degraded: %v", err)
	}

	// Create WARNING alert for degraded performance
	alert := models.Alert{
		AgentID:    agent.AgentID,
		Timestamp:  now,
		Severity:   "WARNING",
		MetricType: "agent_degraded",
		Value:      float64(agent.ConsecutiveSlowCount),
		Threshold:  3.0,
		Message:    fmt.Sprintf("Agent is responding slowly (%d consecutive slow responses, last: %v)", agent.ConsecutiveSlowCount, formatDuration(agent.LastResponseTime)),
		Resolved:   false,
	}

	err = s.db.InsertAlerts(s.serverID, []models.Alert{alert})
	if err != nil {
		s.logger.Printf("Failed to create degraded alert for agent %s: %v", agent.AgentID, err)
	}
}

// handleAgentHanging handles when an agent appears to be hanging (very slow, consistent)
func (s *Server) handleAgentHanging(agent models.Agent, now time.Time) {
	s.logger.Printf("Agent %s (%s) appears to be hanging - critically slow responses (count: %d, response time: %v)",
		agent.AgentID, agent.Hostname, agent.ConsecutiveSlowCount, formatDuration(agent.LastResponseTime))

	// Keep status as degraded (hanging is a severe form of degraded)
	_, err := s.db.UpdateAgentStatus(agent.AgentID, "degraded")
	if err != nil {
		s.logger.Printf("Failed to update agent status: %v", err)
	}

	// Create CRITICAL alert for hanging
	alert := models.Alert{
		AgentID:    agent.AgentID,
		Timestamp:  now,
		Severity:   "CRITICAL",
		MetricType: "agent_hanging",
		Value:      float64(agent.ConsecutiveSlowCount),
		Threshold:  5.0,
		Message:    fmt.Sprintf("Agent appears to be hanging (%d consecutive slow responses, last: %v) - may need restart", agent.ConsecutiveSlowCount, formatDuration(agent.LastResponseTime)),
		Resolved:   false,
	}

	err = s.db.InsertAlerts(s.serverID, []models.Alert{alert})
	if err != nil {
		s.logger.Printf("Failed to create hanging alert for agent %s: %v", agent.AgentID, err)
	}
}

// handleAgentRecovered handles when a degraded agent recovers to normal performance
func (s *Server) handleAgentRecovered(agent models.Agent, now time.Time) {
	s.logger.Printf("Agent %s (%s) recovered to normal performance", agent.AgentID, agent.Hostname)

	// Update agent status back to online
	_, err := s.db.UpdateAgentStatus(agent.AgentID, "online")
	if err != nil {
		s.logger.Printf("Failed to update agent status to online: %v", err)
	}

	// Reset consecutive slow count
	s.db.ResetAgentSlowCount(agent.AgentID)

	// Note: Degraded/hanging alerts remain in database for historical tracking
	// They're marked resolved by status but can be manually resolved if needed
}
