package snmpwalker

import (
	"fmt"
	"log"
	"net"
	"strings"
	"sync"
	"time"

	"github.com/gosnmp/gosnmp"
)

const (
	// WalkerAgentID is the synthetic agent ID used for all walker-discovered entries
	WalkerAgentID = "snmp-walker"

	// System info OIDs
	oidSysName  = "1.3.6.1.2.1.1.5.0"
	oidSysDescr = "1.3.6.1.2.1.1.1.0"

	// Neighbor discovery OIDs (tried in order)
	oidCDPCacheAddress   = "1.3.6.1.4.1.9.9.23.1.2.1.1.4" // Cisco CDP neighbor IPs
	oidARPTable          = "1.3.6.1.2.1.4.22.1.3"          // ipNetToMediaNetAddress
	oidLLDPRemChassisID  = "1.0.8802.1.1.2.1.4.1.1.5"      // lldpRemChassisId — IP stored as string value (PRIMARY for simulators)
	oidLLDPRemMgmtAddr   = "1.0.8802.1.1.2.1.4.2"          // lldpRemManAddrTable
	oidIPRouteNextHop    = "1.3.6.1.2.1.4.21.1.7"          // ipRouteNextHop
)

// WalkConfig holds the parameters for a walk run
type WalkConfig struct {
	SeedIP           string
	Community        string
	Version          string // "1", "2c", "3"
	Port             uint16
	MaxDepth         int
	Timeout          time.Duration
	Retries          int
	// UseIPAsCommunity: when true each device is queried using its own IP as
	// the community string (required by some simulators like SNMP Network
	// Topology Simulator).
	UseIPAsCommunity bool
}

// WalkSession tracks a running or completed walk
type WalkSession struct {
	ID          string     `json:"id"`
	Status      string     `json:"status"` // running, complete, failed
	StartedAt   time.Time  `json:"started_at"`
	CompletedAt *time.Time `json:"completed_at,omitempty"`
	SeedIP      string     `json:"seed_ip"`
	NodesFound  int        `json:"nodes_found"`
	Error       string     `json:"error,omitempty"`
	mu          sync.RWMutex
}

// SessionSnapshot is a mutex-free copy of WalkSession for JSON responses.
type SessionSnapshot struct {
	ID          string     `json:"id"`
	Status      string     `json:"status"`
	StartedAt   time.Time  `json:"started_at"`
	CompletedAt *time.Time `json:"completed_at,omitempty"`
	SeedIP      string     `json:"seed_ip"`
	NodesFound  int        `json:"nodes_found"`
	Error       string     `json:"error,omitempty"`
}

// Snapshot returns a thread-safe copy of the session.
func (s *WalkSession) Snapshot() SessionSnapshot {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return SessionSnapshot{
		ID:          s.ID,
		Status:      s.Status,
		StartedAt:   s.StartedAt,
		CompletedAt: s.CompletedAt,
		SeedIP:      s.SeedIP,
		NodesFound:  s.NodesFound,
		Error:       s.Error,
	}
}

// DiscoveredMetric is a metric row ready to be inserted into snmp_metrics
type DiscoveredMetric struct {
	AgentID    string
	Timestamp  time.Time
	DeviceName string
	DeviceHost string
	OID        string
	MetricName string
	Value      float64
	ValueType  string
	Metadata   map[string]interface{}
}

// Walker manages SNMP topology walks
type Walker struct {
	sessions map[string]*WalkSession
	mu       sync.RWMutex
	logger   *log.Logger
	saveFn   func(metrics []DiscoveredMetric) error
}

// New creates a Walker. saveFn is called with the discovered metrics to persist them.
func New(logger *log.Logger, saveFn func(metrics []DiscoveredMetric) error) *Walker {
	return &Walker{
		sessions: make(map[string]*WalkSession),
		logger:   logger,
		saveFn:   saveFn,
	}
}

// StartWalk launches a BFS topology walk in the background and returns the session ID.
func (w *Walker) StartWalk(cfg WalkConfig) (string, error) {
	if net.ParseIP(cfg.SeedIP) == nil {
		return "", fmt.Errorf("invalid seed IP: %s", cfg.SeedIP)
	}

	// Apply defaults
	if cfg.Community == "" {
		cfg.Community = "public"
	}
	if cfg.Version == "" {
		cfg.Version = "2c"
	}
	if cfg.Port == 0 {
		cfg.Port = 161
	}
	if cfg.MaxDepth <= 0 {
		cfg.MaxDepth = 5
	}
	if cfg.Timeout <= 0 {
		cfg.Timeout = 5 * time.Second
	}
	if cfg.Retries <= 0 {
		cfg.Retries = 1
	}

	sessionID := fmt.Sprintf("walk-%d", time.Now().UnixNano())
	session := &WalkSession{
		ID:        sessionID,
		Status:    "running",
		StartedAt: time.Now(),
		SeedIP:    cfg.SeedIP,
	}

	w.mu.Lock()
	w.sessions[sessionID] = session
	w.mu.Unlock()

	go w.walkBFS(session, cfg)

	return sessionID, nil
}

