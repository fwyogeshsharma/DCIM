const SSE_URL = (import.meta.env.VITE_API_URL || '/api/v1') + '/events'

export class SSEClient {
  private eventSource: EventSource | null = null
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private reconnectDelay = 5000
  private maxReconnectDelay = 60000
  private listeners: Map<string, Set<(data: any) => void>> = new Map()

  connect() {
    if (this.eventSource) {
      return
    }

    console.log('Connecting to SSE endpoint:', SSE_URL)

    this.eventSource = new EventSource(SSE_URL)

    this.eventSource.onopen = () => {
      console.log('SSE connection established')
      this.reconnectDelay = 5000 // Reset reconnect delay on successful connection
    }

    this.eventSource.onerror = (error) => {
      console.error('SSE connection error:', error)
      this.disconnect()
      this.scheduleReconnect()
    }

    // Listen for specific event types
    this.eventSource.addEventListener('agent_update', (event) => {
      this.handleEvent('agent_update', event)
    })

    this.eventSource.addEventListener('metric', (event) => {
      this.handleEvent('metric', event)
    })

    this.eventSource.addEventListener('alert', (event) => {
      this.handleEvent('alert', event)
    })

    this.eventSource.addEventListener('status_change', (event) => {
      this.handleEvent('status_change', event)
    })

    // Generic message handler
    this.eventSource.onmessage = (event) => {
      this.handleEvent('message', event)
    }
  }

  private handleEvent(eventType: string, event: MessageEvent) {
    try {
      const data = JSON.parse(event.data)
      const listeners = this.listeners.get(eventType)

      if (listeners) {
        listeners.forEach((callback) => {
          try {
            callback(data)
          } catch (error) {
            console.error('Error in SSE event listener:', error)
          }
        })
      }

      // Also trigger 'all' listeners
      const allListeners = this.listeners.get('all')
      if (allListeners) {
        allListeners.forEach((callback) => {
          try {
            callback({ event: eventType, data })
          } catch (error) {
            console.error('Error in SSE event listener:', error)
          }
        })
      }
    } catch (error) {
      console.error('Error parsing SSE event data:', error)
    }
  }

  disconnect() {
    if (this.eventSource) {
      this.eventSource.close()
      this.eventSource = null
    }

    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) {
      return
    }

    console.log(`Scheduling SSE reconnect in ${this.reconnectDelay}ms`)

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.connect()

      // Exponential backoff with max delay
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay)
    }, this.reconnectDelay)
  }

  on(eventType: string, callback: (data: any) => void) {
    if (!this.listeners.has(eventType)) {
      this.listeners.set(eventType, new Set())
    }

    this.listeners.get(eventType)!.add(callback)

    // Return unsubscribe function
    return () => {
      const listeners = this.listeners.get(eventType)
      if (listeners) {
        listeners.delete(callback)
      }
    }
  }

  off(eventType: string, callback?: (data: any) => void) {
    if (!callback) {
      this.listeners.delete(eventType)
    } else {
      const listeners = this.listeners.get(eventType)
      if (listeners) {
        listeners.delete(callback)
      }
    }
  }

  isConnected(): boolean {
    return this.eventSource !== null && this.eventSource.readyState === EventSource.OPEN
  }
}

// Export singleton instance
export const sseClient = new SSEClient()
