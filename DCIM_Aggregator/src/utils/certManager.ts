import https from 'https'
import fs from 'fs'
import path from 'path'
import { logger } from './logger'
import { httpsAgent } from './httpClient'

const CERTS_BASE = path.resolve(__dirname, '../../certs/servers')

export function ensureCertsBaseDir(): void {
  if (!fs.existsSync(CERTS_BASE)) {
    fs.mkdirSync(CERTS_BASE, { recursive: true })
  }
}

export function getServerCertDir(serverId: string): string {
  return path.join(CERTS_BASE, serverId)
}

/**
 * Write uploaded cert buffers to disk for a given server.
 */
export function saveServerCerts(
  serverId: string,
  files: { caCert?: Buffer; clientCert?: Buffer; clientKey?: Buffer }
): void {
  const dir = getServerCertDir(serverId)
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true })
  }

  if (files.caCert) fs.writeFileSync(path.join(dir, 'ca.crt'), files.caCert)
  if (files.clientCert) fs.writeFileSync(path.join(dir, 'client.crt'), files.clientCert)
  if (files.clientKey) fs.writeFileSync(path.join(dir, 'client.key'), files.clientKey)

  logger.info(`Saved TLS certs for server ${serverId} → ${dir}`)
}

/**
 * Returns true if the server has cert files on disk.
 */
export function serverHasCerts(serverId: string): boolean {
  const dir = getServerCertDir(serverId)
  return fs.existsSync(path.join(dir, 'ca.crt')) ||
    fs.existsSync(path.join(dir, 'client.crt'))
}

/**
 * Build a per-server https.Agent if certs exist on disk, else return the shared fallback.
 */
export function getAgentForServer(serverId: string): https.Agent {
  const dir = getServerCertDir(serverId)
  const caPath = path.join(dir, 'ca.crt')
  const certPath = path.join(dir, 'client.crt')
  const keyPath = path.join(dir, 'client.key')

  const hasCa = fs.existsSync(caPath)
  const hasCert = fs.existsSync(certPath)
  const hasKey = fs.existsSync(keyPath)

  if (!hasCa && !hasCert) {
    return httpsAgent // fallback to shared agent
  }

  logger.debug(`Using per-server TLS certs for ${serverId}`)
  return new https.Agent({
    rejectUnauthorized: hasCa,
    ...(hasCa && { ca: fs.readFileSync(caPath) }),
    ...(hasCert && { cert: fs.readFileSync(certPath) }),
    ...(hasKey && { key: fs.readFileSync(keyPath) }),
  })
}

/**
 * Remove cert directory when a server is deleted.
 */
export function deleteServerCerts(serverId: string): void {
  const dir = getServerCertDir(serverId)
  if (fs.existsSync(dir)) {
    fs.rmSync(dir, { recursive: true, force: true })
    logger.info(`Deleted TLS certs for server ${serverId}`)
  }
}
