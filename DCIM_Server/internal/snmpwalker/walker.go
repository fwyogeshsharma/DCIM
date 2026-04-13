package snmpwalker

import (
	"fmt"
	"log"
	"net"
	"strconv"
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

	// LLDP remote table OIDs — matches lldp_generator.py in the simulator.
	// All three are walked simultaneously and correlated by OID suffix
	// (format: {timeMark}.{localPort}.{remoteIdx}) to get a complete neighbor record.
	oidLLDPRemSysName   = "1.0.8802.1.1.2.1.4.1.1.9" // neighbor hostname
	oidLLDPRemChassisID = "1.0.8802.1.1.2.1.4.1.1.5" // neighbor IP (ASCII string value)
	oidLLDPRemPortID    = "1.0.8802.1.1.2.1.4.1.1.7" // remote port name

	// Fallback neighbor discovery OIDs (used when LLDP returns nothing)
	oidCDPCacheAddress = "1.3.6.1.4.1.9.9.23.1.2.1.1.4" // Cisco CDP neighbor IPs
	oidARPTable        = "1.3.6.1.2.1.4.22.1.3"         // ipNetToMediaNetAddress
	oidLLDPRemMgmtAddr = "1.0.8802.1.1.2.1.4.2"         // lldpRemManAddrTable
	oidIPRouteNextHop  = "1.3.6.1.2.1.4.21.1.7"         // ipRouteNextHop
)

// WalkConfig holds the parameters for a walk run
type WalkConfig struct {
	SeedIP    string
	Community string
	Version   string // "1", "2c", "3"
	Port      uint16
	MaxDepth  int
	Timeout   time.Duration
	Retries   int
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

// DiscoveredNeighbor holds rich LLDP neighbor info discovered from a single device.
type DiscoveredNeighbor struct {
	IP         string // neighbor IP (from lldpRemChassisId)
	Name       string // neighbor hostname (from lldpRemSysName)
	LocalPort  int    // local interface index (from LLDP suffix: timeMark.localPort.remoteIdx)
	RemotePort string // remote interface name (from lldpRemPortId)
}

// TopologyLink represents a discovered connection between two devices.
// SourceDepth and TargetDepth reflect BFS depth from the seed IP (entry point = 0).
// The synthetic server→seed link uses SourceDepth=-1 to place the server above the network.
// SourcePort and TargetPort carry LLDP port info for UI visualization.
type TopologyLink struct {
	SourceIP    string
	SourceName  string
	SourceDepth int
	SourcePort  int // local port index on source device (from LLDP)
	TargetIP    string
	TargetName  string
	TargetDepth int
	TargetPort  string // remote port name on target device (from LLDP)
}

// Walker manages SNMP topology walks
type Walker struct {
	sessions    map[string]*WalkSession
	mu          sync.RWMutex
	logger      *log.Logger
	saveFn      func(metrics []DiscoveredMetric) error
	saveLinksFn func(links []TopologyLink) error
}

// New creates a Walker. saveFn is called with the discovered metrics to persist them.
// saveLinksFn (optional) is called with topology links discovered during the walk.
func New(logger *log.Logger, saveFn func(metrics []DiscoveredMetric) error, saveLinksFn func(links []TopologyLink) error) *Walker {
	return &Walker{
		sessions:    make(map[string]*WalkSession),
		logger:      logger,
		saveFn:      saveFn,
		saveLinksFn: saveLinksFn,
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
	var collectedLinks []TopologyLink

	// nameByIP caches discovered device names so target names can be resolved
	// from earlier LLDP walks without waiting for the target to be probed.
	nameByIP := make(map[string]string)

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

			// Cache device name for link resolution
			if len(metrics) > 0 {
				nameByIP[ip] = metrics[0].DeviceName
			}

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

			session.mu.Lock()
			session.NodesFound++
			session.mu.Unlock()
		}

		// Enqueue unvisited neighbors and record topology links
		for _, n := range neighbors {
			if !visited[n.IP] {
				if _, seen := depth[n.IP]; !seen {
					depth[n.IP] = depth[ip] + 1
				}
				if _, hasParent := parent[n.IP]; !hasParent {
					parent[n.IP] = ip

					srcName := nameByIP[ip]
					if srcName == "" {
						srcName = ip
					}
					// Use LLDP-discovered target name if available; will be
					// overwritten with actual sysName when target is probed.
					targetName := n.Name
					if targetName == "" {
						targetName = n.IP
					}

					collectedLinks = append(collectedLinks, TopologyLink{
						SourceIP:    ip,
						SourceName:  srcName,
						SourceDepth: depth[ip],
						SourcePort:  n.LocalPort,
						TargetIP:    n.IP,
						TargetName:  targetName,
						TargetDepth: depth[n.IP],
						TargetPort:  n.RemotePort,
					})
				}
				queue = append(queue, n.IP)
			}
		}
	}

	// Second pass: update target names in links using actual sysName from probed devices
	for i, link := range collectedLinks {
		if name, ok := nameByIP[link.TargetIP]; ok && name != "" {
			collectedLinks[i].TargetName = name
		}
	}

	if len(collected) > 0 {
		if err := w.saveFn(collected); err != nil {
			w.logger.Printf("[WALKER] Failed to save %d metrics: %v", len(collected), err)
		}
	}

	if w.saveLinksFn != nil && len(collectedLinks) > 0 {
		if err := w.saveLinksFn(collectedLinks); err != nil {
			w.logger.Printf("[WALKER] Failed to save %d links: %v", len(collectedLinks), err)
		}
	}

	session.mu.Lock()
	now := time.Now()
	session.Status = "complete"
	session.CompletedAt = &now
	session.mu.Unlock()

	w.logger.Printf("[WALKER] Session %s complete — %d nodes found", session.ID, session.NodesFound)
}

