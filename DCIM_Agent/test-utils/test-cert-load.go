package main

import (
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"os"
)

func main() {
	fmt.Println("Testing Certificate Loading...")
	fmt.Println("=" + string(make([]byte, 60)))

	// Test 1: Load client certificate
	fmt.Println("\n1. Loading client certificate and key...")
	cert, err := tls.LoadX509KeyPair("./certs/client.crt", "./certs/client.key")
	if err != nil {
		fmt.Printf("   ERROR: Failed to load client certificate: %v\n", err)
		os.Exit(1)
	}
	fmt.Println("   ✓ Client certificate and key loaded successfully")

	// Parse and display client certificate details
	if len(cert.Certificate) > 0 {
		clientCert, err := x509.ParseCertificate(cert.Certificate[0])
		if err == nil {
			fmt.Printf("   - Subject: %s\n", clientCert.Subject.CommonName)
			fmt.Printf("   - Issuer: %s\n", clientCert.Issuer.CommonName)
			fmt.Printf("   - Valid: %s to %s\n",
				clientCert.NotBefore.Format("2006-01-02"),
				clientCert.NotAfter.Format("2006-01-02"))
		}
	}

	// Test 2: Load CA certificate
	fmt.Println("\n2. Loading CA certificate...")
	caCert, err := os.ReadFile("./certs/ca.crt")
	if err != nil {
		fmt.Printf("   ERROR: Failed to read CA certificate: %v\n", err)
		os.Exit(1)
	}
	fmt.Println("   ✓ CA certificate file read successfully")

	caCertPool := x509.NewCertPool()
	if !caCertPool.AppendCertsFromPEM(caCert) {
		fmt.Println("   ERROR: Failed to parse CA certificate")
		os.Exit(1)
	}
	fmt.Println("   ✓ CA certificate parsed successfully")

	// Test 3: Create TLS config
	fmt.Println("\n3. Creating TLS configuration...")
	_ = &tls.Config{
		Certificates: []tls.Certificate{cert},
		RootCAs:      caCertPool,
		MinVersion:   tls.VersionTLS12,
	}
	fmt.Println("   ✓ TLS configuration created successfully")

	// Test 4: Verify certificate chain
	fmt.Println("\n4. Verifying certificate chain...")
	if len(cert.Certificate) > 0 {
		clientCert, _ := x509.ParseCertificate(cert.Certificate[0])
		opts := x509.VerifyOptions{
			Roots: caCertPool,
		}
		if _, err := clientCert.Verify(opts); err != nil {
			fmt.Printf("   ERROR: Certificate chain verification failed: %v\n", err)
			os.Exit(1)
		}
		fmt.Println("   ✓ Certificate chain verified successfully")
	}

	fmt.Println("\n" + string(make([]byte, 60)))
	fmt.Println("All tests passed! Certificates are valid and loadable.")
}
