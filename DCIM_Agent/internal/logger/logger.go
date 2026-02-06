package logger

import (
	"fmt"
	"io"
	"log"
	"os"
	"strings"

	"github.com/faberlabs/dicm-agent/internal/config"
)

type Level int

const (
	DEBUG Level = iota
	INFO
	WARN
	ERROR
)

type Logger struct {
	level  Level
	logger *log.Logger
	file   *os.File
}

func New(cfg config.LoggingConfig) (*Logger, error) {
	level := parseLevel(cfg.Level)

	var writers []io.Writer
	writers = append(writers, os.Stdout)

	var file *os.File
	if cfg.File != "" {
		var err error
		file, err = os.OpenFile(cfg.File, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
		if err != nil {
			return nil, fmt.Errorf("open log file: %w", err)
		}
		writers = append(writers, file)
	}

	multiWriter := io.MultiWriter(writers...)
	logger := log.New(multiWriter, "", log.LstdFlags|log.Lmicroseconds)

	return &Logger{
		level:  level,
		logger: logger,
		file:   file,
	}, nil
}

func (l *Logger) Close() error {
	if l.file != nil {
		return l.file.Close()
	}
	return nil
}

func (l *Logger) Debugf(format string, v ...interface{}) {
	if l.level <= DEBUG {
		l.logger.Printf("[DEBUG] "+format, v...)
	}
}

func (l *Logger) Infof(format string, v ...interface{}) {
	if l.level <= INFO {
		l.logger.Printf("[INFO] "+format, v...)
	}
}

func (l *Logger) Warnf(format string, v ...interface{}) {
	if l.level <= WARN {
		l.logger.Printf("[WARN] "+format, v...)
	}
}

func (l *Logger) Errorf(format string, v ...interface{}) {
	if l.level <= ERROR {
		l.logger.Printf("[ERROR] "+format, v...)
	}
}

func parseLevel(level string) Level {
	switch strings.ToLower(level) {
	case "debug":
		return DEBUG
	case "info":
		return INFO
	case "warn", "warning":
		return WARN
	case "error":
		return ERROR
	default:
		return INFO
	}
}
