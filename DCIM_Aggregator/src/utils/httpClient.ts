import axios, { AxiosInstance, AxiosRequestConfig } from 'axios'
import https from 'https'
import fs from 'fs'
import path from 'path'
import { logger } from './logger'

// Try to load client certificates for mTLS
function loadCerts(): { cert?: Buffer; key?: Buffer; ca?: Buffer } {
  const certPaths = [
    // Aggregator's own certs
    path.resolve(__dirname, '../../certs'),
    // DCIM_Server certs (sibling directory)
    path.resolve(__dirname, '../../../DCIM_Server/certs'),
  ]

  for (const dir of certPaths) {
    try {
      const certFile = path.join(dir, 'client.crt')
      const keyFile = path.join(dir, 'client.key')
      const caFile = path.join(dir, 'ca.crt')

      if (fs.existsSync(certFile) && fs.existsSync(keyFile)) {
        logger.info(`Loaded mTLS client certificates from ${dir}`)
        return {
          cert: fs.readFileSync(certFile),
          key: fs.readFileSync(keyFile),
          ca: fs.existsSync(caFile) ? fs.readFileSync(caFile) : undefined,
        }
      }
    } catch (e) {
      // continue to next path
    }
  }

  logger.warn('No client certificates found — mTLS connections may fail')
  return {}
}

const certs = loadCerts()

// Shared HTTPS agent with mTLS support and self-signed cert acceptance
export const httpsAgent = new https.Agent({
  rejectUnauthorized: false,
  ...(certs.cert && { cert: certs.cert }),
  ...(certs.key && { key: certs.key }),
  ...(certs.ca && { ca: certs.ca }),
})

export class HttpClient {
  private client: AxiosInstance

  constructor(baseURL: string, timeout: number = 5000, customAgent?: https.Agent) {
    this.client = axios.create({
      baseURL,
      timeout,
      headers: {
        'Content-Type': 'application/json',
      },
      httpsAgent: customAgent || httpsAgent,
    })

    // Request interceptor
    this.client.interceptors.request.use(
      (config) => {
        logger.debug(`HTTP Request: ${config.method?.toUpperCase()} ${config.url}`)
        return config
      },
      (error) => {
        logger.error('HTTP Request Error:', error)
        return Promise.reject(error)
      }
    )

    // Response interceptor
    this.client.interceptors.response.use(
      (response) => {
        logger.debug(`HTTP Response: ${response.status} ${response.config.url}`)
        return response
      },
      (error) => {
        if (error.response) {
          logger.error(`HTTP Error: ${error.response.status} ${error.config.url}`)
        } else if (error.request) {
          logger.error(`HTTP No Response: ${error.config.url}`)
        } else {
          logger.error('HTTP Error:', error.message)
        }
        return Promise.reject(error)
      }
    )
  }

  async get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    const response = await this.client.get<T>(url, config)
    return response.data
  }

  async post<T>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T> {
    const response = await this.client.post<T>(url, data, config)
    return response.data
  }

  async put<T>(url: string, data?: any, config?: AxiosRequestConfig): Promise<T> {
    const response = await this.client.put<T>(url, data, config)
    return response.data
  }

  async delete<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    const response = await this.client.delete<T>(url, config)
    return response.data
  }
}