// GetSession returns the session by ID.
func (w *Walker) GetSession(id string) (*WalkSession, bool) {
	w.mu.RLock()
	defer w.mu.RUnlock()
	s, ok := w.sessions[id]
	return s, ok
}

// GetAllSessions returns a snapshot of all sessions.
func (w *Walker) GetAllSessions() []*WalkSession {
	w.mu.RLock()
	defer w.mu.RUnlock()
	out := make([]*WalkSession, 0, len(w.sessions))
	for _, s := range w.sessions {
		out = append(out, s)
	}
	return out
}

// walkBFS performs a breadth-first SNMP walk starting from session.SeedIP.
func (w *Walker) walkBFS(session *WalkSession, cfg WalkConfig) {
	defer func() {
		if r := recover(); r != nil {
			w.markFailed(session, fmt.Sprintf("panic: %v", r))
		}
	}()

	queue := []string{cfg.SeedIP}
	visited := make(map[string]bool)
	depth := map[string]int{cfg.SeedIP: 0}
	parent := map[string]string{cfg.SeedIP: ""}

	var collected []DiscoveredMetric

	for len(queue) > 0 {
		ip := queue[0]
		queue = queue[1:]

		if visited[ip] {
			continue
		}
		visited[ip] = true

		if depth[ip] > cfg.MaxDepth {
			continue
		}

		w.logger.Printf("[WALKER] Probing %s (depth %d)", ip, depth[ip])

		metrics, neighbors, err := w.probeDevice(ip, cfg)
		if err != nil {
			w.logger.Printf("[WALKER] Unreachable %s: %v", ip, err)
			// Record as unreachable so it still appears in the node list
			collected = append(collected, DiscoveredMetric{
				AgentID:    WalkerAgentID,
				Timestamp:  time.Now(),
				DeviceName: ip,
				DeviceHost: ip,
				OID:        oidSysName,
				MetricName: "reachable",
				Value:      0,
				ValueType:  "gauge",
			})
		} else {
			collected = append(collected, metrics...)

			// Emit topology_depth metric
			devName := metrics[0].DeviceName
			collected = append(collected, DiscoveredMetric{
				AgentID:    WalkerAgentID,
				Timestamp:  time.Now(),
				DeviceName: devName,
				DeviceHost: ip,
				OID:        oidSysName,
				MetricName: "topology_depth",
				Value:      float64(depth[ip]),
				ValueType:  "gauge",
			})

			// Emit topology_parent metric if this device has a parent
			if par := parent[ip]; par != "" {
				collected = append(collected, DiscoveredMetric{
					AgentID:    WalkerAgentID,
					Timestamp:  time.Now(),
					DeviceName: devName,
					DeviceHost: ip,
					OID:        oidSysName,
					MetricName: "topology_parent",
					Value:      float64(ipToUint32(par)),
					ValueType:  "gauge",
				})
			}

			session.mu.Lock()
			session.NodesFound++
			session.mu.Unlock()
		}

		// Enqueue unvisited neighbors within depth limit
		for _, n := range neighbors {
			if !visited[n] {
				if _, seen := depth[n]; !seen {
					depth[n] = depth[ip] + 1
				}
				if _, hasParent := parent[n]; !hasParent {
					parent[n] = ip
				}
				queue = append(queue, n)
			}
		}
	}

	if len(collected) > 0 {
		if err := w.saveFn(collected); err != nil {
			w.logger.Printf("[WALKER] Failed to save %d metrics: %v", len(collected), err)
		}
	}

	session.mu.Lock()
	now := time.Now()
	session.Status = "complete"
	session.CompletedAt = &now
	session.mu.Unlock()

	w.logger.Printf("[WALKER] Session %s complete — %d nodes found", session.ID, session.NodesFound)
}

