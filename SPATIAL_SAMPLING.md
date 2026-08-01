# Próbkowanie przestrzenne i korelacja pikseli — v7.7.0

## Cel

Wersja 7.7.0 rozszerza temporalny pipeline o kontrolowaną geometrię wyboru pikseli, przestrzenne przesunięcie starszej ramki oraz alternatywne kolejności serializacji. Funkcje służą przede wszystkim do badania wzorów widocznych na heatmapach i korelacji pomiędzy sąsiednimi pikselami.

Maska, offset i zmiana kolejności **nie tworzą entropii** i nie zastępują SHA3-512. Finalny tor nadal powinien używać kryptograficznego conditionera, a współczynnik kredytowania entropii musi wynikać z osobnej oceny źródła przed conditionerem.

## Pipeline

```text
bieżąca ramka Y_t
starsza ramka Y_(t-k)
        │
        ├── niezwijające przesunięcie starszej ramki o (dx,dy)
        │
        └── XOR najmłodszych bitów
              LSB(Y_t[x,y]) XOR LSB(Y_(t-k)[x+dx,y+dy])
                        │
                        ├── zamrożona maska aktywna
                        ├── maska przestrzenna
                        ├── clipping / shadow mask / RCT / APT
                        └── serializacja
                              │
                              ├── Von Neumann — diagnostycznie
                              └── SHA3-512 — finalny conditioner
```

Piksele, dla których `x+dx` albo `y+dy` wychodzi poza obraz, są odrzucane. Kod nie używa zawijania krawędzi i nie łączy prawej krawędzi z lewą ani dołu z górą.

## Parametry CLI i zmienne środowiskowe

| CLI | Zmienna | Domyślnie | Znaczenie |
|---|---|---:|---|
| `--spatial-mask-pattern` | `SPATIAL_MASK_PATTERN` | `legacy` | `legacy`, `full`, `checkerboard-even`, `checkerboard-odd`, `grid`, `block` |
| `--spatial-step-x` | `SPATIAL_STEP_X` | `1` | Poziomy krok wzorca `grid` |
| `--spatial-step-y` | `SPATIAL_STEP_Y` | `1` | Pionowy krok wzorca `grid` |
| `--spatial-phase-x` | `SPATIAL_PHASE_X` | `0` | Wybrana klasa modulo X lub lokalna pozycja X w bloku |
| `--spatial-phase-y` | `SPATIAL_PHASE_Y` | `0` | Wybrana klasa modulo Y lub lokalna pozycja Y w bloku |
| `--spatial-block-width` | `SPATIAL_BLOCK_WIDTH` | `4` | Szerokość bloku dla `block` |
| `--spatial-block-height` | `SPATIAL_BLOCK_HEIGHT` | `4` | Wysokość bloku dla `block` |
| `--temporal-spatial-offset-x` | `TEMPORAL_SPATIAL_OFFSET_X` | `0` | Poziome przesunięcie starszego piksela |
| `--temporal-spatial-offset-y` | `TEMPORAL_SPATIAL_OFFSET_Y` | `0` | Pionowe przesunięcie starszego piksela |
| `--serialization-order` | `SERIALIZATION_ORDER` | `row-major` | `row-major`, `serpentine`, `tile-interleave` |
| `--serialization-tile-width` | `SERIALIZATION_TILE_WIDTH` | `16` | Szerokość kafla interleavera |
| `--serialization-tile-height` | `SERIALIZATION_TILE_HEIGHT` | `16` | Wysokość kafla interleavera |

Tryb `legacy` zachowuje dotychczasowe znaczenie `--spatial-sampling`, w tym `full`, `checkerboard-even`, `checkerboard-odd` i `grid2x2`.

## Wzorce masek

### `full`

Wszystkie piksele zaakceptowane przez zamrożoną maskę aktywną i prawidłowy obszar offsetu.

### `checkerboard-even` i `checkerboard-odd`

```text
(x + y) mod 2 = 0
(x + y) mod 2 = 1
```

Fazy są komplementarne. Bezpośredni sąsiad poziomy lub pionowy nie należy do tej samej fazy.

### `grid`

Wybiera jedną klasę współrzędnych modulo krok:

```text
x mod step_x = phase_x
y mod step_y = phase_y
```

Przykład jednego piksela z każdego obszaru 4×4:

```bash
SPATIAL_MASK_PATTERN=grid
SPATIAL_STEP_X=4
SPATIAL_STEP_Y=4
SPATIAL_PHASE_X=0
SPATIAL_PHASE_Y=0
```

### `block`

Wybiera jedną ustaloną lokalną pozycję z każdego prostokątnego bloku. Przykład pozycji `(1,2)` w blokach 4×4:

```bash
SPATIAL_MASK_PATTERN=block
SPATIAL_BLOCK_WIDTH=4
SPATIAL_BLOCK_HEIGHT=4
SPATIAL_PHASE_X=1
SPATIAL_PHASE_Y=2
```

