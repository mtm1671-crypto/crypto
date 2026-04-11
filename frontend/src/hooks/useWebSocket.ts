"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { StreamEvent } from "@/lib/types";

type ConnectionState = "connecting" | "open" | "closed" | "error";

interface UseWebSocketReturn {
  send: (data: Record<string, unknown>) => void;
  connectionState: ConnectionState;
  reconnecting: boolean;
  lastEvent: StreamEvent | null;
}

const BACKOFF_BASE = 1000;
const BACKOFF_MAX = 30000;

export function useWebSocket(
  sessionId: string,
  backendUrl: string,
  onEvent: (event: StreamEvent) => void
): UseWebSocketReturn {
  const wsRef = useRef<WebSocket | null>(null);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const [connectionState, setConnectionState] =
    useState<ConnectionState>("connecting");
  const [reconnecting, setReconnecting] = useState(false);
  const [lastEvent, setLastEvent] = useState<StreamEvent | null>(null);

  const retriesRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmountedRef = useRef(false);

  useEffect(() => {
    unmountedRef.current = false;

    function connect() {
      if (unmountedRef.current) return;

      const url = `${backendUrl}/ws/${sessionId}`;
      const ws = new WebSocket(url);
      wsRef.current = ws;
      setConnectionState("connecting");

      ws.onopen = () => {
        if (unmountedRef.current) return;
        retriesRef.current = 0;
        setConnectionState("open");
        setReconnecting(false);
      };

      ws.onclose = () => {
        if (unmountedRef.current) return;
        setConnectionState("closed");
        scheduleReconnect();
      };

      ws.onerror = () => {
        if (unmountedRef.current) return;
        setConnectionState("error");
        // onclose will fire after onerror, which triggers reconnect
      };

      ws.onmessage = (e) => {
        const lines = (e.data as string).split("\n").filter(Boolean);
        for (const line of lines) {
          try {
            const event = JSON.parse(line) as StreamEvent;
            setLastEvent(event);
            onEventRef.current(event);
          } catch {
            // skip malformed lines
          }
        }
      };
    }

    function scheduleReconnect() {
      if (unmountedRef.current) return;
      setReconnecting(true);
      const delay = Math.min(
        BACKOFF_BASE * Math.pow(2, retriesRef.current),
        BACKOFF_MAX
      );
      retriesRef.current += 1;
      reconnectTimerRef.current = setTimeout(connect, delay);
    }

    connect();

    return () => {
      unmountedRef.current = true;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      wsRef.current?.close();
    };
  }, [sessionId, backendUrl]);

  const send = useCallback((data: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  return { send, connectionState, reconnecting, lastEvent };
}
