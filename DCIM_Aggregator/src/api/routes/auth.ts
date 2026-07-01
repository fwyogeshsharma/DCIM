import { Router } from 'express'
import { Pool } from 'pg'
import { randomBytes, scrypt, timingSafeEqual } from 'crypto'
import { promisify } from 'util'
import {
  resolvePermissions,
  requestableRoles,
  roleCatalog,
  isValidRole,
  autoApprovedRoles,
} from '../../config/rbac'
import { authenticate, requireApprover } from '../middleware/auth'

const scryptAsync = promisify(scrypt)

// Session tokens live for 30 days.
const SESSION_TTL_MS = 30 * 24 * 60 * 60 * 1000

// Password hashing using Node's built-in scrypt — no external dependency.
// Stored format: "<salt-hex>:<derived-key-hex>".
async function hashPassword(password: string): Promise<string> {
  const salt = randomBytes(16).toString('hex')
  const derived = (await scryptAsync(password, salt, 64)) as Buffer
  return `${salt}:${derived.toString('hex')}`
}

async function verifyPassword(password: string, stored: string): Promise<boolean> {
  const [salt, key] = (stored || '').split(':')
  if (!salt || !key) return false
  const keyBuffer = Buffer.from(key, 'hex')
  const derived = (await scryptAsync(password, salt, 64)) as Buffer
  return keyBuffer.length === derived.length && timingSafeEqual(keyBuffer, derived)
}

// Issue a fresh session token for a user and persist it.
async function createSession(dbPool: Pool, userId: number): Promise<string> {
  const token = randomBytes(32).toString('hex')
  const expires = new Date(Date.now() + SESSION_TTL_MS)
  await dbPool.query(
    `INSERT INTO sessions (token, user_id, expires_at) VALUES ($1, $2, $3)`,
    [token, userId, expires]
  )
  return token
}

// Assemble the user payload the frontend needs: identity + roles + the resolved
// permission map (so the UI can gate the sidebar / routes / buttons).
async function buildUserPayload(dbPool: Pool, userId: number) {
  const { rows } = await dbPool.query(
    `SELECT u.id, u.username, u.email, u.status, u.requested_role,
            COALESCE(array_agg(ur.role) FILTER (WHERE ur.role IS NOT NULL), '{}') AS roles
       FROM users u
       LEFT JOIN user_roles ur ON ur.user_id = u.id
      WHERE u.id = $1
      GROUP BY u.id`,
    [userId]
  )
  const u = rows[0]
  const roles: string[] = u.roles || []
  return {
    id: u.id,
    username: u.username,
    email: u.email,
    status: u.status,
    requested_role: u.requested_role,
    roles,
    permissions: resolvePermissions(roles, u.status),
  }
}

