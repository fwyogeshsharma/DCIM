import { Request, Response, NextFunction } from 'express'
import { logger } from '../../utils/logger'

export function errorHandler(err: Error, req: Request, res: Response, next: NextFunction) {
  logger.error('Unhandled error:', err)

  res.status(500).json({
    success: false,
    error: err.message || 'Internal server error',
  })
}

export function notFoundHandler(req: Request, res: Response) {
  res.status(404).json({
    success: false,
    error: 'Route not found',
  })
}