// probeDevice connects to one IP, fetches system info and discovers neighbors.
func (w *Walker) probeDevice(ip string, cfg WalkConfig) ([]DiscoveredMetric, []DiscoveredNeighbor, error) {
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

	// Close GET connection immediately — reusing it for BulkWalk confuses some simulators
	g.Conn.Close()

	sysName := ip
	sysDescr := ""
	if err == nil {
		for _, v := range getResult.Variables {
			switch v.Name {
			case "." + oidSysName, oidSysName:
				if s := snmpString(v); s != "" {
					sysName = strings.TrimSpace(s)
				}
			case "." + oidSysDescr, oidSysDescr:
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

// discoverNeighbors opens a fresh SNMP connection and discovers adjacent devices.
// Primary method: LLDP correlation (name + IP + port from suffix).
// Fallback: CDP, ARP, routing table when LLDP returns nothing.
func (w *Walker) discoverNeighbors(ip string, cfg WalkConfig) []DiscoveredNeighbor {
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

	walkFn := func(label, oid string) []gosnmp.SnmpPDU {
		if cfg.Version != "1" {
			pdus, err := g.BulkWalkAll(oid)
			if err == nil && len(pdus) > 0 {
				return pdus
			}
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

	// ── Primary: LLDP correlation ────────────────────────────────────────────
	// Walk all three LLDP tables simultaneously, then correlate by OID suffix.
	// Suffix format: {timeMark}.{localPort}.{remoteIdx}
	// This matches the approach used by the SNMP simulator's discovery engine.
	namesPDUs := walkFn("LLDPSysName", oidLLDPRemSysName)
	ipsPDUs := walkFn("LLDPChassisId", oidLLDPRemChassisID)
	portsPDUs := walkFn("LLDPPortId", oidLLDPRemPortID)

	names := toSuffixMap(namesPDUs, oidLLDPRemSysName)
	ips := toSuffixMap(ipsPDUs, oidLLDPRemChassisID)
	ports := toSuffixMap(portsPDUs, oidLLDPRemPortID)

	seen := make(map[string]bool)
	var result []DiscoveredNeighbor

	if len(names) > 0 {
		for suffix, neighborName := range names {
			neighborIP := ips[suffix]
			if neighborIP == "" {
				continue
			}
			if net.ParseIP(neighborIP) == nil {
				continue
			}
			if neighborIP == ip || seen[neighborIP] || !isRoutable(neighborIP) {
				continue
			}

			// Extract local port index from suffix: {timeMark}.{localPort}.{remoteIdx}
			localPort := 0
			parts := strings.Split(suffix, ".")
			if len(parts) >= 3 {
				if p, err := strconv.Atoi(parts[1]); err == nil {
					localPort = p
				}
			}

			seen[neighborIP] = true
			result = append(result, DiscoveredNeighbor{
				IP:         neighborIP,
				Name:       strings.TrimSpace(neighborName),
				LocalPort:  localPort,
				RemotePort: strings.TrimSpace(ports[suffix]),
			})
		}

		if len(result) > 0 {
			w.logger.Printf("[WALKER] LLDP discovered %d neighbor(s) for %s: %v",
				len(result), ip, neighborIPs(result))
			return result
		}
	}

	// ── Fallback: IP-only methods ────────────────────────────────────────────
	w.logger.Printf("[WALKER] LLDP returned no neighbors for %s — trying CDP/ARP/routes", ip)

	addIP := func(neighborIP, name string) {
		if neighborIP == "" || neighborIP == ip || seen[neighborIP] || !isRoutable(neighborIP) {
			return
		}
		seen[neighborIP] = true
		result = append(result, DiscoveredNeighbor{IP: neighborIP, Name: name})
	}

	// CDP
	for _, pdu := range walkFn("CDP", oidCDPCacheAddress) {
		if b, ok := pdu.Value.([]byte); ok {
			if s := string(b); net.ParseIP(s) != nil {
				addIP(s, "")
				continue
			}
			switch len(b) {
			case 4:
				addIP(net.IP(b).String(), "")
			case 5:
				if b[0] == 1 {
					addIP(net.IP(b[1:5]).String(), "")
				}
			case 8:
				addIP(net.IP(b[4:8]).String(), "")
			}
			continue
		}
		if parsed := snmpIPValue(pdu); parsed != "" {
			addIP(parsed, "")
		}
	}

	// ARP
	for _, pdu := range walkFn("ARP", oidARPTable) {
		addIP(ipFromOIDSuffix(pdu.Name, oidARPTable), "")
	}

	// IP routing table
	for _, pdu := range walkFn("RouteNextHop", oidIPRouteNextHop) {
		if v := snmpIPValue(pdu); v != "" {
			addIP(v, "")
		}
	}

	// LLDP management address table
	for _, pdu := range walkFn("LLDPMgmtAddr", oidLLDPRemMgmtAddr) {
		if v := snmpIPValue(pdu); v != "" {
			addIP(v, "")
		}
	}

	if len(result) == 0 {
		w.logger.Printf("[WALKER] No neighbors discovered for %s", ip)
	} else {
		w.logger.Printf("[WALKER] Fallback discovered %d neighbor(s) for %s: %v",
			len(result), ip, neighborIPs(result))
	}
	return result
}

// toSuffixMap builds a map from OID suffix → string value.
// Suffix is everything after baseOID + ".".
func toSuffixMap(pdus []gosnmp.SnmpPDU, baseOID string) map[string]string {
	m := make(map[string]string)
	prefix := strings.TrimPrefix(baseOID, ".") + "."
	for _, pdu := range pdus {
		fullOID := strings.TrimPrefix(pdu.Name, ".")
		if strings.HasPrefix(fullOID, prefix) {
			suffix := fullOID[len(prefix):]
			if b, ok := pdu.Value.([]byte); ok {
				m[suffix] = strings.TrimSpace(string(b))
			} else {
				m[suffix] = strings.TrimSpace(fmt.Sprintf("%v", pdu.Value))
			}
		}
	}
	return m
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

func neighborIPs(neighbors []DiscoveredNeighbor) []string {
	ips := make([]string, len(neighbors))
	for i, n := range neighbors {
		ips[i] = n.IP
	}
	return ips
}

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
