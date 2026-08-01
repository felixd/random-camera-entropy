# Camera Entropy Distributed v7.7.0


## Korelacja przestrzenna, maski i dokumentacja v7.7.0

Wersja 7.7.0 dodaje konfigurowalne maski `full`, obie fazy checkerboard, ogólną siatkę `grid`, wybór jednej pozycji z bloku `block`, przestrzenny offset temporalnego XOR oraz kolejności `row-major`, `serpentine` i `tile-interleave`.

Offset jest stosowany spójnie do temporalnego XOR, kalibracji, clippingu, shadow maski i walidacji. Krawędzie są odrzucane bez zawijania. Indeksy serializacji są cache'owane, dzięki czemu `tile-interleave` nie sortuje całej matrycy przy każdej ramce.

Panel WWW zawiera dziesięć profili przestrzennych i kampanię zbiorczą. Każde pole ma pomoc kontekstową dostępną przez hover i fokus klawiatury. Wszystkie pliki README, Markdown i tekstowe instrukcje są dostępne po zalogowaniu pod `/docs/`.

Pełny opis parametrów i zasad interpretacji znajduje się w [SPATIAL_SAMPLING.md](SPATIAL_SAMPLING.md).


## Odporność transportu TLS-Y v7.6.1

Agent USB otwiera teraz urządzenie V4L2 osobno dla każdej uwierzytelnionej sesji i zwalnia je natychmiast po jej zakończeniu. Zapobiega to pozostawianiu niedrenowanego strumienia OpenCV/V4L2 pomiędzy kolejnymi rundami kwalifikacji.

Zamykanie sesji używa komunikatów aplikacyjnych `close` / `close-ack`, dzięki czemu poprawne zakończenie workera nie kończy się oczekiwanym `Broken pipe` po stronie kamery.

Dla źródła `tls-y` domyślne ustawienia to:

```bash
SOURCE_FRAME_TIMEOUT_SECONDS=60
SOURCE_RECONNECT_ATTEMPTS=5
SOURCE_RECONNECT_BACKOFF_SECONDS=2
```

W kampanii `qualification_dual_weave_stagger.sh` liczba prób reconnect wynosi domyślnie 10. Reconnect przed rozpoczęciem produkcji resetuje scheduler par, warm-up i kalibrację maski. Po rozpoczęciu produkcji obowiązuje fail-closed i run musi zostać rozpoczęty od nowa.

## Trwały panel WWW v7.1

Od v7.1 publiczny port `8087` należy do osobnego **control servera**, który działa stale. Proces wykonujący obliczenia jest workerem uruchamianym tylko na czas testu i nasłuchuje wyłącznie na loopbackowym porcie `18087`.

```text
HTTPS / reverse proxy
        ↓
control_server.py — 127.0.0.1:8087 — działa stale
        ├── uruchamianie/zatrzymywanie testów
        ├── stan i log procesu
        ├── proxy aktywnego panelu workera
        ├── /data — wyniki i raporty
        └── opcjonalny read-only share
                         │
                         ▼
camera_entropy_server.py — 127.0.0.1:18087 — tylko podczas testu
```

Zakończenie testu nie wyłącza już strony WWW. Worker kończy pracę, `run_one.sh` wykonuje analizy i zapisuje `READY.json` oraz `run_report.html`, natomiast control server nadal udostępnia panel i katalog wyników.

### Pierwsze uruchomienie control servera

1. Skonfiguruj źródła. Plik `sources.json` jest odczytywany przy starcie aplikacji. Domyślnym pierwszym źródłem jest agent `camera` pod adresem `192.168.1.2`. Pełny wzór dla lokalnego USB, zdalnego USB/mTLS oraz RTSP znajduje się w `sources.example.json`.

```bash
cp sources.example.json sources.json
chmod 600 sources.json
```

URL i hasło RTSP pozostają w osobnym pliku `0600`; formularz WWW wybiera tylko zdefiniowane wcześniej `source_id`. Panel nie pozwala wprowadzać dowolnego polecenia ani dowolnych zmiennych środowiskowych.

2. Utwórz login, hash hasła i sekret sesji:

```bash
OUT_DIR="$HOME/.config/camera-entropy" \
CREDENTIALS_FILE="$HOME/.config/camera-entropy/web-user.json" \
SECRET_FILE="$HOME/.config/camera-entropy/web-secret.key" \
USERNAME=felixd \
./generate_web_credentials.sh
```

3. Uruchom trwały panel na domyślnym porcie:

