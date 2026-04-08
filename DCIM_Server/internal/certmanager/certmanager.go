package certmanager

import (
	"crypto/x509"
	"encoding/pem"
	"fmt"
	"io/ioutil"
	"log"
	"sync"
	"time"

	"github.com/faberlabs/dcim-server/internal/config"
)

// CertificateInfo represents certificate details
type CertificateInfo struct {
	Type         string    // CA, Server, Client
	Path         string    // File path
	Subject      string    // Certificate subject
	Issuer       string    // Certificate issuer
	SerialNumber string    // Serial number
	NotBefore    time.Time // Valid from
	NotAfter     time.Time // Valid until
	DaysUntilExpiry int    // Days until expiry
	IsExpired    bool      // Whether certificate has expired
	IsExpiringSoon bool    // Expiring within 30 days
}

// Manager handles certificate monitoring and renewal
type Manager struct {
	config      *config.TLSConfig
	certificates map[string]*CertificateInfo
	mu          sync.RWMutex
	logger      *log.Logger
	stopChan    chan struct{}
}

// NewManager creates a new certificate manager
func NewManager(cfg *config.TLSConfig) (*Manager, error) {
	manager := &Manager{
		config:       cfg,
		certificates: make(map[string]*CertificateInfo),
		logger:       log.New(log.Writer(), "[CERT-MANAGER] ", log.LstdFlags),
		stopChan:     make(chan struct{}),
	}

	// Load and validate all certificates
	if err := manager.LoadCertificates(); err != nil {
		return nil, fmt.Errorf("failed to load certificates: %w", err)
	}

	return manager, nil
}

// LoadCertificates loads and validates all certificates
func (m *Manager) LoadCertificates() error {
	m.mu.Lock()
	defer m.mu.Unlock()

	// Load CA certificate
	if m.config.CACertPath != "" {
		certInfo, err := m.loadCertificateInfo("CA", m.config.CACertPath)
		if err != nil {
			return fmt.Errorf("failed to load CA certificate: %w", err)
		}
		m.certificates["ca"] = certInfo
	}

	// Load server certificate
	if m.config.ServerCertPath != "" {
		certInfo, err := m.loadCertificateInfo("Server", m.config.ServerCertPath)
		if err != nil {
			return fmt.Errorf("failed to load server certificate: %w", err)
		}
		m.certificates["server"] = certInfo
	}

	return nil
}

// loadCertificateInfo loads and parses certificate information
func (m *Manager) loadCertificateInfo(certType, certPath string) (*CertificateInfo, error) {
	// Read certificate file
	certPEM, err := ioutil.ReadFile(certPath)
	if err != nil {
		return nil, fmt.Errorf("failed to read certificate: %w", err)
	}

	// Parse certificate
	cert, err := parseCertificate(certPEM)
	if err != nil {
		return nil, fmt.Errorf("failed to parse certificate: %w", err)
	}

	// Calculate days until expiry
	now := time.Now()
	daysUntilExpiry := int(time.Until(cert.NotAfter).Hours() / 24)

	certInfo := &CertificateInfo{
		Type:            certType,
		Path:            certPath,
		Subject:         cert.Subject.CommonName,
		Issuer:          cert.Issuer.CommonName,
		SerialNumber:    cert.SerialNumber.String(),
		NotBefore:       cert.NotBefore,
		NotAfter:        cert.NotAfter,
		DaysUntilExpiry: daysUntilExpiry,
		IsExpired:       now.After(cert.NotAfter),
		IsExpiringSoon:  daysUntilExpiry <= 30 && daysUntilExpiry > 0,
	}

	return certInfo, nil
}

// parseCertificate parses a PEM-encoded certificate
func parseCertificate(certPEM []byte) (*x509.Certificate, error) {
	block, _ := pem.Decode(certPEM)
	if block == nil {
		return nil, fmt.Errorf("failed to decode PEM block")
	}

	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("failed to parse certificate: %w", err)
	}

	return cert, nil
}

// GetCertificateInfo returns information about a specific certificate
func (m *Manager) GetCertificateInfo(certType string) (*CertificateInfo, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	certInfo, exists := m.certificates[certType]
	if !exists {
		return nil, fmt.Errorf("certificate not found: %s", certType)
	}

	return certInfo, nil
}

// GetAllCertificates returns information about all certificates
func (m *Manager) GetAllCertificates() map[string]*CertificateInfo {
	m.mu.RLock()
	defer m.mu.RUnlock()

	// Create a copy to avoid race conditions
	certs := make(map[string]*CertificateInfo)
	for k, v := range m.certificates {
		certs[k] = v
	}

	return certs
}

// CheckExpiry checks all certificates for expiry
func (m *Manager) CheckExpiry() []string {
	m.mu.RLock()
	defer m.mu.RUnlock()

	var warnings []string

	for certType, certInfo := range m.certificates {
		if certInfo.IsExpired {
			warnings = append(warnings, fmt.Sprintf(
				"CRITICAL: %s certificate EXPIRED on %s (expired %d days ago)",
				certType,
				certInfo.NotAfter.Format("2006-01-02"),
				-certInfo.DaysUntilExpiry,
			))
		} else if certInfo.IsExpiringSoon {
			warnings = append(warnings, fmt.Sprintf(
				"WARNING: %s certificate expires in %d days (on %s)",
				certType,
				certInfo.DaysUntilExpiry,
				certInfo.NotAfter.Format("2006-01-02"),
			))
		}
	}

	return warnings
}