export function createAuthRouter(dbPool: Pool): Router {
  const router = Router()
  const auth = authenticate(dbPool)

  // ── Role catalog for the sign-up dropdown ──────────────────────────────────
  router.get('/roles', (_req, res) => {
    res.json({ success: true, data: requestableRoles() })
  })

  // ── Register ───────────────────────────────────────────────────────────────
  // The user REQUESTS a role. The very first account ever created is bootstrapped
  // as an approved root (so there is an approver). Everyone else starts pending —
  // their role is recorded but access is clamped to read-only until approved.
  router.post('/register', async (req, res) => {
    const client = await dbPool.connect()
    try {
      const { username, email, password, requested_role } = req.body ?? {}
      if (!username || !email || !password) {
        return res.status(400).json({
          success: false,
          error: 'username, email and password are required',
          message: 'username, email and password are required',
        })
      }

      // Is this the first user? If so, bootstrap as root.
      const { rows: countRows } = await client.query(`SELECT COUNT(*)::int AS n FROM users`)
      const isFirstUser = countRows[0].n === 0

      let role = String(requested_role || 'viewer')
      if (!isValidRole(role)) role = 'viewer'
      // Privileged roles (root/admin) can never be self-requested.
      if (!isFirstUser && autoApprovedRoles().includes(role)) {
        return res.status(400).json({
          success: false,
          error: `Role '${role}' must be granted by an administrator, not self-requested`,
          message: `Role '${role}' must be granted by an administrator`,
        })
      }

      const grantedRole = isFirstUser ? 'root' : role
      const status = isFirstUser ? 'approved' : 'pending'
      const password_hash = await hashPassword(String(password))

      await client.query('BEGIN')
      const { rows } = await client.query(
        `INSERT INTO users (username, email, password_hash, status, requested_role)
         VALUES ($1, $2, $3, $4, $5)
         RETURNING id`,
        [
          String(username).trim(),
          String(email).trim().toLowerCase(),
          password_hash,
          status,
          grantedRole,
        ]
      )
      const userId = rows[0].id
      await client.query(
        `INSERT INTO user_roles (user_id, role, granted_by) VALUES ($1, $2, $2)`,
        [userId, grantedRole]
      )
      await client.query('COMMIT')

      const token = await createSession(dbPool, userId)
      const user = await buildUserPayload(dbPool, userId)
      res.json({ success: true, data: { token, user } })
    } catch (error: any) {
      await client.query('ROLLBACK').catch(() => {})
      if (error?.code === '23505') {
        return res.status(409).json({
          success: false,
          error: 'Username or email already taken',
          message: 'Username or email already taken',
        })
      }
      res.status(500).json({ success: false, error: error.message, message: error.message })
    } finally {
      client.release()
    }
  })

  // ── Login ────────────────────────────────────────────────────────────────────
  router.post('/login', async (req, res) => {
    try {
      const { username, password } = req.body ?? {}
      if (!username || !password) {
        return res.status(400).json({
          success: false,
          error: 'username and password are required',
          message: 'username and password are required',
        })
      }

      const { rows } = await dbPool.query(
        `SELECT id, username, email, password_hash
           FROM users
          WHERE username = $1 OR email = $1
          LIMIT 1`,
        [String(username).trim()]
      )

      const found = rows[0]
      const ok = found ? await verifyPassword(String(password), found.password_hash) : false
      if (!found || !ok) {
        return res.status(401).json({
          success: false,
          error: 'Invalid username or password',
          message: 'Invalid username or password',
        })
      }

      const token = await createSession(dbPool, found.id)
      const user = await buildUserPayload(dbPool, found.id)
      res.json({ success: true, data: { token, user } })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message, message: error.message })
    }
  })

  // ── Current user (re-hydrates permissions on refresh) ──────────────────────
  router.get('/me', auth, async (req, res) => {
    const user = await buildUserPayload(dbPool, req.authUser!.id)
    res.json({ success: true, data: user })
  })

  // ── Logout (invalidate the current token) ──────────────────────────────────
  router.post('/logout', auth, async (req, res) => {
    const header = req.headers.authorization || ''
    const token = /^Bearer\s+(.+)$/i.exec(header)?.[1]?.trim()
    if (token) await dbPool.query(`DELETE FROM sessions WHERE token = $1`, [token])
    res.json({ success: true })
  })

  // ── Approver-only: list users awaiting approval ────────────────────────────
  router.get('/pending', auth, requireApprover, async (_req, res) => {
    const { rows } = await dbPool.query(
      `SELECT u.id, u.username, u.email, u.status, u.requested_role, u.created_at,
              COALESCE(array_agg(ur.role) FILTER (WHERE ur.role IS NOT NULL), '{}') AS roles
         FROM users u
         LEFT JOIN user_roles ur ON ur.user_id = u.id
        WHERE u.status = 'pending'
        GROUP BY u.id
        ORDER BY u.created_at ASC`
    )
    res.json({ success: true, data: rows })
  })

  // ── Approver-only: full role catalog (for assigning roles) ─────────────────
  router.get('/role-catalog', auth, requireApprover, (_req, res) => {
    res.json({ success: true, data: roleCatalog() })
  })

  // ── Approver-only: approve / reject / adjust a user's roles ─────────────────
  // Body: { action: 'approve' | 'reject', roles?: string[] }
  router.post('/users/:userId/approve', auth, requireApprover, async (req, res) => {
    const client = await dbPool.connect()
    try {
      const userId = parseInt(req.params.userId, 10)
      const { action, roles, note } = req.body ?? {}
      if (action !== 'approve' && action !== 'reject') {
        return res.status(400).json({ success: false, error: "action must be 'approve' or 'reject'" })
      }

      // Validate any supplied roles against the catalog.
      let newRoles: string[] | null = null
      if (Array.isArray(roles) && roles.length) {
        const invalid = roles.filter((r: string) => !isValidRole(r))
        if (invalid.length) {
          return res.status(400).json({ success: false, error: `Unknown role(s): ${invalid.join(', ')}` })
        }
        newRoles = roles
      }

      await client.query('BEGIN')
      if (action === 'reject') {
        await client.query(`UPDATE users SET status = 'rejected' WHERE id = $1`, [userId])
      } else {
        await client.query(`UPDATE users SET status = 'approved' WHERE id = $1`, [userId])
        if (newRoles) {
          await client.query(`DELETE FROM user_roles WHERE user_id = $1`, [userId])
          for (const r of newRoles) {
            await client.query(
              `INSERT INTO user_roles (user_id, role, granted_by)
               VALUES ($1, $2, $3) ON CONFLICT DO NOTHING`,
              [userId, r, req.authUser!.id]
            )
          }
        }
      }

      const { rows: roleRows } = await client.query(
        `SELECT COALESCE(array_agg(role), '{}') AS roles FROM user_roles WHERE user_id = $1`,
        [userId]
      )
      await client.query(
        `INSERT INTO role_approvals (user_id, approver_id, action, roles, note)
         VALUES ($1, $2, $3, $4, $5)`,
        [
          userId,
          req.authUser!.id,
          action === 'approve' ? 'approved' : 'rejected',
          (roleRows[0].roles || []).join(','),
          note ?? null,
        ]
      )
      await client.query('COMMIT')

      const user = await buildUserPayload(dbPool, userId)
      res.json({ success: true, data: user })
    } catch (error: any) {
      await client.query('ROLLBACK').catch(() => {})
      res.status(500).json({ success: false, error: error.message })
    } finally {
      client.release()
    }
  })

  return router
}
