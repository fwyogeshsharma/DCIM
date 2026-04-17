// Package snmpdiscovery sweeps CIDR ranges to find devices that respond to
// SNMP. Unlike the topology walker (seed + LLDP neighbor-following with a
// single community), discovery tries every IP in the listed ranges against
// every listed community, so devices that don't share the walker's community
// or that have no LLDP neighbor linking them back to the seed are still found.
//
// Discovered devices are persisted as snmp_metrics rows (metric "reachable"=1)
// via the caller-supplied save callback, so they surface in the same API/UI
// pathways as walker results. Walker-discovered rows keep agent_id
// "snmp-walker"; discovery-only rows use "snmp-discovery".
package snmpdiscovery

import (
	"fmt"
	"log"
	"net"
	"strings"
	"sync"
	"time"

	"github.com/faberlabs/dcim-server/internal/snmpwalker"
	"github.com/gosnmp/gosnmp"
)

// DiscoveryAgentID is the synthetic agent ID used for all discovery-found rows.
const DiscoveryAgentID = "snmp-discovery"

const (
	oidSysName  = "1.3.6.1.2.1.1.5.0"
	oidSysDescr = "1.3.6.1.2.1.1.1.0"
)

// Config holds runtime parameters for a discovery sweep.
type Config struct {
	IPRanges    []string
	Communities []string
	Port        uint16
	Timeout     time.Duration
	Retries     int
	ScanWorkers int
	ScanTimeout time.Duration
	// DeepScan: after a device answers basic SNMP, also walk its LLDP/CDP/ARP
	// tables and emit TopologyLinks. Required for discovery-found devices to
	// contribute edges to the topology_links table.
	DeepScan bool
}

// DiscoveredDevice is one device that answered SNMP during the sweep.
type DiscoveredDevice struct {
	IP         string
	Name       string
	SysDescr   string
	DeviceType string
	Community  string
	Timestamp  time.Time
}

// SaveFunc persists a batch of devices. Returning an error does not abort the
// sweep — the scanner logs and continues.
type SaveFunc func(devices []DiscoveredDevice) error

// SaveLinksFunc persists topology links discovered via deep scan (LLDP/CDP/ARP).
// Called once per sweep with all links collected across every probed device.
type SaveLinksFunc func(links []snmpwalker.TopologyLink) error

// Scanner sweeps CIDRs and reports responsive devices via SaveFunc.
type Scanner struct {
	logger    *log.Logger
	save      SaveFunc
	saveLinks SaveLinksFunc
}

// New creates a Scanner. saveFn receives discovered devices batched per sweep.
// saveLinksFn (optional) receives topology links discovered when DeepScan is enabled.
func New(logger *log.Logger, saveFn SaveFunc, saveLinksFn SaveLinksFunc) *Scanner {
	return &Scanner{logger: logger, save: saveFn, saveLinks: saveLinksFn}
}

// RunOnce performs a single sweep over every IP in cfg.IPRanges, probing with
// each community in cfg.Communities until one answers. Returns the number of
// devices that responded.
func (s *Scanner) RunOnce(cfg Config) int {
	if len(cfg.IPRanges) == 0 {
		s.logger.Printf("[DISCOVERY] No ip_ranges configured — skipping sweep")
		return 0
	}
	if len(cfg.Communities) == 0 {
		cfg.Communities = []string{"public"}
	}
	if cfg.Port == 0 {
		cfg.Port = 161
	}
	if cfg.Timeout <= 0 {
		cfg.Timeout = 2 * time.Second
	}
	if cfg.ScanWorkers <= 0 {
		cfg.ScanWorkers = 50
	}

	ips := make([]string, 0, 256)
	for _, cidr := range cfg.IPRanges {
		expanded, err := expandCIDR(cidr)
		if err != nil {
			s.logger.Printf("[DISCOVERY] Skipping range %q: %v", cidr, err)
			continue
		}
		ips = append(ips, expanded...)
	}
	if len(ips) == 0 {
		s.logger.Printf("[DISCOVERY] Nothing to scan after expanding ip_ranges")
		return 0
	}

	s.logger.Printf("[DISCOVERY] Starting sweep: %d IPs, communities=%v, workers=%d, timeout=%s",
		len(ips), cfg.Communities, cfg.ScanWorkers, cfg.Timeout)

	start := time.Now()
	deadline := time.Time{}
	if cfg.ScanTimeout > 0 {
		deadline = start.Add(cfg.ScanTimeout)
	}

	work := make(chan string, cfg.ScanWorkers*2)
	var resultsMu sync.Mutex
	results := make([]DiscoveredDevice, 0, 32)
	links := make([]snmpwalker.TopologyLink, 0, 32)

	var wg sync.WaitGroup
	for w := 0; w < cfg.ScanWorkers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for ip := range work {
				if !deadline.IsZero() && time.Now().After(deadline) {
					continue
				}
				dev, ok := s.probe(ip, cfg)
				if !ok {
					continue
				}
				var deviceLinks []snmpwalker.TopologyLink
				if cfg.DeepScan {
					deviceLinks = s.deepScan(dev, cfg)
				}
				resultsMu.Lock()
				results = append(results, dev)
				links = append(links, deviceLinks...)
				resultsMu.Unlock()
			}
		}()
	}

	for _, ip := range ips {
		work <- ip
	}
	close(work)
	wg.Wait()

	elapsed := time.Since(start).Round(time.Millisecond)
	s.logger.Printf("[DISCOVERY] Sweep complete: %d/%d responded in %s", len(results), len(ips), elapsed)

	if len(results) > 0 && s.save != nil {
		if err := s.save(results); err != nil {
			s.logger.Printf("[DISCOVERY] Failed to persist %d devices: %v", len(results), err)
		} else {
			s.logger.Printf("[DISCOVERY] Persisted %d devices", len(results))
		}
	}

	if len(links) > 0 && s.saveLinks != nil {
		if err := s.saveLinks(links); err != nil {
			s.logger.Printf("[DISCOVERY] Failed to persist %d links: %v", len(links), err)
		} else {
			s.logger.Printf("[DISCOVERY] Persisted %d topology links", len(links))
		}
	}

	return len(results)
}

