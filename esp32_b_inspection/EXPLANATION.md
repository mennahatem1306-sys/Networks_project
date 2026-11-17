# ESP32 B: detailed explanation

## 1. What the station does

Your ESP32 is the inspection station. It receives virtual-part jobs from Menna's gateway,
waits for the configured inspection duration, calculates PASS or FAIL locally and returns
the result with the original identifiers. No sensor, motor, LED or external wiring is
required for the inspection logic. USB supplies power and console output; Wi-Fi carries TCP.

The complete production route is operator -> gateway -> ESP32 A -> gateway queue ->
ESP32 B -> gateway -> operator. The gateway owns its five-place waiting queue, batch
counts, database and global IDLE/RUNNING/PAUSED/FAULT state. B has one active inspection
slot and reports BUSY or SAFE_IDLE. SAFE_IDLE alone does not mean configuration is ready.

For Milestone 1, B uses custom TCP messages. USB SLIP, ATR and CRC are Zeina's station-A
link. Do not wrap B's inspection JSON in serial ATR framing. The protocol benchmark matrix
belongs to Milestone 2.

## 2. Project layout and build

The root CMakeLists.txt loads ESP-IDF's CMake support and names the project.
main/CMakeLists.txt registers the two application source files, declares required IDF
components, selects C++17 and enables warnings as errors for the application component.
The component manager reads main/idf_component.yml and installs the pinned ArduinoJson
7.4.3 dependency. Despite its name, this is a standalone C++ parser usable in ESP-IDF;
the application does not use Arduino's runtime or Wi-Fi classes.

sdkconfig.defaults establishes a 1000 Hz FreeRTOS tick, a 12288-byte main-task stack,
and a common 4 MB flash baseline. Your actual settings are generated in sdkconfig.
Kconfig.projbuild adds an Inspection Station menu with SSID, password, gateway numeric IPv4
address and port. config.h exposes those generated settings as typed C++ constants.
The credentials and gateway address are deployment settings. N, K and inspection duration
are different: they must arrive from the gateway at runtime, not be compiled into the board.

The VS Code workspace recommends the Espressif ESP-IDF extension and points IntelliSense
at compile_commands.json in the configured build directory, produced by ESP-IDF's build system. That file supplies
the actual compiler options and include paths. Ctrl+Shift+B invokes the extension's native
build task. Flash and Monitor use the extension's own commands and selected serial port.
On this laptop, build output is kept in C:/Espressif/builds/mct431_aya_inspection to avoid
placing compiler temporary files in the OneDrive source folder. The application helpers
and caches live in an anonymous C++ namespace, keeping their names private to that source file.

## 3. C++ types and syntax used

uint8_t stores a byte, 0..255. uint32_t stores 0..4,294,967,295 and is used for identifiers
and local millisecond counters. int32_t stores -2,147,483,648..2,147,483,647. int64_t holds
the wider sum used by the inspection rule. size_t represents buffer lengths and indices.
bool stores true or false; a JSON boolean is not accepted as an integer ID.
The two arithmetic helpers are constexpr: they can be evaluated by the compiler when
arguments are constants and still work normally with runtime arguments. Twelve static_assert
checks in tests/logic_compile_checks.cpp verify rule edge cases and timer boundaries on
every build. These are compile-time arithmetic checks, not physical network tests.

`Job &j` refers to the existing cached record rather than copying it. `const char *`
points to text the function does not modify. `nullptr` means no pointer was supplied.
`strcmp(a,b)` returns zero for matching strings, so `!strcmp(a,b)` means equality.
`std::min(a,b)` selects the smaller value. `extern "C"` gives app_main the symbol name
expected by ESP-IDF even though its source is C++.

Static/global storage is initialized before app_main. Unspecified global integers start
at zero. active starts at -1 because index zero is a valid job-cache entry.

## 4. Persistent identity: initializeBootCounter()

