import time  # type: ignore[import-not-found]

import socketpool  # type: ignore[import-not-found]
import wifi  # type: ignore[import-not-found]

# CircuitPython counterpart of tests/micropython/main-cyw43.py - same emulated CYW43439 bus
# (docs/records/0027-cyw43-wifi.md) and the same NAT bridge (docs/records/
# 0048-cyw43-nat-reflector.md), driven through CircuitPython's own `wifi`/`socketpool` API instead
# of MicroPython's `network`/`socket`. Both firmwares vendor the same `cyw43-driver`, so this
# exercises the identical gSPI/SDPCM protocol path from a completely different host stack -
# the point of the check is that nothing in bus.py/nat.py accidentally depends on MicroPython's
# lwIP configuration specifically.
print("Initializing...")
wifi.radio.enabled = True
print("enabled:", wifi.radio.enabled)
print("mac:", [hex(b) for b in wifi.radio.mac_address])

# scan() answers with the fixed fake "RP2040PY-GUEST" AP (bus.py's own module docstring).
print("Scan for networks")
found = []
for net in wifi.radio.start_scanning_networks():
    found.append((net.ssid, net.rssi, net.channel))
wifi.radio.stop_scanning_networks()
print("scan:", found)

# connect() drives the scripted WLC_E_* join sequence; the DHCP lease that follows is what makes
# ipv4_address non-None (answered by the emulator's own DhcpServer, same as MicroPython's side).
print("Connecting...")
connected = False
try:
    # Not "key" like the MicroPython script: CircuitPython validates the passphrase length
    # client-side (WPA2's own 8-64 rule) and raises ValueError before anything reaches the chip.
    # The emulator scripts the join unconditionally, so the value itself is irrelevant - only its
    # length has to be legal.
    wifi.radio.connect("RP2040PY-GUEST", "password")
    connected = True
except Exception as exc:  # ConnectionError on a real failure
    print("connect failed:", type(exc).__name__, exc)

print("connected:", wifi.radio.connected)
print("ipv4_address:", wifi.radio.ipv4_address)
print("ipv4_gateway:", wifi.radio.ipv4_gateway)

if connected and wifi.radio.ipv4_address is not None:
    pool = socketpool.SocketPool(wifi.radio)

    # Real TCP out through the TcpReflector, same fixed non-loopback target the MicroPython script
    # uses (127.0.0.1 would never reach this bus - the guest's own stack resolves loopback itself).
    print("Connecting a real TCP socket to 1.1.1.1:80...")
    try:
        sock = pool.socket(pool.AF_INET, pool.SOCK_STREAM)
        sock.settimeout(30)
        sock.connect(("1.1.1.1", 80))
        sock.send(b"HEAD / HTTP/1.0\r\nHost: 1.1.1.1\r\nConnection: close\r\n\r\n")
        buf = bytearray(256)
        got = sock.recv_into(buf)
        print("Received", got, "bytes:", bytes(buf[:64]))
        sock.close()
    except Exception as exc:
        print("Real TCP connection failed:", type(exc).__name__, exc)

    # Real DNS through the UdpRelay - CircuitPython resolves via the pool, not a module-level
    # getaddrinfo, but it is the same relayed UDP round trip underneath.
    print("Resolving micropython.org (needs DNS)...")
    try:
        info = pool.getaddrinfo("micropython.org", 80)
        print("getaddrinfo:", info[0][-1])
    except Exception as exc:
        print("DNS failed:", type(exc).__name__, exc)

    print("CIRCUITPYTHON CYW43 OK")

time.sleep(0.1)
