import { Router } from 'express'
import { Pool } from 'pg'
import { randomBytes, scrypt, timingSafeEqual } from 'crypto'
import { promisify } from 'util'

const scryptAsync = promisify(scrypt)

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

export function createAuthRouter(dbPool: Pool): Router {
  const router = Router()

  // ── Register ───────────────────────────────────────────────────────────────
  // Persists the account to the `users` table (migration 009) so the same
  // username/password can be used to log in again, from any browser or device.
  router.post('/register', async (req, res) => {
    try {
      const { username, email, password } = req.body ?? {}
      if (!username || !email || !password) {
        return res.status(400).json({
          success: false,
          error: 'username, email and password are required',
          message: 'username, email and password are required',
        })
      }

      const password_hash = await hashPassword(String(password))
      const { rows } = await dbPool.query(
        `INSERT INTO users (username, email, password_hash)
         VALUES ($1, $2, $3)
         RETURNING id, username, email`,
        [String(username).trim(), String(email).trim().toLowerCase(), password_hash]
      )

      res.json({ success: true, data: rows[0] })
    } catch (error: any) {
      // 23505 = unique_violation (username or email already exists)
      if (error?.code === '23505') {
        return res.status(409).json({
          success: false,
          error: 'Username or email already taken',
          message: 'Username or email already taken',
        })
      }
      res.status(500).json({ success: false, error: error.message, message: error.message })
    }
  })

  // ── Login ────────────────────────────────────────────────────────────────────
  // Looks the user up in the database and verifies the password hash.
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

      // Allow logging in with either username or email.
      const { rows } = await dbPool.query(
        `SELECT id, username, email, password_hash
         FROM users
         WHERE username = $1 OR email = $1
         LIMIT 1`,
        [String(username).trim()]
      )

      const user = rows[0]
      const ok = user ? await verifyPassword(String(password), user.password_hash) : false
      if (!user || !ok) {
        return res.status(401).json({
          success: false,
          error: 'Invalid username or password',
          message: 'Invalid username or password',
        })
      }

      res.json({ success: true, data: { id: user.id, username: user.username, email: user.email } })
    } catch (error: any) {
      res.status(500).json({ success: false, error: error.message, message: error.message })
    }
  })

  return router
}