// probeDevice connects to one IP, fetches system info and neighbor IPs.
func (w *Walker) probeDevice(ip string, cfg WalkConfig) ([]DiscoveredMetric, []string, error) {
	community := cfg.Community
	if cfg.UseIPAsCommunity {
		community = ip
	}

	g := &gosnmp.GoSNMP{
		Target:    ip,
		Port:      cfg.Port,
		Community: community,
		Version:   parseVersion(cfg.Version),
		Timeout:   cfg.Timeout,
		Retries:   cfg.Retries,
	}

	if err := g.Connect(); err != nil {
		return nil, nil, fmt.Errorf("connect: %w", err)
	}

	now := time.Now()

	// Fetch sysName + sysDescr in a single GET
	getResult, err := g.Get([]string{oidSysName, oidSysDescr})

	// Close GET connection immediately so the neighbor walk opens a clean socket.
	// Leaving two simultaneous UDP connections open to the same host confuses
	// some SNMP simulators and causes BulkWalkAll to return empty.
	g.Conn.Close()

	sysName := ip
	sysDescr := ""
	if err == nil {
		for _, v := range getResult.Variables {
			switch v.Name {
			case "."+oidSysName, oidSysName:
				if s := snmpString(v); s != "" {
					sysName = strings.TrimSpace(s)
				}
			case "."+oidSysDescr, oidSysDescr:
				sysDescr = strings.TrimSpace(snmpString(v))
			}
		}
	}

	meta := map[string]interface{}{}
	if sysDescr != "" {
		meta["sys_descr"] = sysDescr
	}
	meta["device_type"] = guessDeviceType(sysDescr)

	metrics := []DiscoveredMetric{
		{
			AgentID:    WalkerAgentID,
			Timestamp:  now,
			DeviceName: sysName,
			DeviceHost: ip,
			OID:        oidSysName,
			MetricName: "reachable",
			Value:      1,
			ValueType:  "gauge",
			Metadata:   meta,
		},
	}

	neighbors := w.discoverNeighbors(ip, cfg)

	return metrics, neighbors, nil
}

// discoverNeighbors opens a fresh SNMP connection and tries multiple MIBs to
// find adjacent device IPs. A separate connection is required because reusing
// the GET connection from probeDevice causes BulkWalkAll to time out on some
// SNMP agents and simulators.
func (w *Walker) discoverNeighbors(ip string, cfg WalkConfig) []string {
	community := cfg.Community
	if cfg.UseIPAsCommunity {
		community = ip
	}

	g := &gosnmp.GoSNMP{
		Target:    ip,
		Port:      cfg.Port,
		Community: community,
		Version:   parseVersion(cfg.Version),
		Timeout:   cfg.Timeout,
		Retries:   cfg.Retries,
	}
	if err := g.Connect(); err != nil {
		w.logger.Printf("[WALKER] discoverNeighbors connect %s: %v", ip, err)
		return nil
	}
	defer g.Conn.Close()

	seen := make(map[string]bool)
	var result []string

	addIP := func(neighbor string) {
		if neighbor == "" || neighbor == ip || seen[neighbor] || !isRoutable(neighbor) {
			return
		}
		seen[neighbor] = true
		result = append(result, neighbor)
	}

	// walkFn tries BulkWalk first (SNMPv2c/v3); if it fails or returns empty it
	// falls back to a plain Walk (SNMPv1-style GET-NEXT). Many simulators and
	// embedded agents respond to GET but do not implement GETBULK, causing
	// BulkWalkAll to time out silently. Errors and empty results are logged so
	// the operator can see which OIDs are not supported.
	walkFn := func(label, oid string) []gosnmp.SnmpPDU {
		if cfg.Version != "1" {
			pdus, err := g.BulkWalkAll(oid)
			if err == nil && len(pdus) > 0 {
				return pdus
			}
			// Only log real errors (timeouts/refused), not empty responses
			if err != nil {
				w.logger.Printf("[WALKER] BulkWalk %s on %s failed: %v — retrying with Walk", label, ip, err)
			}
		}
		pdus, err := g.WalkAll(oid)
		if err != nil {
			w.logger.Printf("[WALKER] Walk %s on %s failed: %v", label, ip, err)
			return nil
		}
		return pdus
	}

	// 1. LLDP lldpRemChassisId — value is the neighbor IP as a plain ASCII string.
	// This is the format used by SNMP topology simulators (snmpsim-based).
	for _, pdu := range walkFn("LLDPChassisId", oidLLDPRemChassisID) {
		if b, ok := pdu.Value.([]byte); ok {
			if s := strings.TrimSpace(string(b)); net.ParseIP(s) != nil {
				addIP(s)
			}
		}
	}

	// 2. Cisco CDP neighbor IP addresses.
	// Value encoding varies by implementation:
	//   - ASCII OctetString: bytes are the IP string e.g. "10.100.0.3"
	//   - net.IP or raw 4-byte slice
	//   - 5-byte CDP prefix [type=1, a,b,c,d]
	//   - 8-byte CDP wire format [0x00,0x01,0x00,0x04, a,b,c,d]
	for _, pdu := range walkFn("CDP", oidCDPCacheAddress) {
		if b, ok := pdu.Value.([]byte); ok {
			if s := string(b); net.ParseIP(s) != nil {
				addIP(s)
				continue
			}
			switch len(b) {
			case 4:
				addIP(net.IP(b).String())
			case 5:
				if b[0] == 1 {
					addIP(net.IP(b[1:5]).String())
				}
			case 8:
				addIP(net.IP(b[4:8]).String())
			}
			continue
		}
		if parsed := snmpIPValue(pdu); parsed != "" {
			addIP(parsed)
		}
	}

	// 3. ARP table — OID suffix encodes the IP: baseOID.ifIndex.a.b.c.d
	for _, pdu := range walkFn("ARP", oidARPTable) {
		addIP(ipFromOIDSuffix(pdu.Name, oidARPTable))
	}

	// 4. IP routing table next hops
	for _, pdu := range walkFn("RouteNextHop", oidIPRouteNextHop) {
		if ip := snmpIPValue(pdu); ip != "" {
			addIP(ip)
		}
	}

	// 5. LLDP remote management address table
	for _, pdu := range walkFn("LLDPMgmtAddr", oidLLDPRemMgmtAddr) {
		if ip := snmpIPValue(pdu); ip != "" {
			addIP(ip)
		}
	}

	if len(result) == 0 {
		w.logger.Printf("[WALKER] No neighbors discovered for %s (LLDPChassisId/CDP/ARP/RouteNextHop/LLDPMgmtAddr all returned empty)", ip)
	} else {
		w.logger.Printf("[WALKER] Discovered %d neighbor(s) for %s: %v", len(result), ip, result)
	}
	return result
}

