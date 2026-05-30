import { useEffect, useRef, useCallback } from 'react'
export function useWebSocket(url, onMessage) {
  const ws    = useRef(null)
  const timer = useRef(null)
  const connect = useCallback(() => {
    ws.current = new WebSocket(url)
    ws.current.onopen    = () => console.log('Dashboard connected')
    ws.current.onmessage = (e) => { try { onMessage(JSON.parse(e.data)) } catch {} }
    ws.current.onclose   = () => { timer.current = setTimeout(connect, 3000) }
    ws.current.onerror   = () => ws.current.close()
  }, [url, onMessage])
  useEffect(() => {
    connect()
    return () => { clearTimeout(timer.current); ws.current?.close() }
  }, [connect])
}
