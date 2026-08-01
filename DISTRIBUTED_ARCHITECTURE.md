# Model rozdzielony

## Granica odpowiedzialności

Agent USB jest częścią toru akwizycji. Serwer obliczeniowy jest częścią toru ekstrakcji i kondycjonowania. Rozdzielenie nie zmienia kolejności logicznej próbek: każda ramka ma monotoniczny `frame_id`, a domyślna polityka zatrzymuje przebieg po luce.

## Dlaczego Y8, a nie RTSP/H.264 z hosta USB

Dla badanego źródła Creative VF0790 dotychczasowa kwalifikacja dotyczy bezpośrednich bajtów Y z YUYV. Zakodowanie ich do H.264 lub JPEG stworzyłoby nowy, zależny od kodeka model źródła i unieważniło porównanie z v6.1.3. Dlatego transport USB zachowuje niekompresowane Y8.

## Czas ramek

- `v4l2`: lokalny `time.monotonic()` w chwili odczytu;
- `tls-y`: monotoniczny czas hosta USB przesłany w nagłówku;
- `rtsp`: monotoniczny czas odbioru pełnej zdekodowanej ramki przez FFmpeg.

`PAIR_LAG_FRAMES` nadal oznacza odległość w kolejności ramek. `pair_delta_seconds` jest wartością diagnostyczną i jego podstawa czasu jest zapisywana w manifeście źródła.

## Zasada izolacji źródeł

Nie wolno łączyć danych z V4L2, TLS-Y i RTSP w jednym zbiorze oceny źródła. Nie wolno także zakładać, że dwa modele kamer mają ten sam dolny limit min-entropii. Każde źródło otrzymuje osobny identyfikator, kampanię, restart test i parametry conditionera.

## Dual weave v2 i alignmenty czasowe

Dwie kolejne rozłączne mapy temporalne `A` i `B` tworzą komplementarne strumienie `C0` i `C1`. Wariant `same-group` podaje do jednego block conditionera `C0_g` oraz `C1_g`. Warianty stagger rozdzielają je w czasie:

```text
same-group: C0_g + C1_g
stagger-1:  C0_g + C1_(g+1)
stagger-2:  C0_g + C1_(g+2)
```

Aktualnym wariantem skupionym do dalszej kwalifikacji jest `row-major / stagger-2`. Serializacja przebiega wierszami, a wejście bloku ma postać `C0_g[1024] || C1_(g+2)[1024]`. C0 i C1 nie są przeplatane bit po bicie.

Każdy wykorzystany bit obrazu trafia do conditionera tylko raz. Dla stagger początkowe grupy C1 oraz odpowiadające im końcowe grupy C0 są celowo pomijane; nie są kopiowane ani ponownie używane. Kolejka istnieje wyłącznie w pamięci serwera obliczeniowego.

Pomiar `time_to_target_seconds` rozpoczyna się, gdy pierwsza grupa wejściowa danego conditionera staje się dostępna. Obejmuje więc także opóźnienie startowe stagger. Pozwala to porównywać rzeczywistą szybkość osiągnięcia celu bez czekania na zakończenie równoległych plików diagnostycznych.

## Warstwa diagnostyczna bajtów

Podczas pracy worker może obserwować kolejne etapy binarne i utrzymywać dla nich histogram 256 wartości oraz macierz `256 × 256` sąsiednich przejść. Obserwatory są wywoływane po utworzeniu danych danego etapu, ale nie zwracają danych do toru ekstrakcji: nie modyfikują bitów, health tests ani conditionera. Fragmenty bitowe są pakowane w sposób ciągły, z zachowaniem niedomkniętych bajtów pomiędzy ramkami. API `/api/byte-diagnostics` udostępnia tylko agregaty. Plotly działa w przeglądarce, a okresowe PNG jest renderowane w osobnym wątku.

Po zakończeniu przebiegu serwer może zbudować histogram bajtów, macierz `256 × 256` sąsiednich par z osiami `X=B_n`, `Y=B_(n+1)`, heatmapę obserwowanych liczności, mapę reszt Pearsona względem modelu niezależnych marginesów, powierzchnię 3D obserwowanej gęstości oraz chmurę kolejnych trójek `(B_n, B_(n+1), B_(n+2))`. Obrazy 2D zawierają histogramy marginalne. Analiza końcowa działa po zamknięciu plików i nie znajduje się w ścieżce generowania bitów.
