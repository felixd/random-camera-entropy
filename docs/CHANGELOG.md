# Changelog

## 8.0.1 — 2026-08-05

- panel WWW wyłącza i pomija wszystkie pola `dataset_*` oraz `final_preprod_*`, gdy wybrane źródło nie jest typu `dataset-y`;
- formularz nie wysyła ukrytych parametrów datasetu do źródeł LIVE, nawet gdy pozostały ustawione po zmianie profilu;
- backend defensywnie ignoruje nieistotne parametry datasetowe dla `v4l2`, `tls-y` i `rtsp` zamiast zwracać błąd HTTP 400;
- dodano test regresyjny uruchomienia profilu produkcyjnego LIVE z payloadem zawierającym pozostałości ustawień datasetu.

## 8.0.0 — 2026-08-05

- zastąpiono Pythonowego agenta USB agentem Go z ciągłym warm-upem, CEYTLS01/mTLS i automatycznym probe `/dev/video0..2`;
- dodano instalator agenta tworzący użytkownika systemowego, członkostwo w grupie `video`, regułę udev, konfigurację PKI oraz usługę systemd;
- przeniesiono kod aplikacji do logicznie podzielonego pakietu `app/`;
- przeniesiono entrypointy do `scripts/run`, kwalifikacje do `scripts/qualification`, smoke testy do `scripts/smoke`, a administrację do `scripts/admin`;
- logi zadań w panelu są wyświetlane w pływającym oknie modalnym zamiast pod tabelą;
- wiele zadań `dataset-y` może działać równolegle oraz współistnieć z jednym zadaniem LIVE; drugie źródło LIVE pozostaje blokowane;
- dodano `MAX_DATASET_JOBS` i `--max-dataset-jobs`;
- zaktualizowano unity systemd, ścieżki raportów, importy, testy i dokumentację.

## 7.14.1 — 2026-08-05

- naprawiono źródło `dataset-y`, które mimo istniejącego cache zawsze wymuszało pełną ponowną weryfikację SHA-256 (`use_cache=False`);
- zwykłe przebiegi, profil produkcyjny i kwalifikacja przedprodukcyjna używają teraz wspólnego pliku `data/.integrity-cache/<dataset-key>.json`;
- cache jest używany wyłącznie dla niezmienionego, nieaktywnego datasetu i nadal wymaga identycznego manifestu checksum, oczekiwanych hashy, rozmiarów oraz `mtime_ns` wszystkich chunków;
- dodano `--dataset-verify-cache`, `--no-dataset-verify-cache` i `--dataset-verify-cache-dir`;
- panel WWW udostępnia cache dla każdego przebiegu `dataset-y`, nie tylko profilu final-preproduction;
- dodano test integracyjny potwierdzający cache miss przy pierwszym otwarciu i cache hit przy drugim.

## 7.14.0 — 2026-08-05

- dodano zatwierdzony profil produkcyjny XOR/1 LSB, disjoint lag 4, pełna maska, SHA3-512/2048, credit 0,5;
- usunięto semantyczną niespójność `checkerboard-even` kontra faktyczna pełna maska;
- raport pipeline'u poprawnie obsługuje numeryczne `0/1` dla etapu Von Neumanna;
- manifesty i API raportują efektywną selekcję przestrzenną oraz osobno wartość legacy;
- produkcyjny skrypt wyłącza kosztowną diagnostykę i pliki walidacyjne.

## 7.13.0 — 2026-08-04

- równoległa weryfikacja SHA-256 zamkniętych chunków z parametrem `--verify-workers` / `FINAL_PREPROD_VERIFY_WORKERS`;
- natychmiastowe logi startu, cykliczny postęp procentowy, prędkość, ETA oraz heartbeat długich analiz;
- cache pełnej weryfikacji dla niezmienionych, zatrzymanych datasetów;
- wspólny moduł `dataset_integrity.py` używany przez final preproduction i zwykłe źródło `dataset-y`;
- parametry liczby workerów dostępne w Control Panelu oraz CLI;
- poprawiony układ tabel „Zadania” i „Raporty”; sticky nagłówek tabeli używa `top:0`, a kolumny nie zlewają się;
- wersja podbita do `2026.08.04.camera-entropy-distributed.7.13.0`.

