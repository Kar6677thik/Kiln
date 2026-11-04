"""KP/1, the Kiln Protocol: how bytes on a socket become messages, and back.

WHY THIS MODULE EXISTS
    Every Kiln process talks to the others over sockets, and a socket only moves bytes. This
    package holds the two halves of the agreement about what those bytes mean: framing
    (where one message ends and the next begins) and messages (what each message says).

HOW IT FITS
    protocol/framing.py   ← bytes ⇄ JSON objects (length-prefixed frames)
    protocol/messages.py  ← JSON objects ⇄ typed requests (Submit, Run, Ready, ...)
    Used by master/, worker/, client.py and (from K5) gateway/. Imports only kiln.errors.

READ FIRST
    docs/spec/02-protocol.md, docs/concepts/framing.md

MILESTONE
    Introduced in K1.
"""
