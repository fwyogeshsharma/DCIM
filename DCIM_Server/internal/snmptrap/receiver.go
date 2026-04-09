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
	OIDTrapOIDVar  = "1.3.6.1.6.3.1.1.4.1.0" // snmpTrapOID.0 varbind

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

	for _, pdu := range packet.Variables {
		oid := strings.TrimPrefix(pdu.Name, ".")

		// snmpTrapOID.0 carries the actual trap OID in SNMPv2c traps
		if oid == OIDTrapOIDVar {
			if s, ok := pdu.Value.(string); ok {
				trapOID = strings.TrimPrefix(s, ".")
			}
			continue
		}

		varbinds[oid] = formatPDUValue(pdu)
	}

	// SNMPv1 traps encode the OID differently
	if trapOID == "" && packet.PDUType == gosnmp.Trap {
		trapOID = strings.TrimPrefix(packet.Enterprise, ".")
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
	default:
		if strings.HasPrefix(oid, "1.3.6.1.6.3.1.1.5.") {
			return "snmpGenericTrap", "info"
		}
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
