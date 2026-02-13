#!/bin/bash
# Setup PostgreSQL for DCIM Server
# Creates database and verifies configuration

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GRAY='\033[0;37m'
NC='\033[0m' # No Color

echo -e "${CYAN}================================"
echo "PostgreSQL Setup for DCIM Server"
echo -e "================================${NC}"
echo ""

# Configuration from config.yaml
DB_NAME="dcim_db"
DB_USER="postgres"
DB_HOST="localhost"
DB_PORT=5432

# Step 1: Check if PostgreSQL is installed
echo -e "${YELLOW}Step 1: Checking PostgreSQL installation...${NC}"

if command -v psql &> /dev/null; then
    VERSION=$(psql --version)
    echo -e "   ${GREEN}[OK] PostgreSQL installed: $VERSION${NC}"
else
    echo -e "   ${RED}[ERROR] PostgreSQL not found!${NC}"
    echo ""
    echo -e "   ${YELLOW}Install PostgreSQL:${NC}"
    echo "   Linux (Debian/Ubuntu): sudo apt-get install postgresql postgresql-client"
    echo "   Linux (RHEL/CentOS):   sudo yum install postgresql-server postgresql"
    echo "   macOS:                 brew install postgresql"
    echo "   Windows:               Download from https://www.postgresql.org/download/windows/"
    echo ""
    exit 1
fi

echo ""

# Step 2: Check if PostgreSQL service is running
echo -e "${YELLOW}Step 2: Checking PostgreSQL service...${NC}"

if command -v systemctl &> /dev/null; then
    if systemctl is-active --quiet postgresql 2>/dev/null || systemctl is-active --quiet postgresql@* 2>/dev/null; then
        echo -e "   ${GREEN}[OK] PostgreSQL service is running${NC}"
    else
        echo -e "   ${YELLOW}[WARNING] PostgreSQL service is not running${NC}"
        echo "   Attempting to start service..."
        if sudo systemctl start postgresql 2>/dev/null; then
            echo -e "   ${GREEN}[OK] Service started${NC}"
        else
            echo -e "   ${YELLOW}[WARNING] Could not start service. You may need to start it manually.${NC}"
        fi
    fi
elif command -v brew &> /dev/null && brew services list | grep -q postgresql; then
    if brew services list | grep postgresql | grep -q started; then
        echo -e "   ${GREEN}[OK] PostgreSQL service is running${NC}"
    else
        echo -e "   ${YELLOW}[WARNING] PostgreSQL service is not running${NC}"
        echo "   Start with: brew services start postgresql"
    fi
else
    echo -e "   ${GRAY}[INFO] Cannot check service status (systemctl/brew not available)${NC}"
    echo "   Assuming PostgreSQL is running..."
fi

echo ""

# Step 3: Test connection to PostgreSQL
echo -e "${YELLOW}Step 3: Testing PostgreSQL connection...${NC}"
echo -e "   ${GRAY}Enter PostgreSQL password for user '$DB_USER'${NC}"

if psql -U "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" -c "SELECT version();" 2>/dev/null >/dev/null; then
    echo -e "   ${GREEN}[OK] Connected to PostgreSQL${NC}"
else
    echo -e "   ${RED}[ERROR] Connection failed${NC}"
    echo -e "   ${YELLOW}Check username and password in config.yaml${NC}"
    echo ""
    echo "   If you need to reset the password:"
    echo "   1. sudo -u postgres psql"
    echo "   2. ALTER USER postgres PASSWORD 'your_password';"
    echo ""
    exit 1
fi

echo ""

# Step 4: Check if database exists
echo -e "${YELLOW}Step 4: Checking if database '$DB_NAME' exists...${NC}"

if psql -U "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw "$DB_NAME"; then
    echo -e "   ${GREEN}[OK] Database '$DB_NAME' already exists${NC}"

    # Ask if user wants to recreate
    echo ""
    read -p "Do you want to DROP and recreate the database? (This will DELETE all data) [y/N]: " recreate
    if [ "$recreate" = "y" ] || [ "$recreate" = "Y" ]; then
        echo -e "   ${YELLOW}[WARNING] Dropping database...${NC}"
        if psql -U "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" -c "DROP DATABASE $DB_NAME;" 2>/dev/null; then
            echo -e "   ${GREEN}[OK] Database dropped${NC}"
            DB_EXISTS=false
        else
            echo -e "   ${RED}[ERROR] Failed to drop database${NC}"
            exit 1
        fi
    else
        DB_EXISTS=true
    fi
