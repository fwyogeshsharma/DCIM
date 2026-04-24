package snmptrap

import (
	"fmt"
	"log"
	"net"
	"strings"
	"time"

	"github.com/gosnmp/gosnmp"
)

// Standard trap OIDs
const (
	OIDColdStart   = "1.3.6.1.6.3.1.1.5.1"
	OIDWarmStart   = "1.3.6.1.6.3.1.1.5.2"
	OIDLinkDown    = "1.3.6.1.6.3.1.1.5.3"
	OIDLinkUp      = "1.3.6.1.6.3.1.1.5.4"
	OIDAuthFailure = "1.3.6.1.6.3.1.1.5.5"

	// Enterprise OIDs — Cisco EnvMon / resource thresholds
	OIDCiscoTempHigh = "1.3.6.1.4.1.9.9.13.3.0.1"
	OIDCiscoTempLow  = "1.3.6.1.4.1.9.9.13.3.0.2"
	OIDCiscoFanFail  = "1.3.6.1.4.1.9.9.13.3.0.3"
	OIDCiscoPSFail   = "1.3.6.1.4.1.9.9.13.3.0.5"
	OIDCiscoCPUHigh  = "1.3.6.1.4.1.9.9.109.2.0.1"
	OIDCiscoMemHigh  = "1.3.6.1.4.1.9.9.48.2.0.1"
	// RFC 2819 RMON rising/falling threshold alarms
	OIDRmonRising  = "1.3.6.1.2.1.16.8.0.1"
	OIDRmonFalling = "1.3.6.1.2.1.16.8.0.2"
	OIDTrapOIDVar  = "1.3.6.1.6.3.1.1.4.1.0" // snmpTrapOID.0 varbind

	// RFC 3584 proxy: carries the originating agent address in forwarded traps
	OIDSnmpTrapAddress = "1.3.6.1.6.3.18.1.3.0"

	// Interface varbind OID prefixes
	OIDIfIndex       = "1.3.6.1.2.1.2.2.1.1"
	OIDIfDescr       = "1.3.6.1.2.1.2.2.1.2"
	OIDIfAdminStatus = "1.3.6.1.2.1.2.2.1.7"
	OIDIfOperStatus  = "1.3.6.1.2.1.2.2.1.8"
)

// Trap represents a parsed SNMP trap event.
type Trap struct {
	SourceIP    string
	Timestamp   time.Time
	TrapOID     string
	TrapType    string
	Severity    string
	Varbinds    map[string]interface{}
	Description string
}

// Receiver listens for SNMP traps on UDP and calls the handler for each one.
type Receiver struct {
	port    uint16
	logger  *log.Logger
	handler func(Trap)
	tl      *gosnmp.TrapListener
}

// New creates a new trap receiver. handler is called synchronously for each trap.
func New(port uint16, logger *log.Logger, handler func(Trap)) *Receiver {
	return &Receiver{port: port, logger: logger, handler: handler}
}

// Start begins listening in a background goroutine.
func (r *Receiver) Start() error {
	tl := gosnmp.NewTrapListener()
	tl.OnNewTrap = func(packet *gosnmp.SnmpPacket, addr *net.UDPAddr) {
		trap := r.parse(packet, addr)
		r.handler(trap)
	}
	r.tl = tl

	addr := fmt.Sprintf("0.0.0.0:%d", r.port)
	r.logger.Printf("[TRAP] Listening for SNMP traps on UDP %s", addr)

	go func() {
		if err := tl.Listen(addr); err != nil {
			if !strings.Contains(err.Error(), "closed") && !strings.Contains(err.Error(), "use of closed") {
				r.logger.Printf("[TRAP] Listener stopped: %v", err)
			}
		}
	}()

	return nil
}

// Stop shuts down the trap listener.
func (r *Receiver) Stop() {
	if r.tl != nil {
		r.tl.Close()
	}
}

