// Exercises real internal-flash writes via Kaluma's own littlefs-backed filesystem (a different
// flash region than this file's own user-program auto-run staging - see README's Kaluma section)
// - the same SSI/RPSSI flash-write path (see docs/BACKLOG.md's "SSI flash-write support") the
// MicroPython tests/micropython/main-flash-rw.py test already covers from the MicroPython side.
//
// Kaluma's fs module has no writeFileSync/readFileSync (unlike Node) - just writeFile(path, data)/
// readFile(path), both synchronous, taking/returning a Uint8Array (confirmed against
// kaluma-project/kaluma's src/modules/fs/fs.js and tests/fs.test.js directly).

var fs = require("fs");
var result;

function toBytes(str) {
  var bytes = new Uint8Array(str.length);
  for (var i = 0; i < str.length; i++) {
    bytes[i] = str.charCodeAt(i);
  }
  return bytes;
}

function bytesEqual(a, b) {
  if (a.length !== b.length) {
    return false;
  }
  for (var i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) {
      return false;
    }
  }
  return true;
}

try {
  var written = toBytes("flash rw works");
  fs.writeFile("/flash_rw_test.txt", written);
  var readBack = fs.readFile("/flash_rw_test.txt");
  result = bytesEqual(written, readBack) ? "FLASH RW OK" : "FLASH RW FAILED: content mismatch";
} catch (e) {
  result = "FLASH RW FAILED: " + e;
}

setInterval(function () {
  console.log(result);
}, 1000);
