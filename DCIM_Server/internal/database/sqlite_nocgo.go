//go:build !cgo

package database

import _ "modernc.org/sqlite" // Pure-Go SQLite (no CGO required)