```bash
WEB_CREDENTIALS_FILE="$HOME/.config/camera-entropy/web-user.json" \
WEB_SECRET_FILE="$HOME/.config/camera-entropy/web-secret.key" \
WEB_SOURCES_FILE="$PWD/sources.json" \
WEB_HOST=127.0.0.1 \
WEB_PORT=8087 \
WORKER_PORT=18087 \
./start_control_server.sh
```

Za reverse proxy strona jest dostępna pod adresem skonfigurowanym w Nginx, np. `https://camera.random.flameit.io`. Przykład konfiguracji znajduje się w `nginx-camera-entropy.conf.example`.

### Funkcje panelu

- uruchomienie profilu `smoke`, uproszczonego `temporal-sha3`, pojedynczego przebiegu, kwalifikacji, cold-start, checkerboard phases oraz testów `dual-weave`, w tym skupionego `dual-weave-stagger2`;
- wybór uprzednio skonfigurowanego źródła `v4l2`, `tls-y` lub `rtsp`;
- ustawienie ekspozycji, lagu ramek, masek przestrzennych, offsetu pikseli, serializacji, rozmiaru conditionera i limitów danych;
- zatrzymanie całej grupy procesów testu;
- ciągły log zadania;
- podgląd panelu aktywnego workera przez `/live`;
- historia zadań;
- przeglądanie i pobieranie wyników pod `/data/`;
- przeglądanie dokumentacji projektu pod `/docs/` oraz pomoc kontekstowa przy parametrach;
- raport `run_report.html` generowany dla każdego ukończonego przebiegu;
- raport kampanii `qualification_report.html` dla kwalifikacji.

Tylko jeden test może być aktywny jednocześnie dla wspólnego katalogu `data`. Zamknięcie przeglądarki nie przerywa testu.

## Uproszczony tor Temporal SHA3

Profil `temporal-sha3` realizuje proponowany tor produkcyjno-eksperymentalny bez checkerboardu, dual weave i Von Neumanna:

```text
LSB(frame g) XOR LSB(frame g-k)
        ↓
zamrożona pełna aktywna maska
        ↓
RCT / APT / clipping / kontrola dryftu maski
        ↓
SHA3-512
        ↓
512 bitów na blok conditionera
```

Domyślny smoke używa bloku `65 536` surowych bitów na jeden digest SHA3-512, czyli kompresji `128:1`, zapisuje 1 MiB wyjścia SHA3 i 8 MiB strumieni walidacyjnych. Wartość jest punktem startowym do testów porównawczych, a nie formalnie zatwierdzonym współczynnikiem kredytowania entropii. Można ją zmienić polem `Conditioner input [bit]` lub zmienną `CONDITIONER_INPUT_BITS`.

```bash
SOURCE_TYPE=tls-y ./smoke_temporal_sha3.sh
```

W tym profilu `ENABLE_VON_NEUMANN=0`, więc etap VN nie jest nawet obliczany. Health tests działają na temporalnych bitach po zamrożonej masce przed SHA3-512. Powtórzenie kolejnego digestu SHA3 nadal powoduje latch fail-closed.

### Obrazy diagnostyczne

Generowanie klatki, kanału Y, mapy LSB i masek w panelu workera jest domyślnie wyłączone. Tak samo okresowe PNG masek. Obie funkcje można zaznaczyć przed uruchomieniem testu:

- `generuj obrazy podglądu WWW workera` → `WEB_IMAGES=1`;
- `archiwizuj okresowe PNG masek` → `MASK_SNAPSHOT_IMAGES=1`.

Wyłączenie PNG masek nie wyłącza metryk dryftu, CSV, JSON ani wykresu Plotly. Dzięki temu kontrola retencji/Jaccarda nadal działa bez kosztu kodowania i przesyłania obrazów.

## Raporty i diagnostyka live v7.7.0

Raporty są teraz projektowane jako krótki panel decyzyjny, a nie surowy zrzut wszystkich pól. Najważniejsze metryki i werdykt znajdują się na górze, a pełne tabele pozostają w sekcjach rozwijanych.

Wykresy korzystają z lokalnej kopii Plotly.js:

```text
static/vendor/plotly-3.3.1.min.js
```

Nie jest potrzebny zewnętrzny CDN. Dzięki temu wykresy działają również w raportach otwieranych przez zalogowane `/data/` oraz read-only `/share/<token>/data/...`. Gdy JavaScript jest wyłączony, wszystkie wartości nadal są dostępne w tabelach HTML, JSON i CSV.

