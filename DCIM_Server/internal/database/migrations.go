package database

import (
	"fmt"
	"io/fs"
	"log"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// Migration represents a database migration
type Migration struct {
	Version     int
	Name        string
	SQL         string
	AppliedAt   time.Time
	IsApplied   bool
}

// RunMigrations executes all pending database migrations
func (d *Database) RunMigrations(migrationsPath string) error {
	// Create schema_migrations table if it doesn't exist
	if err := d.createMigrationsTable(); err != nil {
		return fmt.Errorf("failed to create migrations table: %v", err)
	}

	// Get list of applied migrations
	appliedMigrations, err := d.getAppliedMigrations()
	if err != nil {
		return fmt.Errorf("failed to get applied migrations: %v", err)
	}

	// Read migration files from disk
	availableMigrations, err := d.readMigrationFiles(migrationsPath)
	if err != nil {
		return fmt.Errorf("failed to read migration files: %v", err)
	}

	// Mark which migrations have been applied
	for i := range availableMigrations {
		if appliedAt, exists := appliedMigrations[availableMigrations[i].Version]; exists {
			availableMigrations[i].IsApplied = true
			availableMigrations[i].AppliedAt = appliedAt
		}
	}

	// Sort migrations by version
	sort.Slice(availableMigrations, func(i, j int) bool {
		return availableMigrations[i].Version < availableMigrations[j].Version
	})

	// Run pending migrations
	pendingCount := 0
	for _, migration := range availableMigrations {
		if !migration.IsApplied {
			pendingCount++
		}
	}

	if pendingCount == 0 {
		log.Printf("[MIGRATIONS] All migrations up to date (%d total)", len(availableMigrations))
		return nil
	}

	log.Printf("[MIGRATIONS] Found %d pending migrations (out of %d total)", pendingCount, len(availableMigrations))

	for _, migration := range availableMigrations {
		if !migration.IsApplied {
			log.Printf("[MIGRATIONS] Running migration %03d: %s", migration.Version, migration.Name)
			if err := d.applyMigration(migration); err != nil {
				return fmt.Errorf("failed to apply migration %03d (%s): %v", migration.Version, migration.Name, err)
			}
			log.Printf("[MIGRATIONS] ✓ Migration %03d completed successfully", migration.Version)
		}
	}

	log.Printf("[MIGRATIONS] ✓ All migrations completed successfully")
	return nil
}

// createMigrationsTable creates the schema_migrations table
func (d *Database) createMigrationsTable() error {
	query := `
		CREATE TABLE IF NOT EXISTS schema_migrations (
			version INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			applied_at TIMESTAMP NOT NULL DEFAULT NOW()
		)
	`

	_, err := d.db.Exec(query)
	return err
}

// getAppliedMigrations returns a map of applied migration versions to their applied timestamps
func (d *Database) getAppliedMigrations() (map[int]time.Time, error) {
	query := `SELECT version, applied_at FROM schema_migrations ORDER BY version`

	rows, err := d.db.Query(query)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	applied := make(map[int]time.Time)
	for rows.Next() {
		var version int
		var appliedAt time.Time
		if err := rows.Scan(&version, &appliedAt); err != nil {
			return nil, err
		}
		applied[version] = appliedAt
	}

	return applied, rows.Err()
}

// readMigrationFiles reads all .sql files from the migrations directory
func (d *Database) readMigrationFiles(migrationsPath string) ([]Migration, error) {
	// Check if migrations directory exists
	if _, err := os.Stat(migrationsPath); os.IsNotExist(err) {
		return nil, fmt.Errorf("migrations directory does not exist: %s", migrationsPath)
	}

	var migrations []Migration

	// Read all .sql files in the directory
	err := filepath.WalkDir(migrationsPath, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}

		// Skip directories and non-SQL files
		if d.IsDir() || !strings.HasSuffix(d.Name(), ".sql") {
			return nil
		}

		// Parse migration file name (expected format: 001_migration_name.sql)
		version, name, err := parseMigrationFilename(d.Name())
		if err != nil {
			log.Printf("[MIGRATIONS] Warning: skipping invalid migration file %s: %v", d.Name(), err)
			return nil
		}

		// Read migration SQL
		sqlBytes, err := os.ReadFile(path)
		if err != nil {
			return fmt.Errorf("failed to read migration file %s: %v", path, err)
		}

		migrations = append(migrations, Migration{
			Version: version,
			Name:    name,
			SQL:     string(sqlBytes),
		})

		return nil
	})

	if err != nil {
		return nil, err
	}

	return migrations, nil
}

// parseMigrationFilename extracts version and name from migration filename
// Expected format: 001_migration_name.sql
func parseMigrationFilename(filename string) (int, string, error) {
	// Remove .sql extension
	name := strings.TrimSuffix(filename, ".sql")

	// Split on first underscore
	parts := strings.SplitN(name, "_", 2)
	if len(parts) != 2 {
		return 0, "", fmt.Errorf("invalid migration filename format (expected: 001_name.sql)")
	}

	// Parse version number
	var version int
	_, err := fmt.Sscanf(parts[0], "%d", &version)
	if err != nil {
		return 0, "", fmt.Errorf("invalid version number: %v", err)
	}

	return version, parts[1], nil
}

// applyMigration executes a migration and records it in schema_migrations
func (d *Database) applyMigration(migration Migration) error {
	// Start transaction
	tx, err := d.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()

	// Execute migration SQL
	_, err = tx.Exec(migration.SQL)
	if err != nil {
		return err
	}

	// Record migration in schema_migrations table
	_, err = tx.Exec(
		`INSERT INTO schema_migrations (version, name, applied_at) VALUES ($1, $2, NOW())`,
		migration.Version,
		migration.Name,
	)
	if err != nil {
		return err
	}

	// Commit transaction
	if err := tx.Commit(); err != nil {
		return err
	}

	return nil
}

// GetMigrationStatus returns the current migration status
func (d *Database) GetMigrationStatus() ([]Migration, error) {
	appliedMigrations, err := d.getAppliedMigrations()
	if err != nil {
		return nil, err
	}

	// Get executable directory for migrations path
	exePath, err := os.Executable()
	if err != nil {
		return nil, err
	}
	exeDir := filepath.Dir(exePath)
	migrationsPath := filepath.Join(exeDir, "migrations")

	availableMigrations, err := d.readMigrationFiles(migrationsPath)
	if err != nil {
		return nil, err
	}

	// Mark which migrations have been applied
	for i := range availableMigrations {
		if appliedAt, exists := appliedMigrations[availableMigrations[i].Version]; exists {
			availableMigrations[i].IsApplied = true
			availableMigrations[i].AppliedAt = appliedAt
		}
	}

	// Sort by version
	sort.Slice(availableMigrations, func(i, j int) bool {
		return availableMigrations[i].Version < availableMigrations[j].Version
	})

	return availableMigrations, nil
}