## 7.12.0 — 2026-08-04

- Added the final pre-production profile using the complete current dataset snapshot.
- Added parallel dataset-y jobs and per-job worker ports; live sources remain exclusive.
- Rebuilt the Control Panel around the actual entropy pipeline stages and removed the embedded preview.
- Extracted mask logic to `masking.py` and extraction helpers to `entropy_extractors.py`.
- Added 0–4 Von Neumann passes with per-pass retention diagnostics.
- Added dependency-aware form controls and authoritative server-side validation.
- Added streaming whole-dataset symbol statistics, including aggregate and worst-window Hmin/bias/lag metrics, without storing an unbounded validation bitstream.
- Added a standalone final pre-production report with exact case parameters, rate charts and production gates.

## 7.11.0 — 2026-08-03

- ograniczono wszystkie aktywne profile i walidację do maksymalnie 4 LSB;
- kampania LSB wykonuje 12 profili `xor/direct/delta × 1..4` i generuje schemat podsumowania v4;
- dodano wykresy dla danych porównawczych z tabel oraz wspólny przełącznik jednostek `bit/s`, `kbit/s`, `kB/s`, `MiB/s`, `MB/s`; domyślne jest `kB/s`;
- dodano profil `production-assessment` z poziomami `quick`, `full` i `exhaustive`;
- kompleksowa macierz obejmuje tryby/szerokości LSB, pairing i lagi, geometrię przestrzenną, entropy credit, sweep SHA3, dual weave i powtarzalność;
- `production_assessment_report.html` jest pojedynczym, samodzielnym plikiem z osadzonym Plotly, pełnym JSON i dokładnymi parametrami każdego testu;
- panel WWW pozwala wybrać poziom kompleksowej oceny;
- dodano testy regresyjne macierzy przypadków, raportu standalone i ograniczenia 1..4 LSB.

## 7.10.0 — 2026-08-03

- przeniesiono RCT/APT profili wielobitowych ze zserializowanego bitstreamu na źródłowe symbole k-bitowe;
- wdrożono SP 800-90B APT z symbolem referencyjnym i oknami 1024/512 dla źródeł binarnych/niebinarnych;
- nieudane przebiegi generują raporty oraz analizy częściowych danych przed zwróceniem kodu błędu;
- raport kampanii używa przepustowości lifetime, pokazuje podstawę pomiaru, przyczynę błędu i SHA3/credited entropy;
- wyłączono domyślnie kosztowne diagnostyki wizualne i VN w seryjnej kampanii LSB;
- dodano testy regresyjne health testów symbolowych i raportowania przebiegów failed.

## 7.9.2 — 2026-08-03

- zintegrowano w pełnym drzewie projektu ciągły warm-up agenta USB, rejestrator Y8/LSB, aktywne źródło `dataset-y`, względne `data/frame-buffer-latest` oraz poprawki startu jobów bez HTTP 500;
- poprawiono pole `Entropy credit`: wartość `1,0` w polskiej lokalizacji przeglądarki nie jest już odrzucana przez błędną bazę kroku HTML;
- formularz normalizuje wszystkie pola liczbowe przez `valueAsNumber`, a backend dodatkowo akceptuje przecinek dziesiętny od starszych klientów API;
- ujednolicono wersje komponentów do `7.9.2`.

## 7.9.0 — 2026-08-02