// deepScan runs neighbor discovery for a responsive device and converts each
// neighbor into a TopologyLink with source = the device itself. Uses the same
// LLDP/CDP/ARP logic as the walker via snmpwalker.DiscoverNeighbors.
func (s *Scanner) deepScan(dev DiscoveredDevice, cfg Config) []snmpwalker.TopologyLink {
	wcfg := snmpwalker.WalkConfig{
		Community:        dev.Community,
		Version:          "2c",
		Port:             cfg.Port,
		Timeout:          cfg.Timeout,
		Retries:          cfg.Retries,
		UseIPAsCommunity: false, // the probe already found a working community; reuse it
	}
	neighbors := snmpwalker.DiscoverNeighbors(s.logger, dev.IP, wcfg)
	if len(neighbors) == 0 {
		return nil
	}
	links := make([]snmpwalker.TopologyLink, 0, len(neighbors))
	for _, n := range neighbors {
		targetName := n.Name
		if targetName == "" {
			targetName = n.IP
		}
		links = append(links, snmpwalker.TopologyLink{
			SourceIP:   dev.IP,
			SourceName: dev.Name,
			SourcePort: n.LocalPort,
			TargetIP:   n.IP,
			TargetName: targetName,
			TargetPort: n.RemotePort,
		})
	}
	return links
}

// probe tries each community in order and returns the first that answers.
func (s *Scanner) probe(ip string, cfg Config) (DiscoveredDevice, bool) {
	for _, community := range cfg.Communities {
		community = strings.TrimSpace(community)
		if community == "" {
			continue
		}
		if dev, ok := s.probeOnce(ip, community, cfg); ok {
			return dev, true
		}
	}
	return DiscoveredDevice{}, false
}

func (s *Scanner) probeOnce(ip, community string, cfg Config) (DiscoveredDevice, bool) {
	g := &gosnmp.GoSNMP{
		Target:    ip,
		Port:      cfg.Port,
		Community: community,
		Version:   gosnmp.Version2c,
		Timeout:   cfg.Timeout,
		Retries:   cfg.Retries,
	}
	if err := g.Connect(); err != nil {
		return DiscoveredDevice{}, false
	}
	defer g.Conn.Close()

	result, err := g.Get([]string{oidSysName, oidSysDescr})
	if err != nil || result == nil || len(result.Variables) == 0 {
		return DiscoveredDevice{}, false
	}

	name := ip
	descr := ""
	for _, v := range result.Variables {
		switch v.Name {
		case "." + oidSysName, oidSysName:
			if s := snmpString(v); s != "" {
				name = strings.TrimSpace(s)
			}
		case "." + oidSysDescr, oidSysDescr:
			descr = strings.TrimSpace(snmpString(v))
		}
	}

	return DiscoveredDevice{
		IP:         ip,
		Name:       name,
		SysDescr:   descr,
		DeviceType: guessDeviceType(descr),
		Community:  community,
		Timestamp:  time.Now(),
	}, true
}

// expandCIDR lists every host IP in a CIDR, skipping network/broadcast for /30+.
func expandCIDR(cidr string) ([]string, error) {
	_, network, err := net.ParseCIDR(cidr)
	if err != nil {
		return nil, err
	}
	ones, bits := network.Mask.Size()
	if bits-ones > 16 {
		return nil, fmt.Errorf("prefix too large (max /%d for IPv%d)", bits-16, bits/8)
	}
	var ips []string
	ip := network.IP.Mask(network.Mask)
	skipEdges := bits == 32 && (bits-ones) >= 2
	for network.Contains(ip) {
		cur := make(net.IP, len(ip))
		copy(cur, ip)
		ips = append(ips, cur.String())
		for i := len(ip) - 1; i >= 0; i-- {
			ip[i]++
			if ip[i] != 0 {
				break
			}
		}
	}
	if skipEdges && len(ips) >= 2 {
		ips = ips[1 : len(ips)-1]
	}
	return ips, nil
}

func snmpString(pdu gosnmp.SnmpPDU) string {
	if pdu.Type == gosnmp.OctetString {
		if b, ok := pdu.Value.([]byte); ok {
			return string(b)
		}
	}
	return fmt.Sprintf("%v", pdu.Value)
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
