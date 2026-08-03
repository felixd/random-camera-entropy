# Camera Entropy Distributed v7.11.1-rev2

Data wydania: 2026-08-03

## Najważniejsza korekta naukowa

W kampaniach `LSB_BITS > 1` health tests były uruchamiane na strumieniu
`pixel-major-lsb-first`. Taki strumień nie jest sekwencją binarnych próbek
źródła: kolejne pozycje reprezentują różne bitplane tego samego piksela.
Powodowało to przedwczesne RCT, np. 41–46 jednakowych bitów, mimo że źródłowy
symbol k-bitowy nie był powtórzony 41 razy.

Od 7.10.0:

1. piksel po `xor`, `direct` lub `delta` tworzy jeden symbol `k`-bitowy;
2. frozen mask i kolejność przestrzenna wybierają sekwencję symboli;
3. RCT/APT otrzymuje tę sekwencję symboli;
4. dopiero później symbole są serializowane `pixel-major-lsb-first` na wejście
   plików walidacyjnych, VN i SHA3-512.

APT realizuje procedurę symbolu referencyjnego z pierwszej próbki okna. Dla
źródła binarnego sprawdzana jest także wartość dopełniająca. Domyślne okno to
1024 próbki dla `LSB_BITS=1` i 512 próbek dla `LSB_BITS=2..8`.

## Raportowanie przebiegów zakończonych błędem

`run_one.sh` nie porzuca już raportowania po `run_failed.json`. Analizuje każdy
niepusty plik częściowy, zapisuje `runner_summary.json`, generuje
`run_report.html`, a dopiero potem zwraca kod błędu. `READY.json` nadal nie jest
tworzony dla przebiegu fail-closed.

Raport pojedynczego przebiegu pokazuje:

- bezpośrednią przyczynę zatrzymania;
- domenę i szerokość próbki health testu;
- liczniki i progi RCT/APT;
- odsyłacze do `run_failed.json` i `health_events.csv`;
- statystyki częściowych plików BIN.

## Raport kampanii LSB

Schemat podsumowania został podbity do `camera-entropy-lsb-campaign-v3`.
Przepustowość RAW/masked preferuje pomiar lifetime; starsze przebiegi używają
fallbacku 10 s lub current i jawnie raportują podstawę pomiaru. Dodano:

- `conditioned_vs_credited_entropy`;
- przyczynę błędu bezpośrednio w tabeli;
- parametry health testów;
- linki do raportu częściowego, `run_failed.json` i logu profilu.

## Szybsza kampania offline

`smoke_lsb_profiles.sh` domyślnie wyłącza dla kampanii seryjnej:

- VN diagnostyczny;
- obrazy WWW i snapshoty maski;
- live byte diagnostics;
- geometrię binarną;
- śledzenie rosnącego datasetu.

Opcje nadal można włączyć zmiennymi `LSB_ENABLE_VON_NEUMANN`,
`LSB_WEB_IMAGES`, `LSB_LIVE_BYTE_DIAGNOSTICS`,
`LSB_BINARY_GEOMETRY_REPORT` i `LSB_DATASET_FOLLOW`.

## Zgodność

Dla `LSB_BITS=1` domena health testów pozostaje binarna. Format plików
walidacyjnych i wejścia conditionera nie uległ zmianie. Zmienia się wyłącznie
prawidłowe miejsce wykonania RCT/APT dla profili wielobitowych oraz rozszerzone
raportowanie.
