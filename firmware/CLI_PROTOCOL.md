# Flipper ↔ Mac serial protocol

The contract between the Flipper firmware (a unitemp fork) and `FlipperSerialReader` in
`../reader.py`. Keep both sides in sync with this file.

## Transport
- The Flipper exposes a USB CDC serial device on the Mac, typically `/dev/cu.usbmodemflip_*`.
- The firmware registers a **CLI command** named `bme280read`. A CLI command only exists
  **while the app is running**, so the unitemp-fork app must stay open on the Flipper. It
  keeps polling the BME280 for its on-screen display; the CLI callback returns the latest
  cached reading.

## Request
The Mac writes the command followed by CRLF:

```
bme280read\r\n
```

## Response
Exactly **one JSON line**, then the normal CLI prompt. Success:

```json
{"ok": true, "p_hpa": 1013.25, "rh": 41.2, "t_c": 23.41}
```

Failure (no sensor, I²C error, etc.):

```json
{"ok": false, "err": "no_sensor"}
```

### Fields
| key     | type          | meaning                          | required |
|---------|---------------|----------------------------------|----------|
| `ok`    | bool          | reading is valid                 | yes      |
| `p_hpa` | number        | barometric pressure, hectopascal | yes when ok |
| `rh`    | number / null | relative humidity, %             | no       |
| `t_c`   | number / null | temperature, °C                  | no       |
| `err`   | string        | short error code when `ok=false` | when not ok |

## Parser tolerances (see `parse_reading_line`)
- Surrounding whitespace / CRLF is stripped; an echoed prompt prefix before `{` is trimmed.
- `p_hpa` must be present, numeric, and finite, or the reading is rejected.
- A malformed `rh`/`t_c` is dropped to `null` rather than failing the whole reading.
