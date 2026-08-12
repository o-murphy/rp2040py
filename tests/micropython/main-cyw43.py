import network  # type: ignore[import-not-found]

# Smoke test for CYW43_WIFI_BACKLOG.md step 3b/3c/3d (gSPI F0 bus + ALP/HT/KSO clock handshake +
# backplane windowed addressing + ARM core reset/enable registers). SDPCM/ioctl (step 3f) and
# scan/join (step 3g) aren't implemented yet, so this stops at active(True) - connect() would just
# hang waiting on ioctl responses that don't exist yet.
print("Initializing...")
nic = network.WLAN(network.WLAN.IF_STA)
nic.active(True)
print("active:", nic.active())

# print("Scan for networks")
# nic.scan()  # scan for access points

print("Connected", nic.isconnected())  # check if the station is connected to an AP
# nic.connect('ssid', 'key') # connect to an AP
# nic.config('mac')          # get the interface's MAC address
# nic.ipconfig('addr4')

# ap = network.WLAN(network.WLAN.IF_AP) # create access-point interface
# ap.config(ssid='RP2-AP')              # set the SSID of the access point
# ap.config(max_clients=10)             # set how many clients can connect to the network
# ap.active(True)