## Przestrzenny offset temporalnego XOR

Dla `dx=1`, `dy=1`:

```text
LSB(Y_t[x,y]) XOR LSB(Y_(t-k)[x+1,y+1])
```

Starsza ramka jest najpierw wyrównywana do współrzędnych bieżącej ramki. To samo wyrównanie jest używane w:

- kalibracji maski;
- kontroli clippingu;
- shadow masce;
- temporalnym XOR;
- RAW validation;
- health tests i produkcji.

Dzięki temu nie powstaje sytuacja, w której XOR bada przesunięty piksel, ale clipping nadal sprawdza piksel nieprzesunięty.

## Kolejność serializacji

### `row-major`

Standardowa kolejność wierszami, od lewej do prawej.

### `serpentine`

Co drugi wiersz jest odczytywany w przeciwną stronę. Zmniejsza liczbę skoków od końca jednego wiersza do początku następnego.

### `tile-interleave`

Najpierw emitowana jest ta sama pozycja lokalna z odległych kafli, a dopiero później następna pozycja lokalna. Dla kafla 16×16 kolejne bity zwykle pochodzą z różnych części obrazu.

Indeksy serializacji są obliczane raz dla danej maski i przechowywane w ograniczonym cache. Kolejne ramki nie wykonują ponownego sortowania całej matrycy.

Reordering może zmniejszyć korelację lag-1 w pliku, ale może przenieść zależność na inny lag. Raporty należy oceniać dla wielu lagów i na danych przed SHA3.

## Profile testowe

| Profil WWW / skrypt | Geometria |
|---|---|
| `spatial-baseline` | full, offset 0, row-major |
| `spatial-checker-even` | checkerboard even |
| `spatial-checker-odd` | checkerboard odd |
| `spatial-grid2` | grid 2×2, faza 0,0 |
| `spatial-grid4` | grid 4×4, faza 0,0 |
| `spatial-block4` | block 4×4, faza 1,2 |
| `spatial-offset11` | full, offset +1,+1 |
| `spatial-offset22` | full, offset +2,+2 |
| `spatial-serpentine` | full, serpentine |
| `spatial-tile16` | full, tile-interleave 16×16 |
| `spatial-campaign` | wszystkie powyższe kolejno |

Domyślne profile smoke używają:

```text
warm-up              60 s
kalibracja            128 par
pair lag              4
VN                    1 MiB
SHA3-512              1 MiB
validation RAW        8 MiB
conditioner input     2048 bitów
```

Wartości można zmienić zmiennymi środowiskowymi lub w panelu. Nazwany profil zawsze wymusza własną geometrię, aby wynik był reprodukowalny.

## Kampania

```bash
SOURCE_TYPE=tls-y ./smoke_spatial_profiles.sh
```

Kampania tworzy osobny katalog dla każdego profilu oraz:

```text
spatial_campaign_summary.json
spatial_campaign_report.html
READY.json
```

Raport kampanii jest indeksem. Nie wybiera automatycznie najlepszego wariantu, ponieważ decyzja powinna uwzględniać heatmapy, korelacje wielu lagów, Hmin, stabilność maski i przepustowość.

## Dokumentacja WWW

Po zalogowaniu do control servera dostępne są:

```text
/docs/
/docs/<ścieżka-do-pliku>
```

Indeks pokazuje pliki Markdown, tekstowe instrukcje, README, SECURITY, SOURCE_MODEL, CHANGELOG i BUILD_VERIFICATION. Pomijane są katalogi danych, klucze, certyfikaty, repozytoria i środowiska wirtualne. Surowy HTML w dokumentacji jest escapowany i nie jest wykonywany.

Panel zawiera pomoc kontekstową dla każdego pola. Opis pojawia się po najechaniu, ustawieniu fokusu klawiaturą lub zmianie opcji. Opcje profili mają osobne opisy.

## Zalecana ocena

1. Uruchomić `spatial-campaign` przy tej samej ekspozycji, FPS i lagu ramek.
2. Porównać przede wszystkim `y_temporal_raw_validation.bin` i `y_temporal_masked_validation.bin`.
3. Sprawdzić heatmapy przejść bajtowych, korelacje wielu lagów i korelacje przestrzenne.
4. Sprawdzić liczbę pikseli po masce i spadek przepustowości.
5. Wybrać kilka wariantów i powtórzyć je po zimnym starcie.
6. Nie uznawać wariantu za lepszy wyłącznie dlatego, że jego finalny SHA3 wygląda idealnie.

## Testy lokalne

```bash
python3 selftest_spatial_sampling.py
python3 selftest_control_spatial.py
python3 selftest.py
python3 selftest_temporal_sha3.py
python3 selftest_live_byte_diagnostics.py
python3 selftest_binary_geometry.py
python3 selftest_transport_lifecycle.py
python3 selftest_tls_reconnect.py
```

Pełny `control_selftest.py` wymaga zainstalowanych zależności z `requirements.txt`.
