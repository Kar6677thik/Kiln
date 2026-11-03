# requires: linux
# A listening socket is visible to the whole machine: ask the `ss` tool to list it.
import socket
import subprocess

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind(("127.0.0.1", 0))  # port 0 = let the kernel pick a free port
server.listen()
port = server.getsockname()[1]
print("our server is listening on port", port)

# `ss -ltn` = socket statistics: listening (-l), TCP (-t), numeric ports (-n).
table = subprocess.run(["ss", "-ltn"], capture_output=True, text=True, check=True).stdout
lines = table.splitlines()
print(lines[0])  # the header row
for line in lines[1:]:
    if f":{port} " in line:
        print(line)

server.close()
print("closed; the port is free again")
