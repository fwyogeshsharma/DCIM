//go:build windows

package os

import (
	"fmt"
	"strings"
	"time"

	"github.com/StackExchange/wmi"
	"golang.org/x/sys/windows/registry"
)

// WindowsUpdate represents a single Windows update (KB)
type WindowsUpdate struct {
	HotFixID    string    `json:"hotfix_id"`    // "KB5034763"
	Description string    `json:"description"`   // "Security Update"
	InstalledOn time.Time `json:"installed_on"`
	InstalledBy string    `json:"installed_by"`
	Caption     string    `json:"caption"`
}

// WindowsPatchLevel represents Windows patch level information
type WindowsPatchLevel struct {
	// OS Information
	OSVersion      string    `json:"os_version"`       // "Windows 11 Pro"
	OSEdition      string    `json:"os_edition"`       // "Professional", "Enterprise"
	BuildNumber    string    `json:"build_number"`     // "22621.3085"
	OSBuild        string    `json:"os_build"`         // "22621"
	UBR            int       `json:"ubr"`              // Update Build Revision: 3085
	ReleaseID      string    `json:"release_id"`       // "22H2"
	DisplayVersion string    `json:"display_version"`  // "22H2"

	// Installation
	InstallDate     time.Time `json:"install_date"`
	LastBootUpTime  time.Time `json:"last_boot_time"`

	// Updates
	Updates         []WindowsUpdate `json:"updates"`
	UpdateCount     int             `json:"update_count"`
	PendingUpdates  int             `json:"pending_updates"`
	LastUpdateCheck time.Time       `json:"last_update_check"`

	// Build Details
	CurrentVersion  string    `json:"current_version"`  // "6.3" for Win 8.1, "10.0" for Win 10/11
	ProductName     string    `json:"product_name"`
	RegisteredOwner string    `json:"registered_owner"`

	Timestamp time.Time `json:"timestamp"`
}

// GetWindowsPatchLevel retrieves comprehensive Windows patch level information
func GetWindowsPatchLevel() (*WindowsPatchLevel, error) {
	patchLevel := &WindowsPatchLevel{
		Timestamp: time.Now(),
	}

	// Get OS version from WMI
	if err := patchLevel.getOSInfoWMI(); err != nil {
		return nil, fmt.Errorf("get OS info: %w", err)
	}

	// Get detailed build info from registry
	if err := patchLevel.getRegistryInfo(); err != nil {
		// Non-fatal, continue
	}

	// Get installed updates
	if err := patchLevel.getInstalledUpdates(); err != nil {
		// Non-fatal, continue
	}

	// Get pending updates (requires Windows Update API - complex)
	// For now, set to 0
	patchLevel.PendingUpdates = 0

	return patchLevel, nil
}

// getOSInfoWMI retrieves OS information via WMI
func (p *WindowsPatchLevel) getOSInfoWMI() error {
	// Win32_OperatingSystem
	type Win32_OperatingSystem struct {
		Caption             string
		Version             string
		BuildNumber         string
		OSArchitecture      string
		InstallDate         *time.Time
		LastBootUpTime      *time.Time
		RegisteredUser      string
		OperatingSystemSKU  uint32
	}

	var os []Win32_OperatingSystem
	err := wmi.Query("SELECT * FROM Win32_OperatingSystem", &os)
	if err != nil {
		return err
	}

	if len(os) == 0 {
		return fmt.Errorf("no OS information found")
	}

	p.OSVersion = os[0].Caption
	p.CurrentVersion = os[0].Version
	p.BuildNumber = os[0].BuildNumber
	p.RegisteredOwner = os[0].RegisteredUser

	if os[0].InstallDate != nil {
		p.InstallDate = *os[0].InstallDate
	}

	if os[0].LastBootUpTime != nil {
		p.LastBootUpTime = *os[0].LastBootUpTime
	}

	// Parse OS edition from SKU
	p.OSEdition = getWindowsEditionFromSKU(os[0].OperatingSystemSKU)

	return nil
}

