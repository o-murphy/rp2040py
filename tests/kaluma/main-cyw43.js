// Kaluma counterpart of tests/micropython/main-cyw43.py and tests/circuitpython/main-cyw43.py -
// the same emulated CYW43439 bus (docs/records/0027-cyw43-wifi.md) and NAT bridge
// (docs/records/0048-cyw43-nat-reflector.md), driven from a third, independent network stack.
// Kaluma is JavaScript with callback-style async, so the whole test is a callback chain and the
// final marker only prints once the real TCP response has come back.
//
// scan() answers with the fixed fake "RP2040PY-GUEST" AP (bus.py's own module docstring); connect()
// targets that same AP so join's scripted WLC_E_* sequence fires, and the DHCP lease that follows
// is what makes a real socket possible.

var WiFi = require('wifi').WiFi;
var net = require('net');

var wifi = new WiFi();

console.log('Scan for networks');
wifi.scan(function (scanErr, networks) {
  if (scanErr) {
    console.log('scan failed:', scanErr.message || scanErr);
    return;
  }
  console.log('scan:', JSON.stringify(networks));

  // Any password is accepted - the emulator scripts the join unconditionally, and Kaluma reports
  // the fake AP as OPEN (see 0048's "Known gaps": there is no negative-auth path to test against).
  wifi.connect({ ssid: 'RP2040PY-GUEST', password: 'password' }, function (connectErr) {
    if (connectErr) {
      console.log('connect failed:', connectErr.message || connectErr);
      return;
    }
    console.log('connected');

    // A real TCP connection spliced out to the actual internet by the emulator's TcpReflector.
    // 1.1.1.1:80 for the same reason the other two scripts use it: fixed, highly available, and
    // non-loopback (the guest's own stack would resolve 127.0.0.1 itself and never reach this bus).
    var socket = new net.Socket();
    socket.on('error', function (err) {
      console.log('socket error:', err.message || err);
    });
    socket.on('data', function (data) {
      // Not data.toString(): in this Kaluma build that renders a Buffer as comma-joined byte
      // numbers, which makes a failing run's log unreadable. Decode the status line by hand.
      var text = '';
      for (var i = 0; i < data.length && i < 48; i++) {
        var ch = data[i];
        if (ch === 13 || ch === 10) break;
        text += String.fromCharCode(ch);
      }
      console.log('received:', data.length, 'bytes:', text);
      console.log('KALUMA CYW43 OK');
      socket.end();
    });
    socket.connect({ host: '1.1.1.1', port: 80 }, function () {
      console.log('tcp connected');
      socket.write('HEAD / HTTP/1.0\r\nHost: 1.1.1.1\r\nConnection: close\r\n\r\n');
    });
  });
});
