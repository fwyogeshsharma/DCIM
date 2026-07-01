import { Request, Response, NextFunction } from 'express'
import { Pool } from 'pg'
import { AccessLevel, resolvePermissions, effectiveRoles, meets, isApprover } from '../../config/rbac'

// =============================================================================
// Authentication + RBAC enforcement middleware.
//   authenticate(pool)   — resolves the bearer token to a user (401 if missing)
//   guardDomain(feature) — GET → read, other methods → write on that feature
//   requireApprover      — only root/admin (roles with approves: true)
// The frontend gating is UX only; THIS is the real security boundary.
// =============================================================================

export interface AuthedUser {
  id: number
  username: string
  email: string
  status: string
  roles: string[]
  permissions: Record<string, AccessLevel>
}

declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace Express {
    interface Request {
      authUser?: AuthedUser
    }
  }
}

function bearerToken(req: Request): string | null {
  const header = req.headers.authorization || ''
  const match = /^Bearer\s+(.+)$/i.exec(header)
  return match ? match[1].trim() : null
}

// Resolve a session token → hydrated user (roles + effective permissions).
export async function loadUserFromToken(pool: Pool, token: string): Promise<AuthedUser | null> {
  const { rows } = await pool.query(
    `SELECT u.id, u.username, u.email, u.status, u.requested_role,
            COALESCE(array_agg(ur.role) FILTER (WHERE ur.role IS NOT NULL), '{}') AS roles
       FROM sessions s
       JOIN users u    ON u.id = s.user_id
       LEFT JOIN user_roles ur ON ur.user_id = u.id
      WHERE s.token = $1 AND s.expires_at > NOW()
      GROUP BY u.id, u.username, u.email, u.status`,
    [token]
  )
  const row = rows[0]
  if (!row) return null
  // Recognise root/admin straight from users.requested_role (all-access), even
  // if the user_roles row is absent.
  const roles: string[] = effectiveRoles(row.roles || [], row.requested_role)
  return {
    id: row.id,
    username: row.username,
    email: row.email,
    status: row.status,
    roles,
    permissions: resolvePermissions(roles, row.status),
  }
}

export function authenticate(pool: Pool) {
  return async (req: Request, res: Response, next: NextFunction) => {
    try {
      const token = bearerToken(req)
      if (!token) {
        return res.status(401).json({ success: false, error: 'Authentication required' })
      }
      const user = await loadUserFromToken(pool, token)
      if (!user) {
        return res.status(401).json({ success: false, error: 'Session expired or invalid' })
      }
      req.authUser = user
      next()
    } catch (err: any) {
      res.status(500).json({ success: false, error: err.message })
    }
  }
}

// Coarse per-domain guard applied at router mount time. Reads are allowed with
// `read`; any mutation (POST/PUT/PATCH/DELETE) needs `write`. Finer distinctions
// (e.g. manage-only deletes, technician assigned-only scope) can be layered on
// individual routes later.
export function guardDomain(feature: string) {
  return (req: Request, res: Response, next: NextFunction) => {
    const user = req.authUser
    if (!user) return res.status(401).json({ success: false, error: 'Authentication required' })

    const method = req.method.toUpperCase()
    const need: AccessLevel = method === 'GET' || method === 'HEAD' ? 'read' : 'write'
    const have = user.permissions[feature] ?? 'none'

    if (!meets(have, need)) {
      return res.status(403).json({
        success: false,
        error: `Forbidden: '${feature}' requires ${need} access (you have ${have})`,
        code: 'RBAC_FORBIDDEN',
      })
    }
    next()
  }
}

export function requireApprover(req: Request, res: Response, next: NextFunction) {
  if (!req.authUser) return res.status(401).json({ success: false, error: 'Authentication required' })
  if (!isApprover(req.authUser.roles)) {
    return res.status(403).json({ success: false, error: 'Approver (root/admin) role required' })
  }
  next()
}
