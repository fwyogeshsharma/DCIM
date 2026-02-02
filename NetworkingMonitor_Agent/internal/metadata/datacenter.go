package metadata

import (
	"fmt"
	"time"
)

// DatacenterMetadata contains physical location and asset information
type DatacenterMetadata struct {
	// Physical Location
	Location    string `json:"location" yaml:"location"`         // "DC1-Austin", "DC2-London"
	Datacenter  string `json:"datacenter" yaml:"datacenter"`     // Datacenter name/ID
	Building    string `json:"building" yaml:"building"`         // Building number/name
	Floor       string `json:"floor" yaml:"floor"`               // Floor number
	Room        string `json:"room" yaml:"room"`                 // Room number
	Row         string `json:"row" yaml:"row"`                   // Row identifier (A, B, C, etc.)
	Rack        string `json:"rack" yaml:"rack"`                 // Rack number (R-10, RACK-42)
	Position    string `json:"position" yaml:"position"`         // Position in rack (U24-U26, U1-U2)
	Side        string `json:"side" yaml:"side"`                 // Front/Rear/Left/Right

	// Asset Information
	AssetTag    string `json:"asset_tag" yaml:"asset_tag"`       // Asset tag number
	SerialNumber string `json:"serial_number" yaml:"serial_number"` // Hardware serial
	BarCode     string `json:"barcode" yaml:"barcode"`           // Barcode for inventory
	OwnerTag    string `json:"owner_tag" yaml:"owner_tag"`       // Owner identifier

	// Organizational
	Owner       string `json:"owner" yaml:"owner"`               // Owner name/team
	Department  string `json:"department" yaml:"department"`     // Department/Division
	CostCenter  string `json:"cost_center" yaml:"cost_center"`   // Cost center code
	Project     string `json:"project" yaml:"project"`           // Project name
	ServiceTag  string `json:"service_tag" yaml:"service_tag"`   // Service/support tag

	// Environment
	Environment string `json:"environment" yaml:"environment"`   // Production, Staging, Dev, Test
	Purpose     string `json:"purpose" yaml:"purpose"`           // Application server, DB server, etc.
	Criticality string `json:"criticality" yaml:"criticality"`   // Critical, High, Medium, Low
	Tier        string `json:"tier" yaml:"tier"`                 // Tier 1, Tier 2, Tier 3

	// Networking
	NetworkZone string `json:"network_zone" yaml:"network_zone"` // DMZ, Internal, Management
	VLANs       []int  `json:"vlans" yaml:"vlans"`               // Associated VLANs
	Subnet      string `json:"subnet" yaml:"subnet"`             // Network subnet

	// Additional Information
	PurchaseDate   *time.Time `json:"purchase_date" yaml:"purchase_date"`     // Purchase date
	WarrantyExpiry *time.Time `json:"warranty_expiry" yaml:"warranty_expiry"` // Warranty end date
	InstallDate    *time.Time `json:"install_date" yaml:"install_date"`       // Installation date
	Notes          string     `json:"notes" yaml:"notes"`                     // Additional notes
	Tags           []string   `json:"tags" yaml:"tags"`                       // Custom tags

	// Contact Information
	PrimaryContact   string `json:"primary_contact" yaml:"primary_contact"`     // Primary contact name
	ContactEmail     string `json:"contact_email" yaml:"contact_email"`         // Contact email
	ContactPhone     string `json:"contact_phone" yaml:"contact_phone"`         // Contact phone

	// Metadata
	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
}

// DatacenterManager manages datacenter metadata
type DatacenterManager struct {
	metadata *DatacenterMetadata
}

// NewDatacenterManager creates a new datacenter metadata manager
func NewDatacenterManager(metadata *DatacenterMetadata) *DatacenterManager {
	if metadata == nil {
		metadata = &DatacenterMetadata{}
	}

	now := time.Now()
	if metadata.CreatedAt.IsZero() {
		metadata.CreatedAt = now
	}
	metadata.UpdatedAt = now

	return &DatacenterManager{
		metadata: metadata,
	}
}

// GetMetadata returns the datacenter metadata
func (m *DatacenterManager) GetMetadata() *DatacenterMetadata {
	return m.metadata
}

