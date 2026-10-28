import { resolve } from 'node:path';
import { defineConfig } from 'vitest/config';

const WRAPPER = resolve(__dirname, 'src/wasm-rp2040.ts');

// Every spec that imports `rp2040.js` gets the wasm-backed class instead; everything else (assembler, MockClock, BasePeripheral...) is rp2040js's own.
export default defineConfig({
  plugins: [
    {
      name: 'wasm-rp2040',
      enforce: 'pre',
      resolveId(source, importer) {
        if (/\/rp2040\.js$/.test(source) && importer && /\.spec\.ts$/.test(importer)) return WRAPPER;
        return null;
      },
    },
  ],
});
