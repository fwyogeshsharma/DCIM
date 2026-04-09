package server

import (
	"encoding/json"
	"net/http"
	"strconv"

	"github.com/faberlabs/dcim-server/internal/models"
	"github.com/faberlabs/dcim-server/internal/snmptrap"
)

// handleSNMPTraps  GET /api/v1/traps — list stored traps
func (s *Server) handleSNMPTraps(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		s.sendError(w, http.StatusMethodNotAllowed, "Method not allowed")
		return
	}

	q := r.URL.Query()

	limit := 500
	if l := q.Get("limit"); l != "" {
		if v, err := strconv.Atoi(l); err == nil && v > 0 {
			limit = v
		}
	}

	var resolved *bool
	if rv := q.Get("resolved"); rv != "" {
		b := rv == "true" || rv == "1"
		resolved = &b
	}

	trapType := q.Get("trap_type")

	traps, err := s.db.GetSNMPTraps(s.serverID, resolved, trapType, limit)
	if err != nil {
		s.logger.Printf("Failed to query traps: %v", err)
		s.sendError(w, http.StatusInternalServerError, "Failed to query traps")
		return
	}

	if traps == nil {
		traps = []models.SNMPTrap{}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(models.APIResponse{
		Success: true,
		Data:    traps,
	})
}

// onTrapReceived is called by the trap receiver for every incoming trap.
// It persists the trap to the database.
func (s *Server) onTrapReceived(trap snmptrap.Trap) {
	// Look up device name from topology nodes if available
	deviceName := trap.SourceIP

	row := models.SNMPTrap{
		ServerID:    s.serverID,
		Timestamp:   trap.Timestamp,
		SourceIP:    trap.SourceIP,
		DeviceName:  deviceName,
		TrapType:    trap.TrapType,
		TrapOID:     trap.TrapOID,
		Severity:    trap.Severity,
		Varbinds:    models.JSONMap(trap.Varbinds),
		Description: trap.Description,
	}

	if err := s.db.InsertSNMPTrap(s.serverID, row); err != nil {
		s.logger.Printf("[TRAP] Failed to store trap from %s: %v", trap.SourceIP, err)
		return
	}

	s.logger.Printf("[TRAP] %s from %s — %s", trap.TrapType, trap.SourceIP, trap.Description)
}
