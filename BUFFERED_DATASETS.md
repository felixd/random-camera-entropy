# Buffered Y8 / LSB datasets

## What changed

- The USB camera agent opens the V4L2 device once and drains it continuously, even with no compute client.
- Every TLS-Y frame carries `agent_started_*`, `capture_started_*`, `agent_uptime_seconds`, and `source_warmup_seconds`.
- `frame_buffer_worker.py` records immutable datasets as chunk files plus `frames.csv`, `manifest.json`, and SHA-256 checksums.
- `dataset-y` is a normal compute source alongside `v4l2`, `tls-y`, and `rtsp`.
- The compute worker uses the source-reported warm-up age. Older agents without that field retain the old local waiting behavior.

## Start the continuously warmed USB agent

```bash
DEVICE=/dev/video1 EXPOSURE=7000 CLIENT_BACKLOG_FRAMES=16 ./run_usb_agent.sh
```

The agent keeps no idle frame archive. It only creates a small bounded queue while a client is connected. Queue overflow is fail-closed rather than silently skipping frames.

## Record 300 GB of full Y8 frames (default)

```bash
SOURCE_TYPE=tls-y \
TLS_HOST=192.168.1.2 \
TLS_SERVER_NAME=camera \
FRAME_STORAGE_MODE=y8 \
FRAME_BUFFER_LIMIT_BYTES=300000000000 \
./run_frame_buffer.sh
```

Default output:

```text
data/frame-buffer-YYYYMMDDTHHMMSSZ/
├── manifest.json
├── frames.csv
├── checksums.sha256
├── READY.json                 # or STOPPED.json after a clean manual stop
└── chunks/
    ├── chunk_000000.bin
    ├── chunk_000001.bin
    └── ...
```

After a clean completion or manual stop, the recorder updates:

```text
data/frame-buffer-latest -> frame-buffer-YYYYMMDDTHHMMSSZ
```

## Record packed full-frame LSBs

```bash
SOURCE_TYPE=tls-y \
FRAME_STORAGE_MODE=lsb-packed \
FRAME_BUFFER_LIMIT_BYTES=300000000000 \
./run_frame_buffer.sh
```

`lsb-packed` stores one bit per pixel (`np.packbits`, big bit order). It is exactly 8 times smaller than Y8 for dimensions divisible into complete bytes.

For LSB-only playback, the source reconstructs Y values as `128` and `129`. This preserves every recorded LSB exactly and keeps the existing temporal/LSB algorithms unchanged. Full-luminance and clipping diagnostics are not meaningful for this mode and are explicitly marked in the source manifest/log.

## Custom output folder

```bash
FRAME_BUFFER_OUTPUT_DIR=/mnt/entropy-datasets/camera-a-20260802 \
FRAME_STORAGE_MODE=y8 \
./run_frame_buffer.sh
```

## Process the latest buffered dataset

From the command line:

```bash
SOURCE_TYPE=dataset-y \
DATASET_DIR="$PWD/data/frame-buffer-latest" \
WIDTH=1280 HEIGHT=720 \
WARMUP_SECONDS=1800 \
./run_one.sh
```

The default is offline fast processing. Recorded timestamps and camera warm-up age remain attached to every frame.

Optional real-time replay:

```bash
SOURCE_TYPE=dataset-y \
DATASET_DIR="$PWD/data/frame-buffer-latest" \
DATASET_REALTIME=1 \
DATASET_RATE=1 \
./run_one.sh
```

Optional full chunk verification before processing:

```bash
DATASET_VERIFY_HASHES=1 SOURCE_TYPE=dataset-y ./run_one.sh
```

## WWW source

The installer adds `buffered-latest` to `sources.json` and `sources.example.json`. After restarting the control server, select:

```text
Zbuforowane klatki — ostatni dataset
```

The selected profile and all entropy algorithms operate as before; only the frame source changes.

## Capacity at 1280×720, 10 FPS

- Y8: 921,600 bytes/frame, about 9.216 MB/s; 300 GB is about 9.0 hours.
- Packed LSB: 115,200 bytes/frame, about 1.152 MB/s; 300 GB is about 72.3 hours.

## Dataset safety rules

- A dataset with `recording.lock` is rejected as a compute source.
- Only `complete` and cleanly `stopped` datasets are readable.
- Frames are never split between chunks.
- `checksums.sha256` covers every completed chunk.
- The byte limit is not exceeded; the recorder stops at the last complete frame below the limit.
- Do not loop a dataset for qualification: repetition would create artificial periodicity.
