package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
)

// ResolveAlertRequest represents the request to resolve an alert
type ResolveAlertRequest struct {
	ResolvedBy       string `json:"resolved_by"`        // Username/operator who resolved it
	ResolutionAction string `json:"resolution_action"`  // What fix was applied
	ResolutionNotes  string `json:"resolution_notes"`   // Additional comments/notes
}

// handleAlertsWithID routes /alerts/{id} and /alerts/{id}/resolve requests
func (s *Server) handleAlertsWithID(w http.ResponseWriter, r *http.Request) {
	// Check if path ends with /resolve
	if strings.HasSuffix(r.URL.Path, "/resolve") {
		s.handleResolveAlert(w, r)
	} else {
		s.handleGetAlert(w, r)
	}
}

// handleResolveAlert handles PUT /api/v1/alerts/{id}/resolve
func (s *Server) handleResolveAlert(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPut && r.Method != http.MethodPost {
		s.sendError(w, http.StatusMethodNotAllowed, "Method not allowed")
		return
	}

	// Extract alert ID from URL path
	// Expected format: /api/v1/alerts/{id}/resolve
	path := strings.TrimPrefix(r.URL.Path, s.config.API.BasePath+"/alerts/")
	parts := strings.Split(path, "/")
	if len(parts) < 2 || parts[1] != "resolve" {
		s.sendError(w, http.StatusBadRequest, "Invalid URL format. Expected: /api/v1/alerts/{id}/resolve")
		return
	}

	alertID, err := strconv.ParseInt(parts[0], 10, 64)
	if err != nil {
		s.sendError(w, http.StatusBadRequest, "Invalid alert ID")
		return
	}

	// Read and parse request body
	body, err := io.ReadAll(io.LimitReader(r.Body, s.config.Server.MaxBodySize))
	if err != nil {
		s.sendError(w, http.StatusBadRequest, "Failed to read request body")
		return
	}

	var req ResolveAlertRequest
	if err := json.Unmarshal(body, &req); err != nil {
		s.sendError(w, http.StatusBadRequest, "Invalid JSON: "+err.Error())
		return
	}

	// Validate required fields
	if req.ResolvedBy == "" {
		s.sendError(w, http.StatusBadRequest, "resolved_by is required")
		return
	}

	if req.ResolutionAction == "" {
		s.sendError(w, http.StatusBadRequest, "resolution_action is required")
		return
	}

	// Check if alert exists
	alert, err := s.db.GetAlertByID(alertID)
	if err != nil {
		s.sendError(w, http.StatusNotFound, "Alert not found")
		return
	}

	// Check if already resolved
	if resolved, ok := alert["resolved"].(bool); ok && resolved {
		s.sendError(w, http.StatusConflict, "Alert is already resolved")
		return
	}

	// Resolve the alert
	err = s.db.ResolveAlert(alertID, req.ResolvedBy, req.ResolutionAction, req.ResolutionNotes)
	if err != nil {
		s.logger.Printf("Failed to resolve alert %d: %v", alertID, err)
		s.sendError(w, http.StatusInternalServerError, "Failed to resolve alert")
		return
	}

	s.logger.Printf("Alert %d resolved by %s: %s", alertID, req.ResolvedBy, req.ResolutionAction)

	// Get updated alert
	updatedAlert, err := s.db.GetAlertByID(alertID)
	if err != nil {
		updatedAlert = map[string]interface{}{
			"id":      alertID,
			"message": "Alert resolved successfully",
		}
	}

	// Send success response
	response := map[string]interface{}{
		"success": true,
		"message": fmt.Sprintf("Alert %d resolved successfully", alertID),
		"data":    updatedAlert,
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(response)
}

// handleGetAlert handles GET /api/v1/alerts/{id}
func (s *Server) handleGetAlert(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		s.sendError(w, http.StatusMethodNotAllowed, "Method not allowed")
		return
	}

	// Extract alert ID from URL path
	// Expected format: /api/v1/alerts/{id}
	path := strings.TrimPrefix(r.URL.Path, s.config.API.BasePath+"/alerts/")
	parts := strings.Split(path, "/")

	alertID, err := strconv.ParseInt(parts[0], 10, 64)
	if err != nil {
		s.sendError(w, http.StatusBadRequest, "Invalid alert ID")
		return
	}

	// Get alert from database
	alert, err := s.db.GetAlertByID(alertID)
	if err != nil {
		s.sendError(w, http.StatusNotFound, "Alert not found")
		return
	}

	// Send success response
	response := map[string]interface{}{
		"success": true,
		"data":    alert,
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(response)
}
