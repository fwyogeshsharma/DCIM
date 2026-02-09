import { useEffect, useCallback } from 'react'
import { sseClient } from '@/lib/sse'

export function useSSE<T = any>(
  eventType: string,
  onEvent: (data: T) => void,
  enabled: boolean = true
) {
  const handleEvent = useCallback(
    (data: T) => {
      onEvent(data)
    },
    [onEvent]
  )

  useEffect(() => {
    if (!enabled) {
      return
    }

    // Connect to SSE if not already connected
    if (!sseClient.isConnected()) {
      sseClient.connect()
    }

    // Subscribe to events
    const unsubscribe = sseClient.on(eventType, handleEvent)

    // Cleanup
    return () => {
      unsubscribe()
    }
  }, [eventType, handleEvent, enabled])

  useEffect(() => {
    // Cleanup on unmount
    return () => {
      // Don't disconnect here as other components might be using it
      // SSE client will stay connected for the session
    }
  }, [])
}

export function useSSEConnection() {
  useEffect(() => {
    sseClient.connect()

    return () => {
      sseClient.disconnect()
    }
  }, [])

  return {
    isConnected: sseClient.isConnected(),
    disconnect: () => sseClient.disconnect(),
    reconnect: () => {
      sseClient.disconnect()
      sseClient.connect()
    },
  }
}