platform.cpp initializes NVS, opens the mct431-b namespace and reads boot. A missing key
means the first startup, so the previous value is zero. Other read errors or UINT32_MAX
are failures. It writes previous+1, commits the change and reads it back before accepting
the counter as valid. The handle is then closed.

NVS failure does not trigger automatic flash erasure: erasing would discard identity
history and could hide an uncertain restart. Initialization fails safely instead. The
counter is written once per startup, not once per heartbeat, reducing flash writes.

boot_id identifies a board startup. session_id identifies a TCP connection generation
assigned by the gateway. Reconnecting without resetting changes the session but not the
boot counter. Resetting changes both once a new session is assigned. Both are needed to
recognize uncertain old work. The gateway must generate fresh session IDs and durable work IDs.

## 5. Native Wi-Fi: startNetwork(), networkEvent(), networkReady()

startNetwork checks the SSID/password byte lengths, initializes the network interface
and event loop, creates the station interface and initializes the Wi-Fi driver. It
registers event callbacks, copies the configured credentials, chooses Wi-Fi station mode
and starts the driver. Wi-Fi settings use RAM storage; the application boot counter still
uses NVS. The authentication threshold permits an open or secured team network according
to its configured credentials; M1 application TCP authentication is not implemented.

networkEvent runs on ESP-IDF's event task. On station start it requests a Wi-Fi connection.
On disconnection it clears the ready flag and requests reconnection. On an IPv4-address
event it sets the ready flag; loss of that address clears it. The flag is std::atomic<bool>
because the event task writes it while the application's task reads it.

networkReady returns that flag. The TCP application waits for an IPv4 address before
opening a connection. Wi-Fi reconnection alone never resumes an abandoned inspection job.

## 6. Time and scheduling

monotonicMs derives local milliseconds from esp_timer_get_time, which returns microseconds.
The uint32_t result wraps after about 49.7 days. logic.h's elapsedAtLeast uses unsigned
subtraction, `uint32_t(now-start) >= duration`, rather than comparing with start+duration.
For start=4,294,967,280 and now=20, the unsigned difference is 36 ms, even across wraparound.

yieldStation calls vTaskDelay for one tick. With the configured 1000 Hz tick this is a
one-millisecond scheduling yield. The application does not sleep for the entire inspection
duration. It repeatedly checks elapsed time while processing TCP and heartbeats.
Socket connection and send operations have short bounded waits; those are distinct from
the non-blocking inspection timer and still need timing measurements on real hardware.

## 7. TcpConnection, one method at a time

connect first closes any previous descriptor. It accepts a numeric IPv4 address using
inet_pton, avoiding DNS waits. It creates a TCP socket, enables non-blocking operation
with FIONBIO and disables Nagle buffering with TCP_NODELAY. If connect returns EINPROGRESS,
waitWritable uses select with a 250 ms deadline. SO_ERROR is checked afterward: a writable
socket alone does not prove a successful connection. Failure closes the descriptor.

connected first checks the descriptor and Wi-Fi readiness. It peeks at the incoming stream
without consuming bytes. A zero-byte receive means the peer closed the connection;
unexpected errors also close it. EAGAIN/EWOULDBLOCK simply mean no data is available yet.

available checks connection health and asks FIONREAD for the queued byte count.
read receives one byte without waiting and returns -1 if no byte was obtained.
write repeatedly sends the remaining part of a frame. If the socket temporarily cannot
accept data, it waits for writability within a total 100 ms window. It returns the number
of bytes actually sent. The caller treats a partial frame as unsafe and disconnects.

stop shuts down and closes the socket and sets its descriptor to -1. It is safe to call
when no socket exists. A socket descriptor is an operating-system handle, not an ESP32 pin.

## 8. Main application variables

