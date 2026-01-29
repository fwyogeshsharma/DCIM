package sender

import (
	"bytes"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"time"

	"github.com/faber/network-monitor-agent/internal/config"
	"github.com/faber/network-monitor-agent/internal/logger"
	"github.com/faber/network-monitor-agent/internal/storage"
)

type Sender struct {
	config  config.ServerConfig
	agentID string
	client  *http.Client
	logger  *logger.Logger
	storage *storage.Storage
}

type MetricsPayload struct {
	AgentID   string            `json:"agent_id"`
	Timestamp time.Time         `json:"timestamp"`
	Metrics   []*storage.Metric `json:"metrics"`
}

type AlertsPayload struct {
	AgentID   string           `json:"agent_id"`
	Timestamp time.Time        `json:"timestamp"`
	Alerts    []*storage.Alert `json:"alerts"`
}

type SNMPMetricsPayload struct {
	AgentID     string                 `json:"agent_id"`
	Timestamp   time.Time              `json:"timestamp"`
	SNMPMetrics []*storage.SNMPMetric  `json:"snmp_metrics"`
}

type Response struct {
	Success bool   `json:"success"`
	Message string `json:"message"`
	Error   string `json:"error,omitempty"`
}

func New(cfg config.ServerConfig, agentID string, store *storage.Storage, log *logger.Logger) *Sender {
	// Create HTTP client with optional TLS configuration
	client, err := createHTTPClient(cfg, log)
	if err != nil {
		// If TLS is enabled but fails to configure, this is a FATAL error
		// Don't fall back to insecure connection
		if cfg.TLS.Enabled {
			log.Errorf("FATAL: Failed to create mTLS HTTP client: %v", err)
			log.Errorf("Cannot start agent with invalid TLS configuration")
			log.Errorf("Please check certificate paths in config.yaml")
			os.Exit(1)
		}
		log.Warnf("TLS not configured, using default HTTP client")
		client = &http.Client{
			Timeout: cfg.Timeout,
		}
	}

	return &Sender{
		config:  cfg,
		agentID: agentID,
		client:  client,
		logger:  log,
		storage: store,
	}
}

// createHTTPClient creates an HTTP client with optional mTLS configuration
func createHTTPClient(cfg config.ServerConfig, log *logger.Logger) (*http.Client, error) {
	// If TLS is not enabled, return basic client
	if !cfg.TLS.Enabled {
		log.Infof("TLS is disabled - using standard HTTP client")
		return &http.Client{
			Timeout: cfg.Timeout,
		}, nil
	}

	log.Infof("Configuring mTLS (Mutual TLS) authentication...")

	// Create TLS configuration
	tlsConfig := &tls.Config{}

	// Set minimum TLS version
	switch cfg.TLS.MinTLSVersion {
	case "1.3":
		tlsConfig.MinVersion = tls.VersionTLS13
		log.Infof("Minimum TLS version: 1.3")
	case "1.2":
		tlsConfig.MinVersion = tls.VersionTLS12
		log.Infof("Minimum TLS version: 1.2")
	default:
		tlsConfig.MinVersion = tls.VersionTLS12 // Default to TLS 1.2
		log.Infof("Minimum TLS version: 1.2 (default)")
	}

	// Load client certificate and key (for client authentication)
	if cfg.TLS.ClientCertPath != "" && cfg.TLS.ClientKeyPath != "" {
		cert, err := tls.LoadX509KeyPair(cfg.TLS.ClientCertPath, cfg.TLS.ClientKeyPath)
		if err != nil {
			return nil, fmt.Errorf("failed to load client certificate: %w", err)
		}
		tlsConfig.Certificates = []tls.Certificate{cert}
		log.Infof("✓ Loaded client certificate from: %s", cfg.TLS.ClientCertPath)
		log.Infof("✓ Loaded client key from: %s", cfg.TLS.ClientKeyPath)

		// Parse and log certificate details for debugging
		if len(cert.Certificate) > 0 {
			clientCert, err := x509.ParseCertificate(cert.Certificate[0])
			if err == nil {
				log.Infof("  Client cert Subject: %s", clientCert.Subject.CommonName)
				log.Infof("  Client cert Issuer: %s", clientCert.Issuer.CommonName)
				log.Infof("  Client cert Valid: %s to %s",
					clientCert.NotBefore.Format("2006-01-02"),
					clientCert.NotAfter.Format("2006-01-02"))
			}
		}
	} else {
		log.Warnf("No client certificate configured - server must not require client authentication")
	}

	// Load CA certificate (for server verification)
	if cfg.TLS.CACertPath != "" {
		caCert, err := os.ReadFile(cfg.TLS.CACertPath)
		if err != nil {
			return nil, fmt.Errorf("failed to read CA certificate: %w", err)
		}

		caCertPool := x509.NewCertPool()
		if !caCertPool.AppendCertsFromPEM(caCert) {
			return nil, fmt.Errorf("failed to parse CA certificate")
		}

		tlsConfig.RootCAs = caCertPool
		log.Infof("✓ Loaded CA certificate from: %s", cfg.TLS.CACertPath)
	} else {
		log.Infof("Using system CA certificates for server verification")
	}

	// Handle insecure skip verify (NOT recommended for production)
	if cfg.TLS.InsecureSkipVerify {
		tlsConfig.InsecureSkipVerify = true
		log.Warnf("⚠️  WARNING: SSL certificate verification is DISABLED")
		log.Warnf("⚠️  This is INSECURE and should only be used for testing")
	}

	// Extract hostname from server URL for ServerName (important for certificate verification)
	if parsedURL, err := url.Parse(cfg.URL); err == nil && parsedURL.Host != "" {
		hostname := parsedURL.Hostname()
		if hostname != "" {
			tlsConfig.ServerName = hostname
			log.Infof("  TLS ServerName: %s", hostname)
		}
	}

	// Create HTTP transport with TLS configuration
	transport := &http.Transport{
		TLSClientConfig: tlsConfig,
	}

	log.Infof("✓ mTLS client configured successfully")

	return &http.Client{
		Timeout:   cfg.Timeout,
		Transport: transport,
	}, nil
}

