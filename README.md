# Camera Entropy Distributed 8.0.2

System do pozyskiwania niekompresowanych klatek Y8 z kamery, budowania źródła szumu, wykonywania health testów, kondycjonowania SHA3-512 oraz kwalifikacji źródła. Wersja 8.0.2 naprawia interpretację kontrolek V4L2 typu menu, automatycznie przywraca ekspozycję po rzeczywistej zmianie oraz przekazuje klientowi czytelne błędy agenta zamiast zamknięcia TLS bez komunikatu.

> To oprogramowanie badawcze. Dobre wyniki statystyczne i brak RCT/APT failures nie są automatycznie formalną certyfikacją SP 800-90B.

## Zatwierdzony profil produkcyjny

Domyślny profil `production-final` wykorzystuje:

```text
Y8 → temporal XOR → 1 LSB → pełna zamrożona maska
   → RCT/APT + clipping/shadow fail-closed
   → 2048 bitów wejścia → SHA3-512 → 512 bitów outputu
```

Najważniejsze parametry:

```text
SAMPLE_MODE=xor
LSB_BITS=1
PAIRING_MODE=disjoint
PAIR_LAG_FRAMES=4
SPATIAL_MASK_PATTERN=full
SERIALIZATION_ORDER=row-major
ENTROPY_CREDIT_BITS_PER_PIXEL=0.5
VON_NEUMANN_PASSES=0
CONDITIONER=sha3-512
CONDITIONER_INPUT_BITS=2048
```

Uruchomienie:

```bash
./scripts/run/run_production.sh
```

Dla zatrzymanego datasetu:

```bash
SOURCE_TYPE=dataset-y \
DATASET_DIR=data/frame-buffer-latest \
DATASET_VERIFY_HASHES=1 \
./scripts/run/run_production.sh
```

## Układ projektu

```text
agent/                          agent Go przy kamerze
  cmd/camera-entropy-agent/     kod agenta i testy
  config/                       przykładowa konfiguracja
  install.sh                    budowa, probe i instalacja systemd

app/
  control/                      control server, job manager, profile
  core/                         pipeline, maska, ekstrakcja, health tests
  qualification/                kampanie kwalifikacyjne
  reporting/                    analizy i generatory raportów
  sources/                      V4L2, TLS-Y, RTSP, dataset-y, recorder
  tools/                        narzędzia pomocnicze
  web/                          szablony oraz statyczne zasoby WWW

scripts/
  run/                          zwykłe uruchomienia i produkcja
  qualification/                kwalifikacje i macierze testów
  smoke/                        krótkie testy diagnostyczne
  admin/                        PKI, hasła, tokeny, uprawnienia

tests/                          self-testy
config/                         konfiguracje i baseline'y
deploy/                         przykłady reverse proxy
systemd/                        unity systemd
docs/                           dokumentacja techniczna i historia wydań
```

Kod aplikacji należy uruchamiać jako moduły `app.*` albo przez skrypty z `scripts/`. Nie należy przywracać plików Python do katalogu głównego.

## Wymagania głównej aplikacji

- Linux;
- Python 3.11 lub nowszy;
- `python3-venv`, `curl`, `flock`, `sha256sum`;
- `v4l-utils` dla lokalnego V4L2;
- `ffmpeg` i `ffprobe` dla RTSP;
- zależności z `requirements.txt`.

Skrypty automatycznie tworzą `.venv` i instalują zależności przy pierwszym uruchomieniu.

## Panel kontrolny

```bash
MAX_DATASET_JOBS=12 ./scripts/run/start_control_server.sh
```

Domyślny adres:

```text
http://127.0.0.1:8087/
```

Parametr równoległości można też podać bezpośrednio:

```bash
.venv/bin/python -m app.control.control_server --max-dataset-jobs 12
```

Zasady współbieżności:

