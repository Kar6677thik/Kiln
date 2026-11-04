# A byte stream has no message boundaries; a 4-byte length prefix adds them.
import json
import socket
import struct

# 1. Two separate sends can arrive as ONE chunk.
a, b = socket.socketpair()
a.sendall(b'{"n":1}')
a.sendall(b'{"n":2}')
print(f"one recv() got: {b.recv(4096)!r}")
a.close()
b.close()

# 2. The length prefix: 4 bytes, big-endian ("!"), unsigned int ("I").
payload = json.dumps({"type": "ping"}).encode("utf-8")
frame = struct.pack("!I", len(payload)) + payload
print(f"payload is {len(payload)} bytes; header bytes = {list(frame[:4])}")


# 3. A tiny teaching decoder: feed it bytes in ANY pieces, get whole messages out.
class TinyDecoder:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer += data
        messages = []
        while len(self.buffer) >= 4:
            (length,) = struct.unpack("!I", self.buffer[:4])
            if len(self.buffer) < 4 + length:
                break  # the rest of this message hasn't arrived yet
            body = bytes(self.buffer[4 : 4 + length])
            del self.buffer[: 4 + length]
            messages.append(json.loads(body))
        return messages


decoder = TinyDecoder()
results = [decoder.feed(frame[i : i + 1]) for i in range(len(frame))]  # one byte at a time
completed_at = [i + 1 for i, r in enumerate(results) if r]
print(f"fed {len(frame)} single bytes; a message came out only at byte {completed_at}")
print(f"decoded: {results[-1]}")
print(f"two frames glued together: {TinyDecoder().feed(frame + frame)}")
