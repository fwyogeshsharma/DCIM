//go:build !linux

package os

import (
	"fmt"
	"time"
)

// LinuxPackage represents an installed package
type LinuxPackage struct {
	Name         string    `json:"name"`
	Version      string    `json:"version"`
	Architecture string    `json:"architecture"`
	InstallDate  time.Time `json:"install_date"`
	Description  string    `json:"description"`
	Size         int64     `json:"size"`
}

// LinuxPatchLevel represents Linux patch level information
type LinuxPatchLevel struct {
	Distribution     string         `json:"distribution"`
	DistroVersion    string         `json:"distro_version"`
	DistroCodename   string         `json:"distro_codename"`
	DistroID         string         `json:"distro_id"`
	KernelVersion    string         `json:"kernel_version"`
	KernelRelease    string         `json:"kernel_release"`
	PackageManager   string         `json:"package_manager"`
	Packages         []LinuxPackage `json:"packages"`
	PackageCount     int            `json:"package_count"`
	UpdatesAvailable int            `json:"updates_available"`
	SecurityUpdates  int            `json:"security_updates"`
	LastUpdateCheck  time.Time      `json:"last_update_check"`
	LastUpgrade      time.Time      `json:"last_upgrade"`
	Timestamp        time.Time      `json:"timestamp"`
}

// GetLinuxPatchLevel is a stub for non-Linux platforms
func GetLinuxPatchLevel() (*LinuxPatchLevel, error) {
	return nil, fmt.Errorf("Linux patch level not available on %s", "windows")
}

// FormatDistribution returns formatted distribution name
func (l *LinuxPatchLevel) FormatDistribution() string {
	if l == nil {
		return ""
	}
	return fmt.Sprintf("%s %s", l.Distribution, l.DistroVersion)
}
