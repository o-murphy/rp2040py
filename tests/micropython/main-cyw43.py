import network  # type: ignore[import-not-found]

# Smoke test for docs/records/0027-cyw43-wifi.md's "Implementation order" step 3 (gSPI F0 bus +
# ALP/HT/KSO clock handshake + backplane windowed addressing + ARM core reset/enable registers,
# SDPCM/ioctl framing, and 3g's scripted scan/join). scan() answers with the fixed fake
# "RP2040PY-GUEST" AP (bus.py's own module docstring), and connect() below targets that same AP so
# join's scripted WLC_E_* event sequence actually fires.
print("Initializing...")
nic = network.WLAN(network.WLAN.IF_STA)
nic.active(True)
print("active:", nic.active())

print("Scan for networks")
print(nic.scan())  # scan for access points

print("Connected", nic.isconnected())  # check if the station is connected to an AP
print(nic.connect('RP2040PY-GUEST', 'key')) # connect to an AP
print(nic.config('mac'))      # get the interface's MAC address
print(nic.ipconfig('addr4'))

# ap = network.WLAN(network.WLAN.IF_AP) # create access-point interface
# ap.config(ssid='RP2-AP')              # set the SSID of the access point
# ap.config(max_clients=10)             # set how many clients can connect to the network
# ap.active(True)