Najważniejsze raporty:

- `run_report.html` — porównanie etapów RAW → VN → SHA3, health, clipping i stabilność maski;
- `dual_weave_report.html` — przepustowość, korelacja pozycyjna C0↔C1, lagi granic bloków i porównanie `same-group` / `stagger-1` / `stagger-2`;
- `binary_geometry_report.html` — heatmapa liczności przejść, mapa reszt Pearsona względem niezależności, histogramy marginalne, H(X), H(Y), H(X,Y), H(Y|X), MI, Cramér V oraz interaktywna bryła i chmura 3D;
- `dual_weave_campaign_report.html` — powtarzalność wariantów w kampanii i diagnostyczny ranking;
- `qualification_report.html` — powtarzalność przebiegów, finalne SHA3, maska i pozostała korelacja przestrzenna.

Katalog główny `/data/` i share read-only wyróżniają właściwy raport dla każdego przebiegu oraz pokazują krótkie podsumowanie bez wchodzenia w strukturę plików.

### Diagnostyka workera na żywo

Panel workera pobiera dane bezpośrednio z `/api/byte-diagnostics` i rysuje je lokalnym Plotly bez wpływu na kolejność ani zawartość zapisywanych strumieni. Domyślny widok pokazuje każdy etap na osobnym wykresie liniowym z własną symetryczną skalą odchylenia od `1/256`. Operator może przełączyć widok na linie nakładane ze wspólną skalą; wybór jest zapisywany w `localStorage` przeglądarki. `Direct LSB — aktywna maska` pozostaje w osobnym panelu, aby nie psuć skali etapów po czyszczeniu. Monitorowane są etapy:

```text
Direct LSB
Temporal XOR — pełna mapa
Temporal XOR — aktywna maska
Von Neumann
SHA3-512
```

W trybie dual weave pojawia się osobna siatka wykresów C0/C1 RAW, C0/C1 po Von Neumannie, rzeczywistego wejścia conditionera i SHA3-512. Liczniki bitowe są pakowane ciągle pomiędzy ramkami, bez sztucznego dopełniania każdego fragmentu do pełnego bajtu. Panel pokazuje też H, histogramową Hmin, średnią bajtu i liczbę pełnych bajtów dla każdego etapu.

Histogramy live używają liniowych śladów Plotly `scatter`, czyli renderera SVG. Nie wymagają WebGL i działają z polityką CSP projektu bez dodawania `unsafe-eval`. Dotyczy to zarówno histogramów częstotliwości, jak i wykresu dryftu maski. Błąd renderowania jest przechwytywany i pokazywany wewnątrz panelu zamiast pozostawienia pustego pola.

Wykres dryftu maski pokazuje retencję aktywnej maski i indeks Jaccarda wraz z progami fail-closed. Dane niepoprawne, puste i niefinitywne są odrzucane przed przekazaniem ich do Plotly.

Worker okresowo zapisuje wielopanelowy obraz:

```text
live_byte_heatmaps.png
```

Każdy panel to macierz przejść `B_n → B_(n+1)` znormalizowana do liczby par na milion i pokazana we wspólnej skali `log10(1+pairs_per_million)`. Normalizacja pozwala porównywać etapy o różnej przepustowości bez przyciemniania wolniejszych strumieni. Częstotliwość generowania jest ustawiana w panelu WWW albo zmienną `LIVE_HEATMAP_INTERVAL_SECONDS`. Generowanie obrazu odbywa się w osobnym wątku.

Tekst w generowanych PNG jest rysowany przez Pillow i zainstalowany systemowy font TrueType, dzięki czemu zachowane są polskie znaki. Projekt domyślnie szuka DejaVu Sans, Liberation Sans lub Noto Sans. W nietypowym systemie można jawnie wskazać font bez kopiowania go do projektu:

```bash
CAMERA_ENTROPY_FONT=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf
CAMERA_ENTROPY_FONT_BOLD=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
```

Najważniejsze ustawienia:

```text
LIVE_BYTE_DIAGNOSTICS=1
LIVE_HEATMAP_INTERVAL_SECONDS=60   # 0 wyłącza zapis okresowego PNG
LIVE_HEATMAP_MAX_STAGES=8
LIVE_HEATMAP_MIN_BYTES=4096
```

### Dostęp read-only do raportów

Opcjonalny token pozwala udostępnić wyniki bez dostępu do przycisków sterujących:

