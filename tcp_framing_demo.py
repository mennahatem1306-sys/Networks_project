import json

# Prefix the UTF-8 JSON with its length as four big-endian bytes.
payload = json.dumps({"job_id": 42}).encode("utf-8")
frame = len(payload).to_bytes(4, "big") + payload

# Decode the complete frame to see what the receiver would read.
length = int.from_bytes(frame[:4], "big")
message = json.loads(frame[4:4 + length].decode("utf-8"))

print(length)
print(message)
