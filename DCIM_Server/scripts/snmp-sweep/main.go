// SNMP sweep — quick parallel probe of a CIDR to find what actually answers
// SNMP and with which community string. Run from DCIM_Server root:
//
//   go run ./scripts/snmp-sweep -cidr 10.100.0.0/24
//
// Flags:
//   -cidr        subnet to scan (default 10.100.0.0/24)
//   -port        UDP port (default 161)
//   -communities comma-separated list (default "public,private")
//   -timeout     per-probe timeout (default 1s)
//   -workers     parallel workers (default 64)
//   -try-self    also try community=<ip> for each IP (default true)

package main

import (
	"flag"
	"fmt"
	"net"
	"strings"
	"sync"
	"time"

	"github.com/gosnmp/gosnmp"
)

func main() {
	cidr := flag.String("cidr", "10.100.0.0/24", "subnet to scan")
	port := flag.Int("port", 161, "UDP port")
	commsStr := flag.String("communities", "public,private", "communities, comma-separated")
	timeout := flag.Duration("timeout", 1*time.Second, "per-probe timeout")
	workers := flag.Int("workers", 64, "parallel workers")
	trySelf := flag.Bool("try-self", true, "also try community=<ip>")
	flag.Parse()

	_, network, err := net.ParseCIDR(*cidr)
	if err != nil {
		fmt.Printf("invalid cidr: %v\n", err)
		return
	}

	var ips []string
	ip := network.IP.Mask(network.Mask)
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
	// Skip network and broadcast
	if len(ips) >= 2 {
		ips = ips[1 : len(ips)-1]
	}

	baseComms := strings.Split(*commsStr, ",")
	for i := range baseComms {
		baseComms[i] = strings.TrimSpace(baseComms[i])
	}

	fmt.Printf("Scanning %d IPs on %s:%d (timeout=%s, workers=%d)\n\n", len(ips), *cidr, *port, *timeout, *workers)
	start := time.Now()

	work := make(chan string, len(ips))
	var wg sync.WaitGroup
	var mu sync.Mutex
	var hits int

	probe := func(target, community string) (string, bool) {
		g := &gosnmp.GoSNMP{
			Target:    target,
			Port:      uint16(*port),
			Community: community,
			Version:   gosnmp.Version2c,
			Timeout:   *timeout,
			Retries:   0,
		}
		if err := g.Connect(); err != nil {
			return "", false
		}
		defer g.Conn.Close()
		r, err := g.Get([]string{"1.3.6.1.2.1.1.5.0"})
		if err != nil || r == nil || len(r.Variables) == 0 {
			return "", false
		}
		v := r.Variables[0]
		if b, ok := v.Value.([]byte); ok {
			return string(b), true
		}
		return fmt.Sprintf("%v", v.Value), true
	}

	for w := 0; w < *workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for target := range work {
				var comms []string
				comms = append(comms, baseComms...)
				if *trySelf {
					comms = append(comms, target)
				}
				for _, c := range comms {
					if c == "" {
						continue
					}
					if name, ok := probe(target, c); ok {
						mu.Lock()
						hits++
						fmt.Printf("  %-16s  community=%-20s  sysName=%q\n", target, c, name)
						mu.Unlock()
						break
					}
				}
			}
		}()
	}

	for _, ip := range ips {
		work <- ip
	}
	close(work)
	wg.Wait()

	fmt.Printf("\nScan complete: %d/%d IPs responded in %s\n", hits, len(ips), time.Since(start).Round(time.Millisecond))
}