// getRegistryInfo retrieves detailed build information from Windows Registry
func (p *WindowsPatchLevel) getRegistryInfo() error {
	// Open registry key: HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion
	key, err := registry.OpenKey(registry.LOCAL_MACHINE,
		`SOFTWARE\Microsoft\Windows NT\CurrentVersion`,
		registry.QUERY_VALUE)
	if err != nil {
		return err
	}
	defer key.Close()

	// CurrentBuild: e.g., "22621"
	if value, _, err := key.GetStringValue("CurrentBuild"); err == nil {
		p.OSBuild = value
	}

	// UBR (Update Build Revision): e.g., 3085
	if value, _, err := key.GetIntegerValue("UBR"); err == nil {
		p.UBR = int(value)
	}

	// Combine OSBuild and UBR for full build number
	if p.UBR > 0 {
		p.BuildNumber = fmt.Sprintf("%s.%d", p.OSBuild, p.UBR)
	} else if p.OSBuild != "" {
		p.BuildNumber = p.OSBuild
	}

	// DisplayVersion: e.g., "22H2", "21H2"
	if value, _, err := key.GetStringValue("DisplayVersion"); err == nil {
		p.DisplayVersion = value
	}

	// ReleaseId: Older Windows versions use this
	if value, _, err := key.GetStringValue("ReleaseId"); err == nil {
		if p.DisplayVersion == "" {
			p.DisplayVersion = value
		}
		p.ReleaseID = value
	}

	// ProductName: Full product name
	if value, _, err := key.GetStringValue("ProductName"); err == nil {
		p.ProductName = value
		if p.OSVersion == "" {
			p.OSVersion = value
		}
	}

	// CurrentVersion: e.g., "6.3", "10.0"
	if value, _, err := key.GetStringValue("CurrentVersion"); err == nil {
		if p.CurrentVersion == "" {
			p.CurrentVersion = value
		}
	}

	return nil
}

// getInstalledUpdates retrieves installed Windows updates via WMI
func (p *WindowsPatchLevel) getInstalledUpdates() error {
	// Win32_QuickFixEngineering - Installed hotfixes
	type Win32_QuickFixEngineering struct {
		HotFixID      string
		Description   string
		InstalledOn   *time.Time
		InstalledBy   string
		Caption       string
	}

	var updates []Win32_QuickFixEngineering
	err := wmi.Query("SELECT * FROM Win32_QuickFixEngineering", &updates)
	if err != nil {
		return err
	}

	p.Updates = make([]WindowsUpdate, 0, len(updates))

	for _, update := range updates {
		wu := WindowsUpdate{
			HotFixID:    update.HotFixID,
			Description: update.Description,
			InstalledBy: update.InstalledBy,
			Caption:     update.Caption,
		}

		if update.InstalledOn != nil {
			wu.InstalledOn = *update.InstalledOn
		}

		p.Updates = append(p.Updates, wu)
	}

	p.UpdateCount = len(p.Updates)

	return nil
}

// GetInstalledUpdatesList returns a list of installed KB numbers
func (p *WindowsPatchLevel) GetInstalledUpdatesList() []string {
	kbList := make([]string, 0, len(p.Updates))
	for _, update := range p.Updates {
		if update.HotFixID != "" {
			kbList = append(kbList, update.HotFixID)
		}
	}
	return kbList
}

// HasUpdate checks if a specific KB is installed
func (p *WindowsPatchLevel) HasUpdate(kbNumber string) bool {
	// Normalize KB number (with or without "KB" prefix)
	kbNumber = strings.ToUpper(strings.TrimSpace(kbNumber))
	if !strings.HasPrefix(kbNumber, "KB") {
		kbNumber = "KB" + kbNumber
	}

	for _, update := range p.Updates {
		if strings.ToUpper(update.HotFixID) == kbNumber {
			return true
		}
	}
	return false
}

// GetLatestUpdate returns the most recently installed update
func (p *WindowsPatchLevel) GetLatestUpdate() *WindowsUpdate {
	if len(p.Updates) == 0 {
		return nil
	}

	var latest *WindowsUpdate
	var latestTime time.Time

	for i := range p.Updates {
		if p.Updates[i].InstalledOn.After(latestTime) {
			latestTime = p.Updates[i].InstalledOn
			latest = &p.Updates[i]
		}
	}

	return latest
}

// GetWindowsVersion returns a human-readable Windows version
func (p *WindowsPatchLevel) GetWindowsVersion() string {
	// Parse build number to determine Windows version
	if p.OSVersion != "" {
		return p.OSVersion
	}

	if p.OSBuild != "" {
		build := p.OSBuild
		switch {
		case build >= "22000":
			return "Windows 11"
		case build >= "10240":
			return "Windows 10"
		case build >= "9600":
			return "Windows 8.1"
		case build >= "9200":
			return "Windows 8"
		case build >= "7601":
			return "Windows 7"
		}
	}

	return "Unknown Windows Version"
}