- dodano `--lsb-bits 1..8` oraz `--sample-mode xor|direct|delta`;
- kalibracja maski nadal używa czasowego XOR bitu `LSB0`, aby profile pozostały porównywalne;
- dodano oddzielny budżet `entropy credit` i kontrolę minimalnego wejścia SHA3; liczba pobieranych bitów nie jest traktowana jako deklaracja entropii;
- dodano pełny profil `lsb-campaign`: XOR, bezpośrednie Y i temporalna delta dla każdej szerokości 1..8 LSB, łącznie 24 przebiegi;
- dodano diagnostykę każdego bitu, zależności lag-1 oraz macierze phi/MI pomiędzy bitami tego samego piksela;
- raport kampanii LSB porównuje również przepustowość wejścia, czas osiągnięcia celu i przepustowość SHA3;
- dodano `global-all`, uruchamiający wszystkie wcześniejsze profile WWW oraz pełną kampanię LSB;
- raporty geometrii binarnej są wyłącznie 2D; usunięto powierzchnie wolumetryczne i chmury trójek.

# Changelog

## 7.8.4 — 2026-08-02

- agent USB utrzymuje ciągły capture V4L2 także bez klienta i przekazuje czas startu, uptime oraz `source_warmup_seconds`;
- dodano ciągły rejestrator pełnych klatek Y8 albo pakowanych LSB z limitem domyślnym 300 GB i chunkami SHA-256;
- `frame-buffer-latest` jest publikowany podczas zapisu, a `dataset-y` bezpiecznie śledzi rosnący `frames.csv` z wpisem indeksu jako znacznikiem zatwierdzenia klatki;
- zakończone datasety o statusie `stopped` są prawidłowym źródłem obliczeń;
- domyślna ścieżka datasetu jest przenośna: `data/frame-buffer-latest`, również w `sources.example.json`;
- control server wykonuje preflight manifestu i indeksu, pobiera geometrię z datasetu i rozwiązuje ścieżkę względem katalogu projektu;
- usunięto wyścig `os.getpgid(pid)` przy natychmiastowym zakończeniu workera; endpoint startu zwraca teraz JSON zamiast ogólnej strony HTML 500;
- surowe katalogi `frame-buffer-*` nie są pokazywane jako przebiegi raportowe;
- dodano testy regresyjne Y8/LSB, zatrzymanego datasetu i procesu kończącego się natychmiast po starcie.

## 7.7.0 — 2026-08-02

- dodano maski `full`, `checkerboard-even`, `checkerboard-odd`, ogólny `grid` oraz `block` z fazą X/Y;
- dodano przestrzenny offset temporalnego XOR bez zawijania krawędzi;
- ujednolicono offset dla XOR, kalibracji, clippingu, shadow maski, RAW validation i produkcji;
- dodano serializację `row-major`, `serpentine` i `tile-interleave` z cache'owaniem indeksów maski;
- zachowano zgodność `--spatial-sampling` przez `--spatial-mask-pattern legacy`;
- dodano dziesięć profili przestrzennych i kampanię zbiorczą z `spatial_campaign_report.html`;
- dodano wszystkie parametry do CLI, `run_one.sh`, `runner_config.json`, API statusu i raportów końcowych;
- rozbudowano control server o walidację parametrów, presety i opisy profili;
- dodano pomoc kontekstową po hover/focus dla wszystkich pól panelu;
- dodano chronioną przeglądarkę dokumentacji `/docs/` z bezpiecznym renderowaniem Markdown;
- dodano testy geometrii, braku wrap-around, serializacji, cache, konfiguracji panelu i kampanii;
- poprawiono błąd prototypowego patcha, w którym statyczny renderer overlay odwoływał się do `self`.

## 7.6.1 — 2026-08-01

- agent USB otwiera i zwalnia urządzenie V4L2 dla każdej sesji mTLS, zamiast pozostawiać niedrenowany capture pomiędzy rundami;
- dodano aplikacyjny handshake `close` / `close-ack`, aby normalne zakończenie klienta nie powodowało `Broken pipe`;
- domyślny timeout ramki TLS-Y zwiększono z 15 do 60 sekund;
- dodano automatyczny reconnect TLS-Y z backoffem; przed produkcją reconnect restartuje warm-up i kalibrację, a po rozpoczęciu produkcji domyślnie działa fail-closed;
- kampania dual-weave stagger-2 używa domyślnie 10 prób reconnect;
- panel live pokazuje generację połączenia, liczbę reconnectów i timeout ramki;
- control panel pozwala ustawić timeout, liczbę prób i backoff przed uruchomieniem testu;
- dodano test cyklu życia transportu i czystego zamykania sesji.