```bash
OUT_FILE="$HOME/.config/camera-entropy/report-share.token" \
./generate_share_token.sh

WEB_SHARE_TOKEN_FILE="$HOME/.config/camera-entropy/report-share.token" \
./start_control_server.sh
```

Po zalogowaniu panel pokazuje odnośnik w postaci:

```text
https://camera.random.flameit.io/share/<długi-losowy-token>/
```

Jest to dostęp tylko do odczytu. Token należy traktować jak hasło: jest obecny w URL i może trafić do historii przeglądarki oraz logów reverse proxy. Bezpieczniejszą opcją dla stałej administracji pozostaje normalne logowanie do panelu.

### Porty

```text
8087   control server — stały port panelu i /data
18087  worker — tylko 127.0.0.1, uruchamiany na czas testu
9443   agent USB mTLS — na hoście przy kamerze
```

Nie należy wystawiać `18087` do sieci.

Rozdzielona wersja kandydata przedprodukcyjnego v6.1.3. Przechwytywanie USB może działać na małym hoście przy kamerze, natomiast kalibracja, maski, korelacje, RCT/APT, Von Neumann, SHA3-512, raporty i panel WWW działają na mocnym serwerze obliczeniowym.

> Projekt nadal jest eksperymentalnym źródłem entropii. Każdy typ kamery i tor wejściowy wymaga osobnej oceny SP 800-90B. Dobrych wyników SHA3 nie wolno utożsamiać z udowodnioną min-entropią wejścia.

## Architektura

### USB przez bezpieczną sieć

```text
host przy kamerze USB
  V4L2 / YUYV
  → bezpośrednie wyjęcie kanału Y
  → niekompresowane ramki Y8
  → mTLS 1.3 + numer ramki + timestamp źródła + SHA-256 ramki
                       │
                       ▼
serwer obliczeniowy
  disjoint k=4
  → temporalny XOR LSB
  → aktywna maska
  → checkerboard-even
  → RCT/APT
  → diagnostyczny Von Neumann
  → SHA3-512 2048 → 512
```

Host USB **nie wykonuje** kalibracji, ekstrakcji entropii, RCT/APT, Von Neumanna, SHA3 ani analiz. Wykonuje tylko obsługę V4L2, wyjęcie istniejących bajtów Y z YUYV, kontrolę ekspozycji, TLS i kontrolną sumę SHA-256 transportowanej ramki.

W sieci nie używamy JPEG, H.264 ani H.265 dla kamery USB. Przesyłany jest niezmieniony, niekompresowany kanał Y8.

### RTSP

```text
kamera RTSP
  H.264 / H.265 / MJPEG / inny kodek
  → FFmpeg na serwerze obliczeniowym
  → zdekodowana płaszczyzna Y8
  → ten sam pipeline analityczny
```

RTSP jest osobnym modelem źródła. Typowa kamera RTSP dostarcza dane po ISP i stratnym kodeku. Wyników RTSP nie wolno mieszać z kwalifikacją bezpośredniego USB/YUYV ani łączyć kilku kamer w jeden strumień przed osobną oceną każdego źródła.

## Tryby wejścia

```text
SOURCE_TYPE=v4l2   lokalna kamera USB, zgodność z v6.1.3
SOURCE_TYPE=tls-y  zdalny agent USB przez wzajemny TLS 1.3
SOURCE_TYPE=rtsp   odbiór i dekodowanie przez FFmpeg
```

Jeden proces przetwarza jedno źródło. Kilka kamer uruchamia się jako osobne instancje z innymi `PORT`, `RUN_DIR` i katalogami danych. Takie rozdzielenie zapobiega przypadkowemu mieszaniu modeli entropii.

## Wymagania

### Host USB

- Linux, Python 3 i `venv`;
- `v4l2-ctl`;
- dostęp R/W do `/dev/video*` bez `sudo`;
- sieć do serwera obliczeniowego;
- certyfikat agenta, klucz agenta i zaufany CA.

`run_usb_agent.sh` tworzy osobne `.venv-agent` i instaluje tylko NumPy oraz OpenCV headless.

### Serwer obliczeniowy

- Linux, Python 3 i `venv`;
- `curl`, `flock`, `sha256sum`;
- dla RTSP: `ffmpeg` i `ffprobe`;
- dla lokalnego V4L2: również `v4l2-ctl`.

## 1. Utworzenie lokalnego PKI mTLS

W bezpiecznym miejscu:

```bash
chmod +x ./*.sh

./generate_mtls_pki.sh
```

Bez dodatkowych zmiennych skrypt używa `pki/` w katalogu projektu i generuje certyfikat serwera dla `camera` / `192.168.1.2`. Skrypt tworzy:

