// Exercises real internal-flash writes via Kaluma's own littlefs-backed filesystem (a different
// flash region than this file's own user-program auto-run staging - see README's Kaluma section)
// - the same SSI/RPSSI flash-write path (see docs/BACKLOG.md's "SSI flash-write support") the
// MicroPython tests/micropython/main-flash-rw.py test already covers from the MicroPython side.

var fs = require("fs");
var result;

try {
  fs.writeFileSync("flash_rw_test.txt", "flash rw works");
  var data = fs.readFileSync("flash_rw_test.txt", "utf8");
  result = data === "flash rw works" ? "FLASH RW OK" : "FLASH RW FAILED: unexpected content " + data;
} catch (e) {
  result = "FLASH RW FAILED: " + e;
}

setInterval(function () {
  console.log(result);
}, 1000);
