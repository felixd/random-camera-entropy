# Entropy from Camera

Ekspozycja: 7000 
FPS: 5 lub 10

Max ekspozycja: 7000
Ekspozycja 2000 -> ~~FPS 5
Ekspozycja 1000 -> ~~FPS 10 ???

Przykładowy start: 

```bash
.venv/bin/python3 camera_entropy_server.py \
  --host 0.0.0.0 \
  --port 8087 \
  --width 1280 \
  --height 720 \
  --camera-fps 10 \
  --calibration-pairs 512 \
  --clip-low 16 \
  --clip-high 250 \
  --assessed-min-entropy 0.50 \
  --manual-exposure \
  --exposure-value 2000
  ```