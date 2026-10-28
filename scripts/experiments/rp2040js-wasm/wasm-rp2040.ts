// Experiment (rp2040py record 0099, step 2b): the public surface of rp2040js's `RP2040` over rp2040py's wasm module, using ONLY what the module exports. Whatever
// cannot be done that way throws `NotExported` with the reason - the failures of rp2040js's own specs against this class are the table of gaps.
import { readFileSync } from 'node:fs';

export const RAM_START_ADDRESS = 0x20000000;
export const FLASH_START_ADDRESS = 0x10000000;

export class NotExported extends Error {
  constructor(what: string) {
    super(`NOT EXPORTED by the wasm module: ${what}`);
  }
}

const moduleBytes = readFileSync(process.env.RP2040_WASM as string);
const compiled = new WebAssembly.Module(moduleBytes);

const NVIC_ISER = 0xe000e100;
const NVIC_ICER = 0xe000e180;
const NVIC_ISPR = 0xe000e200;
const NVIC_ICPR = 0xe000e280;
const SYSM_IPSR = 5;

type Exports = Record<string, any>;

export class RP2040 {
  private readonly x: Exports;
  logger: any = { debug() {}, info() {}, warn() {}, error() {} };
  // rp2040js lets an embedder plug a JS peripheral into the bus window table; the module imports nothing, so it cannot call one.
  readonly peripherals: { [index: number]: any } = new Proxy(
    {},
    {
      set() {
        throw new NotExported('plugging a JS peripheral into the bus (the module imports nothing: no host callback can be called)');
      },
    },
  );

  constructor(readonly clock?: any) {
    const instance = new WebAssembly.Instance(compiled, {});
    this.x = instance.exports as Exports;
    this.x._initialize();
    this.x.rp2040_erase_flash();
    if (this.x.rp2040_boot() !== 0) throw new Error('rp2040_boot failed');
    if (clock && typeof clock.advance === 'function') {
      // rp2040js specs drive time with MockClock.advance(); the module keeps its own time and has no "advance simulated time by n ns" export.
    }
  }

  private mem() {
    return this.x.memory.buffer as ArrayBuffer;
  }
  get flash() {
    return new Uint8Array(this.mem(), this.x.rp2040_flash(), this.x.rp2040_flash_size());
  }
  get sram() {
    return new Uint8Array(this.mem(), this.x.rp2040_sram(), 264 * 1024);
  }
  get usbDPRAM() {
    return new Uint8Array(this.mem(), this.x.rp2040_dpram(), 4096);
  }

  loadBootrom(bootromData: Uint32Array) {
    new Uint32Array(this.mem(), this.x.rp2040_rom(), bootromData.length).set(bootromData);
    this.reset();
  }

  reset() {
    if (this.x.rp2040_boot() !== 0) throw new Error('rp2040_boot failed');
  }

  readUint32(address: number) {
    return this.x.rp2040_read32(address) >>> 0;
  }
  readUint16(address: number) {
    return this.x.rp2040_read16(address) >>> 0;
  }
  readUint8(address: number) {
    return this.x.rp2040_read8(address) >>> 0;
  }
  writeUint32(address: number, value: number) {
    this.x.rp2040_write32(address, value >>> 0);
  }
  writeUint16(address: number, value: number) {
    this.x.rp2040_write16(address, value & 0xffff);
  }
  writeUint8(address: number, value: number) {
    this.x.rp2040_write8(address, value & 0xff);
  }

  step() {
    if (this.x.rp2040_step() < 0) throw new Error('the core faulted');
  }

  readonly core = (() => {
    const self = this;
    return {
      get x() {
        return self.x;
      },
      get PC() {
        return self.x.rp2040_register(15) >>> 0;
      },
      set PC(value: number) {
        self.x.rp2040_set_register(15, value >>> 0);
      },
      get SP() {
        return self.x.rp2040_register(13) >>> 0;
      },
      set SP(value: number) {
        self.x.rp2040_set_register(13, value >>> 0);
      },
      get IPSR() {
        return self.x.rp2040_core_special(0, SYSM_IPSR) >>> 0;
      },
      registers: new Proxy([] as number[], {
        get: (_t, key) => (typeof key === 'string' && /^\d+$/.test(key) ? self.x.rp2040_register(Number(key)) >>> 0 : undefined),
        set: (_t, key, value) => {
          self.x.rp2040_set_register(Number(key), (value as number) >>> 0);
          return true;
        },
      }),
      // Through the bus, as an embedder could: NVIC_ISPR/ICPR and ISER/ICER hold the same two words.
      get pendingInterrupts() {
        return self.readUint32(NVIC_ISPR) | 0; // rp2040js keeps these as int32
      },
      set pendingInterrupts(value: number) {
        self.writeUint32(NVIC_ICPR, 0xffffffff);
        self.writeUint32(NVIC_ISPR, value >>> 0);
      },
      get enabledInterrupts() {
        return self.readUint32(NVIC_ISER) | 0;
      },
      set enabledInterrupts(value: number) {
        self.writeUint32(NVIC_ICER, 0xffffffff);
        self.writeUint32(NVIC_ISER, value >>> 0);
      },
      get interruptsUpdated() {
        return false;
      },
      set interruptsUpdated(_value: boolean) {
        // assumed: the core re-checks after an NVIC write by itself
      },
      get interruptPriorities(): Uint32Array {
        throw new NotExported('core.interruptPriorities (internal per-level bitmasks; only the NVIC_IPR registers are on the bus)');
      },
    };
  })();
}
