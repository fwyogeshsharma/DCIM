package os

import (
	"fmt"
	"runtime"
	"time"
)

// PatchLevel represents unified OS patch level information
type PatchLevel struct {
	OS          string    `json:"os"`           // "windows", "linux"
	OSName      string    `json:"os_name"`      // Human-readable name
	Version     string    `json:"version"`      // OS version
	BuildNumber string    `json:"build_number"` // Build/kernel version
	PatchCount  int       `json:"patch_count"`  // Number of patches/updates
	LastUpdate  time.Time `json:"last_update"`  // Last patch installation

	// Platform-specific data
	Windows *WindowsPatchLevel `json:"windows,omitempty"`
	Linux   *LinuxPatchLevel   `json:"linux,omitempty"`

	Timestamp time.Time `json:"timestamp"`
}

// GetPatchLevel retrieves OS patch level for current platform
func GetPatchLevel() (*PatchLevel, error) {
	patchLevel := &PatchLevel{
		OS:        runtime.GOOS,
		Timestamp: time.Now(),
	}

	switch runtime.GOOS {
	case "windows":
		return getPatchLevelWindows(patchLevel)
	case "linux":
		return getPatchLevelLinux(patchLevel)
	default:
		return nil, fmt.Errorf("unsupported OS: %s", runtime.GOOS)
	}
}

// getPatchLevelWindows retrieves patch level for Windows
func getPatchLevelWindows(p *PatchLevel) (*PatchLevel, error) {
	winPatch, err := GetWindowsPatchLevel()
	if err != nil {
		return nil, err
	}

	p.Windows = winPatch
	p.OSName = winPatch.OSVersion
	p.Version = winPatch.DisplayVersion
	p.BuildNumber = winPatch.BuildNumber
	p.PatchCount = winPatch.UpdateCount

	if latest := winPatch.GetLatestUpdate(); latest != nil {
		p.LastUpdate = latest.InstalledOn
	}

	return p, nil
}

// getPatchLevelLinux retrieves patch level for Linux
func getPatchLevelLinux(p *PatchLevel) (*PatchLevel, error) {
	linuxPatch, err := GetLinuxPatchLevel()
	if err != nil {
		return nil, err
	}

	p.Linux = linuxPatch
	p.OSName = linuxPatch.FormatDistribution()
	p.Version = linuxPatch.DistroVersion
	p.BuildNumber = linuxPatch.KernelVersion
	p.PatchCount = linuxPatch.PackageCount
	p.LastUpdate = linuxPatch.LastUpgrade

	return p, nil
}

// FormatSummary returns a formatted summary string
func (p *PatchLevel) FormatSummary() string {
	return fmt.Sprintf("%s %s (Build: %s) - %d patches installed",
		p.OSName, p.Version, p.BuildNumber, p.PatchCount)
}

// HasRecentUpdates checks if system has been updated recently
func (p *PatchLevel) HasRecentUpdates(within time.Duration) bool {
	if p.LastUpdate.IsZero() {
		return false
	}
	return time.Since(p.LastUpdate) <= within
}

// NeedsReboot checks if system needs a reboot (Windows only)
func (p *PatchLevel) NeedsReboot() bool {
	// This would require additional implementation
	// For Windows: Check registry keys for pending reboot
	// For Linux: Check for /var/run/reboot-required
	return false
}

// GetOSType returns the OS type
func (p *PatchLevel) GetOSType() string {
	return p.OS
}

// IsWindows returns true if running on Windows
func (p *PatchLevel) IsWindows() bool {
	return p.OS == "windows"
}

// IsLinux returns true if running on Linux
func (p *PatchLevel) IsLinux() bool {
	return p.OS == "linux"
}

// GetDetailedInfo returns detailed OS-specific information as string
func (p *PatchLevel) GetDetailedInfo() string {
	if p.IsWindows() && p.Windows != nil {
		info := fmt.Sprintf("Windows Information:\n")
		info += fmt.Sprintf("  OS: %s\n", p.Windows.OSVersion)
		info += fmt.Sprintf("  Edition: %s\n", p.Windows.OSEdition)
		info += fmt.Sprintf("  Build: %s\n", p.Windows.BuildNumber)
		info += fmt.Sprintf("  Version: %s\n", p.Windows.DisplayVersion)
		info += fmt.Sprintf("  Updates Installed: %d\n", p.Windows.UpdateCount)
		info += fmt.Sprintf("  Uptime: %s\n", p.Windows.FormatUptime())

		if latest := p.Windows.GetLatestUpdate(); latest != nil {
			info += fmt.Sprintf("  Latest Update: %s (%s)\n",
				latest.HotFixID, latest.InstalledOn.Format("2006-01-02"))
		}

		return info
	}

	if p.IsLinux() && p.Linux != nil {
		info := fmt.Sprintf("Linux Information:\n")
		info += fmt.Sprintf("  Distribution: %s\n", p.Linux.FormatDistribution())
		info += fmt.Sprintf("  Kernel: %s\n", p.Linux.KernelVersion)
		info += fmt.Sprintf("  Package Manager: %s\n", p.Linux.PackageManager)
		info += fmt.Sprintf("  Packages Installed: %d\n", p.Linux.PackageCount)

		if p.Linux.UpdatesAvailable > 0 {
			info += fmt.Sprintf("  Updates Available: %d", p.Linux.UpdatesAvailable)
			if p.Linux.SecurityUpdates > 0 {
				info += fmt.Sprintf(" (%d security)\n", p.Linux.SecurityUpdates)
			} else {
				info += "\n"
			}
		}

		return info
	}

	return "No detailed information available"
}

// CompareVersions compares two OS versions
// Returns: -1 if v1 < v2, 0 if equal, 1 if v1 > v2
func CompareVersions(v1, v2 string) int {
	// Simple string comparison for now
	// In production, implement proper semantic versioning
	if v1 < v2 {
		return -1
	} else if v1 > v2 {
		return 1
	}
	return 0
}

// GetInstalledPackagesCount returns the number of installed packages/updates
func (p *PatchLevel) GetInstalledPackagesCount() int {
	return p.PatchCount
}

// GetAvailableUpdatesCount returns the number of available updates
func (p *PatchLevel) GetAvailableUpdatesCount() int {
	if p.IsWindows() && p.Windows != nil {
		return p.Windows.PendingUpdates
	}
	if p.IsLinux() && p.Linux != nil {
		return p.Linux.UpdatesAvailable
	}
	return 0
}

// GetSecurityUpdatesCount returns the number of available security updates
func (p *PatchLevel) GetSecurityUpdatesCount() int {
	if p.IsLinux() && p.Linux != nil {
		return p.Linux.SecurityUpdates
	}
	return 0
}

// HasSecurityUpdates returns true if security updates are available
func (p *PatchLevel) HasSecurityUpdates() bool {
	return p.GetSecurityUpdatesCount() > 0
}
