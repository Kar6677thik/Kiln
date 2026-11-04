# An echo server and a client in one program, using asyncio streams.
import asyncio


async def handle(reader, writer):
    data = await reader.readline()
    writer.write(b"echo: " + data)
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def main():
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"hello kiln\n")
    await writer.drain()
    print((await reader.readline()).decode().strip())
    writer.close()
    await writer.wait_closed()
    server.close()
    await server.wait_closed()


asyncio.run(main())