// parse converts a raw gosnmp packet into a Trap.
func (r *Receiver) parse(packet *gosnmp.SnmpPacket, addr *net.UDPAddr) Trap {
	sourceIP := addr.IP.String()
	varbinds := make(map[string]interface{})
	trapOID := ""
	var ipFromVarbind string

	for _, pdu := range packet.Variables {
		oid := strings.TrimPrefix(pdu.Name, ".")

		// snmpTrapOID.0 carries the actual trap OID in SNMPv2c traps
		if oid == OIDTrapOIDVar {
			if s, ok := pdu.Value.(string); ok {
				trapOID = strings.TrimPrefix(s, ".")
			}
			continue
		}

		// RFC 3584 snmpTrapAddress: originating agent IP in proxied/forwarded traps
		if oid == OIDSnmpTrapAddress && ipFromVarbind == "" {
			if ip := fmt.Sprintf("%v", pdu.Value); ip != "" && ip != "0.0.0.0" && !strings.HasPrefix(ip, "127.") {
				ipFromVarbind = ip
			}
		}

		// Capture the first non-loopback IPAddress varbind as a fallback source IP
		if pdu.Type == gosnmp.IPAddress && ipFromVarbind == "" {
			if ip := fmt.Sprintf("%v", pdu.Value); ip != "" && ip != "0.0.0.0" && !strings.HasPrefix(ip, "127.") {
				ipFromVarbind = ip
			}
		}

		varbinds[oid] = formatPDUValue(pdu)
	}

	// SNMPv1 traps encode the OID differently and embed the real device IP in AgentAddress
	if packet.PDUType == gosnmp.Trap {
		if trapOID == "" {
			trapOID = strings.TrimPrefix(packet.Enterprise, ".")
		}
		// AgentAddress is the management IP of the device that generated the trap.
		// Prefer it over the UDP source address which may be a relay/proxy/loopback.
		if a := packet.AgentAddress; a != "" && a != "0.0.0.0" && !strings.HasPrefix(a, "127.") {
			sourceIP = a
		}
	}

	// For traps originating from a local simulator the UDP source is loopback.
	// Walk through progressively looser fallbacks to find the real device IP.
	if strings.HasPrefix(sourceIP, "127.") {
		// 1. IP found in snmpTrapAddress varbind or an IPAddress-typed varbind (set above)
		if ipFromVarbind != "" {
			sourceIP = ipFromVarbind
		} else if community := packet.Community; community != "" {
			// 2. Community string with embedded IP — simulators often send "public@10.0.0.1"
			//    or use the device IP directly as the community string.
			candidate := community
			if idx := strings.LastIndex(community, "@"); idx >= 0 {
				candidate = community[idx+1:]
			}
			if ip := net.ParseIP(strings.TrimSpace(candidate)); ip != nil && !ip.IsLoopback() {
				sourceIP = ip.String()
			}
		}

		// Full packet dump — printed once per loopback trap so we can see exactly
		// what format the simulator uses and add the right extraction.
		r.logger.Printf("[TRAP] loopback source detected — resolvedIP=%s community=%q agentAddr=%q pdType=%v varbinds=%d",
			sourceIP, packet.Community, packet.AgentAddress, packet.PDUType, len(packet.Variables))
		for _, pdu := range packet.Variables {
			r.logger.Printf("[TRAP]   varbind oid=%-45s type=%-15v value=%v",
				strings.TrimPrefix(pdu.Name, "."), pdu.Type, pdu.Value)
		}
	}

	trapType, severity := classifyTrap(trapOID)
	description := buildDescription(trapType, sourceIP, varbinds)

	return Trap{
		SourceIP:    sourceIP,
		Timestamp:   time.Now().UTC().Truncate(time.Second), // second precision for dedup index
		TrapOID:     trapOID,
		TrapType:    trapType,
		Severity:    severity,
		Varbinds:    varbinds,
		Description: description,
	}
}