// SendAlerts sends alerts to the server immediately
func (s *Sender) SendAlerts(alerts []*storage.Alert) error {
	if len(alerts) == 0 {
		return nil
	}

	payload := AlertsPayload{
		AgentID:   s.agentID,
		Timestamp: time.Now(),
		Alerts:    alerts,
	}

	data, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("marshal alerts payload: %w", err)
	}

	url := s.config.URL + "/alerts"
	var lastErr error

	for attempt := 0; attempt <= s.config.RetryAttempts; attempt++ {
		if attempt > 0 {
			s.logger.Warnf("Retrying alert send (attempt %d/%d)", attempt, s.config.RetryAttempts)
			time.Sleep(s.config.RetryDelay)
		}

		resp, err := s.sendRequest("POST", url, data)
		if err == nil {
			if resp.Success {
				// Mark alerts as sent
				var ids []int64
				for _, alert := range alerts {
					ids = append(ids, alert.ID)
				}
				s.storage.MarkAlertsSent(ids)

				// Log successful transmission
				for _, alert := range alerts {
					s.storage.LogTransmission("alert", alert.ID, "success", "")
					s.logger.Infof("Alert sent: %s - %s", alert.Severity, alert.Message)
				}
				return nil
			}
			lastErr = fmt.Errorf("server returned error: %s", resp.Error)
		} else {
			lastErr = err
		}
	}

	// Log failed transmission
	for _, alert := range alerts {
		s.storage.IncrementAlertRetry(alert.ID)
		s.storage.LogTransmission("alert", alert.ID, "failed", lastErr.Error())
	}

	return fmt.Errorf("failed to send alerts after %d attempts: %w", s.config.RetryAttempts, lastErr)
}

// SendMetrics sends batched metrics to the server
func (s *Sender) SendMetrics(metrics []*storage.Metric) error {
	if len(metrics) == 0 {
		return nil
	}

	payload := MetricsPayload{
		AgentID:   s.agentID,
		Timestamp: time.Now(),
		Metrics:   metrics,
	}

	data, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("marshal metrics payload: %w", err)
	}

	url := s.config.URL + "/metrics"
	var lastErr error

	for attempt := 0; attempt <= s.config.RetryAttempts; attempt++ {
		if attempt > 0 {
			time.Sleep(s.config.RetryDelay)
		}

		resp, err := s.sendRequest("POST", url, data)
		if err == nil {
			if resp.Success {
				// Mark metrics as sent
				var ids []int64
				for _, metric := range metrics {
					ids = append(ids, metric.ID)
				}
				s.storage.MarkMetricsSent(ids)

				// Log successful transmission
				for _, metric := range metrics {
					s.storage.LogTransmission("metric", metric.ID, "success", "")
				}
				s.logger.Infof("Sent %d metrics successfully", len(metrics))
				return nil
			}
			lastErr = fmt.Errorf("server returned error: %s", resp.Error)
		} else {
			lastErr = err
		}
	}

	// Log failed transmission
	for _, metric := range metrics {
		s.storage.LogTransmission("metric", metric.ID, "failed", lastErr.Error())
	}

	return fmt.Errorf("failed to send metrics after %d attempts: %w", s.config.RetryAttempts, lastErr)
}

