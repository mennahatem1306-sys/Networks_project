# Station B in the team network

```mermaid
flowchart LR
    Operator[Operator PC] <-->|HTTP and WebSocket| Gateway[Gateway: Menna's laptop]
    Production[ESP32 A: Zeina] <-->|USB serial: SLIP and ATR| Gateway
    Gateway -->|TCP port 5001: settings and jobs| Inspection[ESP32 B: Aya]
    Inspection -->|Delivery, acceptance, result and heartbeat| Gateway
    Gateway --> Database[(Fady's SQLite database)]
```

Only the inspection station's application is implemented in this project. The PC tools
are test peers. They do not replace the team's production gateway or operator dashboard.

# Connection and execution flow

The boxes below describe internal processing stages, not extra global operating states.
The gateway still owns exactly IDLE, RUNNING, PAUSED and FAULT.

```mermaid
flowchart TD
    Boot[Startup: commit next boot ID] --> Wifi[Join Wi-Fi and obtain IPv4]
    Wifi --> Connect[Connect to gateway and send HELLO]
    Connect --> Sync[Validate SYNC_SESSION]
    Sync --> Ready[Reply READY; wait for runtime configuration]
    Ready --> Config[Validate and atomically apply settings]
    Config --> Idle[SAFE_IDLE: ready for a new job]
    Idle --> Job[Validate IDs and snapshot settings]
    Job --> Busy[BUSY: check timer while handling TCP and heartbeats]
    Busy --> Verdict[Timer expires: compute PASS or FAIL on B]
    Verdict --> Result[Cache and send INSPECTION_RESULT]
    Result --> Idle
    Result --> Receipt{Gateway confirms safe recording?}
    Receipt -->|Yes| Keep[Keep cached result for duplicate recovery]
    Receipt -->|No, retry budget remains| Resend[Resend original cached result]
    Resend --> Receipt
    Receipt -->|Retries exhausted| Disconnect[Close TCP and abandon uncertain active work]
    Busy -->|Disconnect or missing heartbeat| Disconnect
    Disconnect --> Connect
```

The gateway must abort the interrupted batch before allowing fresh work in a new session.
After reconnecting, B starts with no active job and no applied runtime configuration.
An identical duplicate in the same session returns status or its cached result; it does
not follow the new-job timer path again.
