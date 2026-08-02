# Camera Entropy Distributed v7.9.0

Wersja została zbudowana bezpośrednio z przesłanego archiwum `random-camera-entropy-dev(1).zip` w wersji bazowej `7.8.4`.

## Najważniejsze zmiany

- Pobieranie od 1 do 8 najmłodszych bitów próbki Y.
- Trzy tryby próbkowania:
  - `xor` — czasowy XOR bieżącej i starszej klatki;
  - `direct` — bezpośrednie dolne bity bieżącej klatki Y;
  - `delta` — różnica `Y_t - Y_(t-k) mod 256`.
- Kolejność bitów `pixel-major-lsb-first`.
- Kalibracja maski pozostaje oparta na czasowym XOR bitu `LSB0`, dzięki czemu profile są porównywalne.
- Oddzielny parametr `ENTROPY_CREDIT_BITS_PER_PIXEL`; liczba pobieranych bitów nie jest automatycznie zaliczana jako entropia.
- Kontrola minimalnego rozmiaru bloku wejściowego SHA3-512.
- Analiza każdego bitu i całego symbolu wielobitowego.
- Raporty geometrii binarnej ograniczone do 2D.

## Profile

### Pełna kampania LSB

```bash
./smoke_lsb_profiles.sh
```

Uruchamia 24 przebiegi:

```text
xor    × 1..8 LSB
direct × 1..8 LSB
delta  × 1..8 LSB
```

Raport:

```text
lsb_campaign_report.html
lsb_campaign_summary.json
lsb_campaign_summary.csv
```

Raport porównuje między innymi:

- przepustowość wejścia RAW i po masce;
- liczbę symboli pikselowych na sekundę;
- przepustowość wynikającą z podanego entropy credit;
- empiryczną diagnostyczną przepustowość Hmin symbolu;
- przepustowość SHA3 i czas osiągnięcia celu;
- zależności pomiędzy bitami tego samego piksela.

### Profil GLOBAL

```bash
./qualification_global_all_profiles.sh
```

Uruchamia wszystkie 22 wcześniejsze ścieżki profili dostępne w panelu WWW, włącznie z kampaniami zbiorczymi i pełną kampanią LSB. Domyślnie kontynuuje po błędzie, ale zwraca niezerowy kod końcowy, jeżeli dowolny etap nie przejdzie.

```bash
GLOBAL_CONTINUE_ON_ERROR=0 ./qualification_global_all_profiles.sh
```

włącza tryb fail-fast.

Raport:

```text
global_campaign_report.html
global_campaign_summary.json
```

## Przykłady pojedynczego przebiegu

```bash
SAMPLE_MODE=xor LSB_BITS=2 ENTROPY_CREDIT_BITS_PER_PIXEL=0.5 ./run_one.sh
```

```bash
SAMPLE_MODE=direct LSB_BITS=4 ENTROPY_CREDIT_BITS_PER_PIXEL=0.25 CONDITIONER_INPUT_BITS=8192 ./run_one.sh
```

```bash
SAMPLE_MODE=delta LSB_BITS=8 ENTROPY_CREDIT_BITS_PER_PIXEL=0.25 CONDITIONER_INPUT_BITS=16384 ./run_one.sh
```

## Ważna interpretacja

`ENTROPY_CREDIT_BITS_PER_PIXEL` jest parametrem konserwatywnej oceny źródła. Wyniki ENT, Dieharder, histogramowa Hmin, korelacje i poprawny rozkład po SHA3 nie ustalają samodzielnie bezpiecznego kredytu entropii.

Pełne kampanie nie zostały wykonane w środowisku budowania, ponieważ nie było dostępu do fizycznej kamery ani agenta mTLS. Wszystkie testy kodu, serializacji, CLI, raportów i spójności profili zakończyły się powodzeniem.
