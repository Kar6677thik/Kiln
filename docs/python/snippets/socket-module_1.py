# A tiny TCP conversation on one machine: listen, connect, accept, send, close.
# requires: linux
import socket

# The server side: a listening socket on 127.0.0.1, port 0 = "kernel, pick any free port".
listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(("127.0.0.1", 0))
listener.listen(16)
host, port = listener.getsockname()
print("listening on", host, "port chosen by the kernel:", port > 0)

# The client connects; the kernel completes the handshake even before accept() runs.
client = socket.create_connection((host, port), timeout=2)
conn, address = listener.accept()
print("accepted a connection from", address[0])

client.sendall(b"submit labs.echo")
print("server received:", conn.recv(1024))
conn.sendall(b"ok")
print("client received:", client.recv(1024))

client.close()
print("after the client closes, recv returns:", conn.recv(1024))
conn.close()
listener.close()

# Nobody listens on that port any more, so connecting is refused.
try:
    socket.create_connection((host, port), timeout=2)
except ConnectionRefusedError as error:
    print("connect again:", type(error).__name__)
