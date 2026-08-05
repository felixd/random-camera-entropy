# Buffered Y8 / LSB datasets

## Architecture

- The USB camera agent opens the V4L2 device once and drains it continuously, even with no compute client.
- Every TLS-Y frame carries the agent/capture start time, uptime, and `source_warmup_seconds`.
- `app/sources/frame_buffer_worker.py` records full Y8 frames or packed whole-frame LSBs into chunk files.
- `dataset-y` is a normal compute source alongside `v4l2`, `tls-y`, and `rtsp`.
- A dataset can be processed while it is still growing.

## Safe live-read protocol

A frame becomes visible to readers in this order:

```text
write complete payload to chunk
        ↓
flush chunk payload
        ↓
append complete frames.csv row
        ↓
flush frames.csv row
```

The `frames.csv` row is the commit marker. A reader never guesses available bytes from the current chunk size and never reads an unindexed frame. At temporary end-of-file, `dataset-y` waits for the next committed row while `recording.lock` or `status=recording` indicates that the recorder is active.

## Start the continuously warmed USB agent

Install the Go agent on the camera host:

```bash
sudo ./agent/install.sh
```

The installer probes `/dev/video0..2` as the service user and configures access through the `video` group and an optional udev rule. Configuration is stored in `/etc/camera-entropy/camera-agent.env`.

The agent keeps no idle archive. It only creates a small bounded queue while a client is connected.

## Record 300 GB of full Y8 frames

```bash
SOURCE_TYPE=tls-y \
TLS_HOST=192.168.1.2 \
TLS_SERVER_NAME=camera \
FRAME_STORAGE_MODE=y8 \
FRAME_BUFFER_LIMIT_BYTES=300000000000 \
./scripts/run/run_frame_buffer.sh
```

Default output while recording:

```text
data/frame-buffer-YYYYMMDDTHHMMSSZ/
├── recording.lock
├── manifest.json              # status=recording, updated periodically
├── frames.csv                 # append-only frame commit log
├── checksums.sha256           # hashes of chunks already closed
└── chunks/
    ├── chunk_000000.bin       # may currently be growing
    ├── chunk_000001.bin
    └── ...
```

As soon as the manifest, index header, and checksum file exist, the recorder publishes:

```text
data/frame-buffer-latest -> frame-buffer-YYYYMMDDTHHMMSSZ
```

After a clean completion or manual stop it also publishes:

```text
data/frame-buffer-latest-complete -> frame-buffer-YYYYMMDDTHHMMSSZ
```

`frame-buffer-latest` therefore means the newest dataset, whether active or finished. Use `frame-buffer-latest-complete` when live growth is not desired.

## Record packed full-frame LSBs

```bash
SOURCE_TYPE=tls-y \
FRAME_STORAGE_MODE=lsb-packed \
FRAME_BUFFER_LIMIT_BYTES=300000000000 \
./scripts/run/run_frame_buffer.sh
```

`lsb-packed` stores one bit per pixel (`np.packbits`, big bit order), so it is eight times smaller than Y8 for complete bytes. During playback it reconstructs neutral values `128` and `129`; all LSB and temporal-XOR operations remain bit-exact, while full-luminance diagnostics are not meaningful.

## Process the dataset while it is recording

Start the recorder, then in another terminal or through WWW select the buffered source:

```bash
SOURCE_TYPE=dataset-y \
DATASET_DIR="$PWD/data/frame-buffer-latest" \
DATASET_FOLLOW=1 \
WIDTH=1280 HEIGHT=720 \
WARMUP_SECONDS=1800 \
./scripts/run/run_one.sh
```

Default behavior is:

```text
DATASET_FOLLOW=1
DATASET_POLL_SECONDS=0.1
DATASET_FOLLOW_TIMEOUT_SECONDS=0
```

The processor first consumes already committed frames as fast as CPU/disk allow. When it catches up, it waits for each newly committed frame and continues at the recorder's effective rate. A timeout of `0` means to wait until the recorder changes the dataset to a terminal status.

To process only the frames that are committed at the time the source reaches EOF:

```bash
DATASET_FOLLOW=0 SOURCE_TYPE=dataset-y ./scripts/run/run_one.sh
```

To stop after 60 seconds without a new committed frame:

```bash
DATASET_FOLLOW_TIMEOUT_SECONDS=60 SOURCE_TYPE=dataset-y ./scripts/run/run_one.sh
```

## Recorded timing

By default, backlog frames are processed as fast as possible. To reproduce their original timing:

```bash
DATASET_REALTIME=1 DATASET_RATE=1 SOURCE_TYPE=dataset-y ./scripts/run/run_one.sh
```

For ten-times-faster recorded timing:

```bash
DATASET_REALTIME=1 DATASET_RATE=10 SOURCE_TYPE=dataset-y ./scripts/run/run_one.sh
```

## SHA-256 verification

```bash
DATASET_VERIFY_HASHES=1 SOURCE_TYPE=dataset-y ./scripts/run/run_one.sh
```

For a completed dataset, all chunks are verified. For an active dataset, only chunks already closed by the recorder have final entries in `checksums.sha256`. The current growing chunk is safely readable through commit ordering but receives its final SHA-256 only at rollover or shutdown.

## WWW source

The installer adds or upgrades the `buffered-latest` source profile. After restarting the control server, select:

```text
Zbuforowane klatki — aktualny/najnowszy dataset
```

All entropy algorithms continue to operate as before; only the provider of successive Y matrices changes.

## Capacity at 1280×720, 10 FPS

- Y8: 921,600 bytes/frame, about 9.216 MB/s; 300 GB is about 9.0 hours.
- Packed LSB: 115,200 bytes/frame, about 1.152 MB/s; 300 GB is about 72.3 hours.

## Safety properties

- Frames are never split between chunks.
- A complete, newline-terminated index row commits exactly one complete frame.
- Temporary EOF in an active index is not treated as dataset completion.
- Multiple read-only processors may consume the same active dataset independently.
- A running reader resolves the dataset symlink once, so a later `frame-buffer-latest` update does not silently switch its input.
- The configured byte limit is not exceeded; capture ends on the last complete frame below the limit.
- Do not loop a dataset for qualification because repetition creates artificial periodicity.