- wiele zadań `dataset-y` może pracować równolegle;
- zadania `dataset-y` mogą działać jednocześnie z jednym zadaniem LIVE;
- tylko drugie równoległe zadanie LIVE jest blokowane, ponieważ kamera ma jednego konsumenta;
- każdy job otrzymuje własny port workera;
- `final-preproduction` rezerwuje pięć kolejnych portów dla swoich wariantów;
- limit `MAX_DATASET_JOBS` dotyczy jobów uruchamianych z panelu, nie wewnętrznych procesów pojedynczej kampanii.

Przycisk `Log` przy zadaniu otwiera pływające okno. Okno obsługuje automatyczne przewijanie, pauzę, zmianę zadania, czyszczenie widoku oraz zamknięcie przez `Esc` lub kliknięcie w tło. Log nie zajmuje już miejsca pod tabelą zadań.

Konfiguracja Nginx znajduje się w:

```text
deploy/nginx/nginx-camera-entropy.conf.example
```

## Agent kamery Go

Agent stale otwiera i opróżnia niekompresowany strumień YUYV. Z każdej klatki wyciąga pełną płaszczyznę Y8 i udostępnia ją przez istniejący protokół `CEYTLS01` z mTLS. Gdy klient nie jest podłączony, agent nie archiwizuje klatek — utrzymuje kamerę rozgrzaną i odrzuca payload.

### Automatyczny wybór urządzenia

Domyślnie agent kolejno sprawdza:

```text
/dev/video0
/dev/video1
/dev/video2
```

Dla każdego kandydata sprawdza:

1. istnienie urządzenia;
2. dostęp do odczytu i zapisu;
3. możliwość ustawienia ekspozycji;
4. możliwość ustawienia YUYV i żądanej geometrii;
5. faktyczne pobranie pełnych klatek testowych.

Pierwsza kamera, która przejdzie cały probe, zostaje użyta. Wymuszenie konkretnej kamery:

```text
DEVICE=/dev/video1
```

Sam test bez uruchamiania usługi:

```bash
cd agent
go run ./cmd/camera-entropy-agent --probe
```

### Instalacja agenta

Wymagany jest Go 1.22 lub nowszy oraz pliki `pki/ca.crt`, `pki/agent.crt`, `pki/agent.key`.

```bash
sudo ./agent/install.sh
```

Instalator:

- instaluje `v4l-utils`, jeżeli go brakuje;
- buduje i testuje statyczny binarny agent;
- tworzy użytkownika systemowego `cameraentropy`;
- dodaje go do grupy `video`;
- opcjonalnie instaluje regułę udev `GROUP=video, MODE=0660`;
- kopiuje konfigurację i certyfikaty;
- wykonuje probe `/dev/video0..2` jako użytkownik usługi;
- instaluje i uruchamia `camera-entropy-agent.service`.

Logi:

```bash
journalctl -u camera-entropy-agent -f
```

Kontrolki V4L2 typu menu są normalizowane do wartości numerycznych. Przykładowy poprawny odczyt:

```text
auto_exposure: 1 (Manual Mode)
```

jest interpretowany jako `auto_exposure=1`. Przy rzeczywistej zmianie ustawień agent najpierw próbuje ponownie ustawić tryb manualny i ekspozycję; zatrzymuje źródło dopiero wtedy, gdy korekta się nie powiedzie.

Gdy kamera nie przechodzi probe, instalator pokazuje uprawnienia urządzeń i grupy użytkownika. Typowa ręczna naprawa dla zwykłego użytkownika:

```bash
sudo usermod -aG video "$USER"
# wyloguj się i zaloguj ponownie
```

W środowisku bez podłączonej kamery można zainstalować usługę bez udanego probe:

```bash
sudo ALLOW_NO_CAMERA=1 ./agent/install.sh
```

Konfiguracja agenta po instalacji:

```text
/etc/camera-entropy/camera-agent.env
```

## Rejestrator Y8/LSB

Pełny Y8, limit 300 GB:

```bash
SOURCE_TYPE=tls-y \
TLS_HOST=192.168.1.2 \
FRAME_STORAGE_MODE=y8 \
FRAME_BUFFER_LIMIT_BYTES=300000000000 \
./scripts/run/run_frame_buffer.sh
```

