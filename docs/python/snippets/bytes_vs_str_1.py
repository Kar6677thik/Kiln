# Text must be encoded to bytes before it can leave the program (file, socket, pipe),
# and decoded back to text when it comes in. UTF-8 is the encoding Kiln uses everywhere.
text = "naïve café ☕"
data = text.encode("utf-8")

print("characters:", len(text))
print("bytes:     ", len(data))
print("raw bytes: ", data)
print("first 6 bytes as numbers 0-255:", list(data[:6]))
print("round trip ok:", data.decode("utf-8") == text)

# Decoding with the WRONG encoding does not crash here; it silently gives garbage.
print("decoded as latin-1:", data.decode("latin-1"))

# A bytearray is a bytes buffer you can grow and shrink, like a list of bytes.
# Kiln's protocol reader (K1) collects incoming network bytes like this.
buffer = bytearray()
for chunk in [b'{"type":', b'"ping"}', b'{"type":"stats"}']:
    buffer += chunk
print("buffer has", len(buffer), "bytes:", bytes(buffer))
first = bytes(buffer[:15])
del buffer[:15]
print("took:", first, "| left:", bytes(buffer))
