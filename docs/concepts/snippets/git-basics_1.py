# Line endings are invisible characters. This shows the bytes behind "two lines".
unix_text = "first line\nsecond line\n"
windows_text = "first line\r\nsecond line\r\n"

print("Unix (LF)      :", unix_text.encode("utf-8"))
print("Windows (CRLF) :", windows_text.encode("utf-8"))
print("lengths in bytes:", len(unix_text.encode()), "vs", len(windows_text.encode()))

# The first line of a shell script, as saved by a Windows editor (CRLF ending).
# Bash cuts the line at the end-of-line byte "\n", so the "\r" stays glued to the word.
first_line_with_crlf = "#!/usr/bin/env bash\r"
interpreter = first_line_with_crlf.split(" ")[-1]
print("program env is asked to find:", repr(interpreter))