```text
ca.crt
ca.key
agent.crt
agent.key
client.crt
client.key
```

Na host USB kopiujemy tylko:

```text
ca.crt
agent.crt
agent.key
```

Na serwer obliczeniowy:

```text
ca.crt
client.crt
client.key
```

`ca.key` nie jest potrzebny do pracy i powinien pozostać poza hostami roboczymi. Klucze prywatne muszą mieć prawa `0600`.

## 2. Agent na hoście USB

```bash
DEVICE=/dev/video1 \
EXPOSURE=7000 \
TLS_PORT=9443 \
./run_usb_agent.sh
```

Domyślnie agent:

- wymaga certyfikatu klienta podpisanego przez wskazany CA;
- wymaga TLS 1.3;
- udostępnia jednego klienta naraz;
- wysyła Y8 `1280×720` bez kompresji;
- przesyła identyfikator i czas przechwycenia każdej ramki;
- okresowo kontroluje `auto_exposure=1` oraz `exposure_time_absolute=7000`;
- zamyka sesję po zmianie kontrolek.

Port `9443` należy ograniczyć firewallem wyłącznie do adresu serwera obliczeniowego.

Domyślne ścieżki agenta to `pki/ca.crt`, `pki/agent.crt` i `pki/agent.key` względem katalogu projektu. Można je nadpisać przez `PKI_DIR`, `TLS_CA`, `TLS_CERT` i `TLS_KEY`.

## 3. Smoke zdalnej kamery USB

Na mocnym serwerze:

```bash
EXPOSURE=7000 \
./run_remote_usb_smoke.sh
```

Normalny przebieg:

```bash
SOURCE_TYPE=tls-y \
WARMUP_SECONDS=1800 \
CALIBRATION_PAIRS=512 \
CONDITIONED_BYTES=104857600 \
./run_one.sh
```

Domyślne połączenie klienta używa `TLS_HOST=192.168.1.2`, `TLS_SERVER_NAME=camera` oraz `pki/ca.crt`, `pki/client.crt` i `pki/client.key` z katalogu projektu. Każdą z tych wartości można nadpisać zmienną środowiskową.

Domyślnie przerwa w numeracji ramek z agenta jest błędem fail-closed. `ALLOW_SOURCE_FRAME_GAPS=1` istnieje wyłącznie do diagnostyki i nie powinno być używane w kwalifikacji.

## 4. RTSP

Najbezpieczniej przechowywać pełny URL, w tym hasło, w pliku z prawami `0600`:

```bash
install -m 600 /dev/null ~/.config/camera-entropy/camera01.rtsp
printf '%s\n' 'rtsp://user:password@192.168.1.60:554/cam/realmonitor?channel=1&subtype=0' \
  > ~/.config/camera-entropy/camera01.rtsp
```

Smoke:

```bash
RTSP_URL_FILE=~/.config/camera-entropy/camera01.rtsp \
RTSP_TRANSPORT=tcp \
RTSP_LUMA_MODE=extract-y \
./run_rtsp_smoke.sh
```

Program nie zapisuje hasła w `command.txt`, `runner_config.json`, panelu ani manifeście. W logach URL jest redagowany.

Dostępne ustawienia:

```text
RTSP_TRANSPORT=tcp|udp|http|https
RTSP_LUMA_MODE=extract-y|gray-convert
RTSP_TIMEOUT_SECONDS=15
RTSP_STRICT_DIMENSIONS=0|1
WIDTH=1280
HEIGHT=720
```

`extract-y` pobiera zdekodowaną płaszczyznę luminancji. `gray-convert` jest wariantem zgodności dla źródeł, dla których filtr `extractplanes=y` nie działa.

Sam `rtsp://` nie zapewnia poufności poświadczeń ani obrazu. Kamera RTSP powinna pracować w odizolowanym VLAN-ie, przez VPN albo przez wariant szyfrowany obsługiwany przez konkretną kamerę.

## 5. Lokalny tryb zgodności

```bash
SOURCE_TYPE=v4l2 \
DEVICE=/dev/video1 \
./smoke_preproduction.sh
```

## Dual weave v2: same-group kontra stagger

Wyniki v7.2 pokazały, że zwykły dual weave odzyskuje około `2×` przepustowości, lecz przenosi lokalną korelację pikseli do relacji krzyżowej `C0↔C1` przy przesunięciu `±1`. v7.3 dodaje dwa warianty czasowego rozdzielenia komplementarnych faz:

