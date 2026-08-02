# Analiza `exposure-benchmark-20260730T093803Z`

## Najważniejsza korekta

Ten przebieg **nie używał checkerboardu**. Polecenie uruchamiało wersję v5 z:

- ekspozycją `7000`,
- `disjoint / k=4`,
- rozgrzewką `1800 s`,
- kalibracją `512` par,
- finalnym plikiem VN `100 MiB`,
- pełną aktywną maską.

W `command.txt` nie ma parametru wyboru przestrzennego, a `output_complete.json` raportuje tylko wariant `full`. Wynik jest więc długim przebiegiem kontrolnym dla **pełnej maski**, nie kwalifikacją wybranego checkerboardu.

Przebieg miał także benchmarkowe flagi `--continue-output-after-health-failure` i `--no-shadow-stop-on-drift`. Nie wystąpił żaden RCT/APT failure ani alarm dryftu, więc nie zmieniło to danych, ale taki tryb nie jest fail-closed i nie powinien być używany w wersji przedprodukcyjnej.

## Wyniki źródła i toru VN

| Metryka | Wynik |
|---|---:|
| Finalny plik VN | 100 MiB |
| SHA-256 | `08e8f192f115d6a5980109196cf975b588ce46b64d0e6dfe1e8d03f79d22c8e0` |
| RCT / APT | PASS / PASS |
| Rzeczywisty FPS | 1,4320 |
| Średni odstęp pary | 2,8451 s |
| Prędkość VN | 159 172 bit/s |
| Aktywne piksele | 904 839 / 921 600 (98,18%) |
| `P(1)` po VN | 0,5003453 |
| Marginalna Hmin | 0,999004 bit/bit |
| Korelacja bitowa lag-1 | **0,0067944** |
| Hmin rozkładu bajtów | 7,91967 / 8 |
| Chi-square bajtów | **34 564,61** |
| Najczęstszy bajt | `0xFF`, 0,41299% |
| Monte Carlo π | 3,1336287 |

Finalny strumień jest stabilny i dobrze zbalansowany, ale nadal wyraźnie nie-IID. Duży chi-square i nadmiar `0xFF` są zgodne z dodatnią zależnością kolejnych bitów.

## Stabilność w czasie

Dziesięć fragmentów po 10 MiB dało:

- lag-1: `0,006573–0,007102`, średnia `0,006794`, odchylenie standardowe około `0,000163`;
- Hmin bajtu: `7,9088–7,9319`;
- brak RCT/APT failure;
- brak zatrzaśnięcia dryftu maski.

To potwierdza, że 30-minutowa rozgrzewka stabilizuje kamerę znacznie lepiej niż wcześniejsze smoke testy z 60 sekundami.

## Korelacja pikseli

Pomiar obejmował 371 map temporalnych i około 331–342 mln par dla każdego kierunku/dystansu.

Po masce:

- poziomo, `d=1`: `φ = 0,0067070`;
- pionowo, `d=1`: `φ = 0,0062272`;
- diagonalnie, `d=1`: `φ = 0,0009825`;
- roboczy dystans dekorrelacji dla progu `|φ| < 0,001`: około 2 piksele.

Finalna korelacja bitowa `0,0067944` jest niemal identyczna z poziomą korelacją sąsiadujących pikseli. To ponownie potwierdza, że pozostała struktura pochodzi przede wszystkim z zapisywania aktywnych pikseli w kolejności wierszowej. Checkerboard usuwa właśnie tę relację.

## Maska i dryft

Przez około 88 minut monitorowania:

- active retention: końcowo `99,602%`, minimum `99,600%`;
- Jaccard: końcowo `99,537%`, minimum `99,533%`;
- maksymalna niezgodność masek: `0,459%`;
- brak alarmu dryftu.

Maska jest więc stabilna po długiej rozgrzewce.

## Dynamiczne odrzucanie clipped pixels

Stara wersja usuwała z każdej pary aktualnie clipped pixels. Zamrożona maska miała 904 839 pikseli, natomiast do poszczególnych par trafiało 904 604–904 732 pikseli, średnio 904 666. Oznacza to około 173 dynamicznie usuwane pozycje na parę (`~0,019%`).

W v6 produkcyjny układ pozycji jest domyślnie stały. Clipping jest osobnym health monitorem; przekroczenie progu powoduje pominięcie pary i po kolejnych przekroczeniach zatrzaśnięcie błędu. Upraszcza to model próbkowania i audyt.

## Offline SHA3-512 — walidacja koncepcji

SHA3-512 został zastosowany offline blokami `2048 → 512 bitów` (kompresja 4:1).

### Na 10 MiB danych wybranych przed VN

| Metryka | Przed SHA3 | Po SHA3 (2,5 MiB) |
|---|---:|---:|
| `P(1)` | 0,498158 | 0,5000237 |
| lag-1 | 0,0071665 | 0,0004614 |
| Hmin bajtu | 7,8641 | 7,9590 |
| chi-square bajtów | 5232,9 | 255,33 (`p≈0,482`) |

### Na 100 MiB danych po VN

Po SHA3 powstało 25 MiB:

- `P(1) = 0,49999937`;
- lag-1 `= 0,0000667`;
- Hmin bajtu `= 7,98841`;
- chi-square `= 266,14`, `p≈0,303`.

To potwierdza poprawne działanie conditionera i usunięcie widocznych struktur statystycznych. Nie jest to jeszcze dowód ani deklaracja pełnej entropii — jej limit musi wynikać z formalnej estymacji danych wejściowych.

## Decyzja

Nastaw pozostaje:

```text
exposure = 7000
pairing = disjoint
k = 4
spatial = checkerboard
warm-up = 1800 s
calibration = 512 par
health = RCT/APT przed conditioningiem
conditioner candidate = SHA3-512, 2048 -> 512 bitów
```

Przed długą kwalifikacją należy porównać obie fazy checkerboardu na identycznych parach klatek. Następnie wykonać co najmniej trzy długie przebiegi wybranej fazy i ocenić dane przed conditionerem ścieżką non-IID SP 800-90B.
