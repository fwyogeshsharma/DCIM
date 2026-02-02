//go:build linux

package os

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

// LinuxPackage represents an installed package
type LinuxPackage struct {
	Name         string    `json:"name"`
	Version      string    `json:"version"`
	Architecture string    `json:"architecture"`
	InstallDate  time.Time `json:"install_date"`
	Description  string    `json:"description"`
	Size         int64     `json:"size"` // Size in KB
}

// LinuxPatchLevel represents Linux patch level information
type LinuxPatchLevel struct {
	// Distribution Information
	Distribution    string `json:"distribution"`     // "Ubuntu", "CentOS", "RHEL", "Debian"
	DistroVersion   string `json:"distro_version"`   // "22.04", "8.5"
	DistroCodename  string `json:"distro_codename"`  // "jammy", "focal"
	DistroID        string `json:"distro_id"`        // "ubuntu", "centos"

	// Kernel Information
	KernelVersion   string `json:"kernel_version"`   // "5.15.0-97-generic"
	KernelRelease   string `json:"kernel_release"`   // Full release string

	// Package Management
	PackageManager  string          `json:"package_manager"`  // "apt", "yum", "dnf", "zypper"
	Packages        []LinuxPackage  `json:"packages"`
	PackageCount    int             `json:"package_count"`
	UpdatesAvailable int            `json:"updates_available"`
	SecurityUpdates int             `json:"security_updates"`

	// System Information
	LastUpdateCheck time.Time `json:"last_update_check"`
	LastUpgrade     time.Time `json:"last_upgrade"`

	Timestamp time.Time `json:"timestamp"`
}

// GetLinuxPatchLevel retrieves comprehensive Linux patch level information
func GetLinuxPatchLevel() (*LinuxPatchLevel, error) {
	patchLevel := &LinuxPatchLevel{
		Timestamp: time.Now(),
	}

	// Get distribution information
	if err := patchLevel.getDistributionInfo(); err != nil {
		return nil, fmt.Errorf("get distribution info: %w", err)
	}

	// Get kernel information
	if err := patchLevel.getKernelInfo(); err != nil {
		// Non-fatal
	}

	// Detect package manager
	patchLevel.PackageManager = detectPackageManager()

	// Get installed packages (can be slow, make optional)
	// if err := patchLevel.getInstalledPackages(); err != nil {
	// 	// Non-fatal
	// }

	// Get available updates
	if err := patchLevel.getAvailableUpdates(); err != nil {
		// Non-fatal
	}

	return patchLevel, nil
}

// getDistributionInfo retrieves Linux distribution information
func (p *LinuxPatchLevel) getDistributionInfo() error {
	// Try /etc/os-release first (standard)
	if err := p.parseOSRelease(); err == nil {
		return nil
	}

	// Fallback to /etc/lsb-release
	if err := p.parseLSBRelease(); err == nil {
		return nil
	}

	// Fallback to specific distribution files
	p.parseDistroSpecificFiles()

	return nil
}

// parseOSRelease parses /etc/os-release
func (p *LinuxPatchLevel) parseOSRelease() error {
	file, err := os.Open("/etc/os-release")
	if err != nil {
		return err
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "NAME=") {
			p.Distribution = unquote(strings.TrimPrefix(line, "NAME="))
		} else if strings.HasPrefix(line, "VERSION_ID=") {
			p.DistroVersion = unquote(strings.TrimPrefix(line, "VERSION_ID="))
		} else if strings.HasPrefix(line, "VERSION_CODENAME=") {
			p.DistroCodename = unquote(strings.TrimPrefix(line, "VERSION_CODENAME="))
		} else if strings.HasPrefix(line, "ID=") {
			p.DistroID = unquote(strings.TrimPrefix(line, "ID="))
		}
	}

	return scanner.Err()
}

// parseLSBRelease parses /etc/lsb-release
func (p *LinuxPatchLevel) parseLSBRelease() error {
	file, err := os.Open("/etc/lsb-release")
	if err != nil {
		return err
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "DISTRIB_ID=") {
			p.DistroID = unquote(strings.TrimPrefix(line, "DISTRIB_ID="))
		} else if strings.HasPrefix(line, "DISTRIB_RELEASE=") {
			p.DistroVersion = unquote(strings.TrimPrefix(line, "DISTRIB_RELEASE="))
		} else if strings.HasPrefix(line, "DISTRIB_CODENAME=") {
			p.DistroCodename = unquote(strings.TrimPrefix(line, "DISTRIB_CODENAME="))
		} else if strings.HasPrefix(line, "DISTRIB_DESCRIPTION=") {
			p.Distribution = unquote(strings.TrimPrefix(line, "DISTRIB_DESCRIPTION="))
		}
	}

	return scanner.Err()
}