else
    DB_EXISTS=false
fi

# Step 5: Create database if it doesn't exist
if [ "$DB_EXISTS" = false ]; then
    echo ""
    echo -e "${YELLOW}Step 5: Creating database '$DB_NAME'...${NC}"

    if psql -U "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" -c "CREATE DATABASE $DB_NAME;" 2>/dev/null; then
        echo -e "   ${GREEN}[OK] Database '$DB_NAME' created successfully${NC}"
    else
        echo -e "   ${RED}[ERROR] Failed to create database${NC}"
        exit 1
    fi
fi

echo ""

# Step 6: Verify database connection
echo -e "${YELLOW}Step 6: Verifying database connection...${NC}"

if psql -U "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" -d "$DB_NAME" -c "SELECT current_database();" 2>/dev/null >/dev/null; then
    echo -e "   ${GREEN}[OK] Successfully connected to '$DB_NAME'${NC}"
else
    echo -e "   ${RED}[ERROR] Failed to connect to database${NC}"
    exit 1
fi

echo ""

# Step 7: Check config.yaml
echo -e "${YELLOW}Step 7: Checking config.yaml...${NC}"

if [ ! -f "config.yaml" ]; then
    echo -e "   ${RED}[ERROR] config.yaml not found!${NC}"
    exit 1
fi

# Check database type
if grep -q 'type:.*"postgres"' config.yaml || grep -q "type:.*'postgres'" config.yaml; then
    echo -e "   ${GREEN}[OK] Database type set to 'postgres'${NC}"
else
    echo -e "   ${YELLOW}[WARNING] Database type is not 'postgres'${NC}"
    echo -e "   ${YELLOW}Update config.yaml: database.type: 'postgres'${NC}"
fi

# Check SSL mode
if grep -q 'sslmode:.*"require"' config.yaml || grep -q "sslmode:.*'require'" config.yaml; then
    echo -e "   ${YELLOW}[WARNING] SSL mode is set to 'require'${NC}"
    echo -e "   ${YELLOW}For local development, change to 'disable' in config.yaml:${NC}"
    echo -e "   ${GRAY}database.postgres.sslmode: 'disable'${NC}"
    echo ""
    read -p "Do you want to change sslmode to 'disable' now? [Y/n]: " change_ssl
    if [ "$change_ssl" != "n" ] && [ "$change_ssl" != "N" ]; then
        sed -i.bak 's/sslmode:.*"require"/sslmode: "disable"/' config.yaml 2>/dev/null || \
        sed -i '' 's/sslmode:.*"require"/sslmode: "disable"/' config.yaml 2>/dev/null
        echo -e "   ${GREEN}[OK] Updated sslmode to 'disable'${NC}"
    fi
elif grep -q 'sslmode:.*"disable"' config.yaml || grep -q "sslmode:.*'disable'" config.yaml; then
    echo -e "   ${GREEN}[OK] SSL mode set to 'disable' (good for local dev)${NC}"
else
    echo -e "   ${GRAY}[INFO] SSL mode not found in config${NC}"
fi

echo ""

# Step 8: Summary
echo -e "${GREEN}================================"
echo "Setup Complete!"
echo -e "================================${NC}"
echo ""
echo -e "${CYAN}Database Configuration:${NC}"
echo "  Database: $DB_NAME"
echo "  Host: $DB_HOST"
echo "  Port: $DB_PORT"
echo "  User: $DB_USER"
echo ""
echo -e "${CYAN}Next Steps:${NC}"
echo "  1. Review config.yaml settings"
echo "  2. Build the server (if needed):"
echo -e "     ${GRAY}./build.sh${NC}"
echo "  3. Run the DCIM Server:"
echo -e "     ${GRAY}./build/linux-amd64/dcim-server${NC}"
echo ""
echo -e "${YELLOW}The server will automatically:${NC}"
echo "  - Create all tables (agents, metrics, alerts, etc.)"
echo "  - Create indexes"
echo "  - Initialize the schema"
echo ""
echo -e "${YELLOW}Data Persistence:${NC}"
echo -e "  ${GREEN}All data will persist across server restarts${NC}"
echo ""
echo -e "${CYAN}To verify tables after server starts:${NC}"
echo -e "  ${GRAY}psql -U $DB_USER -d $DB_NAME -c \"\\dt\"${NC}"
echo ""
