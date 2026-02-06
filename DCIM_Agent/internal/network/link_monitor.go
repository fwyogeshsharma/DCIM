package network

import (
	"fmt"
	"runtime"
	"sync"
	"time"
)

// LinkEvent represents a network link state change event
type LinkEvent struct {
	Interface   string    `json:"interface"`
	State       string    `json:"state"`        // "up", "down"
	PrevState   string    `json:"prev_state"`
	Timestamp   time.Time `json:"timestamp"`
	LinkSpeed   int       `json:"link_speed"`   // Mbps
	Duplex      string    `json:"duplex"`
	Reason      string    `json:"reason"`       // Optional reason
}

// LinkMonitor monitors network interface link state changes
type LinkMonitor struct {
	interfaces map[string]*InterfaceState
	events     chan LinkEvent
	stopCh     chan struct{}
	mu         sync.RWMutex
	running    bool
}

// InterfaceState tracks the current state of an interface
type InterfaceState struct {
	Name          string
	State         string
	LinkSpeed     int
	Duplex        string
	LastChange    time.Time
	EventCount    int
}

// NewLinkMonitor creates a new link monitor
func NewLinkMonitor() *LinkMonitor {
	return &LinkMonitor{
		interfaces: make(map[string]*InterfaceState),
		events:     make(chan LinkEvent, 100),
		stopCh:     make(chan struct{}),
	}
}

// Start begins monitoring link state changes
func (m *LinkMonitor) Start(pollInterval time.Duration) error {
	m.mu.Lock()
	if m.running {
		m.mu.Unlock()
		return fmt.Errorf("monitor already running")
	}
	m.running = true
	m.mu.Unlock()

	// Initialize current states
	if err := m.updateStates(); err != nil {
		return fmt.Errorf("initialize states: %w", err)
	}

	// Start monitoring goroutine
	go m.monitorLoop(pollInterval)

	return nil
}

// Stop stops the link monitor
func (m *LinkMonitor) Stop() {
	m.mu.Lock()
	defer m.mu.Unlock()

	if m.running {
		close(m.stopCh)
		m.running = false
	}
}

// Events returns the channel for receiving link events
func (m *LinkMonitor) Events() <-chan LinkEvent {
	return m.events
}

// GetState returns the current state of an interface
func (m *LinkMonitor) GetState(ifaceName string) (*InterfaceState, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()

	state, ok := m.interfaces[ifaceName]
	if !ok {
		return nil, fmt.Errorf("interface not found: %s", ifaceName)
	}

	return state, nil
}

// GetAllStates returns states for all monitored interfaces
func (m *LinkMonitor) GetAllStates() map[string]*InterfaceState {
	m.mu.RLock()
	defer m.mu.RUnlock()

	states := make(map[string]*InterfaceState)
	for name, state := range m.interfaces {
		states[name] = state
	}

	return states
}

// monitorLoop periodically checks for link state changes
func (m *LinkMonitor) monitorLoop(pollInterval time.Duration) {
	ticker := time.NewTicker(pollInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ticker.C:
			m.updateStates()
		case <-m.stopCh:
			return
		}
	}
}

// updateStates updates interface states and detects changes
func (m *LinkMonitor) updateStates() error {
	// Get current interface details
	details, err := GetAllInterfaceDetails()
	if err != nil {
		return err
	}

	m.mu.Lock()
	defer m.mu.Unlock()

	for _, detail := range details {
		// Skip loopback
		if isLoopback(detail.Flags) {
			continue
		}

		currentState := "down"
		if detail.IsUp() {
			currentState = "up"
		}

		prevState, exists := m.interfaces[detail.Name]

		if !exists {
			// New interface discovered
			m.interfaces[detail.Name] = &InterfaceState{
				Name:       detail.Name,
				State:      currentState,
				LinkSpeed:  detail.LinkSpeed,
				Duplex:     detail.Duplex,
				LastChange: time.Now(),
			}
			continue
		}

		// Check for state change
		if prevState.State != currentState {
			event := LinkEvent{
				Interface:  detail.Name,
				State:      currentState,
				PrevState:  prevState.State,
				Timestamp:  time.Now(),
				LinkSpeed:  detail.LinkSpeed,
				Duplex:     detail.Duplex,
			}

			// Determine reason
			if currentState == "down" {
				if !detail.LinkDetected {
					event.Reason = "cable_unplugged"
				} else {
					event.Reason = "admin_down"
				}
			} else {
				event.Reason = "link_established"
			}

			// Update state
			prevState.State = currentState
			prevState.LinkSpeed = detail.LinkSpeed
			prevState.Duplex = detail.Duplex
			prevState.LastChange = event.Timestamp
			prevState.EventCount++

			// Send event (non-blocking)
			select {
			case m.events <- event:
			default:
				// Event channel full, skip
			}
		} else {
			// No state change, but update link details
			prevState.LinkSpeed = detail.LinkSpeed
			prevState.Duplex = detail.Duplex
		}
	}

	return nil
}

// isLoopback checks if interface is loopback
func isLoopback(flags []string) bool {
	for _, flag := range flags {
		if flag == "LOOPBACK" {
			return true
		}
	}
	return false
}

// MonitorLinkStateChanges is a convenience function to start monitoring
func MonitorLinkStateChanges(pollInterval time.Duration) (*LinkMonitor, error) {
	if runtime.GOOS == "windows" && pollInterval < 5*time.Second {
		// Windows WMI queries can be slow, enforce minimum interval
		pollInterval = 5 * time.Second
	}

	monitor := NewLinkMonitor()
	if err := monitor.Start(pollInterval); err != nil {
		return nil, err
	}

	return monitor, nil
}

// WaitForLinkUp waits for an interface to come up with timeout
func (m *LinkMonitor) WaitForLinkUp(ifaceName string, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)

	for time.Now().Before(deadline) {
		state, err := m.GetState(ifaceName)
		if err == nil && state.State == "up" {
			return nil
		}

		time.Sleep(1 * time.Second)
	}

	return fmt.Errorf("timeout waiting for interface %s to come up", ifaceName)
}

// GetLinkHistory returns recent link events for an interface
func (m *LinkMonitor) GetLinkHistory(ifaceName string, duration time.Duration) []LinkEvent {
	var history []LinkEvent
	cutoff := time.Now().Add(-duration)

	// This is a simple implementation - in production, you'd want to
	// store events in a ring buffer or database

	m.mu.RLock()
	state, exists := m.interfaces[ifaceName]
	m.mu.RUnlock()

	if exists && state.LastChange.After(cutoff) {
		// Return last known state change
		history = append(history, LinkEvent{
			Interface: ifaceName,
			State:     state.State,
			Timestamp: state.LastChange,
			LinkSpeed: state.LinkSpeed,
			Duplex:    state.Duplex,
		})
	}

	return history
}

// GetFlappingInterfaces returns interfaces that changed state multiple times
func (m *LinkMonitor) GetFlappingInterfaces(threshold int) []string {
	m.mu.RLock()
	defer m.mu.RUnlock()

	var flapping []string

	for name, state := range m.interfaces {
		if state.EventCount >= threshold {
			flapping = append(flapping, name)
		}
	}

	return flapping
}

// ResetEventCount resets the event counter for an interface
func (m *LinkMonitor) ResetEventCount(ifaceName string) {
	m.mu.Lock()
	defer m.mu.Unlock()

	if state, exists := m.interfaces[ifaceName]; exists {
		state.EventCount = 0
	}
}