func (w *Walker) markFailed(session *WalkSession, msg string) {
	session.mu.Lock()
	defer session.mu.Unlock()
	now := time.Now()
	session.Status = "failed"
	session.CompletedAt = &now
	session.Error = msg
}

// ── Helpers ──────────────────────────────────────────────────────────────────

func parseVersion(v string) gosnmp.SnmpVersion {
	switch strings.TrimSpace(v) {
	case "1":
		return gosnmp.Version1
	case "3":
		return gosnmp.Version3
	default:
		return gosnmp.Version2c
	}
}

func snmpString(pdu gosnmp.SnmpPDU) string {
	switch pdu.Type {
	case gosnmp.OctetString:
		b, ok := pdu.Value.([]byte)
		if ok {
			return string(b)
		}
	}
	return fmt.Sprintf("%v", pdu.Value)
}

func snmpIPValue(pdu gosnmp.SnmpPDU) string {
	switch v := pdu.Value.(type) {
	case net.IP:
		return v.String()
	case []byte:
		if len(v) == 4 {
			return net.IP(v).String()
		}
		if len(v) == 16 {
			return net.IP(v).String()
		}
	case string:
		if net.ParseIP(v) != nil {
			return v
		}
	}
	return ""
}

// ipFromOIDSuffix extracts an IPv4 address from an OID with a dotted-IP suffix.
// ARP table OID: baseOID.ifIndex.a.b.c.d  — last 4 segments are the IP.
func ipFromOIDSuffix(fullOID, baseOID string) string {
	// Strip leading dot if present
	full := strings.TrimPrefix(fullOID, ".")
	base := strings.TrimPrefix(baseOID, ".")

	suffix := strings.TrimPrefix(full, base+".")
	if suffix == full {
		return ""
	}

	parts := strings.Split(suffix, ".")
	if len(parts) < 4 {
		return ""
	}
	ipParts := parts[len(parts)-4:]
	candidate := strings.Join(ipParts, ".")
	if net.ParseIP(candidate) != nil {
		return candidate
	}
	return ""
}

func isRoutable(ip string) bool {
	parsed := net.ParseIP(ip)
	if parsed == nil {
		return false
	}
	if parsed.IsLoopback() || parsed.IsMulticast() || parsed.IsUnspecified() {
		return false
	}
	// Skip broadcast-style addresses
	if parsed.Equal(net.IPv4bcast) {
		return false
	}
	return true
}

func guessDeviceType(sysDescr string) string {
	lower := strings.ToLower(sysDescr)
	switch {
	case strings.Contains(lower, "router") || strings.Contains(lower, "cisco ios") || strings.Contains(lower, "junos"):
		return "router"
	case strings.Contains(lower, "switch") || strings.Contains(lower, "catalyst"):
		return "switch"
	case strings.Contains(lower, "linux") || strings.Contains(lower, "windows") || strings.Contains(lower, "ubuntu"):
		return "host"
	default:
		return "unknown"
	}
}

// ipToUint32 converts a dotted IPv4 string to its uint32 representation.
func ipToUint32(ipStr string) uint32 {
	ip := net.ParseIP(ipStr)
	if ip == nil {
		return 0
	}
	ip = ip.To4()
	if ip == nil {
		return 0
	}
	return uint32(ip[0])<<24 | uint32(ip[1])<<16 | uint32(ip[2])<<8 | uint32(ip[3])
}