// SendSNMPMetrics sends batched SNMP metrics to the server
func (s *Sender) SendSNMPMetrics(metrics []*storage.SNMPMetric) error {
	if len(metrics) == 0 {
		return nil
	}

	payload := SNMPMetricsPayload{
		AgentID:     s.agentID,
		Timestamp:   time.Now(),
		SNMPMetrics: metrics,
	}

	data, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("marshal SNMP metrics payload: %w", err)
	}

	url := s.config.URL + "/snmp-metrics"
	var lastErr error

	for attempt := 0; attempt <= s.config.RetryAttempts; attempt++ {
		if attempt > 0 {
			time.Sleep(s.config.RetryDelay)
		}

		resp, err := s.sendRequest("POST", url, data)
		if err == nil {
			if resp.Success {
				// Mark SNMP metrics as sent
				var ids []int64
				for _, metric := range metrics {
					ids = append(ids, metric.ID)
				}
				s.storage.MarkSNMPMetricsSent(ids)

				// Log successful transmission
				for _, metric := range metrics {
					s.storage.LogTransmission("snmp_metric", metric.ID, "success", "")
				}
				s.logger.Infof("Sent %d SNMP metrics successfully", len(metrics))
				return nil
			}
			lastErr = fmt.Errorf("server returned error: %s", resp.Error)
		} else {
			lastErr = err
		}
	}

	// Log failed transmission
	for _, metric := range metrics {
		s.storage.LogTransmission("snmp_metric", metric.ID, "failed", lastErr.Error())
	}

	return fmt.Errorf("failed to send SNMP metrics after %d attempts: %w", s.config.RetryAttempts, lastErr)
}

// sendRequest sends an HTTP request and parses the response
func (s *Sender) sendRequest(method, url string, data []byte) (*Response, error) {
	req, err := http.NewRequest(method, url, bytes.NewReader(data))
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "NetworkMonitorAgent/1.0")
	req.Header.Set("X-Agent-ID", s.agentID)

	httpResp, err := s.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("send request: %w", err)
	}
	defer httpResp.Body.Close()

	body, err := io.ReadAll(httpResp.Body)
	if err != nil {
		return nil, fmt.Errorf("read response: %w", err)
	}

	if httpResp.StatusCode < 200 || httpResp.StatusCode >= 300 {
		return nil, fmt.Errorf("HTTP %d: %s", httpResp.StatusCode, string(body))
	}

	var resp Response
	if err := json.Unmarshal(body, &resp); err != nil {
		return nil, fmt.Errorf("parse response: %w", err)
	}

	return &resp, nil
}

// ProcessQueue processes unsent alerts and metrics from the database
func (s *Sender) ProcessQueue(batchSize int) error {
	// PRIORITY 1: Send unsent alerts first (they're time-sensitive)
	alerts, err := s.storage.GetUnsentAlerts(batchSize)
	if err != nil {
		return fmt.Errorf("get unsent alerts: %w", err)
	}

	if len(alerts) > 0 {
		s.logger.Infof("Processing %d unsent alerts", len(alerts))
		if err := s.SendAlerts(alerts); err != nil {
			s.logger.Errorf("Failed to send alerts: %v", err)
			// Continue to try metrics anyway
		}
	}

	// PRIORITY 2: Send normal metrics in batches
	metrics, err := s.storage.GetUnsentMetrics(batchSize)
	if err != nil {
		return fmt.Errorf("get unsent metrics: %w", err)
	}

	if len(metrics) > 0 {
		s.logger.Infof("Processing %d unsent metrics", len(metrics))
		if err := s.SendMetrics(metrics); err != nil {
			s.logger.Errorf("Failed to send metrics: %v", err)
			return err
		}
	}

	// PRIORITY 3: Send SNMP metrics
	snmpMetrics, err := s.storage.GetUnsentSNMPMetrics(batchSize)
	if err != nil {
		return fmt.Errorf("get unsent SNMP metrics: %w", err)
	}

	if len(snmpMetrics) > 0 {
		s.logger.Infof("Processing %d unsent SNMP metrics", len(snmpMetrics))
		if err := s.SendSNMPMetrics(snmpMetrics); err != nil {
			s.logger.Errorf("Failed to send SNMP metrics: %v", err)
			return err
		}
	}

	return nil
}
