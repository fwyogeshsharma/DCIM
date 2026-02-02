package config

import "time"

// DatacenterConfig contains datacenter metadata configuration
type DatacenterConfig struct {
	// Enable datacenter metadata
	Enabled bool `yaml:"enabled"`

	// Physical Location
	Location   string `yaml:"location"`    // "DC1-Austin", "DC2-London"
	Datacenter string `yaml:"datacenter"`  // Datacenter name/ID
	Building   string `yaml:"building"`    // Building number/name
	Floor      string `yaml:"floor"`       // Floor number
	Room       string `yaml:"room"`        // Room number
	Row        string `yaml:"row"`         // Row identifier (A, B, C)
	Rack       string `yaml:"rack"`        // Rack number (R-10, RACK-42)
	Position   string `yaml:"position"`    // Position in rack (U24-U26)
	Side       string `yaml:"side"`        // Front/Rear/Left/Right

	// Asset Information
	AssetTag     string `yaml:"asset_tag"`     // Asset tag number
	SerialNumber string `yaml:"serial_number"` // Hardware serial
	BarCode      string `yaml:"barcode"`       // Barcode
	OwnerTag     string `yaml:"owner_tag"`     // Owner identifier

	// Organizational
	Owner      string `yaml:"owner"`       // Owner name/team
	Department string `yaml:"department"`  // Department/Division
	CostCenter string `yaml:"cost_center"` // Cost center code
	Project    string `yaml:"project"`     // Project name
	ServiceTag string `yaml:"service_tag"` // Service/support tag

	// Environment
	Environment string `yaml:"environment"` // Production, Staging, Dev, Test
	Purpose     string `yaml:"purpose"`     // Application server, DB server
	Criticality string `yaml:"criticality"` // Critical, High, Medium, Low
	Tier        string `yaml:"tier"`        // Tier 1, Tier 2, Tier 3

	// Networking
	NetworkZone string `yaml:"network_zone"` // DMZ, Internal, Management
	VLANs       []int  `yaml:"vlans"`        // Associated VLANs
	Subnet      string `yaml:"subnet"`       // Network subnet

	// Dates
	PurchaseDate   *time.Time `yaml:"purchase_date"`   // Purchase date
	WarrantyExpiry *time.Time `yaml:"warranty_expiry"` // Warranty end
	InstallDate    *time.Time `yaml:"install_date"`    // Installation date

	// Additional
	Notes string   `yaml:"notes"` // Additional notes
	Tags  []string `yaml:"tags"`  // Custom tags

	// Contact
	PrimaryContact string `yaml:"primary_contact"` // Primary contact
	ContactEmail   string `yaml:"contact_email"`   // Contact email
	ContactPhone   string `yaml:"contact_phone"`   // Contact phone
}

// IsEnabled returns true if datacenter metadata is enabled
func (c *DatacenterConfig) IsEnabled() bool {
	return c.Enabled
}

// HasLocation returns true if any location information is provided
func (c *DatacenterConfig) HasLocation() bool {
	return c.Location != "" || c.Datacenter != "" ||
	       c.Rack != "" || c.Row != ""
}

// GetLocation returns a formatted location string
func (c *DatacenterConfig) GetLocation() string {
	if c.Location != "" {
		return c.Location
	}

	location := ""
	if c.Datacenter != "" {
		location = c.Datacenter
	}
	if c.Row != "" {
		if location != "" {
			location += " / "
		}
		location += "Row " + c.Row
	}
	if c.Rack != "" {
		if location != "" {
			location += " / "
		}
		location += "Rack " + c.Rack
	}
	if c.Position != "" {
		if location != "" {
			location += " / "
		}
		location += c.Position
	}

	if location == "" {
		return "Not Configured"
	}

	return location
}