// UpdateMetadata updates the datacenter metadata
func (m *DatacenterManager) UpdateMetadata(metadata *DatacenterMetadata) {
	if metadata != nil {
		metadata.UpdatedAt = time.Now()
		if metadata.CreatedAt.IsZero() {
			metadata.CreatedAt = m.metadata.CreatedAt
		}
		m.metadata = metadata
	}
}

// GetPhysicalLocation returns formatted physical location string
func (m *DatacenterManager) GetPhysicalLocation() string {
	metadata := m.metadata

	if metadata.Location != "" {
		return metadata.Location
	}

	// Build location string from components
	location := ""

	if metadata.Datacenter != "" {
		location += metadata.Datacenter
	}

	if metadata.Building != "" {
		if location != "" {
			location += " / "
		}
		location += "Building " + metadata.Building
	}

	if metadata.Floor != "" {
		if location != "" {
			location += " / "
		}
		location += "Floor " + metadata.Floor
	}

	if metadata.Room != "" {
		if location != "" {
			location += " / "
		}
		location += "Room " + metadata.Room
	}

	if metadata.Row != "" {
		if location != "" {
			location += " / "
		}
		location += "Row " + metadata.Row
	}

	if metadata.Rack != "" {
		if location != "" {
			location += " / "
		}
		location += "Rack " + metadata.Rack
	}

	if metadata.Position != "" {
		if location != "" {
			location += " / "
		}
		location += metadata.Position
	}

	if location == "" {
		return "Unknown Location"
	}

	return location
}

// GetRackPosition returns formatted rack position
func (m *DatacenterManager) GetRackPosition() string {
	metadata := m.metadata

	if metadata.Rack == "" {
		return "N/A"
	}

	position := metadata.Rack

	if metadata.Row != "" {
		position = "Row " + metadata.Row + ", " + position
	}

	if metadata.Position != "" {
		position += " (" + metadata.Position + ")"
	}

	return position
}

// IsProduction returns true if environment is production
func (m *DatacenterManager) IsProduction() bool {
	env := m.metadata.Environment
	return env == "Production" || env == "production" || env == "PRODUCTION" || env == "prod"
}

// IsCritical returns true if criticality is Critical or High
func (m *DatacenterManager) IsCritical() bool {
	crit := m.metadata.Criticality
	return crit == "Critical" || crit == "critical" ||
	       crit == "High" || crit == "high"
}

// HasWarranty returns true if warranty is still valid
func (m *DatacenterManager) HasWarranty() bool {
	if m.metadata.WarrantyExpiry == nil {
		return false
	}
	return time.Now().Before(*m.metadata.WarrantyExpiry)
}

// DaysUntilWarrantyExpiry returns days until warranty expires
func (m *DatacenterManager) DaysUntilWarrantyExpiry() int {
	if m.metadata.WarrantyExpiry == nil {
		return -1
	}

	days := int(time.Until(*m.metadata.WarrantyExpiry).Hours() / 24)
	return days
}

// GetAge returns the age of the system since installation
func (m *DatacenterManager) GetAge() time.Duration {
	if m.metadata.InstallDate == nil {
		return 0
	}
	return time.Since(*m.metadata.InstallDate)
}

// FormatAge returns human-readable age
func (m *DatacenterManager) FormatAge() string {
	age := m.GetAge()
	if age == 0 {
		return "Unknown"
	}

	days := int(age.Hours() / 24)
	years := days / 365
	months := (days % 365) / 30

	if years > 0 {
		return fmt.Sprintf("%d years, %d months", years, months)
	}
	if months > 0 {
		return fmt.Sprintf("%d months", months)
	}
	return fmt.Sprintf("%d days", days)
}