## 7.6.0 — 2026-08-01

- dodano profil `temporal-sha3`: temporalny XOR LSB → zamrożona pełna aktywna maska → RCT/APT i clipping → SHA3-512;
- profil `temporal-sha3` wyłącza checkerboard, porównania przestrzenne, dual weave i obliczanie Von Neumanna; domyślny blok conditionera ma 65 536 bitów, a smoke zapisuje 1 MiB SHA3;
- dodano przełącznik `--von-neumann-stage/--no-von-neumann-stage`, dzięki któremu uproszczony tor nie ponosi kosztu ekstraktora VN;
- obrazy podglądu WWW są domyślnie wyłączone we wszystkich profilach i w formularzu control servera;
- dodano niezależny przełącznik `--mask-snapshot-images`, domyślnie wyłączony; metryki dryftu, CSV i JSON pozostają aktywne bez PNG;
- histogramy live wróciły do wykresów liniowych i domyślnie są rozdzielone na osobne panele z niezależną symetryczną skalą;
- dodano opcjonalny widok nakładanych linii, a wybór trybu jest zapisywany w `localStorage`;
- `Direct LSB — aktywna maska` pozostaje na osobnym wykresie;
- statyczne i interaktywne histogramy raportowe używają linii z wypełnieniem zamiast 256 nakładających się słupków.

## 7.5.2 — 2026-08-01

- histogramy częstotliwości bajtów w panelu live i w raportach pokazują teraz odchylenie od rozkładu jednostajnego 1/256, wyśrodkowane wokół zera;
- wykresy live zostały przełączone na słupkowe, co ułatwia porównywanie lokalnych nadmiarów i niedoborów poszczególnych wartości bajtu;
- etap `Direct LSB — aktywna maska` został wydzielony do osobnego wykresu, aby nie psuł skali głównego pipeline'u;
- wykres dryftu maski używa teraz lokalnie dopasowanego zakresu osi Y zamiast wymuszonego startu od zera;
- histogramy PNG w raportach przedstawiają dodatnie i ujemne odchylenie od 1/256 w punktach procentowych, z poziomem odniesienia na osi 0.

## 7.5.1 — 2026-08-01

- usunięto `scattergl` z panelu workera; histogramy live i wykres dryftu maski korzystają teraz z renderera SVG Plotly i nie wymagają WebGL;
- zachowano restrykcyjną politykę CSP bez `unsafe-eval`; błędy Plotly są przechwytywane i wyświetlane jako czytelny komunikat w obrębie wykresu;
- raporty 3D przy restrykcyjnej CSP celowo używają statycznych projekcji PNG; dodatkowe sprawdzenie WebGL i fallback JS zabezpieczają raport przed pustym polem po błędzie renderera;
- dodano statyczne pliki `byte_pairs_surface_3d_*.png` i `byte_triplets_scatter_3d_*.png` dla każdego analizowanego strumienia;
- wszystkie teksty w generowanych histogramach i heatmapach PNG są rysowane przez Pillow z systemowym fontem Unicode, co naprawia polskie znaki;
- przetłumaczono tytuły, osie, legendy i opisy generowanych obrazów na język polski;
- dodano zmienne `CAMERA_ENTROPY_FONT` i `CAMERA_ENTROPY_FONT_BOLD` do jawnego wskazania fontu w nietypowych systemach;
- rozszerzono testy o brak `scattergl`, zgodność SVG Plotly z bieżącym CSP, fallback 3D bez WebGL oraz generowanie Unicode PNG.

## 7.5.0 — 2026-08-01