| Name | Purpose |
|---|---|
| client | Native TCP connection to the gateway |
| bootId / sessionId | Startup identity and current connection identity |
| txSeq | Increasing sequence for B's transmissions |
| heartbeatSeq | B's outgoing heartbeat counter |
| gatewayHeartbeatSeq / gatewayHeartbeatSeen | Detect repeated/older gateway heartbeats |
| lastHeartbeat / lastSentHeartbeat | Local receive/send times for heartbeat supervision |
| synced / configured | Session ready and runtime settings available |
| fatalBoot | Initialization failed; no application network work is allowed |
| configVersion / inspectionMs / ruleN / ruleK | Settings for the next newly accepted job |
| jobs / jobCount | Accepted session jobs and occupied cache entries |
| active | Current inspecting job's index or -1 |
| commands / commandCount | Accepted configuration command cache |
| syncCommand | Identifies the session synchronization command |
| reconnectAt / reconnectDelay | Next TCP attempt and backoff interval |
| rx / rxUsed / rxNeed / frameStarted | Receive framing buffer and partial-frame progress |

protocol.h names the schema/device IDs, buffer/cache limits, timer margins and retry limits.
station_types.h defines Job and ConfigCommand. Job stores original IDs, immutable rule and
duration, timer start, final elapsed time/verdict, completion flag and receipt/retry state.

## 9. Identifiers and transmissions

batch_id identifies a batch, part_id a virtual part, job_id an inspection, cmd_id a command,
and config_version a settings revision. Equal numbers in different namespaces are unrelated.
seq identifies a transmission, not an operation. Retrying job 27 preserves job_id=27 while
using a new seq. Deduplicating by seq would execute a retried job twice.

The gateway allocates IDs; B preserves them. Sender device_id is always 2 on B's messages
and 0 on gateway messages. Schema version is 1. Operational messages carry the current
session_id; HELLO is the exception because the gateway has not assigned a session yet.

## 10. Framing and JSON

TCP supplies an ordered byte stream without application-message boundaries. A read may
contain part of a JSON document or several documents. The wire format is a four-byte
unsigned big-endian length followed by exactly that many UTF-8 JSON bytes. The prefix
is not included in the length. A 100-byte JSON body begins with hex 00 00 00 64.

receiveFrames first gathers four bytes, reconstructs length with shifts and bitwise OR,
rejects zero or values above 1024, then gathers the body. It keeps partial bytes across
loop passes and resets for the next prefix after parsing a complete body. A 2048-byte
per-pass budget prevents incoming traffic from monopolizing the main task. An unfinished
frame older than two seconds closes the connection because reliable alignment is essential.

buildEnvelope adds schema, message type, sender ID and current session. sendMessage adds
the next seq, checks JSON allocation, measures the JSON byte count, writes the four-byte
prefix and serializes the body into a bounded array. An extra byte accommodates the
serializer's terminating NUL, which is not sent. It logs the sent JSON through ESP-IDF
logging. A failed or short write closes the connection; another message must not be
appended after a truncated frame. Sequence exhaustion also requires a new connection.

handleMessage requires a flat JSON object, a string message type, correct numeric schema,
gateway ID and sequence type. Nested arrays/objects are rejected. isUint32 excludes
booleans as IDs. After synchronization it also checks session identity before operation.

## 11. Lifecycle replies and their functions

sendResponse preserves the request's cmd_id or job_id and adds ref_msg_type. Optional result
and error fields are added only when applicable. rejectRequest sends REJECTED with an
error code. sendError emits an ERROR for failures that may not have an identifiable job.

DELIVERY_ACK means an identifiable framed request arrived. ACCEPTED means the application
validated it and agreed to act. COMPLETED or INSPECTION_RESULT means the action finished.
A delivered request may still be rejected. A successful socket write proves none of the
receiving application's validation, execution or durable storage. ACKs are never ACKed.

## 12. Session synchronization

On a new TCP connection B sends HELLO with boot_id and SAFE_IDLE. The gateway sends
SYNC_SESSION containing cmd_id, fresh session_id, target_device_id=2 and the matching
target_boot_id. handleMessage checks these fields, saves the session, clears session caches
and responds DELIVERY_ACK -> ACCEPTED -> COMPLETED(READY).