```text
same-group: C0_g + C1_g
stagger-1:  C0_g + C1_(g+1)
stagger-2:  C0_g + C1_(g+2)
```

W `row-major` komplementarne strumienie są budowane jako:

```text
C0_g = A_g[EVEN] + B_g[ODD]
C1_g = B_g[EVEN] + A_g[ODD]
```

Piksele aktywnej maski są czytane od lewej do prawej, wiersz po wierszu. Nie następuje przeplatanie pojedynczych bitów C0/C1. Conditioner dostaje dwa bloki po 1024 bity.

Dla `stagger-2` pierwsze dwie grupy `C1` oraz dwie końcowe grupy `C0` nie trafiają do conditionera. Pierwszy pełny blok ma dokładnie postać `C0_g || C1_(g+2)`. W długim przebiegu strata jest pomijalna, a każda wykorzystana próbka nadal występuje tylko raz.

### Skupiony smoke row-major / stagger-2

Profil WWW:

```text
dual-weave-stagger2
```

lub z terminala:

```bash
SOURCE_TYPE=tls-y ./smoke_dual_weave_stagger2.sh
```

Profil uruchamia tylko `row-major / stagger-2`, zachowuje checkerboard EVEN/ODD i automatycznie generuje raport geometrii 2D/3D.

### Smoke porównawczy

Profil WWW:

```text
dual-weave
```

lub z terminala:

```bash
SOURCE_TYPE=tls-y ./smoke_dual_weave.sh
```

Domyślnie smoke używa:

```text
exposure:              7000
pairing:               disjoint/k4
order:                 row-major
alignments:            same-group, stagger-1, stagger-2
warm-up:               60 s
calibration:           128 par
baseline SHA3:         5 MiB
każdy dual SHA3:       5 MiB
C0/C1 VN:              5 MiB
walidacja:             4 MiB
conditioner:           2048 → 512
```

Najważniejsze pliki:

```text
dual_weave_row_major_c0_raw_validation.bin
dual_weave_row_major_c1_raw_validation.bin

dual_weave_row_major_same_group_conditioner_input_validation.bin
dual_weave_row_major_same_group_sha3_512.bin

dual_weave_row_major_stagger_1_conditioner_input_validation.bin
dual_weave_row_major_stagger_1_sha3_512.bin

dual_weave_row_major_stagger_2_conditioner_input_validation.bin
dual_weave_row_major_stagger_2_sha3_512.bin
```

Raport:

```text
dual_weave_report.html
dual_weave_report.json
dual_weave_comparison.csv
dual_weave_cross_correlation.csv
dual_weave_positional_correlation.csv
binary_geometry_report.html
binary_geometry_summary.json
binary_geometry_metrics.csv
dual_weave_stagger2_checkerboard.svg
```

### Geometria 2D/3D plików BIN

`run_one.sh` domyślnie uruchamia analizę geometrii dla przebiegów dual weave. Analiza tworzy macierz `256 × 256` o stałej orientacji:

```text
X = B_n
Y = B_(n+1)
wartość = liczba wystąpień pary X → Y
```

Powstają trzy obrazy:

```text
byte_histogram_<strumień>.png       częstość 256 wartości bajtu + poziom 1/256
byte_pairs_2d_<strumień>.png        log10(1 + obserwowana liczność pary)
byte_pairs_residual_<strumień>.png  reszta Pearsona (O-E)/sqrt(E), skala -8…+8
```

Model niezależności zachowuje oba histogramy marginalne:

```text
E(x,y) = count_X(x) × count_Y(y) / N
```

Histogram pokazuje marginalny bias wartości bajtów. Heatmapa liczności pokazuje wszystkie struktury przejść, również te wynikające wyłącznie z tego biasu, a mapa reszt izoluje lokalne odchylenia od niezależności kolejnych bajtów. Nad heatmapą i po jej lewej stronie znajdują się marginalne histogramy `B_n` i `B_(n+1)`.

Obrazy nie są samodzielną „entropią 2D”. Z tych samych danych raport liczy:

```text
H(X), H(Y)    entropie obu histogramów marginalnych
H(X,Y)        entropia łączna pary, maksimum 16 bitów
H(Y|X)        niepewność następnego bajtu po poznaniu poprzedniego
Hmin(X,Y)     histogramowa min-entropia pary
I(X;Y)        informacja wzajemna
MI excess     MI ponad deterministyczny shuffle-baseline
chi2 / df     globalne odchylenie od modelu niezależności
Cramer V      znormalizowana siła zależności
|r| p95/p99   percentyle bezwzględnych reszt Pearsona
```

