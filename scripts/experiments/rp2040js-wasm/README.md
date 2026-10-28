# rp2040js specs on the wasm core (experiment, record 0099)

Not part of the package or of CI: a one-off measurement of how far `rp2040.wasm` is from a drop-in for [wokwi/rp2040js](https://github.com/wokwi/rp2040js), kept so it can be repeated.
Results and what they mean: the 2026-10-28 entry of [docs/records/0099-wasm-drop-in-for-rp2040js.md](../../../docs/records/0099-wasm-drop-in-for-rp2040js.md).

- `wasm-rp2040.ts` - the public surface of rp2040js's `RP2040` over **only** the exports of the wasm module (bus read/write, registers, special registers, `rp2040_step`, buffers). What the module cannot do throws `NotExported`
  with the reason; NVIC `pendingInterrupts` / `enabledInterrupts` go through the bus.
- `vitest.wasm.config.ts` - a vitest plugin that swaps the `./rp2040.js` import of rp2040js's own `*.spec.ts` for the class above; the specs themselves are unchanged.

## Run

```bash
python build_wasm.py /tmp/rp2040.wasm          # needs ziglang (uv run --no-project --with 'ziglang>=0.16.0,<0.17' python build_wasm.py ...)
git clone https://github.com/wokwi/rp2040js /tmp/rp2040js && cd /tmp/rp2040js && git checkout 5fbd205 && npm install --ignore-scripts
cp <this directory>/wasm-rp2040.ts src/ && cp <this directory>/vitest.wasm.config.ts .
RP2040_WASM=/tmp/rp2040.wasm npx vitest run --config vitest.wasm.config.ts src/rp2040.spec.ts src/peripherals/timer.spec.ts src/peripherals/clocks.spec.ts src/peripherals/dma.spec.ts src/peripherals/pwm.spec.ts src/peripherals/uart.spec.ts
```

Expected on 2026-10-28 (rp2040js `main` at `5fbd205`, `package.json` version 1.4.1, with the module of `f663ceb`): 22 of 54 pass. The `v1.4.1` tag exists, but the numbers were measured on `5fbd205`, which may be ahead of it.