An identical sync retry receives the lifecycle replies without clearing caches again.
A changed sync command/session on an already synchronized connection is rejected. No new
jobs are allowed until configuration is applied. A three-second timeout also bounds waiting
for initial synchronization.

## 13. Runtime configuration

SET_INSPECTION_CONFIG contains cmd_id, config_version, inspection_time_ms, rule_n and rule_k.
The entire tuple is validated: duration 1..3,600,000 ms; N positive and within signed
32-bit; K within signed 32-bit. A new configuration version must increase within the session.

The command cache is checked before applying anything. An identical accepted cmd_id returns
its previous acceptance/completion without reapplying old settings. Changed values under
the same ID produce DUPLICATE_ID_CONFLICT. A new command with an old version produces
CONFIG_VERSION_CONFLICT. Capacity exhaustion rejects new work rather than evicting IDs.

After acceptance, the configuration is cached and applied together in one application-task
execution path, then COMPLETED(CONFIG_APPLIED) is sent. A failed acceptance send stops
the handler. An active inspection keeps its own saved settings, even if the current global
settings change. The gateway should hold new dispatch until configuration completion.

## 14. Inspection jobs and duplicate handling

INSPECT_PART contains batch_id, job_id, part_id and config_version. B looks up cached job_id
before comparing the current config version. This ordering lets it replay an old-version
result after the current settings have advanced.

An identical in-progress duplicate repeats ACCEPTED without changing started or duration.
An identical completed duplicate repeats ACCEPTED and the original cached result, including
its original elapsed time. Different part/batch/version under that ID is a conflict.

A new job requires current configuration, the correct version, an idle active slot and
cache space. B copies IDs, N, K and duration into a Job, sends acceptance, records the
start time, stores the record and sets active. Each main-loop pass checks the timer.
When it expires, B records actual elapsed time, calculates the verdict, marks completion,
clears active and sends INSPECTION_RESULT. Scheduling can make elapsed time slightly
greater than requested; it is a minimum wait rather than an exact real-time deadline.

## 15. Verdict arithmetic

inspectionFails checks positive N and evaluates `(int64_t(part_id) + int64_t(K)) % N == 0`.
Only B computes the production verdict. The 64-bit casts prevent overflow of the sum.
Negative K is valid. Although C++'s negative remainder convention differs from mathematical
modulo, remainder==0 still tests exactly whether the sum is divisible by positive N.

| part_id | N | K | Sum | Verdict |
|---:|---:|---:|---:|---|
| 2 | 3 | 1 | 3 | FAIL |
| 3 | 3 | 1 | 4 | PASS |
| 1 | 3 | -1 | 0 | FAIL |
| 4,294,967,295 | 1 | -2,147,483,648 | 2,147,483,647 | FAIL |

N=1 fails every part. N=0 is rejected before inspection, so division by zero cannot occur
in accepted work. Fixed expected answers in the PC test are assertions, not production verdicts.

## 16. Results, confirmation and retries

sendInspectionResult sends original batch/job/part IDs, config_version, PASS/FAIL and the
saved inspection_elapsed_ms. After a successful send it updates sentAt and attempts.
An unconfirmed result is resent after one second. Four automatic transmissions are allowed:
the original plus three retries. After the final unanswered timeout, the board sends
RETRY_EXHAUSTED and disconnects safely. The inspection itself is never run again.

A correlated gateway ACCEPTED or DELIVERY_ACK for ref_msg_type=INSPECTION_RESULT and
job_id marks the cached job confirmed. For this implementation either is a final positive
receipt, so Menna must issue it only after safe recording. The handoff leaves the precise
positive receipt type open; agree the convention during integration. Explicit same-ID
status recovery can replay cached results; the gateway must bound its own recovery attempts.

## 17. Heartbeats, safety and freshness

Once synchronized, B sends a heartbeat every second with session, boot counter, heartbeat
sequence and local state. Only a valid gateway heartbeat with a newer heartbeat counter
refreshes lastHeartbeat. Ordinary traffic and repeated old heartbeats do not refresh it.
Three seconds without that heartbeat causes safe disconnection.