func classifyTrap(oid string) (trapType, severity string) {
	switch oid {
	case OIDColdStart:
		return "coldStart", "warning"
	case OIDWarmStart:
		return "warmStart", "info"
	case OIDLinkDown:
		return "linkDown", "critical"
	case OIDLinkUp:
		return "linkUp", "info"
	case OIDAuthFailure:
		return "authenticationFailure", "warning"
	case OIDCiscoTempHigh, OIDCiscoTempLow:
		return "highTemperature", "critical"
	case OIDCiscoFanFail:
		return "fanFailure", "warning"
	case OIDCiscoPSFail:
		return "powerAlert", "critical"
	case OIDCiscoCPUHigh:
		return "highCPU", "warning"
	case OIDCiscoMemHigh:
		return "highMemory", "warning"
	case OIDRmonRising:
		return "thresholdRising", "warning"
	case OIDRmonFalling:
		return "thresholdFalling", "info"
	}
	// OID prefix families
	switch {
	case strings.HasPrefix(oid, "1.3.6.1.4.1.9.9.13."):
		return "environmentalAlert", "warning"
	case strings.HasPrefix(oid, "1.3.6.1.4.1.9.9.109."):
		return "highCPU", "warning"
	case strings.HasPrefix(oid, "1.3.6.1.4.1.9.9.48."):
		return "highMemory", "warning"
	case strings.HasPrefix(oid, "1.3.6.1.6.3.1.1.5."):
		return "snmpGenericTrap", "info"
	default:
		return "enterpriseTrap", "info"
	}
}

func buildDescription(trapType, sourceIP string, varbinds map[string]interface{}) string {
	switch trapType {
	case "linkDown":
		if iface := getVarbindStr(varbinds, OIDIfDescr); iface != "" {
			reason := linkDownReason(varbinds)
			return fmt.Sprintf("Interface %s went DOWN on %s%s", iface, sourceIP, reason)
		}
		return fmt.Sprintf("Link DOWN on device %s", sourceIP)
	case "linkUp":
		if iface := getVarbindStr(varbinds, OIDIfDescr); iface != "" {
			return fmt.Sprintf("Interface %s came UP on %s", iface, sourceIP)
		}
		return fmt.Sprintf("Link UP on device %s", sourceIP)
	case "coldStart":
		return fmt.Sprintf("Device %s performed a cold start (power cycle / reboot)", sourceIP)
	case "warmStart":
		return fmt.Sprintf("Device %s performed a warm restart", sourceIP)
	case "authenticationFailure":
		return fmt.Sprintf("SNMP authentication failure on %s", sourceIP)
	case "highTemperature":
		return fmt.Sprintf("Temperature threshold exceeded on %s", sourceIP)
	case "highCPU":
		return fmt.Sprintf("CPU utilization threshold exceeded on %s", sourceIP)
	case "highMemory":
		return fmt.Sprintf("Memory utilization threshold exceeded on %s", sourceIP)
	case "fanFailure":
		return fmt.Sprintf("Fan failure detected on %s", sourceIP)
	case "powerAlert":
		return fmt.Sprintf("Power supply issue on %s", sourceIP)
	case "environmentalAlert":
		return fmt.Sprintf("Environmental alert on %s", sourceIP)
	case "thresholdRising":
		return fmt.Sprintf("RMON rising threshold exceeded on %s", sourceIP)
	case "thresholdFalling":
		return fmt.Sprintf("RMON threshold returned to normal on %s", sourceIP)
	default:
		return fmt.Sprintf("SNMP trap from %s", sourceIP)
	}
}

// linkDownReason infers why an interface went down from varbind values.
func linkDownReason(varbinds map[string]interface{}) string {
	adminStatus := getVarbindStr(varbinds, OIDIfAdminStatus)
	if adminStatus == "2" {
		return " — reason: administratively disabled"
	}
	return " — reason: physical failure / cable issue"
}

// getVarbindStr finds the first varbind whose OID starts with baseOID and returns its string value.
func getVarbindStr(varbinds map[string]interface{}, baseOID string) string {
	for oid, val := range varbinds {
		if strings.HasPrefix(oid, baseOID) {
			return fmt.Sprintf("%v", val)
		}
	}
	return ""
}

func formatPDUValue(pdu gosnmp.SnmpPDU) interface{} {
	switch pdu.Type {
	case gosnmp.OctetString:
		if b, ok := pdu.Value.([]byte); ok {
			return string(b)
		}
	case gosnmp.ObjectIdentifier:
		return strings.TrimPrefix(fmt.Sprintf("%v", pdu.Value), ".")
	case gosnmp.IPAddress:
		return fmt.Sprintf("%v", pdu.Value)
	}
	return pdu.Value
}