// StartMonitoring starts periodic certificate monitoring
func (m *Manager) StartMonitoring(checkInterval time.Duration) {
	go func() {
		ticker := time.NewTicker(checkInterval)
		defer ticker.Stop()

		// Initial check
		m.checkAndLog()

		for {
			select {
			case <-ticker.C:
				m.checkAndLog()
			case <-m.stopChan:
				return
			}
		}
	}()
}

// checkAndLog performs certificate check and logs warnings
func (m *Manager) checkAndLog() {
	// Reload certificate info to get latest expiry data
	if err := m.LoadCertificates(); err != nil {
		m.logger.Printf("Error reloading certificates: %v", err)
		return
	}

	warnings := m.CheckExpiry()
	for _, warning := range warnings {
		m.logger.Println(warning)
	}
}

// StopMonitoring stops certificate monitoring
func (m *Manager) StopMonitoring() {
	close(m.stopChan)
}

// LogCertificateInfo logs detailed certificate information
func (m *Manager) LogCertificateInfo() {
	m.mu.RLock()
	defer m.mu.RUnlock()

	m.logger.Println("================================================================================")
	m.logger.Println("Certificate Information")
	m.logger.Println("================================================================================")

	for certType, certInfo := range m.certificates {
		m.logger.Printf("\n%s Certificate:", certType)
		m.logger.Printf("  Subject: %s", certInfo.Subject)
		m.logger.Printf("  Issuer: %s", certInfo.Issuer)
		m.logger.Printf("  Serial: %s", certInfo.SerialNumber)
		m.logger.Printf("  Valid From: %s", certInfo.NotBefore.Format("2006-01-02 15:04:05"))
		m.logger.Printf("  Valid Until: %s", certInfo.NotAfter.Format("2006-01-02 15:04:05"))
		m.logger.Printf("  Days Until Expiry: %d days", certInfo.DaysUntilExpiry)

		if certInfo.IsExpired {
			m.logger.Printf("  Status: EXPIRED ⚠️")
		} else if certInfo.IsExpiringSoon {
			m.logger.Printf("  Status: EXPIRING SOON (< 30 days) ⚠️")
		} else {
			m.logger.Printf("  Status: Valid ✓")
		}
	}

	m.logger.Println("\n================================================================================")
}

// RenewalInfo provides information for certificate renewal
type RenewalInfo struct {
	CertType        string
	CurrentExpiry   time.Time
	DaysRemaining   int
	ShouldRenew     bool
	RenewalScript   string
	BackupLocation  string
}

// GetRenewalInfo provides renewal information for certificates
func (m *Manager) GetRenewalInfo() []*RenewalInfo {
	m.mu.RLock()
	defer m.mu.RUnlock()

	var renewalInfos []*RenewalInfo

	for certType, certInfo := range m.certificates {
		shouldRenew := certInfo.IsExpiringSoon || certInfo.IsExpired

		info := &RenewalInfo{
			CertType:       certType,
			CurrentExpiry:  certInfo.NotAfter,
			DaysRemaining:  certInfo.DaysUntilExpiry,
			ShouldRenew:    shouldRenew,
			RenewalScript:  getRenewalScript(certType),
			BackupLocation: getBackupLocation(certType, certInfo.Path),
		}

		renewalInfos = append(renewalInfos, info)
	}

	return renewalInfos
}

// getRenewalScript returns the appropriate renewal script for a certificate type
func getRenewalScript(certType string) string {
	switch certType {
	case "ca":
		return "scripts/renew-ca-cert.ps1"
	case "server":
		return "scripts/renew-server-cert.ps1"
	default:
		return "scripts/generate-certs.ps1"
	}
}

// getBackupLocation returns backup location for a certificate
func getBackupLocation(certType, originalPath string) string {
	timestamp := time.Now().Format("20060102_150405")
	return fmt.Sprintf("%s.backup_%s", originalPath, timestamp)
}

// BackupCertificate creates a backup of a certificate
func (m *Manager) BackupCertificate(certType string) error {
	m.mu.RLock()
	certInfo, exists := m.certificates[certType]
	m.mu.RUnlock()

	if !exists {
		return fmt.Errorf("certificate not found: %s", certType)
	}

	backupPath := getBackupLocation(certType, certInfo.Path)

	// Read original certificate
	data, err := ioutil.ReadFile(certInfo.Path)
	if err != nil {
		return fmt.Errorf("failed to read certificate: %w", err)
	}

	// Write backup
	if err := ioutil.WriteFile(backupPath, data, 0644); err != nil {
		return fmt.Errorf("failed to write backup: %w", err)
	}

	m.logger.Printf("Backed up %s certificate to: %s", certType, backupPath)
	return nil
}

// ValidateCertificate validates a certificate file
func ValidateCertificate(certPath string) error {
	certPEM, err := ioutil.ReadFile(certPath)
	if err != nil {
		return fmt.Errorf("failed to read certificate: %w", err)
	}

	_, err = parseCertificate(certPEM)
	if err != nil {
		return fmt.Errorf("invalid certificate: %w", err)
	}

	return nil
}

