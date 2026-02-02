package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/faber/network-monitor-agent/internal/agent"
	"github.com/faber/network-monitor-agent/internal/config"
	"github.com/faber/network-monitor-agent/internal/logger"
	"github.com/kardianos/service"
)

var (
	version   = "1.0.0"
	buildTime = "unknown"
)

type program struct {
	agent  *agent.Agent
	logger service.Logger
}

func (p *program) Start(s service.Service) error {
	p.logger.Info("Starting Network Monitor Agent...")
	go p.agent.Run()
	return nil
}

func (p *program) Stop(s service.Service) error {
	p.logger.Info("Stopping Network Monitor Agent...")
	p.agent.Stop()
	return nil
}

func main() {
	var (
		configPath    = flag.String("config", "config.yaml", "Path to configuration file")
		serviceAction = flag.String("service", "", "Service action: install, uninstall, start, stop, restart")
		showVersion   = flag.Bool("version", false, "Show version information")
	)
	flag.Parse()

	if *showVersion {
		fmt.Printf("Network Monitor Agent v%s (built: %s)\n", version, buildTime)
		os.Exit(0)
	}

	// Convert config path to absolute path
	absConfigPath, err := filepath.Abs(*configPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to get absolute config path: %v\n", err)
		os.Exit(1)
	}
	configPath = &absConfigPath

	// Load configuration
	cfg, err := config.Load(*configPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to load config: %v\n", err)
		os.Exit(1)
	}

	// Initialize logger
	log, err := logger.New(cfg.Logging)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to initialize logger: %v\n", err)
		os.Exit(1)
	}

	// Create agent
	agentInstance, err := agent.New(cfg, log)
	if err != nil {
		log.Errorf("Failed to create agent: %v", err)
		os.Exit(1)
	}

	// Service configuration
	svcConfig := &service.Config{
		Name:        "NetworkMonitorAgent",
		DisplayName: "Network Monitor Agent",
		Description: "Collects system metrics and sends them to monitoring server",
		Arguments:   []string{"-config", *configPath},
	}

	prg := &program{
		agent: agentInstance,
	}

	s, err := service.New(prg, svcConfig)
	if err != nil {
		log.Errorf("Failed to create service: %v", err)
		os.Exit(1)
	}

	prg.logger, err = s.Logger(nil)
	if err != nil {
		log.Errorf("Failed to get service logger: %v", err)
		os.Exit(1)
	}

	// Handle service actions
	if *serviceAction != "" {
		err = service.Control(s, *serviceAction)
		if err != nil {
			log.Errorf("Failed to %s service: %v", *serviceAction, err)
			os.Exit(1)
		}
		fmt.Printf("Service %s completed successfully\n", *serviceAction)
		return
	}

	// Check if running interactively (not as a service)
	isInteractive := service.Interactive()

	if isInteractive {
		// Run interactively with signal handling
		log.Infof("Running in interactive mode. Press Ctrl+C to stop.")

		// Start agent
		go agentInstance.Run()

		// Setup signal handling
		sigChan := make(chan os.Signal, 1)
		signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM, syscall.SIGINT)

		// Wait for signal
		sig := <-sigChan
		log.Infof("Received signal: %v. Initiating graceful shutdown...", sig)

		// Create shutdown context with timeout
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()

		// Stop agent in a goroutine
		done := make(chan struct{})
		go func() {
			agentInstance.Stop()
			close(done)
		}()

		// Wait for shutdown or timeout
		select {
		case <-done:
			log.Infof("Agent stopped gracefully")
		case <-shutdownCtx.Done():
			log.Warnf("Shutdown timeout reached, forcing exit")
		}
	} else {
		// Run as a service
		err = s.Run()
		if err != nil {
			log.Errorf("Service failed: %v", err)
			os.Exit(1)
		}
	}
}