Pakowane LSB całej klatki:

```bash
SOURCE_TYPE=tls-y \
FRAME_STORAGE_MODE=lsb-packed \
FRAME_BUFFER_LIMIT_BYTES=300000000000 \
./scripts/run/run_frame_buffer.sh
```

Domyślny wskaźnik:

```text
data/frame-buffer-latest
```

Dataset może być czytany podczas zapisu. Wpis w `frames.csv` jest znacznikiem zatwierdzenia pełnej klatki.

Szczegóły: [docs/BUFFERED_DATASETS.md](docs/BUFFERED_DATASETS.md).

## Zwykłe przebiegi

Pojedynczy przebieg:

```bash
SOURCE_TYPE=dataset-y \
DATASET_DIR=data/frame-buffer-latest \
./scripts/run/run_one.sh
```

Finalna kwalifikacja całego snapshotu:

```bash
SOURCE_TYPE=dataset-y \
DATASET_DIR=data/frame-buffer-latest \
DATASET_VERIFY_HASHES=1 \
./scripts/qualification/final_preproduction.sh \
  --workers 5 \
  --verify-workers 8
```

Kompleksowa macierz produkcyjna:

```bash
SOURCE_TYPE=dataset-y \
DATASET_DIR=data/frame-buffer-latest \
ASSESSMENT_LEVEL=full \
./scripts/qualification/production_assessment.sh
```

Pozostałe profile znajdują się w `scripts/qualification/` i `scripts/smoke/`.

## Cache integralności datasetu

Dla zatrzymanego, niezmienionego datasetu wynik pełnej weryfikacji SHA-256 jest przechowywany w:

```text
data/.integrity-cache/
```

Cache jest używany tylko, gdy nie zmieniły się: lista chunków, sumy oczekiwane, rozmiary, `mtime_ns`, plik `checksums.sha256` i rozwiązana ścieżka datasetu.

```bash
DATASET_VERIFY_HASHES=1 \
DATASET_VERIFY_CACHE=1 \
DATASET_VERIFY_WORKERS=8 \
./scripts/run/run_one.sh
```

Wymuszenie świeżej weryfikacji:

```bash
DATASET_VERIFY_CACHE=0 ./scripts/run/run_one.sh
```

## PKI i panel WWW

Generowanie mTLS:

```bash
./scripts/admin/generate_mtls_pki.sh
```

Generowanie konta panelu:

```bash
sudo ./scripts/admin/generate_web_credentials.sh
sudo ./scripts/admin/generate_share_token.sh
```

Opis certyfikatów: [pki/README.md](pki/README.md).

## Testy

Python:

```bash
PYTHONPATH=. python3 -m compileall -q app tests
for test in tests/selftest*.py; do PYTHONPATH=. python3 "$test" || exit 1; done
```

Agent Go:

```bash
cd agent
go test ./...
go build ./cmd/camera-entropy-agent
```

Skrypty Bash:

```bash
find scripts agent -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n
```

## Migracja z 7.x

Wersja 8.0.0 zmienia ścieżki. Najważniejsze odpowiedniki:

```text
camera_entropy_server.py          → app/core/camera_entropy_server.py
control_server.py                 → app/control/control_server.py
frame_sources.py                  → app/sources/frame_sources.py
masking.py                        → app/core/masking.py
run_one.sh                        → scripts/run/run_one.sh
start_control_server.sh           → scripts/run/start_control_server.sh
qualification_final_*.py          → app/qualification/ + scripts/qualification/
usb_y_capture_agent.py            → agent/cmd/camera-entropy-agent/
run_usb_agent.sh                  → agent/install.sh + systemd
```

Przy wdrożeniu pełnego drzewa zachowaj istniejące `data/`, `pki/` i lokalny `sources.json`. Zaktualizuj unity systemd, ponieważ stare wpisy `ExecStart` wskazują na pliki z katalogu głównego.

Historia zmian: [docs/CHANGELOG.md](docs/CHANGELOG.md).
