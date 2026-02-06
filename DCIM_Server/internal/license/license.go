package license

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/faberlabs/dcim-server/internal/config"
	"github.com/faberlabs/dcim-server/internal/models"
)

// Manager handles license validation and enforcement
type Manager struct {
	config  *config.LicenseConfig
	license *models.License
}

// LicenseFile represents the license file structure
type LicenseFile struct {
	LicenseKey     string    `json:"license_key"`
	CompanyName    string    `json:"company_name"`
	Email          string    `json:"email"`
	MaxAgents      int       `json:"max_agents"`
	MaxSNMPDevices int       `json:"max_snmp_devices"`
	Features       []string  `json:"features"`
	IssuedAt       time.Time `json:"issued_at"`
	ExpiresAt      time.Time `json:"expires_at"`
	Signature      string    `json:"signature"`
}

// NewManager creates a new license manager
func NewManager(cfg *config.LicenseConfig) (*Manager, error) {
	manager := &Manager{
		config: cfg,
	}

	// Load license based on mode
	if cfg.Mode == "file" {
		if err := manager.LoadFromFile(cfg.FilePath); err != nil {
			if cfg.Enforce {
				return nil, fmt.Errorf("failed to load license: %w", err)
			}
			// Use default license if enforcement is disabled
			manager.license = manager.getDefaultLicense()
		}
	} else if cfg.Mode == "disabled" {
		// Use default license when disabled
		manager.license = manager.getDefaultLicense()
	}

	// Validate license
	if err := manager.Validate(); err != nil {
		if cfg.Enforce {
			return nil, fmt.Errorf("license validation failed: %w", err)
		}
		// Use default license if enforcement is disabled
		manager.license = manager.getDefaultLicense()
	}

	return manager, nil
}

// LoadFromFile loads license from a JSON file
func (m *Manager) LoadFromFile(path string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("failed to read license file: %w", err)
	}

	var licFile LicenseFile
	if err := json.Unmarshal(data, &licFile); err != nil {
		return fmt.Errorf("failed to parse license file: %w", err)
	}

	// Verify signature
	if !m.verifySignature(&licFile) {
		return fmt.Errorf("invalid license signature")
	}

	// Convert to license model
	m.license = &models.License{
		LicenseKey:     licFile.LicenseKey,
		CompanyName:    licFile.CompanyName,
		Email:          licFile.Email,
		MaxAgents:      licFile.MaxAgents,
		MaxSNMPDevices: licFile.MaxSNMPDevices,
		Features:       models.JSONArray(licFile.Features),
		IssuedAt:       licFile.IssuedAt,
		ExpiresAt:      licFile.ExpiresAt,
		Active:         true,
	}

	return nil
}

// verifySignature verifies the license signature
func (m *Manager) verifySignature(licFile *LicenseFile) bool {
	// Create signature data
	data := fmt.Sprintf("%s|%s|%s|%d|%d|%s|%s",
		licFile.LicenseKey,
		licFile.CompanyName,
		licFile.Email,
		licFile.MaxAgents,
		licFile.MaxSNMPDevices,
		licFile.IssuedAt.Format(time.RFC3339),
		licFile.ExpiresAt.Format(time.RFC3339),
	)

	// Calculate hash
	hash := sha256.Sum256([]byte(data))
	calculatedSignature := hex.EncodeToString(hash[:])

	// For demo purposes, we accept any signature
	// In production, you would verify against a private key
	return licFile.Signature != "" && len(licFile.Signature) == len(calculatedSignature)
}

// getDefaultLicense returns a default license based on config
func (m *Manager) getDefaultLicense() *models.License {
	now := time.Now()
	return &models.License{
		LicenseKey:     "DEFAULT-LICENSE",
		CompanyName:    "Default",
		Email:          "default@localhost",
		MaxAgents:      m.config.Default.MaxAgents,
		MaxSNMPDevices: m.config.Default.MaxSNMPDevices,
		Features:       models.JSONArray(m.config.Default.Features),
		IssuedAt:       now,
		ExpiresAt:      now.AddDate(100, 0, 0), // 100 years
		Active:         true,
	}
}