Skompresowany plik `byte_pairs_counts_<strumień>.npz` zawiera `counts`, `expected`, `pearson_residuals`, `log2_enrichment`, `x_counts`, `y_counts` i `byte_counts`, więc można wykonać dalszą analizę bez ponownego czytania dużego BIN-a.

Jeżeli odpowiednie pliki istnieją, raport zawsze rezerwuje miejsce dla czterech etapów porównawczych:

```text
Direct LSB → temporal difference + active mask → Von Neumann → SHA3-512
```

W sekcji porównawczej histogramy używają wspólnej skali częstotliwości, wszystkie heatmapy liczności wspólnego maksimum `log10(1+count)`, a mapy reszt wspólnej skali `-8…+8`. Dzięki temu nie można przypadkowo ukryć różnic przez automatyczne przeskalowanie każdego obrazu osobno. Każdy histogram ma również interaktywny odpowiednik Plotly w raporcie.

Raport zawiera powierzchnię 3D obserwowanej gęstości oraz chmurę kolejnych trójek `(B_n, B_(n+1), B_(n+2))`. Przy obecnej restrykcyjnej CSP i pełnym pakiecie Plotly widoki WebGL są celowo zastępowane automatycznie wygenerowanymi statycznymi projekcjami PNG. Eliminuje to komunikaty `eval`/WebGL bez osłabiania CSP przez `unsafe-eval`. Dane 3D pozostają zapisane w raporcie i będzie można ponownie włączyć interakcję po przejściu na CSP-safe strict bundle Plotly. Są to narzędzia diagnostyczne, nie zamiennik SP 800-90B non-IID.

Limity analizy można ustawić przez:

```text
BINARY_GEOMETRY_REPORT=0|1
BINARY_GEOMETRY_MAX_FILES=8
BINARY_GEOMETRY_MAX_BYTES=16777216
BINARY_GEOMETRY_SCATTER_POINTS=15000
```

### Nowe kontrole v7.3

Każdy conditioner zapisuje własne:

```text
production_started_utc
conditioner_completed_utc
time_to_target_seconds
output_bps_until_complete
```

Przepustowość nie jest już dzielona przez czas trwania całego zadania równoległego. Dzięki temu dual weave można poprawnie porównać z baseline checkerboard-even.

Analiza wejścia SHA3 obejmuje:

```text
lagi: 1022, 1023, 1024, 1025, 1026
lagi: 2047, 2048, 2049
C0[j] ↔ C1[j-2..j+2] wewnątrz każdego bloku
```

Dodatkowo fail-closed clipping działa osobno na:

```text
full active mask
checkerboard-even
checkerboard-odd
```

Przekroczenie progu przez dowolną fazę odrzuca parę i po wymaganej liczbie kolejnych zdarzeń zatrzaskuje generator.

### Kampania lagów

```bash
SOURCE_TYPE=tls-y ./smoke_dual_weave_lags.sh
```

Sekwencja pozostaje:

```text
k4-start → k2 → k8 → k4-repeat
```

Każdy przebieg porównuje domyślnie `same-group` i docelowy `stagger-2` w `row-major`.

### Długa kwalifikacja stagger-2

Po pozytywnym smoke:

```bash
SOURCE_TYPE=tls-y ./qualification_dual_weave_stagger.sh
```

Domyślnie powstają trzy przebiegi, każdy z:

```text
warm-up pierwszego:    1800 s
warm-up kolejnych:     300 s
calibration:           512 par
baseline SHA3:         100 MiB
dual stagger-2 SHA3:   100 MiB
walidacja RAW:         10 MiB
VN diagnostyczny:      10 MiB
```

Profil jest również dostępny w panelu WWW jako:

```text
dual-weave-stagger-qualification
```

### Sumy SHA-256

`SHA256SUMS` jest teraz tworzony rekursywnie. Dla kampanii zawiera pliki BIN z katalogów wszystkich przebiegów zamiast pustego pliku na poziomie kampanii.

Dual weave pozostaje kandydatem przedprodukcyjnym. Wynik SHA3 nie zastępuje estymacji SP 800-90B non-IID.

## Kwalifikacja

`qualification_preproduction.sh` przekazuje ustawienia źródła do `run_one.sh`, więc działa również dla `tls-y` i `rtsp`.

Przykład zdalnego USB:

```bash
SOURCE_TYPE=tls-y \
RUNS=3 \
./qualification_preproduction.sh
```