disconnectSafe closes TCP, marks session/configuration unavailable, abandons the active
timer, resets framing and schedules reconnect. localState returns BUSY only while active
is valid, otherwise SAFE_IDLE. Reconnect delay grows 1, 2, 4, 8, then at most 10 seconds,
and resets after successful synchronization. Old work never resumes automatically.

The gateway must FAULT/ABORT the interrupted batch, reject old-session results and require
operator reset before new work. It also owns result-age validation: use its own monotonic
time since acceptance, falling back conservatively to first send, and compare with the
job's captured inspection duration+2000 ms. Never subtract the ESP32 clock from the PC clock.
A late same-session result can only be reconciled explicitly once using the same cached job.

## 18. app_main(), stationSetup(), stationLoop()

ESP-IDF invokes app_main on its main FreeRTOS task. stationSetup initializes the persistent
boot counter and networking. Failure sets fatalBoot; app_main logs initialization failure
and returns rather than starting application work.

Otherwise app_main repeatedly calls stationLoop. Each pass checks TCP connectivity and
scheduled reconnect attempts, receives framed messages, checks heartbeat timeout, sends
any due heartbeat, finishes a due inspection, retries unconfirmed reports and yields.
Initialization, Wi-Fi driver tasks and the application loop are separate parts of the system.
Only the application task modifies job/config caches, so their tuple updates are not
interleaved by another job handler.

## 19. Memory limits

The session cache holds at most 1000 accepted jobs and 256 configuration commands. It does
not discard completed IDs while the session remains valid, because an evicted ID might
execute again on retry. A full cache rejects new work. Limits apply per session, even
across several batches. New safe sessions may be established between settled batches.

Linear searches are bounded by the project's 1000-part planning limit. Measure actual RAM
and scheduling on the selected board. The JSON documents use heap allocation; outgoing
allocation failure closes safely instead of sending an incomplete message. The gateway
database remains necessary because ESP32 RAM is not durable across reset.

## 20. Python test tools

frame turns a dictionary into UTF-8 JSON and adds its big-endian length. exact repeatedly
reads until the required bytes arrive; a zero-byte receive means closure. receive checks
the prefix limits and parses the body.

Peer holds the connection, outgoing seq, session, receive queue, pending unmatched messages,
stop event and locks. send serializes concurrent main-test and heartbeat writes. record
writes monotonic event traces and synchronizes received result evidence to disk before
the fixture confirms it. reader validates response envelopes, records messages and passes
them to the queue. heartbeats runs once per second in a separate thread. wait filters
responses by type and fields, preserves unmatched messages for later waits, and times out.

run listens on the PC, accepts B and exercises handshake, partial frames, rule examples,
duplicate/conflicting jobs, live configuration snapshots and invalid N. The heartbeat-loss
scenario deliberately stops gateway heartbeats. The result-retries scenario withholds
receipts and expects four cached reports followed by safe disconnection.

export_trace converts actual JSONL records to CSV without inventing or merging events.
monotonic_s is measured by the PC in seconds; inspection_elapsed_ms is measured by B in
milliseconds. They are not interchangeable clocks. test_tools checks chunked frame reading,
invalid lengths, closure, pending-message preservation, timeouts and faithful CSV export.
These tests validate the PC tools; physical firmware behavior still needs the real board.

## 21. What to explain in discussion

- TCP is a byte stream, so application framing is necessary.
- Delivery, acceptance and completion prove different events.
- Job IDs and caches make retries safe; transmission seq is a different concept.
- Immutable snapshots prevent live rule changes from altering active jobs.
- Wide arithmetic handles the full ID and signed-K ranges safely.
- A non-blocking inspection timer keeps heartbeats and TCP active during long waits.
- Boot IDs detect resets; session IDs distinguish reconnects.
- An uncertain disconnected job is abandoned, not silently resumed.
- The gateway checks result age and durable counts; B alone computes the verdict.
- Compilation is not evidence of physical Wi-Fi timing or successful team integration.
