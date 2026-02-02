package storage

import (
	"encoding/json"
	"fmt"
	"time"

	"github.com/faber/network-monitor-agent/internal/metadata"
)

// DatacenterInfo represents datacenter metadata in the database
type DatacenterInfo struct {
	ID        int64
	AgentID   string
	Metadata  *metadata.DatacenterMetadata
	UpdatedAt time.Time
}

// SaveDatacenterInfo saves datacenter metadata to the database
func (s *Storage) SaveDatacenterInfo(agentID string, metadata *metadata.DatacenterMetadata) error {
	if metadata == nil {
		return fmt.Errorf("metadata is nil")
	}

	// Serialize VLANs and tags to JSON
	vlansJSON, _ := json.Marshal(metadata.VLANs)
	tagsJSON, _ := json.Marshal(metadata.Tags)

	// Check if record exists
	var existingID int64
	err := s.db.QueryRow("SELECT id FROM datacenter_info WHERE agent_id = ?", agentID).Scan(&existingID)

	if err != nil {
		// Insert new record
		_, err = s.db.Exec(`
			INSERT INTO datacenter_info (
				agent_id, location, datacenter, building, floor, room, row, rack,
				position, side, asset_tag, serial_number, barcode, owner_tag,
				owner, department, cost_center, project, service_tag,
				environment, purpose, criticality, tier,
				network_zone, vlans, subnet,
				purchase_date, warranty_expiry, install_date,
				notes, tags,
				primary_contact, contact_email, contact_phone,
				created_at, updated_at
			) VALUES (
				?, ?, ?, ?, ?, ?, ?, ?,
				?, ?, ?, ?, ?, ?,
				?, ?, ?, ?, ?,
				?, ?, ?, ?,
				?, ?, ?,
				?, ?, ?,
				?, ?,
				?, ?, ?,
				?, ?
			)
		`,
			agentID,
			metadata.Location, metadata.Datacenter, metadata.Building, metadata.Floor,
			metadata.Room, metadata.Row, metadata.Rack, metadata.Position, metadata.Side,
			metadata.AssetTag, metadata.SerialNumber, metadata.BarCode, metadata.OwnerTag,
			metadata.Owner, metadata.Department, metadata.CostCenter, metadata.Project, metadata.ServiceTag,
			metadata.Environment, metadata.Purpose, metadata.Criticality, metadata.Tier,
			metadata.NetworkZone, string(vlansJSON), metadata.Subnet,
			metadata.PurchaseDate, metadata.WarrantyExpiry, metadata.InstallDate,
			metadata.Notes, string(tagsJSON),
			metadata.PrimaryContact, metadata.ContactEmail, metadata.ContactPhone,
			metadata.CreatedAt.Unix(), metadata.UpdatedAt.Unix(),
		)
	} else {
		// Update existing record
		_, err = s.db.Exec(`
			UPDATE datacenter_info SET
				location = ?, datacenter = ?, building = ?, floor = ?,
				room = ?, row = ?, rack = ?, position = ?,
				side = ?, asset_tag = ?, serial_number = ?, barcode = ?,
				owner_tag = ?, owner = ?, department = ?, cost_center = ?,
				project = ?, service_tag = ?, environment = ?, purpose = ?,
				criticality = ?, tier = ?, network_zone = ?, vlans = ?,
				subnet = ?, purchase_date = ?, warranty_expiry = ?, install_date = ?,
				notes = ?, tags = ?, primary_contact = ?, contact_email = ?,
				contact_phone = ?, updated_at = ?
			WHERE agent_id = ?
		`,
			metadata.Location, metadata.Datacenter, metadata.Building, metadata.Floor,
			metadata.Room, metadata.Row, metadata.Rack, metadata.Position,
			metadata.Side, metadata.AssetTag, metadata.SerialNumber, metadata.BarCode,
			metadata.OwnerTag, metadata.Owner, metadata.Department, metadata.CostCenter,
			metadata.Project, metadata.ServiceTag, metadata.Environment, metadata.Purpose,
			metadata.Criticality, metadata.Tier, metadata.NetworkZone, string(vlansJSON),
			metadata.Subnet, metadata.PurchaseDate, metadata.WarrantyExpiry, metadata.InstallDate,
			metadata.Notes, string(tagsJSON), metadata.PrimaryContact, metadata.ContactEmail,
			metadata.ContactPhone, metadata.UpdatedAt.Unix(),
			agentID,
		)
	}

	return err
}

