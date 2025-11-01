#!/bin/bash
# MIRA Database Deployment Script
# Deploys mira_service database with unified schema
# For fresh PostgreSQL installations

set -e  # Exit on any error

echo "==================================================================="
echo "=== MIRA Database Deployment                                    ==="
echo "==================================================================="
echo ""

# =====================================================================
# STEP 1: Find PostgreSQL superuser
# =====================================================================

echo "Step 1: Detecting PostgreSQL superuser..."

# Check if current user is a superuser
CURRENT_USER=$(whoami)
IS_SUPERUSER=$(psql -U $CURRENT_USER -h localhost -d postgres -tAc "SELECT COUNT(*) FROM pg_roles WHERE rolname = '$CURRENT_USER' AND rolsuper = true" 2>/dev/null || echo "0")

if [ "$IS_SUPERUSER" = "1" ]; then
    SUPERUSER=$CURRENT_USER
    echo "✓ Using current user as superuser: $SUPERUSER"
else
    # Try to find a superuser
    SUPERUSER=$(psql -U $CURRENT_USER -h localhost -d postgres -tAc "SELECT rolname FROM pg_roles WHERE rolsuper = true LIMIT 1" 2>/dev/null || echo "")

    if [ -z "$SUPERUSER" ]; then
        echo "✗ Error: No PostgreSQL superuser found"
        echo "Please run as a PostgreSQL superuser or specify one manually"
        exit 1
    fi

    echo "✓ Using detected superuser: $SUPERUSER"
fi

# =====================================================================
# STEP 2: Check if mira_service already exists
# =====================================================================

echo ""
echo "Step 2: Checking for existing mira_service database..."

if psql -U $SUPERUSER -h localhost -lqt | cut -d \| -f 1 | grep -qw mira_service; then
    echo "✗ Error: mira_service database already exists"
    echo "Please drop it first: dropdb -U $SUPERUSER mira_service"
    exit 1
else
    echo "✓ No existing mira_service database found"
fi

# =====================================================================
# STEP 3: Deploy clean schema
# =====================================================================

echo ""
echo "Step 3: Deploying mira_service schema..."

psql -U $SUPERUSER -h localhost -d postgres -f deploy/mira_service_schema.sql > /dev/null 2>&1

if [ $? -eq 0 ]; then
    echo "✓ Schema deployed successfully"
else
    echo "✗ Schema deployment failed"
    exit 1
fi

# =====================================================================
# STEP 4: Verify deployment
# =====================================================================

echo ""
echo "Step 4: Verifying deployment..."

# Check database exists
DB_EXISTS=$(psql -U $SUPERUSER -h localhost -lqt | cut -d \| -f 1 | grep -w mira_service | wc -l)
if [ "$DB_EXISTS" -eq "1" ]; then
    echo "✓ Database mira_service created"
else
    echo "✗ Database mira_service not found"
    exit 1
fi

# Check roles
echo ""
echo "Roles created:"
psql -U $SUPERUSER -h localhost -d postgres -c "
SELECT
    rolname,
    rolsuper as superuser,
    rolcreaterole as create_role,
    rolcreatedb as create_db,
    CASE
        WHEN rolname = 'mira_admin' THEN 'Database owner (migrations, schema)'
        WHEN rolname = 'mira_dbuser' THEN 'Application runtime (data only)'
    END as purpose
FROM pg_roles
WHERE rolname IN ('mira_admin', 'mira_dbuser')
ORDER BY rolname;
" 2>/dev/null

# Check tables
echo ""
echo "Tables created:"
psql -U $SUPERUSER -h localhost -d mira_service -c "
SELECT schemaname, tablename
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;
" 2>/dev/null | head -20

# Count rows (should all be 0 on fresh install)
echo ""
echo "Table row counts (should be 0):"
psql -U $SUPERUSER -h localhost -d mira_service -c "
SELECT 'users' as table, COUNT(*) FROM users
UNION ALL SELECT 'continuums', COUNT(*) FROM continuums
UNION ALL SELECT 'messages', COUNT(*) FROM messages
UNION ALL SELECT 'memories', COUNT(*) FROM memories
UNION ALL SELECT 'entities', COUNT(*) FROM entities;
" 2>/dev/null

echo ""
echo "==================================================================="
echo "✓ Database deployment complete!"
echo ""
echo "Next steps:"
echo "1. Add to Vault (mira/database):"
echo "   service_url: postgresql://mira_dbuser:PASSWORD@localhost:5432/mira_service"
echo "   username: mira_admin"
echo "   password: new_secure_password_2024"
echo ""
echo "2. Update application config to use mira_service"
echo "3. Start the application: python main.py"
echo "==================================================================="
