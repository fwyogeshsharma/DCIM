//go:build !windows

package os

import (
	"fmt"
	"time"
)

// WindowsUpdate represents a Windows update (stub for non-Windows)
type WindowsUpdate struct {
	HotFixID    string    `json:"hotfix_id"`
	Description string    `json:"description"`
	InstalledOn time.Time `json:"installed_on"`
	InstalledBy string    `json:"installed_by"`
}

// WindowsPatchLevel represents Windows patch level information (stub for non-Windows)
type WindowsPatchLevel struct {
	OSVersion       string          `json:"os_version"`
	OSEdition       string          `json:"os_edition"`
	BuildNumber     string          `json:"build_number"`
	OSBuild         string          `json:"os_build"`
	UBR             int             `json:"ubr"`
	ReleaseID       string          `json:"release_id"`
	DisplayVersion  string          `json:"display_version"`
	InstallDate     time.Time       `json:"install_date"`
	LastBootUpTime  time.Time       `json:"last_boot_time"`
	Updates         []WindowsUpdate `json:"updates"`
	UpdateCount     int             `json:"update_count"`
	PendingUpdates  int             `json:"pending_updates"`
	LastUpdateCheck time.Time       `json:"last_update_check"`
	Timestamp       time.Time       `json:"timestamp"`
}

// GetWindowsPatchLevel is a stub for non-Windows platforms
func GetWindowsPatchLevel() (*WindowsPatchLevel, error) {
	return nil, fmt.Errorf("Windows patch level not available on this platform")
}

// GetLatestUpdate is a stub method
func (w *WindowsPatchLevel) GetLatestUpdate() *WindowsUpdate {
	if w == nil || len(w.Updates) == 0 {
		return nil
	}
	return &w.Updates[0]
}

// FormatSummary is a stub method
func (w *WindowsPatchLevel) FormatSummary() string {
	if w == nil {
		return ""
	}
	return fmt.Sprintf("%s %s (Build: %s) - %d updates installed",
		w.OSVersion, w.DisplayVersion, w.BuildNumber, w.UpdateCount)
}

// FormatUptime is a stub method
func (w *WindowsPatchLevel) FormatUptime() string {
	if w == nil || w.LastBootUpTime.IsZero() {
		return "Unknown"
	}
	uptime := time.Since(w.LastBootUpTime)
	days := int(uptime.Hours() / 24)
	hours := int(uptime.Hours()) % 24
	return fmt.Sprintf("%d days, %d hours", days, hours)
}