// IsWindows11 returns true if running Windows 11
func (p *WindowsPatchLevel) IsWindows11() bool {
	return p.OSBuild >= "22000"
}

// IsWindows10 returns true if running Windows 10
func (p *WindowsPatchLevel) IsWindows10() bool {
	return p.OSBuild >= "10240" && p.OSBuild < "22000"
}

// IsWindows11OrLater returns true if Windows 11 or later
func (p *WindowsPatchLevel) IsWindows11OrLater() bool {
	return p.OSBuild >= "22000"
}

// getWindowsEditionFromSKU maps Windows SKU to edition name
func getWindowsEditionFromSKU(sku uint32) string {
	// Common SKU values
	skuMap := map[uint32]string{
		0:   "Undefined",
		1:   "Ultimate",
		2:   "Home Basic",
		3:   "Home Premium",
		4:   "Enterprise",
		5:   "Home Basic N",
		6:   "Business",
		7:   "Server Standard",
		8:   "Server Datacenter",
		9:   "Small Business Server",
		10:  "Enterprise Server",
		11:  "Starter",
		12:  "Datacenter Server Core",
		13:  "Standard Server Core",
		14:  "Enterprise Server Core",
		15:  "Enterprise Server IA64",
		16:  "Business N",
		17:  "Web Server",
		18:  "Cluster Server",
		19:  "Home Server",
		20:  "Storage Express Server",
		21:  "Storage Standard Server",
		22:  "Storage Workgroup Server",
		23:  "Storage Enterprise Server",
		24:  "Server For Small Business",
		25:  "Small Business Server Premium",
		27:  "Enterprise N",
		28:  "Ultimate N",
		29:  "Web Server Core",
		30:  "Essential Business Server Management Server",
		31:  "Essential Business Server Security Server",
		32:  "Essential Business Server Messaging Server",
		33:  "Server Foundation",
		34:  "Home Premium N",
		35:  "Enterprise Server N",
		36:  "Ultimate E",
		41:  "Security Appliance",
		42:  "Storage Server Express",
		43:  "Storage Server Standard",
		44:  "Storage Server Workgroup",
		45:  "Storage Server Enterprise",
		46:  "Starter N",
		48:  "Professional",
		49:  "Professional N",
		50:  "Small Business Server 2011 Essentials",
		71:  "Enterprise N Evaluation",
		72:  "Embedded",
		76:  "Essential Server Solution Additional",
		77:  "Essential Server Solution Additional SVC",
		79:  "Enterprise Embedded",
		80:  "Embedded Industry",
		81:  "Embedded Industry E",
		82:  "Embedded Industry A",
		101: "Home",
		103: "Professional for Workstations",
		104: "Professional for Workstations N",
		121: "Education",
		122: "Education N",
		143: "IoT Core",
		161: "Pro for Workstations",
		162: "Pro N for Workstations",
		164: "Pro Education",
		165: "Pro Education N",
	}

	if edition, ok := skuMap[sku]; ok {
		return edition
	}

	return fmt.Sprintf("Unknown SKU (%d)", sku)
}

// FormatBuildInfo returns formatted build information
func (p *WindowsPatchLevel) FormatBuildInfo() string {
	return fmt.Sprintf("%s (Build %s)", p.OSVersion, p.BuildNumber)
}

// GetUptime returns system uptime duration
func (p *WindowsPatchLevel) GetUptime() time.Duration {
	if p.LastBootUpTime.IsZero() {
		return 0
	}
	return time.Since(p.LastBootUpTime)
}

// FormatUptime returns human-readable uptime
func (p *WindowsPatchLevel) FormatUptime() string {
	uptime := p.GetUptime()
	if uptime == 0 {
		return "Unknown"
	}

	days := int(uptime.Hours() / 24)
	hours := int(uptime.Hours()) % 24
	minutes := int(uptime.Minutes()) % 60

	if days > 0 {
		return fmt.Sprintf("%d days, %d hours, %d minutes", days, hours, minutes)
	}
	return fmt.Sprintf("%d hours, %d minutes", hours, minutes)
}
