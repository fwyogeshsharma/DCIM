#!/bin/bash
# Check Certificate Expiry
# Checks all certificates for expiry and provides renewal recommendations

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GRAY='\033[0;37m'
NC='\033[0m' # No Color

echo -e "${CYAN}================================"
echo "Certificate Expiry Check"
echo -e "================================${NC}"
echo ""

# Check if OpenSSL is installed
if ! command -v openssl &> /dev/null; then
    echo -e "${RED}ERROR: OpenSSL not found!${NC}"
    echo "Please install OpenSSL to check certificates."
    exit 1
fi

get_certificate_expiry() {
    local cert_path=$1

    if [ ! -f "$cert_path" ]; then
        return 1
    fi

    # Get expiry date
    local expiry_output=$(openssl x509 -in "$cert_path" -noout -enddate 2>&1)
    if [ $? -ne 0 ]; then
        return 1
    fi

    # Parse date (format: notAfter=Jan 27 06:57:44 2036 GMT)
    local date_str=$(echo "$expiry_output" | sed 's/notAfter=//' | sed 's/ GMT//')

    # Convert to epoch time
    local expiry_epoch=$(date -d "$date_str" +%s 2>/dev/null || date -j -f "%b %d %T %Y" "$date_str" +%s 2>/dev/null)
    if [ -z "$expiry_epoch" ]; then
        echo "Warning: Could not parse date '$date_str' for $cert_path" >&2
        return 1
    fi

    local current_epoch=$(date +%s)
    local days_until_expiry=$(( ($expiry_epoch - $current_epoch) / 86400 ))

    # Get certificate subject
    local subject=$(openssl x509 -in "$cert_path" -noout -subject 2>&1 | sed 's/subject=//')

    # Export values for parent shell
    echo "$cert_path|$subject|$expiry_epoch|$days_until_expiry"
}

show_certificate_status() {
    local name=$1
    local cert_info=$2
    local renewal_script=$3

    IFS='|' read -r path subject expiry_epoch days_until_expiry <<< "$cert_info"

    local expiry_date=$(date -d "@$expiry_epoch" '+%Y-%m-%d' 2>/dev/null || date -r "$expiry_epoch" '+%Y-%m-%d' 2>/dev/null)

    echo -e "${CYAN}$name Certificate:${NC}"
    echo -e "  Path: ${GRAY}$path${NC}"

    if [ "$days_until_expiry" -lt 0 ]; then
        local days_expired=$(( -1 * $days_until_expiry ))
        echo -e "  Status: ${RED}EXPIRED${NC}"
        echo -e "  Expired: ${RED}$days_expired days ago${NC}"
        echo -e "  Expiry Date: ${GRAY}$expiry_date${NC}"
        echo -e "  ACTION REQUIRED: ${RED}Renew immediately!${NC}"
        echo -e "  Command: ${YELLOW}$renewal_script${NC}"
    elif [ "$days_until_expiry" -le 30 ]; then
        echo -e "  Status: ${YELLOW}EXPIRING SOON${NC}"
        echo -e "  Days Until Expiry: ${YELLOW}$days_until_expiry days${NC}"
        echo -e "  Expiry Date: ${GRAY}$expiry_date${NC}"
        echo -e "  RECOMMENDED: ${YELLOW}Renew within 7 days${NC}"
        echo -e "  Command: ${YELLOW}$renewal_script${NC}"
    else
        echo -e "  Status: ${GREEN}Valid${NC}"
        echo -e "  Days Until Expiry: ${GREEN}$days_until_expiry days${NC}"
        echo -e "  Expiry Date: ${GRAY}$expiry_date${NC}"

        local renewal_date=$(date -d "@$(($expiry_epoch - 2592000))" '+%Y-%m-%d' 2>/dev/null || date -r "$(($expiry_epoch - 2592000))" '+%Y-%m-%d' 2>/dev/null)
        echo -e "  Renewal Reminder: ${GRAY}Set for $renewal_date${NC}"
    fi

    echo ""
}

# Check CA certificate
if [ -f "../../certs/ca.crt" ]; then
    CA_CERT=$(get_certificate_expiry "../../certs/ca.crt")
    if [ -n "$CA_CERT" ]; then
        show_certificate_status "CA" "$CA_CERT" "./generate-certs.sh"
    fi
fi

# Check server certificate
if [ -f "../../certs/server.crt" ]; then
    SERVER_CERT=$(get_certificate_expiry "../../certs/server.crt")
    if [ -n "$SERVER_CERT" ]; then
        show_certificate_status "Server" "$SERVER_CERT" "./renew-server-cert.sh"
    fi
fi

# Check client certificate
if [ -f "../../certs/client.crt" ]; then
    CLIENT_CERT=$(get_certificate_expiry "../../certs/client.crt")
    if [ -n "$CLIENT_CERT" ]; then
        show_certificate_status "Client" "$CLIENT_CERT" "./renew-client-cert.sh -AgentName <agent-name>"
    fi
fi

# Check agent certificates
if [ -d "../../certs/agents" ]; then
    AGENT_DIRS=$(find "../../certs/agents" -mindepth 1 -maxdepth 1 -type d 2>/dev/null || true)

    if [ -n "$AGENT_DIRS" ]; then
        echo -e "${CYAN}Agent Certificates:${NC}"
        echo ""

        while IFS= read -r agent_dir; do
            agent_name=$(basename "$agent_dir")
            if [ -f "$agent_dir/client.crt" ]; then
                AGENT_CERT=$(get_certificate_expiry "$agent_dir/client.crt")
                if [ -n "$AGENT_CERT" ]; then
                    show_certificate_status "  $agent_name" "$AGENT_CERT" "./renew-client-cert.sh -AgentName $agent_name"
                fi
            fi
        done <<< "$AGENT_DIRS"
    fi
fi

echo -e "${CYAN}================================"
echo "Summary"
echo -e "================================${NC}"
echo ""

# Count certificates by status
TOTAL=0
VALID=0
EXPIRING_SOON=0
EXPIRED=0

count_cert() {
    local cert_info=$1
    if [ -z "$cert_info" ]; then
        return
    fi

    IFS='|' read -r path subject expiry_epoch days_until_expiry <<< "$cert_info"
    TOTAL=$((TOTAL + 1))

    if [ "$days_until_expiry" -lt 0 ]; then
        EXPIRED=$((EXPIRED + 1))
    elif [ "$days_until_expiry" -le 30 ]; then
        EXPIRING_SOON=$((EXPIRING_SOON + 1))
    else
        VALID=$((VALID + 1))
    fi
}

[ -n "$CA_CERT" ] && count_cert "$CA_CERT"
[ -n "$SERVER_CERT" ] && count_cert "$SERVER_CERT"
[ -n "$CLIENT_CERT" ] && count_cert "$CLIENT_CERT"

echo "Total Certificates: $TOTAL"
echo -e "  Valid: ${GREEN}$VALID${NC}"
echo -e "  Expiring Soon (< 30 days): ${YELLOW}$EXPIRING_SOON${NC}"
echo -e "  Expired: ${RED}$EXPIRED${NC}"
echo ""

if [ "$EXPIRED" -gt 0 ]; then
    echo -e "${RED}ACTION REQUIRED: $EXPIRED certificate(s) have expired!${NC}"
    exit 1
elif [ "$EXPIRING_SOON" -gt 0 ]; then
    echo -e "${YELLOW}WARNING: $EXPIRING_SOON certificate(s) expiring soon!${NC}"
    exit 0
else
    echo -e "${GREEN}All certificates are valid.${NC}"
    exit 0
fi
