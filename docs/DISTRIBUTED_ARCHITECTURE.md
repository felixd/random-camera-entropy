# Architektura rozdzielona

## Warstwy

```text
kamera USB
   ↓ YUYV
agent Go (`agent/`)
   ↓ niekompresowane Y8 / CEYTLS01 / mTLS
źródło `tls-y` albo recorder
   ↓
Python `app/sources`
   ↓
Python `app/core`
   ↓
health tests → conditioner → raporty
```

Agent kamery odpowiada wyłącznie za niezawodną akwizycję, utrzymywanie kamery w stanie rozgrzanym, kontrolę ekspozycji i transport pełnych klatek Y8. Serwer obliczeniowy odpowiada za kalibrację maski, budowanie symboli, health tests, statystyki, ekstrakcję i kondycjonowanie.

## Agent Go

Agent automatycznie testuje `/dev/video0`, `/dev/video1` i `/dev/video2`, chyba że wskazano konkretne `DEVICE`. Probe obejmuje prawa R/W, ustawienia V4L2, ekspozycję, YUYV, geometrię oraz odczyt kompletnych klatek.

Agent otwiera jeden ciągły stream V4L2. Bez klienta payload jest odrzucany. Po podłączeniu jednego uwierzytelnionego klienta tworzona jest mała kolejka ograniczona `CLIENT_BACKLOG_FRAMES`. Drugi klient obliczeniowy jest odrzucany.

Każdy nagłówek powitalny zawiera m.in.:

```text
agent_started_unix_ns
capture_started_unix_ns
agent_uptime_seconds
source_warmup_seconds
continuous_capture=true
idle_buffering=false
```

Od wersji 8.0.5 każda ramka CEYTLS01 również zawiera aktualne `source_warmup_seconds`. Worker najpierw zalicza wartość z `hello` (zgodność z agentem 8.0.4), a następnie koryguje ją na podstawie kolejnych ramek. Jeżeli źródło nie raportuje wieku, pozostaje pełny lokalny timer fail-closed.

## Dlaczego Y8, a nie H.264/JPEG

Kwalifikacja źródła dotyczy bezpośrednich bajtów luminancji Y z YUYV. Stratne kodowanie stworzyłoby inny model źródła, zależny od predykcji i kwantyzacji kodeka. TLS-Y zachowuje jeden bajt Y na piksel bez ponownego kodowania.

## Źródła i izolacja

Obsługiwane źródła:

- `v4l2` — lokalne urządzenie;
- `tls-y` — zdalny agent Go;
- `rtsp` — zdekodowana luminancja obrazu sieciowego;
- `dataset-y` — zapisany lub rosnący dataset Y8/LSB.

Nie należy łączyć różnych źródeł, modeli kamer ani konfiguracji ekspozycji w jednej deklaracji entropii. Każde źródło powinno mieć osobną kwalifikację, restart test i parametry conditionera.

## Współbieżność

Źródło LIVE ma jednego konsumenta, dlatego aktywne może być najwyżej jedno zadanie LIVE. Read-only `dataset-y` może być otwierane przez wiele niezależnych workerów. Control server pozwala na:

```text
N × dataset-y + 0 lub 1 × LIVE
```

Limit `N` ustala `MAX_DATASET_JOBS` lub `--max-dataset-jobs`. Każdy job otrzymuje osobny port loopback i osobną grupę procesów.

## Czas ramek

- `v4l2`: lokalny czas monotoniczny w chwili odczytu;
- `tls-y`: czas monotoniczny i UTC hosta agenta przesłany w nagłówku;
- `rtsp`: czas odbioru pełnej zdekodowanej ramki;
- `dataset-y`: timestamp zachowany w `frames.csv`.

`PAIR_LAG_FRAMES` oznacza odległość w kolejności ramek. `pair_delta_seconds` jest metryką diagnostyczną.

## Kod aplikacji

```text
app/core          maska, selekcja, ekstraktory, health tests, pipeline
app/sources       transport i odczyt klatek
app/control       panel i nadzór jobów
app/reporting     analizy oraz raporty
app/qualification kampanie kwalifikacyjne
app/web           UI
```

Ta granica ma pozostać zachowana: moduły raportujące nie mogą modyfikować danych w torze produkcyjnym, a control server nie implementuje matematyki źródła.
