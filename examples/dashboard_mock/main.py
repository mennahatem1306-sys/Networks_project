import asyncio  # Run the simulation and send updates without blocking the server.
from contextlib import asynccontextmanager, suppress  # Manage application startup and shutdown.
from pathlib import Path  # Locate the dashboard HTML file.
from typing import Annotated, Literal  # Describe request types and constraints.

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError  # Handle invalid JSON and request values.
from fastapi.responses import FileResponse, JSONResponse  # Return HTML files or JSON responses.
from pydantic import BaseModel, ConfigDict, Field, PositiveInt  # Define and validate request models.

from mock_gateway import CommandError, MockGateway  # Import the local replacement for the Gateway core.


Target = Annotated[int, Field(ge=1, le=1000)]  # Limit the requested number of parts.
Duration = Annotated[int, Field(ge=1, le=3_600_000)]  # Limit durations in milliseconds.
Mode = Literal["PRODUCTION", "BENCHMARKING"]  # Define the two available operating modes.


class Command(BaseModel):  # Define fields shared by all commands.
    model_config = ConfigDict(strict=True, extra="forbid")  # Reject type coercion and unexpected fields.
    schema_version: Literal[1]  # Identify the message schema version.
    cmd_id: PositiveInt  # Require a previously reserved command ID.


class SelectMode(Command):  # Request a change to the operating mode.
    msg_type: Literal["SELECT_MODE"]
    operating_mode: Mode  # Specify the requested mode.


class SetConfig(Command):  # Update production and inspection settings.
    msg_type: Literal["SET_BATCH_CONFIG"]
    target_parts: Target  # Specify the target number of parts.
    cycle_time_ms: Duration  # Specify the production time per part.
    inspection_time_ms: Duration  # Specify the inspection time per part.
    rule_n: PositiveInt  # Require a positive divisor.
    rule_k: int  # Set the offset used by the inspection rule.


class StartBatch(Command):  # Request a new batch.
    msg_type: Literal["START_BATCH"]
    target_parts: Target  # Specify the target size for this batch.


class BatchCommand(Command):  # Control an existing batch.
    msg_type: Literal["PAUSE_BATCH", "RESUME_BATCH", "STOP_BATCH"]
    batch_id: PositiveInt  # Identify the batch to control.


class ResetFault(Command):  # Request recovery from FAULT.
    msg_type: Literal["RESET_FAULT"]


CommandRequest = Annotated[  # Select the request model by msg_type.
    SelectMode | SetConfig | StartBatch | BatchCommand | ResetFault,
    Field(discriminator="msg_type"),
]

gateway = MockGateway()  # Create a single local Gateway instance.
clients = set()  # Track one outbound message queue per browser.


def broadcast(message):  # Enqueue a message for every connected browser.
    for client in tuple(clients):
        if client.full():
            clients.discard(client)  # Isolate the slow client from the other clients.
            while not client.empty():
                client.get_nowait()  # Discard its queued updates.
            client.put_nowait(None)  # Signal that this client must reconnect and resync.
        else:
            client.put_nowait(message)  # Queue the update for this browser.


async def run_gateway():  # Advance the simulation and publish changes while the app runs.
    previous = None  # Track the last broadcast snapshot.
    while True:
        gateway.tick()  # Advance production and inspection.
        for event in gateway.events:
            broadcast(event)  # Publish command and inspection events.
        gateway.events.clear()  # Clear events after they have been queued for clients.
        current = gateway.snapshot()  # Read the current line state.
        if current != previous:
            broadcast(current)  # Send a snapshot when the status changes.
            previous = current  # Save the snapshot for the next comparison.
        await asyncio.sleep(0.025)  # Let the server handle other work between ticks.


@asynccontextmanager
async def lifespan(app):  # Manage work that runs for the lifetime of the app.
    task = asyncio.create_task(run_gateway())  # Start the simulation in the background.
    try:
        yield  # Let the server handle requests until shutdown.
    finally:
        task.cancel()  # Cancel the simulation on shutdown.
        with suppress(asyncio.CancelledError):
            await task  # Wait for the canceled task to finish.


app = FastAPI(  # Configure the operator API.
    title="MCT431 Operator API",
    lifespan=lifespan,
    openapi_url=None,  # Disable automatic API documentation and its schema.
)


