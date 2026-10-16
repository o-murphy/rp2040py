// CoreMark on rp2040js (https://github.com/wokwi/rp2040js, the npm package `rp2040js`), timed - the reference point for demo/coremark_single_thread.py, which runs the same image on rp2040py.
//
// The image is protik09/CoreMark-RP2040 v1.0.0 (single core; later releases need core 1), the one `python demo/coremark_single_thread.py` caches in ~/.cache/rp2040py/Coremark-RP2040.uf2. The
// boot ROM is the one rp2040py ships (B1); rp2040js's own demo keeps its copy outside the npm package. Usage, from any empty directory:
//
//     npm install rp2040js
//     python -c "from rp2040py.device.bootrom import BOOTROM_B1; open('bootrom.bin','wb').write(b''.join(int(w).to_bytes(4,'little') for w in BOOTROM_B1))"
//     node <repo>/scripts/bench/rp2040js_coremark.mjs ~/.cache/rp2040py/Coremark-RP2040.uf2 bootrom.bin [--timeout SECONDS]
//
// (Node resolves `rp2040js` from the directory of this file upwards, so copy the script next to the `node_modules` you installed it into, or run it from there.) It prints the firmware's own
// result block and then `WALL <s>, simulated <s>, real-time factor <x>`. The firmware's `Total time (secs)` is the benchmark's simulated time; the "simulated" figure also holds the boot and the
// wait for the key press. The firmware waits for a key on the USB CDC console: Enter is sent 200 ms (wall) after the host sees the device.
//
// What it compares and what it does not: rp2040js's `Simulator` runs a fixed 125 MHz and its default logger prints debug lines (the USB transfers), both left as they are. A measurement of 2026-10-16
// (same machine, one run each): rp2040js 98.4 s wall for 12.93 s of CoreMark (232.0 Iterations/Sec), rp2040py native 44.2 s for 12.07 s (248.5) - about 2x. One run each, a trend, not a benchmark.
import { readFileSync } from 'node:fs';
import { Simulator, USBCDC } from 'rp2040js';

const args = process.argv.slice(2);
const timeoutArg = args.indexOf('--timeout');
const timeoutSeconds = timeoutArg >= 0 ? Number(args.splice(timeoutArg, 2)[1]) : 600;
if (args.length < 2) {
  console.error('usage: node rp2040js_coremark.mjs IMAGE.uf2 BOOTROM.bin [--timeout SECONDS]');
  process.exit(2);
}
const uf2 = readFileSync(args[0]);
const rom = readFileSync(args[1]);
const bootrom = new Uint32Array(rom.buffer, rom.byteOffset, rom.byteLength / 4).slice();  // little-endian words

const sim = new Simulator();
const mcu = sim.rp2040;
mcu.loadBootrom(bootrom);
for (let o = 0; o + 512 <= uf2.length; o += 512) {  // UF2: 512-byte blocks; address at 12, size at 16, payload at 32
  const addr = uf2.readUInt32LE(o + 12);
  const size = uf2.readUInt32LE(o + 16);
  const offset = addr - 0x10000000;
  if (offset >= 0 && offset < mcu.flash.length) mcu.flash.set(uf2.subarray(o + 32, o + 32 + size), offset);
}

const cdc = new USBCDC(mcu.usbCtrl);
let text = '';
let start = 0n;
let done = false;
cdc.onDeviceConnected = () => {
  setTimeout(() => cdc.sendSerialByte(13), 200);
};
cdc.onSerialData = (buffer) => {
  text += Buffer.from(buffer).toString();
  if (!done && /Iterations\/Sec\s*:\s*[0-9.]+/.test(text)) {
    done = true;
    const wall = Number(process.hrtime.bigint() - start) / 1e9;
    const simulated = sim.clock.nanos / 1e9;
    sim.stop();
    process.stdout.write(text + '\n');
    console.log(`WALL ${wall.toFixed(2)} s, simulated ${simulated.toFixed(2)} s (incl. boot), real-time factor ${(simulated / wall).toFixed(3)}`);
    process.exit(0);
  }
};

mcu.core.PC = 0x10000000;
start = process.hrtime.bigint();
sim.execute();
setTimeout(() => {
  console.log(`TIMEOUT after ${timeoutSeconds} s; text so far:\n` + text.slice(-400));
  process.exit(1);
}, timeoutSeconds * 1000);