// Validate validates the current license
func (m *Manager) Validate() error {
	if m.license == nil {
		return fmt.Errorf("no license loaded")
	}

	now := time.Now()

	// Check if expired
	if now.After(m.license.ExpiresAt) {
		// Check grace period
		gracePeriod := time.Duration(m.config.GracePeriodDays) * 24 * time.Hour
		if now.After(m.license.ExpiresAt.Add(gracePeriod)) {
			return fmt.Errorf("license expired on %s (grace period ended)", m.license.ExpiresAt.Format("2006-01-02"))
		}
		// Still in grace period
		return nil
	}

	// Check if not yet valid
	if now.Before(m.license.IssuedAt) {
		return fmt.Errorf("license not yet valid (valid from %s)", m.license.IssuedAt.Format("2006-01-02"))
	}

	return nil
}

// IsExpired checks if the license is expired
func (m *Manager) IsExpired() bool {
	if m.license == nil {
		return true
	}
	return time.Now().After(m.license.ExpiresAt)
}

// IsInGracePeriod checks if the license is in grace period
func (m *Manager) IsInGracePeriod() bool {
	if m.license == nil || !m.IsExpired() {
		return false
	}
	gracePeriod := time.Duration(m.config.GracePeriodDays) * 24 * time.Hour
	return time.Now().Before(m.license.ExpiresAt.Add(gracePeriod))
}

// CanAddAgent checks if a new agent can be added
func (m *Manager) CanAddAgent(currentAgentCount int) error {
	if !m.config.Enforce {
		return nil
	}

	if err := m.Validate(); err != nil {
		return err
	}

	if currentAgentCount >= m.license.MaxAgents {
		return fmt.Errorf("agent limit reached (%d/%d)", currentAgentCount, m.license.MaxAgents)
	}

	return nil
}

// CanAddSNMPDevice checks if a new SNMP device can be added
func (m *Manager) CanAddSNMPDevice(currentDeviceCount int) error {
	if !m.config.Enforce {
		return nil
	}

	if err := m.Validate(); err != nil {
		return err
	}

	if currentDeviceCount >= m.license.MaxSNMPDevices {
		return fmt.Errorf("SNMP device limit reached (%d/%d)", currentDeviceCount, m.license.MaxSNMPDevices)
	}

	return nil
}

// HasFeature checks if a feature is enabled in the license
func (m *Manager) HasFeature(feature string) bool {
	if !m.config.Enforce {
		return true
	}

	if m.license == nil {
		return false
	}

	for _, f := range m.license.Features {
		if f == feature {
			return true
		}
	}

	return false
}

// GetLicense returns the current license information
func (m *Manager) GetLicense() *models.License {
	return m.license
}

// GetMaxAgents returns the maximum number of agents allowed
func (m *Manager) GetMaxAgents() int {
	if m.license == nil {
		return m.config.Default.MaxAgents
	}
	return m.license.MaxAgents
}

// GetMaxSNMPDevices returns the maximum number of SNMP devices allowed
func (m *Manager) GetMaxSNMPDevices() int {
	if m.license == nil {
		return m.config.Default.MaxSNMPDevices
	}
	return m.license.MaxSNMPDevices
}

// GetExpiryDays returns the number of days until license expiry
func (m *Manager) GetExpiryDays() int {
	if m.license == nil {
		return 0
	}
	days := int(time.Until(m.license.ExpiresAt).Hours() / 24)
	if days < 0 {
		return 0
	}
	return days
}

// GenerateLicenseFile generates a sample license file
func GenerateLicenseFile(outputPath string, companyName, email string, maxAgents, maxSNMPDevices int, validityYears int) error {
	now := time.Now()
	expiresAt := now.AddDate(validityYears, 0, 0)

	// Generate license key
	licenseKey := fmt.Sprintf("DCIM-%s-%d", companyName, now.Unix())

	// Create signature data
	data := fmt.Sprintf("%s|%s|%s|%d|%d|%s|%s",
		licenseKey,
		companyName,
		email,
		maxAgents,
		maxSNMPDevices,
		now.Format(time.RFC3339),
		expiresAt.Format(time.RFC3339),
	)

	// Calculate signature
	hash := sha256.Sum256([]byte(data))
	signature := hex.EncodeToString(hash[:])

	licFile := LicenseFile{
		LicenseKey:     licenseKey,
		CompanyName:    companyName,
		Email:          email,
		MaxAgents:      maxAgents,
		MaxSNMPDevices: maxSNMPDevices,
		Features: []string{
			"basic_monitoring",
			"alerting",
			"snmp_monitoring",
			"advanced_analytics",
			"dashboard",
		},
		IssuedAt:  now,
		ExpiresAt: expiresAt,
		Signature: signature,
	}

	// Write to file
	data2, err := json.MarshalIndent(licFile, "", "  ")
	if err != nil {
		return err
	}

	return os.WriteFile(outputPath, data2, 0644)
}