@app.exception_handler(RequestValidationError)
async def invalid_request(request, error):  # Return a readable error for invalid request data.
    first = error.errors()[0]  # Pick the first validation issue to show the operator.
    location = ".".join(str(item) for item in first["loc"])  # Identify the invalid field or request location.
    reason = f"{location}: {first['msg']}"  # Combine the field location and validation message.
    return JSONResponse(  # Return a consistent HTTP 422 error.
        status_code=422,
        content={"detail": {"error_code": "INVALID_REQUEST", "reason": reason}},
    )


@app.get("/")
async def dashboard():  # Serve the dashboard at the site root.
    return FileResponse(Path(__file__).with_name("index.html"))  # Resolve the HTML file next to this module.


@app.get("/api/status")
async def status():  # Return a snapshot of the current status.
    return gateway.snapshot()


@app.post("/api/command-ids", status_code=201)
async def reserve_command_id():  # Reserve an ID before the operator submits a command.
    return {"cmd_id": gateway.reserve_id()}


@app.post("/api/commands")
async def submit_command(command: CommandRequest):  # Receive a command after request validation.
    try:
        record = gateway.submit(command.model_dump())  # Pass the validated payload to the mock Gateway.
    except CommandError as error:
        raise HTTPException(  # Convert a missing or conflicting ID into an HTTP error.
            status_code=error.status,
            detail={"error_code": error.code, "reason": str(error)},
        ) from error

    if "error" in record:
        error = record["error"]  # Read the recorded rejection.
        return JSONResponse(status_code=error["status"], content=record)  # Return the rejected command's lifecycle record.
    pending = record["events"][-1]["event"] == "ACCEPTED"  # Check whether the command is still pending.
    return JSONResponse(status_code=202 if pending else 200, content=record)


@app.get("/api/commands/{cmd_id}")
async def command_status(cmd_id: int):  # Look up the lifecycle of one command.
    record = gateway.commands.get(cmd_id)  # Search the command records by ID.
    if record is None:
        raise HTTPException(404, detail={"error_code": "COMMAND_NOT_FOUND", "reason": "No submitted command has that ID."})
    return record  # Return its stages and any available result.


@app.get("/api/batches/{batch_id}")
async def batch_status(batch_id: int):  # Look up a batch and its counters.
    batch = gateway.batches.get(batch_id)  # Search the saved batches by ID.
    if batch is None:
        raise HTTPException(404, detail={"error_code": "BATCH_NOT_FOUND", "reason": "Unknown batch ID."})
    return batch  # Return the batch record.


@app.post("/api/mock/fault")
async def mock_fault():  # Test Fault and Reset locally without boards.
    gateway.simulate_fault()  # Put the simulation into FAULT.
    return gateway.snapshot()  # Return the new status snapshot.


@app.websocket("/ws/status")
async def live_status(websocket: WebSocket):  # Stream live status and events to the browser.
    await websocket.accept()  # Accept the browser's WebSocket connection.
    queue = asyncio.Queue(maxsize=100)  # Allocate a bounded queue for this connection.
    clients.add(queue)  # Register the client for broadcasts.
    disconnect = asyncio.create_task(websocket.receive())  # Wait for an incoming message or disconnect.
    next_message = None  # Keep a reference to the pending queue read.
    try:
        await websocket.send_json(gateway.snapshot())  # Send a full snapshot as soon as the client connects.
        while True:
            next_message = asyncio.create_task(queue.get())  # Wait for the next queued update.
            done, _ = await asyncio.wait(  # Wake on an update or a disconnect.
                {disconnect, next_message}, return_when=asyncio.FIRST_COMPLETED
            )
            if disconnect in done:
                return  # End the server-to-browser stream when the receive task finishes.
            message = next_message.result()  # Read the queued update.
            if message is None:
                await websocket.close(code=1013)  # Tell an overloaded client to reconnect.
                return
            await websocket.send_json(message)  # Send the update to the browser.
    except WebSocketDisconnect:
        pass  # Handle a client disconnect during transmission.
    finally:
        clients.discard(queue)  # Remove the connection from the broadcast list.
        tasks = [task for task in (disconnect, next_message) if task is not None]
        for task in tasks:
            task.cancel()  # Cancel any remaining wait.
        await asyncio.gather(*tasks, return_exceptions=True)  # Collect the canceled tasks before leaving.
