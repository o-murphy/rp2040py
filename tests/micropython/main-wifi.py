import network

# Smoke test for CYW43_WIFI_BACKLOG.md step 3b/3c/3d (gSPI F0 bus + ALP/HT/KSO clock handshake +
# backplane windowed addressing + ARM core reset/enable registers). SDPCM/ioctl (step 3f) and
# scan/join (step 3g) aren't implemented yet, so this stops at active(True) - connect() would just
# hang waiting on ioctl responses that don't exist yet.
nic = network.WLAN(network.WLAN.IF_STA)
nic.active(True)
print("active:", nic.active())
