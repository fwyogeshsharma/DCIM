package server

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/faberlabs/dcim-server/internal/config"
	"github.com/faberlabs/dcim-server/internal/database"
)

func generateUUID() string {
	b := make([]byte, 16)
	rand.Read(b)
	b[6] = (b[6] & 0x0f) | 0x40 // version 4
	b[8] = (b[8] & 0x3f) | 0x80 // variant bits
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x",
		b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}

// initializeServerID initializes and registers the server instance
func initializeServerID(cfg *config.Config, db *database.Database) (string, error) {
	var serverID string

	// Check if server ID is configured
	if cfg.ServerID.ID != "" {
		serverID = cfg.ServerID.ID
	} else if cfg.ServerID.AutoGenerate {
		// Try to load from file
		serverIDFile := "./data/server_id.txt"
		data, err := os.ReadFile(serverIDFile)
		if err == nil {
			serverID = strings.TrimSpace(string(data))
		} else {
			// Generate new server ID as a proper UUID
			serverID = generateUUID()

			// Ensure data directory exists
			os.MkdirAll("./data", 0755)

			// Save to file
			err = os.WriteFile(serverIDFile, []byte(serverID), 0644)
			if err != nil {
				return "", fmt.Errorf("failed to save server ID: %w", err)
			}
		}
	} else {
		return "", fmt.Errorf("server_id not configured and auto_generate is false")
	}

	// Register server in database
	hostname, _ := os.Hostname()
	version := "2.0.0" // TODO: Get from build info

	err := db.RegisterServer(
		serverID,
		cfg.ServerID.Name,
		cfg.ServerID.Location,
		cfg.ServerID.Environment,
		hostname,
		version,
	)

	if err != nil {
		return "", fmt.Errorf("failed to register server: %w", err)
	}

	return serverID, nil
}

// generateRandomID generates a random hex string of specified length
func generateRandomID(length int) string {
	bytes := make([]byte, length/2)
	rand.Read(bytes)
	return hex.EncodeToString(bytes)
}

// serverHeartbeat periodically updates the server's last_seen timestamp
func (s *Server) serverHeartbeat() {
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()

	for range ticker.C {
		err := s.db.UpdateServerLastSeen(s.serverID)
		if err != nil {
			s.logger.Printf("Failed to update server heartbeat: %v", err)
		}
	}
}