// GetDatacenterInfo retrieves datacenter metadata from the database
func (s *Storage) GetDatacenterInfo(agentID string) (*metadata.DatacenterMetadata, error) {
	var (
		location, datacenter, building, floor, room, row, rack, position, side string
		assetTag, serialNumber, barcode, ownerTag                              string
		owner, department, costCenter, project, serviceTag                     string
		environment, purpose, criticality, tier                                string
		networkZone, subnet                                                    string
		notes                                                                  string
		primaryContact, contactEmail, contactPhone                            string
		vlansJSON, tagsJSON                                                    string
		purchaseDate, warrantyExpiry, installDate                              *int64
		createdAt, updatedAt                                                   int64
	)

	err := s.db.QueryRow(`
		SELECT location, datacenter, building, floor, room, row, rack, position, side,
			asset_tag, serial_number, barcode, owner_tag,
			owner, department, cost_center, project, service_tag,
			environment, purpose, criticality, tier,
			network_zone, vlans, subnet,
			purchase_date, warranty_expiry, install_date,
			notes, tags,
			primary_contact, contact_email, contact_phone,
			created_at, updated_at
		FROM datacenter_info
		WHERE agent_id = ?
	`, agentID).Scan(
		&location, &datacenter, &building, &floor, &room, &row, &rack, &position, &side,
		&assetTag, &serialNumber, &barcode, &ownerTag,
		&owner, &department, &costCenter, &project, &serviceTag,
		&environment, &purpose, &criticality, &tier,
		&networkZone, &vlansJSON, &subnet,
		&purchaseDate, &warrantyExpiry, &installDate,
		&notes, &tagsJSON,
		&primaryContact, &contactEmail, &contactPhone,
		&createdAt, &updatedAt,
	)

	if err != nil {
		return nil, err
	}

	meta := &metadata.DatacenterMetadata{
		Location:       location,
		Datacenter:     datacenter,
		Building:       building,
		Floor:          floor,
		Room:           room,
		Row:            row,
		Rack:           rack,
		Position:       position,
		Side:           side,
		AssetTag:       assetTag,
		SerialNumber:   serialNumber,
		BarCode:        barcode,
		OwnerTag:       ownerTag,
		Owner:          owner,
		Department:     department,
		CostCenter:     costCenter,
		Project:        project,
		ServiceTag:     serviceTag,
		Environment:    environment,
		Purpose:        purpose,
		Criticality:    criticality,
		Tier:           tier,
		NetworkZone:    networkZone,
		Subnet:         subnet,
		Notes:          notes,
		PrimaryContact: primaryContact,
		ContactEmail:   contactEmail,
		ContactPhone:   contactPhone,
		CreatedAt:      time.Unix(createdAt, 0),
		UpdatedAt:      time.Unix(updatedAt, 0),
	}

	// Deserialize VLANs
	if vlansJSON != "" {
		json.Unmarshal([]byte(vlansJSON), &meta.VLANs)
	}

	// Deserialize tags
	if tagsJSON != "" {
		json.Unmarshal([]byte(tagsJSON), &meta.Tags)
	}

	// Convert timestamps
	if purchaseDate != nil {
		t := time.Unix(*purchaseDate, 0)
		meta.PurchaseDate = &t
	}
	if warrantyExpiry != nil {
		t := time.Unix(*warrantyExpiry, 0)
		meta.WarrantyExpiry = &t
	}
	if installDate != nil {
		t := time.Unix(*installDate, 0)
		meta.InstallDate = &t
	}

	return meta, nil
}
