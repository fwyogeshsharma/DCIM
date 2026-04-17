// Probe seed — dumps what the SNMP simulator serves for the seed device under
// different community strings, for the same OIDs the walker uses to discover
// neighbors. Run from DCIM_Server root:
//
//   go run ./scripts/probe-seed -ip 10.100.0.4
//
// Flags:
//   -ip         seed IP (default 10.100.0.4)
//   -port       UDP port (default 161)
//   -communities comma-separated list (default "public,private,10.100.0.4")
//   -timeout    per-op timeout (default 5s)

package main

import (
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/gosnmp/gosnmp"
)

type probe struct {
	label string
	oid   string
}

var probes = []probe{
	{"sysName", "1.3.6.1.2.1.1.5.0"},
	{"sysDescr", "1.3.6.1.2.1.1.1.0"},
	{"ifTable (ifDescr)", "1.3.6.1.2.1.2.2.1.2"},
	{"LLDP remSysName", "1.0.8802.1.1.2.1.4.1.1.9"},
	{"LLDP remChassisID", "1.0.8802.1.1.2.1.4.1.1.5"},
	{"LLDP remPortID", "1.0.8802.1.1.2.1.4.1.1.7"},
	{"LLDP mgmtAddr", "1.0.8802.1.1.2.1.4.2.1.3"},
	{"CDP cacheAddress", "1.3.6.1.4.1.9.9.23.1.2.1.1.4"},
	{"ARP (ipNetToMedia)", "1.3.6.1.2.1.4.22.1.2"},
	{"ipRouteNextHop", "1.3.6.1.2.1.4.21.1.7"},
}

func main() {
	ip := flag.String("ip", "10.100.0.4", "target IP")
	port := flag.Int("port", 161, "UDP port")
	commsStr := flag.String("communities", "public,private,10.100.0.4", "communities to try, comma-separated")
	timeout := flag.Duration("timeout", 5*time.Second, "timeout per op")
	flag.Parse()

	communities := strings.Split(*commsStr, ",")

	fmt.Printf("=== Probing %s:%d ===\n\n", *ip, *port)

	for _, c := range communities {
		c = strings.TrimSpace(c)
		fmt.Printf("----- community=%q -----\n", c)
		runCommunity(*ip, *port, c, *timeout)
		fmt.Println()
	}
}

func runCommunity(ip string, port int, community string, timeout time.Duration) {
	g := &gosnmp.GoSNMP{
		Target:    ip,
		Port:      uint16(port),
		Community: community,
		Version:   gosnmp.Version2c,
		Timeout:   timeout,
		Retries:   1,
	}
	if err := g.Connect(); err != nil {
		fmt.Fprintf(os.Stderr, "  connect: %v\n", err)
		return
	}
	defer g.Conn.Close()

	for _, p := range probes {
		n, sample := walk(g, p.oid)
		marker := "  "
		if n == 0 {
			marker = "✗ "
		} else {
			marker = "✓ "
		}
		fmt.Printf("  %s%-22s %4d entries", marker, p.label, n)
		if sample != "" {
			fmt.Printf("   first: %s", sample)
		}
		fmt.Println()
	}
}

func walk(g *gosnmp.GoSNMP, oid string) (int, string) {
	var pdus []gosnmp.SnmpPDU
	var err error
	if g.Version == gosnmp.Version2c || g.Version == gosnmp.Version3 {
		pdus, err = g.BulkWalkAll(oid)
	}
	if err != nil || len(pdus) == 0 {
		pdus, err = g.WalkAll(oid)
	}
	if err != nil || len(pdus) == 0 {
		return 0, ""
	}
	return len(pdus), fmt.Sprintf("%s = %s", pdus[0].Name, valStr(pdus[0]))
}

func valStr(p gosnmp.SnmpPDU) string {
	switch v := p.Value.(type) {
	case []byte:
		return fmt.Sprintf("%q", string(v))
	case string:
		return fmt.Sprintf("%q", v)
	default:
		return fmt.Sprintf("%v", v)
	}
}