- dodano live histogramy częstotliwości 256 wartości bajtu dla kolejnych etapów pipeline: Direct LSB, temporal RAW, temporal po masce, Von Neumann i SHA3-512;
- w trybie dual weave raportowane są również C0/C1 RAW, C0/C1 po VN, rzeczywiste wejścia conditionera i jego wyjścia SHA3;
- liczniki bitowe są pakowane ciągle pomiędzy ramkami i grupami, bez artefaktów dopełniania na granicy wywołań;
- dodano read-only API `/api/byte-diagnostics` oraz wykresy Plotly aktualizowane w panelu workera co 2 sekundy;
- zastąpiono niedziałający wykres dryftu maski interaktywnym Plotly z retencją, Jaccardem i progami fail-closed;
- dodano okresowo generowany `live_byte_heatmaps.png` obejmujący kilka etapów na wspólnej skali; interwał i minimalną próbkę można konfigurować;
- raporty końcowe otrzymały osobny histogram bajtów PNG i interaktywny histogram Plotly dla każdego analizowanego pliku BIN;
- porównanie Direct LSB → temporal/maska → VN → SHA3 używa wspólnej skali histogramów, heatmap liczności i map reszt;
- archiwum NPZ zawiera dodatkowo `byte_counts`;
- dodano test ciągłego pakowania bitów, przejść pomiędzy porcjami, callbacków conditionera i generowania obrazu live.

## 7.4.1 — 2026-08-01

- raport geometrii generuje teraz dwa uzupełniające obrazy dla każdego pliku BIN: surowe liczności przejść oraz reszty Pearsona względem modelu niezależnych marginesów;
- model odniesienia używa `E(x,y)=count_X(x)×count_Y(y)/N`, a mapa residualna wspólnej skali `-8…+8` pokazuje pary nad- i niedoreprezentowane;
- obie heatmapy mają stałą orientację `X=B_n` (bieżący bajt), `Y=B_(n+1)` (następny bajt), krótkie tytuły oraz histogramy marginalne u góry i po lewej;
- dodano χ² niezależności, liczbę stopni swobody, χ²/df, Craméra V, RMS i percentyle reszt Pearsona oraz percentyl wzbogacenia `log2(O/E)`;
- archiwum NPZ zawiera teraz macierze `counts`, `expected`, `pearson_residuals`, `log2_enrichment` i oba histogramy marginalne;
- `run_report.html` pokazuje pary miniaturek „liczności / vs niezależność”, a pełny raport prezentuje oba obrazy obok siebie wraz z H(X), H(Y), H(X,Y), H(Y|X), MI i Cramérem V;
- domyślny wybór plików gwarantuje, o ile są dostępne, cztery etapy `Direct LSB → temporal difference + mask → Von Neumann → SHA3-512`; ich liczności używają wspólnej skali, a reszty stałej skali `±8`;
- rozszerzono testy syntetyczne o dokładny przypadek niezależny, kontrolę nowych macierzy NPZ oraz regresję nowych raportów.

## 7.4.0 — 2026-07-31

- dodano skupiony profil `dual-weave-stagger2`, który uruchamia wyłącznie `row-major / stagger-2` z blokiem `C0_g || C1_(g+2)` i conditionerem SHA3-512 `2048 → 512`;
- zmieniono domyślny wariant długiej kwalifikacji z `stagger-1` na `stagger-2`, a kampanię lagów na bezpośrednie porównanie `same-group` kontra `stagger-2`;
- dodano generowany programowo diagram SVG pokazujący checkerboard EVEN/ODD, komplementarne C0/C1, kolejność row-major i dwugrupową kolejkę;
- dodano analizę rozkładu sąsiednich par bajtów `256 × 256` dla najważniejszych plików `.bin`;
- raport geometrii wylicza H(X,Y), H(Y|X), histogramową Hmin par, informację wzajemną, shuffle-baseline, korelację bajtową i zajętość przestrzeni par/trójek;
- dodano statyczne heatmapy PNG, skompresowane macierze NPZ, CSV/JSON oraz interaktywną powierzchnię 3D i chmurę `(B_n, B_(n+1), B_(n+2))` w lokalnym Plotly;
- `run_report.html` i `dual_weave_report.html` zawierają bezpośrednie odnośniki oraz podgląd nowych wyników;
- read-only API `api/latest` zwraca również `binary_geometry_summary.json`;
- dodano test regresyjny dokładnego harmonogramu stagger-2 oraz syntetyczny test rozróżniający strumień losowy od silnie zależnego.

