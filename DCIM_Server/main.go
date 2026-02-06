package main

import (
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/faberlabs/dcim-server/internal/config"
	"github.com/faberlabs/dcim-server/internal/database"
	"github.com/faberlabs/dcim-server/internal/license"
	"github.com/faberlabs/dcim-server/internal/server"
)

const (
	Version = "1.0.0"
	Banner  = `
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   ██████╗  ██████╗██╗███╗   ███╗    ███████╗███████╗██████╗ ║
║   ██╔══██╗██╔════╝██║████╗ ████║    ██╔════╝██╔════╝██╔══██╗║
║   ██║  ██║██║     ██║██╔████╔██║    ███████╗█████╗  ██████╔╝║
║   ██║  ██║██║     ██║██║╚██╔╝██║    ╚════██║██╔══╝  ██╔══██╗║
║   ██████╔╝╚██████╗██║██║ ╚═╝ ██║    ███████║███████╗██║  ██║║
║   ╚═════╝  ╚═════╝╚═╝╚═╝     ╚═╝    ╚══════╝╚══════╝╚═╝  ╚═╝║
║                                                              ║
║         Data Center Infrastructure Monitoring Server        ║
║                       Version %s                         ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
`
)

func main() {
	// Command-line flags
	configPath := flag.String("config", "config.yaml", "Path to configuration file")
	generateLicense := flag.Bool("generate-license", false, "Generate a sample license file")
	licenseOutput := flag.String("license-output", "license.json", "Output path for generated license")
	licenseCompany := flag.String("license-company", "Example Company", "Company name for license")
	licenseEmail := flag.String("license-email", "admin@example.com", "Email for license")
	licenseAgents := flag.Int("license-agents", 100, "Maximum agents for license")
	licenseSNMP := flag.Int("license-snmp", 500, "Maximum SNMP devices for license")
	licenseYears := flag.Int("license-years", 1, "License validity in years")
	showVersion := flag.Bool("version", false, "Show version information")

	flag.Parse()

	// Print banner
	fmt.Printf(Banner, Version)
	fmt.Println()

	// Handle version flag
	if *showVersion {
		fmt.Printf("DCIM Server version %s\n", Version)
		fmt.Println("Copyright (c) 2024 Faber Labs")
		return
	}

	// Handle license generation
	if *generateLicense {
		fmt.Printf("Generating license file...\n")
		fmt.Printf("  Company: %s\n", *licenseCompany)
		fmt.Printf("  Email: %s\n", *licenseEmail)
		fmt.Printf("  Max Agents: %d\n", *licenseAgents)
		fmt.Printf("  Max SNMP Devices: %d\n", *licenseSNMP)
		fmt.Printf("  Validity: %d years\n", *licenseYears)
		fmt.Println()

		if err := license.GenerateLicenseFile(
			*licenseOutput,
			*licenseCompany,
			*licenseEmail,
			*licenseAgents,
			*licenseSNMP,
			*licenseYears,
		); err != nil {
			log.Fatalf("Failed to generate license: %v", err)
		}

		fmt.Printf("License file generated successfully: %s\n", *licenseOutput)
		return
	}

	// Load configuration
	log.Printf("Loading configuration from: %s", *configPath)
	cfg, err := config.Load(*configPath)
	if err != nil {
		log.Fatalf("Failed to load configuration: %v", err)
	}
	log.Printf("Configuration loaded successfully")

	// Initialize database
	log.Printf("Initializing database (%s)...", cfg.Database.Type)
	db, err := database.New(cfg)
	if err != nil {
		log.Fatalf("Failed to initialize database: %v", err)
	}
	defer db.Close()
	log.Printf("Database initialized successfully")

	// Initialize license manager
	log.Printf("Initializing license manager...")
	licMgr, err := license.NewManager(&cfg.License)
	if err != nil {
		log.Fatalf("Failed to initialize license manager: %v", err)
	}
	log.Printf("License manager initialized successfully")

	// Create server
	log.Printf("Creating server...")
	srv, err := server.New(cfg, db, licMgr)
	if err != nil {
		log.Fatalf("Failed to create server: %v", err)
	}

	// Start cleanup routine
	if cfg.Database.Retention.CleanupInterval > 0 {
		go runCleanupRoutine(db, cfg)
	}

	// Start license check routine
	if cfg.License.CheckInterval > 0 {
		go runLicenseCheckRoutine(licMgr, cfg)
	}

	// Setup graceful shutdown
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)

	// Start server in goroutine
	errChan := make(chan error, 1)
	go func() {
		if err := srv.Start(); err != nil {
			errChan <- err
		}
	}()

	// Wait for shutdown signal or error
	select {
	case err := <-errChan:
		log.Fatalf("Server error: %v", err)
	case sig := <-sigChan:
		log.Printf("Received signal: %v", sig)
		log.Println("Shutting down gracefully...")

		if err := srv.Stop(); err != nil {
			log.Printf("Error during shutdown: %v", err)
		}

		log.Println("Server stopped")
	}
}

// runCleanupRoutine periodically cleans up old data
func runCleanupRoutine(db *database.Database, cfg *config.Config) {
	ticker := time.NewTicker(cfg.Database.Retention.CleanupInterval)
	defer ticker.Stop()

	for range ticker.C {
		log.Println("Running data cleanup...")

		if err := db.CleanupOldData(
			cfg.Database.Retention.MetricsDays,
			cfg.Database.Retention.AlertsDays,
			cfg.Database.Retention.AgentStatusDays,
		); err != nil {
			log.Printf("Cleanup error: %v", err)
		} else {
			log.Println("Cleanup completed successfully")
		}
	}
}

// runLicenseCheckRoutine periodically checks license validity
func runLicenseCheckRoutine(licMgr *license.Manager, cfg *config.Config) {
	ticker := time.NewTicker(cfg.License.CheckInterval)
	defer ticker.Stop()

	for range ticker.C {
		if err := licMgr.Validate(); err != nil {
			log.Printf("LICENSE WARNING: %v", err)

			if licMgr.IsInGracePeriod() {
				log.Printf("License is in grace period")
			}
		}

		// Warn if license expires soon
		daysUntilExpiry := licMgr.GetExpiryDays()
		if daysUntilExpiry > 0 && daysUntilExpiry <= 30 {
			log.Printf("LICENSE WARNING: License expires in %d days", daysUntilExpiry)
		}
	}
}
