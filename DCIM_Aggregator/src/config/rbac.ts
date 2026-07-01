import fs from 'fs'
import path from 'path'
import yaml from 'js-yaml'

// =============================================================================
// RBAC config loader — reads rbac.yml (the single source of truth) and resolves
// a set of roles + approval status into an effective per-feature permission map.
// Both the route-guard middleware and the /auth endpoints consume this.
// =============================================================================

export type AccessLevel = 'none' | 'read' | 'write' | 'manage'

const ORDER: Record<AccessLevel, number> = { none: 0, read: 1, write: 2, manage: 3 }

interface RoleDef {
  label: string
  tier?: number
  approves?: boolean
  assignable_by?: string[]
  scope?: string
  wildcard?: AccessLevel
  features?: Record<string, AccessLevel>
  overrides?: Record<string, AccessLevel>
}

interface RbacConfig {
  version: number
  features: Record<string, { label: string; sensitive?: boolean }>
  roles: Record<string, RoleDef>
  approval: {
    default_status: string
    approver_roles: string[]
    pending_access: AccessLevel
    auto_approved_roles: string[]
    allow_multiple_roles: boolean
  }
}

let cached: RbacConfig | null = null

// rbac.yml lives in src/config. When compiled with tsc it is NOT copied into
// dist/, so resolve against a few candidate locations (dev ts-node runs from
// src; prod runs from dist and falls back to the source copy).
function rbacFilePath(): string {
  const candidates = [
    path.join(__dirname, 'rbac.yml'),
    path.join(__dirname, '..', '..', 'src', 'config', 'rbac.yml'),
    path.join(process.cwd(), 'src', 'config', 'rbac.yml'),
  ]
  for (const c of candidates) if (fs.existsSync(c)) return c
  return candidates[0]
}

export function loadRbac(): RbacConfig {
  if (cached) return cached
  cached = yaml.load(fs.readFileSync(rbacFilePath(), 'utf-8')) as RbacConfig
  return cached
}

export function featureKeys(): string[] {
  return Object.keys(loadRbac().features)
}

// A single role's declared per-feature levels (wildcard → features → overrides).
function roleLevels(roleKey: string): Record<string, AccessLevel> {
  const cfg = loadRbac()
  const role = cfg.roles[roleKey]
  const out: Record<string, AccessLevel> = {}
  if (!role) return out
  if (role.wildcard) for (const f of Object.keys(cfg.features)) out[f] = role.wildcard
  if (role.features) for (const [f, lvl] of Object.entries(role.features)) out[f] = lvl
  if (role.overrides) for (const [f, lvl] of Object.entries(role.overrides)) out[f] = lvl
  return out
}

// Effective permissions for a user: highest level across their roles, then
// clamped to `pending_access` while the account is not yet approved.
export function resolvePermissions(roles: string[], status: string): Record<string, AccessLevel> {
  const cfg = loadRbac()
  const out: Record<string, AccessLevel> = {}
  for (const f of Object.keys(cfg.features)) out[f] = 'none'

  for (const r of roles) {
    for (const [f, lvl] of Object.entries(roleLevels(r))) {
      if (out[f] === undefined || ORDER[lvl] > ORDER[out[f]]) out[f] = lvl
    }
  }

  if (status !== 'approved') {
    const cap = cfg.approval.pending_access
    for (const f of Object.keys(out)) if (ORDER[out[f]] > ORDER[cap]) out[f] = cap
  }
  return out
}

export function meets(have: AccessLevel | undefined, need: AccessLevel): boolean {
  return ORDER[have ?? 'none'] >= ORDER[need]
}

// Roles a new user may request at sign-up (everything except auto-approved
// privileged roles like root/admin, which only an approver can grant).
export function requestableRoles() {
  const cfg = loadRbac()
  const privileged = new Set(cfg.approval.auto_approved_roles)
  return Object.entries(cfg.roles)
    .filter(([key]) => !privileged.has(key))
    .map(([key, r]) => ({ key, label: r.label, tier: r.tier ?? 99 }))
    .sort((a, b) => a.tier - b.tier)
}

// Full catalog (used by approver UI when assigning roles).
export function roleCatalog() {
  const cfg = loadRbac()
  return Object.entries(cfg.roles).map(([key, r]) => ({
    key,
    label: r.label,
    tier: r.tier ?? 99,
    approves: !!r.approves,
    assignable_by: r.assignable_by ?? [],
  }))
}

export function isValidRole(role: string): boolean {
  return !!loadRbac().roles[role]
}

export function autoApprovedRoles(): string[] {
  return loadRbac().approval.auto_approved_roles
}

// True if any of the user's roles is allowed to approve others.
export function isApprover(roles: string[]): boolean {
  const cfg = loadRbac()
  const approverSet = new Set(cfg.approval.approver_roles)
  return roles.some((r) => approverSet.has(r) || cfg.roles[r]?.approves)
}