## 7.3.2 — 2026-07-31

- Naprawiono błąd HTTP 500 w `/api/control/status` i widoku `/data/`, gdy katalog zwykłego przebiegu zawierał `run_report.html`, ale nie zawierał `dual_weave_report.json`.
- Odczyt podsumowań kampanii, kwalifikacji i dual weave jest teraz odporny na brakujący, pusty lub uszkodzony JSON.
- Dodano test regresyjny dla zwykłych raportów oraz niepełnych plików podsumowań.

## 7.3.1 — 2026-07-31

- przebudowano raporty HTML tak, aby najważniejsze wnioski, status health i metryki były widoczne od razu;
- dodano lokalnie dostarczany Plotly.js i istotne wykresy bez zależności od zewnętrznego CDN;
- raport dual weave pokazuje przepustowość względem checkerboardu, korelację pozycyjną C0↔C1, lagi granic bloków 1022–1026/2047–2049 oraz clipping full/even/odd;
- raport kampanii dual weave agreguje warianty i wskazuje diagnostycznie najlepszy alignment;
- raport pojedynczego przebiegu porównuje RAW, VN i SHA3 pod kątem biasu, lag-1, Hmin i chi-square;
- raport kwalifikacyjny pokazuje powtarzalność, stabilność maski i rzeczywistą korelację przestrzenną;
- poprawiono przeglądanie `/data/` i share read-only: raporty są wyróżnione kartami, krótkim podsumowaniem i bezpośrednim przyciskiem otwarcia;
- poprawiono odczyt korelacji przestrzennej w raporcie kwalifikacyjnym z aktualnej struktury `pixel_correlation.masked`;
- szczegółowe tabele i pliki nadal są dostępne, ale zostały przeniesione do sekcji rozwijanych.

## 7.3.0 — 2026-07-31

- dodano porównanie alignmentów `same-group`, `stagger-1` i `stagger-2`;
- `stagger-1` łączy C0 z grupy g z C1 z grupy g+1, ograniczając bezpośrednie współdzielenie tej samej mapy temporalnej;
- dodano własny czas osiągnięcia celu i `output_bps_until_complete` dla każdego conditionera;
- rozszerzono analizę o lagi 1022–1026 i 2047–2049 oraz pozycyjną korelację C0↔C1 wewnątrz bloków 2048-bitowych;
- fail-closed clipping działa osobno dla full/even/odd w trybie dual weave;
- dodano profil `dual-weave-stagger-qualification` i trzy przebiegi po 100 MiB;
- `SHA256SUMS` dla kampanii jest generowany rekursywnie i obejmuje pliki z podkatalogów;
- domyślnym porządkiem nowego smoke jest prostszy `row-major`;
- rozszerzono raporty HTML/JSON/CSV oraz panel live o alignmenty i clipping obu faz.

## 7.2.0 — 2026-07-31

- dodano eksperymentalny `complementary temporal checkerboard` z kompozytami C0/C1 tworzonymi z dwóch kolejnych rozłącznych map temporalnych;
- dodano równoległe porównanie kolejności `serpentine` i `row-major` z tych samych ramek;
- dodano oddzielne RCT/APT dla faz A-even/A-odd/B-even/B-odd oraz C0/C1;
- dodano zbalansowany conditioner SHA3-512: połowa bloku z C0 i połowa z C1, separator domeny oraz licznik bloku;
- dodano walidacyjne pliki RAW, VN i SHA3 dla obu wariantów oraz pełny raport korelacji krzyżowej;
- dodano profile WWW `dual-weave` i `dual-weave-lags`;
- dodano kampanię `k4-start → k2 → k8 → k4-repeat` z raportem zbiorczym;
- read-only `/api/latest` zwraca również raport dual weave i raport kampanii;
- domyślny kandydat produkcyjny pozostaje `checkerboard-even`; dual weave jest trybem testowym.