Dla każdej innej kamery RTSP należy utworzyć osobną kampanię, osobny katalog wyników i osobną ocenę non-IID/restart.

## Bezpieczeństwo transportu USB

Warstwa aplikacyjna ramki zawiera:

```text
wersję protokołu
source_id
frame_id
czas Unix i monotoniczny z hosta przechwytującego
width / height / pixel_format=Y8
stan kontrolek kamery
liczbę bajtów
SHA-256 payloadu
```

TLS zapewnia poufność, integralność i wzajemne uwierzytelnienie. SHA-256 ramki jest dodatkową kontrolą diagnostyczną i nie jest conditionerem entropii.

## Przepustowość sieci

Dla Y8 `1280×720`:

```text
1 ramka = 921 600 B
1,5 FPS  ≈ 11,1 Mbit/s
10 FPS   ≈ 73,7 Mbit/s
```

Gigabit Ethernet ma duży zapas. Wi-Fi jest mniej pożądane ze względu na zmienność opóźnień i zerwania; przerwa numeracji ramek domyślnie zatrzymuje przebieg.

## Najważniejsze pliki

```text
usb_y_capture_agent.py       agent przy kamerze USB
frame_transport.py           protokół i konfiguracja mTLS
frame_sources.py             V4L2 / TLS-Y / RTSP
camera_entropy_server.py     cały pipeline obliczeniowy
smoke_temporal_sha3.sh       temporal LSB + pełna maska + health tests + SHA3-512
spatial_sampling.py          maski, offset bez wrap-around i serializacja bitów
spatial_profile.sh           wspólna baza profili geometrii przestrzennej
smoke_spatial_profiles.sh    pełna kampania porównawcza wszystkich profili przestrzennych
summarize_spatial_campaign.py  indeks JSON/HTML kampanii przestrzennej
spatial_docs.py              chroniona przeglądarka README/Markdown/instrukcji pod /docs/
SPATIAL_SAMPLING.md          pełna dokumentacja parametrów i interpretacji wyników
smoke_dual_weave.sh          same-group kontra stagger-1/stagger-2
smoke_dual_weave_stagger2.sh skupiony kandydat row-major / stagger-2
smoke_dual_weave_lags.sh     kampania k4/k2/k8/k4: same-group kontra stagger-2
analyze_dual_weave.py        lagi 1023/1024/1025, korelacja pozycyjna i raporty
analyze_binary_geometry.py   histogramy bajtów, liczności, reszty vs niezależność, entropie/MI i widoki 3D
run_usb_agent.sh             uruchomienie hosta USB
run_remote_usb_smoke.sh      smoke z mTLS-Y
run_rtsp_smoke.sh            smoke RTSP
run_one.sh                   wspólny runner obliczeniowy
generate_mtls_pki.sh         lokalne CA i certyfikaty
```

## Ograniczenia v7.7.0

- jedna instancja przetwarza jedno źródło;
- agent USB obsługuje jednego klienta naraz;
- RTSP używa czasu odbioru po dekodowaniu, nie PTS kamery, do raportowania `pair_delta_seconds`;
- brak sterowania ekspozycją kamer RTSP, ponieważ mechanizm jest zależny od producenta/ONVIF;
- nie ma automatycznego łączenia entropii z wielu kamer;
- stagger usuwa bezpośrednie współdzielenie grupy, ale nie dowodzi niezależności C0 i C1;
- histogramy bajtów oraz wykresy 2D/3D są diagnostyką wizualną i nie są formalnym estymatorem min-entropii;
- interaktywne widoki 3D zależą od WebGL, ale raport zawsze generuje ich statyczne odpowiedniki PNG;
- diagnostyka live utrzymuje macierze `256×256` w pamięci dla obserwowanych etapów; dla wielu wariantów dual weave należy ograniczyć `LIVE_HEATMAP_MAX_STAGES`;
- certyfikacja i deklaracja min-entropii nadal wymagają pełnej oceny źródła.

### API read-only dla późniejszej analizy

Po włączeniu tokenu share dostępne są również:

```text
/share/<token>/api/runs
/share/<token>/api/latest
```

`api/latest` zwraca metadane najnowszego przebiegu oraz, gdy istnieją, `qualification_summary.json`, `dual_weave_report.json`, `binary_geometry_summary.json`, `runner_summary.json`, `READY.json` lub `run_failed.json`. Dzięki temu zewnętrzny analizator może odczytać wynik bez dostępu do panelu administracyjnego.
