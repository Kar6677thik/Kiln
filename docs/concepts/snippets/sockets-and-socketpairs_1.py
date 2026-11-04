# requires: linux
# A socketpair is two already-connected sockets; a TCP socket needs listen/connect/accept.
import socket

# 1. socketpair: like a two-way pipe between two ends.
left, right = socket.socketpair()
left.sendall(b"hello from left")
print(f"right received: {right.recv(100)!r}")
right.sendall(b"hi back from right")
print(f"left received:  {left.recv(100)!r}")
print(f"family: {left.family.name}, type: {left.type.name}")
left.close()
right.close()

# 2. TCP on localhost: a listening socket, a client, and the accepted connection.
listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.bind(("127.0.0.1", 0))  # port 0 = "kernel, pick any free port"
listener.listen(16)  # 16 = backlog: connections allowed to wait for accept()
host, port = listener.getsockname()
client = socket.create_connection((host, port))
server_side, client_address = listener.accept()  # a NEW socket for this one client
client.sendall(b"ping")
print(f"server got {server_side.recv(100)!r} from {client_address[0]}")
print(f"listener and connection are different sockets: {listener.fileno() != server_side.fileno()}")
for s in (client, server_side, listener):
    s.close()

# 3. Nobody listening on a port -> "connection refused".
try:
    socket.create_connection((host, port), timeout=1)
except ConnectionRefusedError as error:
    print(f"connecting to a closed port: ConnectionRefusedError ({error.strerror})")
