# A tiny server and a tiny client in one script, talking over TCP on localhost.
# The server answers each request with the same text in upper case.
import socket
import threading

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", 0))  # port 0 = "kernel, pick any free port for me"
server.listen()
host, port = server.getsockname()
print(f"server: listening on {host}:{port}")


def serve_forever_ish(n_clients):
    # A real server loops forever; this one stops after n_clients so the demo ends.
    for _ in range(n_clients):
        conn, address = server.accept()
        with conn:
            request = conn.recv(1024)
            print(f"server: got {request!r} from a client")
            conn.sendall(request.upper())


thread = threading.Thread(target=serve_forever_ish, args=(2,))
thread.start()

for message in (b"hello kiln", b"run job 42"):
    with socket.create_connection((host, port)) as client:
        client.sendall(message)
        reply = client.recv(1024)
        print(f"client: sent {message!r}, received {reply!r}")

thread.join()
server.close()
print("server: closed")
