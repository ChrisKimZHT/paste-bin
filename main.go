package main

import (
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"strconv"
)

const defaultMaxBytes = 128 * 1024
const defaultMaxEntries = 1024

// Override at build time with -ldflags="-X main.version=v1.0.0".
var version = "dev"

type settings struct {
	host       string
	port       string
	maxBytes   int
	maxEntries int
}

func loadSettings() (settings, error) {
	maxBytes, err := strconv.Atoi(env("PASTEBIN_MAX_BYTES", strconv.Itoa(defaultMaxBytes)))
	// Leave room for JSON escaping and the message envelope in the read limit.
	const largestContentLimit = (1<<63 - 1 - messageOverhead) / 6
	if err != nil || maxBytes < 0 || int64(maxBytes) > largestContentLimit {
		return settings{}, fmt.Errorf("PASTEBIN_MAX_BYTES must be a non-negative integer within the supported range")
	}
	maxEntries, err := strconv.Atoi(env("PASTEBIN_MAX_ENTRIES", strconv.Itoa(defaultMaxEntries)))
	if err != nil || maxEntries <= 0 {
		return settings{}, fmt.Errorf("PASTEBIN_MAX_ENTRIES must be a positive integer within the supported range")
	}
	return settings{
		host:       env("PASTEBIN_HOST", "127.0.0.1"),
		port:       env("PASTEBIN_PORT", "8000"),
		maxBytes:   maxBytes,
		maxEntries: maxEntries,
	}, nil
}

func env(name, fallback string) string {
	if value, ok := os.LookupEnv(name); ok {
		return value
	}
	return fallback
}

func main() {
	fmt.Printf("Pastebin %s\n", version)
	config, err := loadSettings()
	if err != nil {
		log.Fatal(err)
	}
	address := net.JoinHostPort(config.host, config.port)
	fmt.Printf("Listening on http://%s\n", address)
	log.Fatal(http.ListenAndServe(address, newServer(config.maxBytes, config.maxEntries)))
}