// Validate validates the datacenter metadata
func (m *DatacenterManager) Validate() error {
	metadata := m.metadata

	// At minimum, we should have some location information
	if metadata.Location == "" && metadata.Datacenter == "" &&
	   metadata.Rack == "" && metadata.Row == "" {
		return fmt.Errorf("no location information provided")
	}

	// Validate environment
	if metadata.Environment != "" {
		validEnvs := []string{"Production", "Staging", "Development", "Test", "DR", "UAT"}
		valid := false
		for _, env := range validEnvs {
			if metadata.Environment == env {
				valid = true
				break
			}
		}
		if !valid {
			return fmt.Errorf("invalid environment: %s (must be one of: %v)",
				metadata.Environment, validEnvs)
		}
	}

	// Validate criticality
	if metadata.Criticality != "" {
		validCrit := []string{"Critical", "High", "Medium", "Low"}
		valid := false
		for _, crit := range validCrit {
			if metadata.Criticality == crit {
				valid = true
				break
			}
		}
		if !valid {
			return fmt.Errorf("invalid criticality: %s (must be one of: %v)",
				metadata.Criticality, validCrit)
		}
	}

	return nil
}

// ToMap converts metadata to map for database storage
func (m *DatacenterManager) ToMap() map[string]interface{} {
	metadata := m.metadata
	result := make(map[string]interface{})

	result["location"] = metadata.Location
	result["datacenter"] = metadata.Datacenter
	result["building"] = metadata.Building
	result["floor"] = metadata.Floor
	result["room"] = metadata.Room
	result["row"] = metadata.Row
	result["rack"] = metadata.Rack
	result["position"] = metadata.Position
	result["side"] = metadata.Side

	result["asset_tag"] = metadata.AssetTag
	result["serial_number"] = metadata.SerialNumber
	result["barcode"] = metadata.BarCode
	result["owner_tag"] = metadata.OwnerTag

	result["owner"] = metadata.Owner
	result["department"] = metadata.Department
	result["cost_center"] = metadata.CostCenter
	result["project"] = metadata.Project
	result["service_tag"] = metadata.ServiceTag

	result["environment"] = metadata.Environment
	result["purpose"] = metadata.Purpose
	result["criticality"] = metadata.Criticality
	result["tier"] = metadata.Tier

	result["network_zone"] = metadata.NetworkZone
	result["subnet"] = metadata.Subnet

	result["notes"] = metadata.Notes

	result["primary_contact"] = metadata.PrimaryContact
	result["contact_email"] = metadata.ContactEmail
	result["contact_phone"] = metadata.ContactPhone

	result["created_at"] = metadata.CreatedAt
	result["updated_at"] = metadata.UpdatedAt

	if metadata.PurchaseDate != nil {
		result["purchase_date"] = *metadata.PurchaseDate
	}
	if metadata.WarrantyExpiry != nil {
		result["warranty_expiry"] = *metadata.WarrantyExpiry
	}
	if metadata.InstallDate != nil {
		result["install_date"] = *metadata.InstallDate
	}

	return result
}

// GetSummary returns a formatted summary of the metadata
func (m *DatacenterManager) GetSummary() string {
	summary := "Datacenter Metadata:\n"
	summary += fmt.Sprintf("  Location: %s\n", m.GetPhysicalLocation())

	if m.metadata.AssetTag != "" {
		summary += fmt.Sprintf("  Asset Tag: %s\n", m.metadata.AssetTag)
	}

	if m.metadata.Environment != "" {
		summary += fmt.Sprintf("  Environment: %s\n", m.metadata.Environment)
	}

	if m.metadata.Criticality != "" {
		summary += fmt.Sprintf("  Criticality: %s\n", m.metadata.Criticality)
	}

	if m.metadata.Owner != "" {
		summary += fmt.Sprintf("  Owner: %s\n", m.metadata.Owner)
	}

	if m.metadata.Department != "" {
		summary += fmt.Sprintf("  Department: %s\n", m.metadata.Department)
	}

	if m.metadata.InstallDate != nil {
		summary += fmt.Sprintf("  Age: %s\n", m.FormatAge())
	}

	if m.metadata.WarrantyExpiry != nil {
		if m.HasWarranty() {
			days := m.DaysUntilWarrantyExpiry()
			summary += fmt.Sprintf("  Warranty: %d days remaining\n", days)
		} else {
			summary += "  Warranty: EXPIRED\n"
		}
	}

	return summary
}

// Clone creates a copy of the metadata
func (m *DatacenterManager) Clone() *DatacenterMetadata {
	metadata := *m.metadata
	return &metadata
}
