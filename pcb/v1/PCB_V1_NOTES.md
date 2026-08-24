# PCB v1 — known issues and bring-up notes

Findings from bringing up the first SPARC PCB on **2026-08-23**. This board carries a bare
`ESP32-WROOM-32` module instead of a devboard, which is where most of the trouble came from.

**TL;DR:** the board works, but the Reset and BOOT buttons are wired across the power rail
instead of to their signal pins, so they do nothing except short 3.3V to ground. Flashing
requires a jumper wire until v2. See [Flashing v1](#flashing-v1-the-jumper-workaround).

---

## What happened

A teammate flashed this board successfully twice on 2026-08-22, then it stopped working
entirely and never flashed again — on his machine or a different one, with or without
reinstalling the USB driver. The same CP2102 adapter flashed a devboard on a breadboard
without issue, which ruled out the adapter, the cable, and the host.

The cause was in the schematic, not the computers.

### The buttons short the power rail

`SW4` (Reset) and `SW5` (BOOT) are each connected **between +3.3V and GND**, rather than
between their signal pin and GND. Tracing the nets:

```
+3.3V ──┬── R4.2   (R4 = 10k, R4.1 → EN)
        └── SW4.2
                      SW4.1 ── GND        ← SW4 closed = +3.3V shorted to GND

+3.3V ──┬── R5.1   (R5 = 10k, R5.2 → IO0)
        └── SW5.2
                      SW5.1 ── GND        ← SW5 closed = +3.3V shorted to GND
```

The pull-ups themselves are correct — `R4` pulls `EN` high, `R5` pulls `IO0` high. The
mistake is that the buttons landed on the **rail side** of each resistor instead of the
**signal side**. Two consequences:

1. Pressing either button collapses the 3.3V rail instead of pulling its pin low.
2. `EN` and `IO0` have **no path to ground at all**, so `GPIO0` can never be held low —
   which makes download mode unreachable by design.

Correct wiring is `EN ── SW4 ── GND` and `IO0 ── SW5 ── GND`.

### Why it worked twice and then never again

Pressing a button browns out the rail. On release the ESP32 does a power-on reset while
`EN` and `IO0` ramp back up through *identical* 10k pull-ups. If `IO0` happens to still be
below its threshold when `EN` crosses its own, the chip latches into download mode by
accident.

That is a genuine race, sensitive to supply ramp rate, cable, temperature and USB port. It
succeeds occasionally and then never again, and no amount of driver reinstalling touches
it. It matched the reported symptom exactly.

> **Do not press Reset or BOOT on a v1 board.** They only short the rail. Use `Switch1` as
> the reset.

### Other v1 problems found

- **`LDO1` (AP2112K-3.3) enable pin floats.** Pin 3 is unconnected, and the part has no
  internal pull-up on `EN` — it must be driven high. The rail happened to come up on
  2026-08-23, but this is an independent source of "works one day, dead the next."
- **No capacitor on `EN`.** The usual 0.1µF to GND for a clean power-on reset is missing.
- **No I2C pull-ups.** `IO16` and `IO17` connect only the ESP32 to the sensor connector.
  It currently works because most VL53L0X breakouts carry their own and the ESP32's
  internal pull-ups are enabled by default, but those are weak (~45kΩ) and fragile.
- **`CP2102.1 (VCC)` is unconnected.** Not a defect — the board is self-powered from the
  TP4056 — but it means the programmer cannot power the board. `Switch1` must be on and
  the TP4056 must have power before flashing.

---

## Flashing v1: the jumper workaround

Since the BOOT button can't pull `GPIO0` low, do it manually. **No soldering required** —
a jumper wire held by hand is enough.

### The two pads

Both are castellations on the **same edge of the ESP32 module** — the edge facing the
CP2102 connector:

| Pad | Signal | Where |
|---|---|---|
| **25** | `IO0` | The **corner** castellation where the bottom row turns and heads up the side, immediately next to pad 24. Sits level with the CP2102 connector's 4th pad, ~5mm away. |
| **38** | `GND` | The far end of that same edge, ~16mm from pad 25. |

> **Counting trap.** This footprint is **14 / 10 / 14**, not the 15 / 8 / 15 shown in most
> online WROOM-32 pinout diagrams. The bottom row is pads 15–24, so **pad 25 is the first
> pin of the side column, not the second.** Getting this wrong costs a lot of failed
> attempts.

Alternative front-side target, ~3mm apart on larger SMD pads: `R5`'s pad facing away from
`R4` (= `IO0`), and `SW5` pad 1 (= `GND`).

### Steps

1. Power the board: TP4056 powered, `Switch1` **on**. The programmer does not power it.
2. Connect the CP2102 to the 4-pin JST header and to the computer. macOS needs **no
   driver** — it has had a built-in CP2102 driver since Big Sur, and installing the Silicon
   Labs one can conflict with it and break things.
3. Hold a jumper wire between **pad 25 and pad 38**.
4. **While still holding it**, flip `Switch1` off, then on. `GPIO0` is sampled only at the
   instant of reset, so the wire must already be in contact — touching it down afterwards
   does nothing.
5. Hold a second longer, then release. Download mode persists until the next reset, so
   there is no rush to run the command.

### Confirming it worked

Watch the ROM banner at **115200 baud** (the app itself runs at 9600):

| Output | Meaning |
|---|---|
| `boot:0x13 (SPI_FAST_FLASH_BOOT)` | Normal boot — `GPIO0` was high, **try again** |
| `boot:0x3 (DOWNLOAD_BOOT(UART0/...))` | **Download mode reached** |

This is much faster feedback than letting `esptool` time out, and it distinguishes "wrong
pad" from "no contact" from "board is dead."

### Flashing

There is **no auto-reset circuit** (DTR/RTS are not wired to the module), so `esptool` must
be told not to reset:

```bash
ESPTOOL=~/Library/Arduino15/packages/esp32/tools/esptool_py/5.3.1/esptool
BUILD=~/Library/Caches/arduino/sketches/<sketch-hash>

$ESPTOOL --port /dev/cu.usbserial-0001 --before no-reset --after no-reset chip-id

$ESPTOOL --port /dev/cu.usbserial-0001 --before no-reset --after no-reset write-flash \
  0x1000  $BUILD/sketch_esp_hid.ino.bootloader.bin \
  0x8000  $BUILD/sketch_esp_hid.ino.partitions.bin \
  0xe000  $BUILD/boot_app0.bin \
  0x10000 $BUILD/sketch_esp_hid.ino.bin
```

Then **remove the jumper** and flip `Switch1` off/on to run the new firmware. Leaving the
wire on just drops it back into download mode.

### Troubleshooting

| Symptom | Cause |
|---|---|
| No `/dev/cu.usbserial*` at all | Adapter unplugged, or a charge-only USB cable |
| `Failed to connect: No serial data received` | Not in download mode — repeat the jumper sequence |
| Nothing on serial at any baud | No 3.3V (floating LDO enable), or TX/RX swapped |
| Boot log missing but board runs | `GPIO15` low at reset suppresses the ROM log by design |

---

## The I2C swap: unresponsive sensor

After remapping the sketch to the PCB pinout, the sensor still reported:

```
VL53L0X NOT FOUND - check wiring (SDA=16, SCL=17, 3V3, GND)
```

### How it was found

The schematic nets read:

```
U2.27(IO16) ── VL53L0X1.3(SDA)
U2.28(IO17) ── VL53L0X1.4(SCL)
```

A brief detour worth recording: `U2.27` and `U2.28` are **pad numbers, not GPIO numbers**.
Pad 27 carries `IO16` and pad 28 carries `IO17`. The giveaway is that **`GPIO28` does not
exist on the ESP32** — its GPIOs are 0–19, 21–23, 25–27 and 32–39.

So per the schematic, `SDA = IO16` and `SCL = IO17`. But on the physical board the two are
**reversed**: `SCL` is on `IO16` and `SDA` is on `IO17`. Either the connector's pin labels
in the schematic are wrong, or the sensor cable is crossed.

I2C is unforgiving about this — a swapped pair produces no bus acknowledgement at all,
which surfaces as `lox.begin()` failing, indistinguishable from an unplugged sensor.

### The fix

Swapping the two constants in the sketch was enough — no rewiring:

```c
#define I2C_SDA 17          // schematic labels these the other way round;
#define I2C_SCL 16          // the board is actually wired SDA=17, SCL=16
```

After reflashing: `VL53L0X ready`, with live distance readings.

---

## Firmware pin mapping

The PCB and the perfboard use completely different pins, so `sketch_esp_hid.ino` selects
between them with a single `#define` at the top. Comment out `BOARD_PCB_V1` to build for
the perfboard.

| Function | PCB v1 | Perfboard |
|---|---|---|
| VL53L0X SDA | `IO17` | `IO21` |
| VL53L0X SCL | `IO16` | `IO22` |
| NeoPixel DIN | `IO15` (via R6) | `IO18` |
| Status LED | `IO27` (via R7) | `IO4` |

`GPIO18`, `GPIO4`, `GPIO21` and `GPIO22` are unconnected on the PCB.

> `GPIO15` is a strapping pin — if it is low at reset the ROM boot log is suppressed. It
> reads high in practice here, so the NeoPixel on that pin has not caused problems, but it
> is worth remembering when a board appears silent.

---

## PCB v2 change list

The four changes to make, in priority order.

### 1. Rewire both buttons — *critical*

Move each button from the rail side of its pull-up to the signal side. This is the change
that turns flashing from a jumper-wire ordeal into pressing two buttons.

```
current (broken):                    v2 (correct):

+3.3V ─┬─ R4 ─ EN                    +3.3V ─ R4 ─┬─ EN
       └─ SW4 ─ GND                               └─ SW4 ─ GND

+3.3V ─┬─ R5 ─ IO0                   +3.3V ─ R5 ─┬─ IO0
       └─ SW5 ─ GND                               └─ SW5 ─ GND
```

- `SW4` (Reset): `EN` ── `SW4` ── `GND`
- `SW5` (BOOT): `IO0` ── `SW5` ── `GND`

### 2. Tie the LDO enable pin high — *critical*

`LDO1` pin 3 (`EN`) is floating. Connect it to `VIN` (pin 1) for always-on. Without this
the 3.3V rail is unreliable and the board may fail to power up at random.

### 3. Add I2C pull-ups

Add **4.7k to +3.3V** on both `IO16` and `IO17`. Currently there are none; the board only
works because the sensor breakout carries its own.

### 4. Fix the SDA/SCL labels

The `VL53L0X1` connector's `SDA`/`SCL` pin labels do not match how the board is actually
built. Correct the schematic (or the cable) so the two agree, and the firmware can go back
to the intuitive `SDA=16, SCL=17`.

### Also worth doing

- **Add a 0.1µF capacitor from `EN` to GND** for a clean power-on reset.
- **Consider wiring `CP2102` DTR/RTS** to the standard auto-reset transistor pair, which
  would remove the manual button dance entirely.
- **Connect `CP2102.1 (VCC)`** or mark it clearly as no-connect, so it is obvious the
  programmer does not power the board.
