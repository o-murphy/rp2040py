import socket  # type: ignore[import-not-found]
import time  # type: ignore[import-not-found]

import network  # type: ignore[import-not-found]

# Smoke test for docs/records/0027-cyw43-wifi.md's "Implementation order" step 3 (gSPI F0 bus +
# ALP/HT/KSO clock handshake + backplane windowed addressing + ARM core reset/enable registers,
# SDPCM/ioctl framing, and 3g's scripted scan/join) and step 4, docs/records/
# 0048-cyw43-nat-reflector.md (MAC/DHCP/ARP address assignment + the TCP reflector). scan() answers
# with the fixed fake "RP2040PY-GUEST" AP (bus.py's own module docstring), and connect() below
# targets that same AP so join's scripted WLC_E_* event sequence actually fires.
print("Initializing...")
nic = network.WLAN(network.WLAN.IF_STA)
nic.active(True)
print("active:", nic.active())

print("Scan for networks")
print(nic.scan())  # scan for access points

print("Connected", nic.isconnected())  # check if the station is connected to an AP
nic.connect('RP2040PY-GUEST', 'key') # connect to an AP
print("Connected", nic.isconnected())  # check if the station is connected to an AP - link-layer
                                        # join alone only reaches CYW43_LINK_NOIP, so this is
                                        # expected to still print False here (see the poll below).

# Step 4b/4c: a real DHCP lease (answered by the emulator's DhcpServer, docs/records/
# 0048-cyw43-nat-reflector.md) is what makes isconnected() actually become True. DHCP here is a
# same-process synthetic exchange (no real network round trip involved on this side), so a short
# poll window is enough - not the 10s+ a real router might need.
connected = False
for _ in range(50):
    if nic.isconnected():
        connected = True
        break
    time.sleep(0.1)
print("isconnected() after DHCP poll:", connected)
print(nic.config('mac'))      # get the interface's MAC address
print(nic.ipconfig('addr4'))

# Step 4d: a real TCP connection, spliced by the emulator's TcpReflector out to the actual
# internet - the whole point of step 4. 1.1.1.1:80 (Cloudflare's public resolver landing page) is
# used as a fixed, highly-available, non-loopback target reachable from the *host* machine running
# this emulator (127.0.0.1 would not exercise this path at all - the guest's own lwIP resolves its
# own loopback locally and never sends a frame over the WiFi interface for it).
if connected:
    print("Connecting a real TCP socket to 1.1.1.1:80...")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect(('1.1.1.1', 80))
        s.send(b'HEAD / HTTP/1.0\r\nHost: 1.1.1.1\r\nConnection: close\r\n\r\n')
        response = s.recv(256)
        print("Received", len(response), "bytes:", response[:64])
        s.close()
    except OSError as exc:
        print("Real TCP connection failed:", exc)

    # Step 4e: a real DNS query, relayed out to a fixed public resolver - mip.install() resolves
    # a hostname (micropython.org) before it ever opens a TCP connection, so this is the thing
    # that made mip fail outright (OSError -2, confirmed empirically before 4e was built) even
    # though 4d's own raw-IP TCP splice already worked.
    print("Attempting mip.install (needs DNS)...")
    import mip  # type: ignore[import-not-found]
    try:
        mip.install("os-path")
        print("mip.install succeeded")
        import os.path  # type: ignore[import-not-found]  # the package mip.install() just fetched
        print("os.path.join test:", os.path.join('a', 'b'))
    except Exception as exc:
        print("mip.install / os.path failed:", type(exc).__name__, exc)

# ap = network.WLAN(network.WLAN.IF_AP) # create access-point interface
# ap.config(ssid='RP2-AP')              # set the SSID of the access point
# ap.config(max_clients=10)             # set how many clients can connect to the network
# ap.active(True)