// parseDistroSpecificFiles checks for distribution-specific files
func (p *LinuxPatchLevel) parseDistroSpecificFiles() {
	// Check for Red Hat/CentOS
	if data, err := os.ReadFile("/etc/redhat-release"); err == nil {
		p.Distribution = strings.TrimSpace(string(data))
		p.DistroID = "rhel"
		return
	}

	// Check for Debian
	if data, err := os.ReadFile("/etc/debian_version"); err == nil {
		p.DistroVersion = strings.TrimSpace(string(data))
		p.Distribution = "Debian"
		p.DistroID = "debian"
		return
	}

	// Check for SUSE
	if data, err := os.ReadFile("/etc/SuSE-release"); err == nil {
		lines := strings.Split(string(data), "\n")
		if len(lines) > 0 {
			p.Distribution = strings.TrimSpace(lines[0])
			p.DistroID = "suse"
		}
		return
	}
}

// getKernelInfo retrieves kernel information
func (p *LinuxPatchLevel) getKernelInfo() error {
	// Get kernel release (uname -r)
	cmd := exec.Command("uname", "-r")
	output, err := cmd.Output()
	if err != nil {
		return err
	}
	p.KernelRelease = strings.TrimSpace(string(output))

	// Get kernel version (uname -v)
	cmd = exec.Command("uname", "-v")
	output, err = cmd.Output()
	if err == nil {
		p.KernelVersion = strings.TrimSpace(string(output))
	}

	// If KernelVersion is empty, use KernelRelease
	if p.KernelVersion == "" {
		p.KernelVersion = p.KernelRelease
	}

	return nil
}

// detectPackageManager detects the system package manager
func detectPackageManager() string {
	// Check for common package managers
	managers := []string{"apt", "yum", "dnf", "zypper", "pacman"}

	for _, mgr := range managers {
		if _, err := exec.LookPath(mgr); err == nil {
			return mgr
		}
	}

	return "unknown"
}

// getInstalledPackages retrieves list of installed packages
func (p *LinuxPatchLevel) getInstalledPackages() error {
	switch p.PackageManager {
	case "apt":
		return p.getAptPackages()
	case "yum", "dnf":
		return p.getRpmPackages()
	case "zypper":
		return p.getZypperPackages()
	case "pacman":
		return p.getPacmanPackages()
	default:
		return fmt.Errorf("unsupported package manager: %s", p.PackageManager)
	}
}

// getAptPackages retrieves packages for Debian/Ubuntu (apt)
func (p *LinuxPatchLevel) getAptPackages() error {
	cmd := exec.Command("dpkg-query", "-W", "-f", "${Package}\t${Version}\t${Architecture}\t${Installed-Size}\n")
	output, err := cmd.Output()
	if err != nil {
		return err
	}

	scanner := bufio.NewScanner(strings.NewReader(string(output)))
	for scanner.Scan() {
		parts := strings.Split(scanner.Text(), "\t")
		if len(parts) >= 3 {
			pkg := LinuxPackage{
				Name:         parts[0],
				Version:      parts[1],
				Architecture: parts[2],
			}

			if len(parts) >= 4 {
				fmt.Sscanf(parts[3], "%d", &pkg.Size)
			}

			p.Packages = append(p.Packages, pkg)
		}
	}

	p.PackageCount = len(p.Packages)
	return nil
}

// getRpmPackages retrieves packages for RHEL/CentOS (rpm)
func (p *LinuxPatchLevel) getRpmPackages() error {
	cmd := exec.Command("rpm", "-qa", "--queryformat", "%{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\t%{SIZE}\n")
	output, err := cmd.Output()
	if err != nil {
		return err
	}

	scanner := bufio.NewScanner(strings.NewReader(string(output)))
	for scanner.Scan() {
		parts := strings.Split(scanner.Text(), "\t")
		if len(parts) >= 3 {
			pkg := LinuxPackage{
				Name:         parts[0],
				Version:      parts[1],
				Architecture: parts[2],
			}

			if len(parts) >= 4 {
				fmt.Sscanf(parts[3], "%d", &pkg.Size)
			}

			p.Packages = append(p.Packages, pkg)
		}
	}

	p.PackageCount = len(p.Packages)
	return nil
}

// getZypperPackages retrieves packages for SUSE (zypper)
func (p *LinuxPatchLevel) getZypperPackages() error {
	cmd := exec.Command("rpm", "-qa", "--queryformat", "%{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\n")
	output, err := cmd.Output()
	if err != nil {
		return err
	}

	scanner := bufio.NewScanner(strings.NewReader(string(output)))
	for scanner.Scan() {
		parts := strings.Split(scanner.Text(), "\t")
		if len(parts) >= 3 {
			pkg := LinuxPackage{
				Name:         parts[0],
				Version:      parts[1],
				Architecture: parts[2],
			}

			p.Packages = append(p.Packages, pkg)
		}
	}

	p.PackageCount = len(p.Packages)
	return nil
}

// getPacmanPackages retrieves packages for Arch Linux (pacman)
func (p *LinuxPatchLevel) getPacmanPackages() error {
	cmd := exec.Command("pacman", "-Q")
	output, err := cmd.Output()
	if err != nil {
		return err
	}

	scanner := bufio.NewScanner(strings.NewReader(string(output)))
	for scanner.Scan() {
		parts := strings.Fields(scanner.Text())
		if len(parts) >= 2 {
			pkg := LinuxPackage{
				Name:    parts[0],
				Version: parts[1],
			}

			p.Packages = append(p.Packages, pkg)
		}
	}

	p.PackageCount = len(p.Packages)
	return nil
}