## 7.1.1 — 2026-07-31

- ustawiono domyślny agent kamery jako `camera` pod adresem `192.168.1.2`;
- ustawiono `TLS_SERVER_NAME=camera` i domyślny `SOURCE_ID=camera`;
- certyfikaty i klucze mTLS są domyślnie odczytywane z katalogu projektu `pki/`;
- `generate_mtls_pki.sh` bez parametrów tworzy PKI dla `camera` / `192.168.1.2`;
- domyślne `sources.json` wybiera zdalną kamerę mTLS jako pierwsze źródło;
- zaktualizowano przykłady systemd i dokumentację.

## 7.1.0 — 2026-07-31

- dodano stale działający `control_server.py` na domyślnym porcie `8087`;
- worker obliczeniowy działa tylko podczas testu na loopbackowym porcie `18087`;
- dodano uruchamianie i zatrzymywanie testów z WWW;
- dodano profile smoke, single, qualification, cold-start i spatial phases;
- dodano bezpieczny wybór źródeł z administrowanego pliku `sources.json`;
- dodano log zadania, historię jobów, podgląd aktywnego workera i odporność na zamknięcie przeglądarki;
- dodano przeglądarkę raportów i plików pod `/data/`;
- dodano automatyczny `run_report.html`;
- dodano logowanie aplikacyjne, ochronę CSRF, secure cookies i nagłówki bezpieczeństwa;
- dodano opcjonalny token read-only do udostępniania wyników;
- dodano przykładową usługę systemd oraz konfigurację Nginx.

## 7.0.0 — 2026-07-31

- dodano rozdzielony agent USB wysyłający niekompresowane ramki Y8;
- dodano wzajemny TLS 1.3, certyfikaty klienta i serwera, numerację ramek oraz SHA-256 payloadu;
- przeniesiono cały pipeline entropii na serwer obliczeniowy;
- dodano abstrakcję źródła `v4l2`, `tls-y` i `rtsp`;
- dodano RTSP przez FFmpeg z transportem TCP/UDP/HTTP/HTTPS i pobieraniem płaszczyzny Y;
- dodano bezpieczne przechowywanie URL RTSP w pliku oraz redakcję poświadczeń w raportach;
- zachowano pipeline v6.1.3: disjoint k=4, checkerboard, RCT/APT, diagnostyczny VN i SHA3-512;
- dodano fail-closed po luce w numeracji zdalnych ramek;
- podgląd WWW obsługuje źródła mające tylko Y8;
- dodano skrypty PKI, runnery USB/RTSP i przykładowe usługi systemd.

# Zmiany v6.1.3

- Ujednolicono domyślny port wszystkich skryptów do `8087`.
- `smoke_preproduction.sh`, `smoke_checkerboard_phases.sh`, `qualification_preproduction.sh`, `qualification_cold_start_run.sh` i `web_overhead_ab.sh` nie wybierają już innych portów.
- Zmienna środowiskowa `PORT` nadal może jawnie nadpisać wartość domyślną.
- Zachowano aliasy API `/api/stats` i `/api/status` z v6.1.1.
- Podniesiono numer aplikacji do `2026.07.30.camera-entropy-preproduction.6.1.2`.

## 6.1.3

- Fail-closed ACTIVE_CLIP_RATE is evaluated on the configured production sampling mask, not on unused pixels from the other checkerboard phase.
- Full active-mask clipping is still exposed as a diagnostic metric.
- Failure and completion markers are mutually exclusive.
- Server returns exit code 2 when `run_failed.json` exists.
- Runner prints the exact fail-closed reason instead of dumping the entire JSON report.
- Default port remains 8087.