// getAvailableUpdates checks for available updates
func (p *LinuxPatchLevel) getAvailableUpdates() error {
	switch p.PackageManager {
	case "apt":
		return p.getAptUpdates()
	case "yum", "dnf":
		return p.getYumUpdates()
	case "zypper":
		return p.getZypperUpdates()
	default:
		return nil
	}
}

// getAptUpdates checks for apt updates
func (p *LinuxPatchLevel) getAptUpdates() error {
	// Update cache first (requires sudo, skip if no permissions)
	// exec.Command("apt-get", "update").Run()

	// List upgradable packages
	cmd := exec.Command("apt", "list", "--upgradable")
	output, err := cmd.Output()
	if err != nil {
		return err
	}

	lines := strings.Split(string(output), "\n")
	p.UpdatesAvailable = 0
	p.SecurityUpdates = 0

	for _, line := range lines {
		if strings.Contains(line, "upgradable") {
			p.UpdatesAvailable++
			if strings.Contains(strings.ToLower(line), "security") {
				p.SecurityUpdates++
			}
		}
	}

	return nil
}

// getYumUpdates checks for yum/dnf updates
func (p *LinuxPatchLevel) getYumUpdates() error {
	cmdName := p.PackageManager
	if cmdName == "dnf" || cmdName == "yum" {
		cmd := exec.Command(cmdName, "check-update", "--quiet")
		output, _ := cmd.CombinedOutput()

		// Count update lines (non-empty, non-header lines)
		lines := strings.Split(string(output), "\n")
		p.UpdatesAvailable = 0

		for _, line := range lines {
			line = strings.TrimSpace(line)
			if line != "" && !strings.HasPrefix(line, "Last metadata") {
				p.UpdatesAvailable++
			}
		}

		// Check for security updates
		cmd = exec.Command(cmdName, "updateinfo", "list", "security", "--quiet")
		output, _ = cmd.Output()
		p.SecurityUpdates = len(strings.Split(string(output), "\n")) - 1
		if p.SecurityUpdates < 0 {
			p.SecurityUpdates = 0
		}
	}

	return nil
}

// getZypperUpdates checks for zypper updates
func (p *LinuxPatchLevel) getZypperUpdates() error {
	cmd := exec.Command("zypper", "list-updates")
	output, err := cmd.Output()
	if err != nil {
		return err
	}

	lines := strings.Split(string(output), "\n")
	p.UpdatesAvailable = 0

	for _, line := range lines {
		if strings.HasPrefix(line, "v |") || strings.HasPrefix(line, "i |") {
			p.UpdatesAvailable++
		}
	}

	return nil
}

// HasPackage checks if a specific package is installed
func (p *LinuxPatchLevel) HasPackage(packageName string) bool {
	for _, pkg := range p.Packages {
		if pkg.Name == packageName {
			return true
		}
	}
	return false
}

// GetPackage returns information for a specific package
func (p *LinuxPatchLevel) GetPackage(packageName string) *LinuxPackage {
	for i := range p.Packages {
		if p.Packages[i].Name == packageName {
			return &p.Packages[i]
		}
	}
	return nil
}

// GetPackagesByPattern returns packages matching a pattern
func (p *LinuxPatchLevel) GetPackagesByPattern(pattern string) []LinuxPackage {
	var matches []LinuxPackage
	pattern = strings.ToLower(pattern)

	for _, pkg := range p.Packages {
		if strings.Contains(strings.ToLower(pkg.Name), pattern) {
			matches = append(matches, pkg)
		}
	}

	return matches
}

// unquote removes quotes from string
func unquote(s string) string {
	s = strings.TrimSpace(s)
	if len(s) >= 2 && s[0] == '"' && s[len(s)-1] == '"' {
		return s[1 : len(s)-1]
	}
	return s
}

// FormatDistribution returns formatted distribution information
func (p *LinuxPatchLevel) FormatDistribution() string {
	if p.DistroCodename != "" {
		return fmt.Sprintf("%s %s (%s)", p.Distribution, p.DistroVersion, p.DistroCodename)
	}
	return fmt.Sprintf("%s %s", p.Distribution, p.DistroVersion)
}

// IsUbuntu returns true if running Ubuntu
func (p *LinuxPatchLevel) IsUbuntu() bool {
	return strings.ToLower(p.DistroID) == "ubuntu"
}

// IsDebian returns true if running Debian
func (p *LinuxPatchLevel) IsDebian() bool {
	return strings.ToLower(p.DistroID) == "debian"
}

// IsRHEL returns true if running RHEL or CentOS
func (p *LinuxPatchLevel) IsRHEL() bool {
	distro := strings.ToLower(p.DistroID)
	return distro == "rhel" || distro == "centos" || distro == "rocky" || distro == "almalinux"
}

// IsSUSE returns true if running SUSE
func (p *LinuxPatchLevel) IsSUSE() bool {
	distro := strings.ToLower(p.DistroID)
	return strings.Contains(distro, "suse")
}
