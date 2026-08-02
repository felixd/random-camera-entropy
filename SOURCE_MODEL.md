# Model źródła i granice deklaracji

## Bazowy kandydat produkcyjny

Pojedynczą binarną próbką do oceny SP 800-90B jest bit ze strumienia:

```text
LSB(Y[A]) XOR LSB(Y[B])
```

po zastosowaniu:

1. zamrożonej aktywnej maski skalibrowanej dla bieżącej epoki;
2. stałej fazy `checkerboard-even`;
3. odrzuceniu całej pary, gdy health monitor clippingu przekroczy próg.

Health tests RCT/APT działają na tej sekwencji przed SHA3-512 i przed diagnostycznym Von Neumannem.

## Uproszczony kandydat `temporal-sha3`

Profil `temporal-sha3` zachowuje tę samą temporalną próbkę `LSB(Y[A]) XOR LSB(Y[B])`, ale używa całej zamrożonej aktywnej maski (`spatial_sampling=full`) i pomija checkerboard, dual weave oraz Von Neumanna. RCT/APT i clipping są wykonywane przed conditionowaniem.

```text
temporal LSB po pełnej masce [65 536 bitów / blok]
        ↓
SHA3-512
        ↓
512 bitów
```

Domyślne `65 536 → 512` oznacza roboczą kompresję `128:1`. Nie jest to jeszcze zatwierdzony współczynnik kredytowania entropii; ma zostać porównany z klasycznym i dual-weave pipeline'em na danych non-IID, restart tests i testach awarii źródła.

## Eksperymentalny dual weave — kandydat row-major / stagger-2

Dual weave v2 wykorzystuje dwie kolejne rozłączne mapy temporalne:

```text
A = LSB(F0) XOR LSB(F4)
B = LSB(F1) XOR LSB(F5)
```

oraz kompozyty:

```text
C0: even <- A, odd <- B
C1: even <- B, odd <- A
```

Warianty conditionera:

```text
same-group: C0_g + C1_g
stagger-1:  C0_g + C1_(g+1)
stagger-2:  C0_g + C1_(g+2)
```

Aktualnym kandydatem do dalszych testów jest `row-major / stagger-2`. Aktywne piksele każdej mapy są serializowane od lewej do prawej, wiersz po wierszu. Pierwszy pełny blok conditionera ma dokładnie postać `C0_g[1024] || C1_(g+2)[1024]`; nie występuje naprzemienne przeplatanie pojedynczych bitów.

Stagger jest eksperymentem zmieniającym zależności czasowe wejścia, a nie dowodem niezależności. Do oceny należy zachować osobno fazy `A-even`, `A-odd`, `B-even`, `B-odd`, surowe `C0/C1` oraz dokładnie zserializowane wejście każdego conditionera. Wyniki baseline i dual weave nie mogą być łączone w jeden zbiór estymacyjny.

RCT/APT działają niezależnie na czterech fazach A/B oraz na C0 i C1. Przekroczenie progu przez dowolną ścieżkę zatrzaskuje cały przebieg.

## Epoka maski

Epoka zaczyna się po:

- rozgrzewce;
- ustawieniu i weryfikacji ekspozycji;
- kalibracji 512 par;
- zapisaniu `active_mask.png`, `production_mask.png`, identyfikatora epoki i SHA-256 maski.

Maska aktywna nie jest aktualizowana w trakcie epoki. Shadow mask służy tylko do monitorowania dryftu. Trwały dryft zatrzymuje wyjście.

## Clipping

Dla baseline fail-closed dotyczy wybranej maski produkcyjnej. Dla dual weave próg jest egzekwowany niezależnie dla:

```text
full
even
odd
```

Przekroczenie progu przez dowolny z tych zakresów przez wymaganą liczbę kolejnych par zatrzymuje generator. Zakres `production` pozostaje raportowany dla porównania z baseline.

## Conditionery

Baseline:

```text
SHA3-512(input_block[2048 bitów]) -> 512 bitów
```

Dual weave:

```text
SHA3-512(domain || block_counter || C0[1024] || C1[1024]) -> 512 bitów
```

Dla stagger C0 i C1 mogą pochodzić z różnych grup zgodnie z alignmentem. Separator domeny i licznik są publicznymi metadanymi, nie są zaliczane do entropii. Służą rozdzieleniu wariantów i bloków; nie zwiększają deklarowanej min-entropii.

Współczynnik 4:1 jest kandydatem roboczym. Pełne 512 bitów entropii na wyjściu można deklarować dopiero wtedy, gdy konserwatywna dolna granica entropii wejścia na blok i reguły zatwierdzonego conditionera na to pozwalają.

## Co nie jest twierdzeniem o entropii

Następujące wyniki są diagnostyczne i same nie stanowią deklaracji min-entropii:

- Shannon entropy z `ent`;
- marginalna Hmin z częstości zer i jedynek;
- histogram częstotliwości bajtów i Hmin histogramu bajtów;
- korelacja lag-1;
- korelacja pozycyjna C0↔C1;
- chi-square;
- Monte Carlo π;
- PASS RCT/APT;
- brak widocznych wzorców po SHA3;
- live histogramy etapów, heatmapa obserwowanych przejść, mapa reszt Pearsona względem niezależnych marginesów, H(X), H(Y), H(X,Y), H(Y|X), Cramér V i histogramowa Hmin par.

Ocena źródła musi używać ścieżki non-IID i restart testu, wraz z dokumentacją fizycznego modelu oraz warunków działania.

## Wyjścia

Baseline:

- `y_temporal_masked_validation.bin`: wybrany strumień próbek przed conditioningiem, pakowany MSB-first; historyczna nazwa pliku pozostaje dla zgodności, natomiast `runner_config.json` określa `sample_mode`, `lsb_bits` i kolejność `pixel-major-lsb-first`;
- `camera_entropy_sha3_512.bin`: kandydat kondycjonowanego wyjścia;
- `y_temporal_vn.bin`: równoległe wyjście diagnostyczne.

Dual weave:

- `dual_weave_phase_*_validation.bin`: cztery fazy źródłowe;
- `dual_weave_<order>_c0_raw_validation.bin` i `...c1...`: kompozyty przed conditioningiem;
- `dual_weave_<order>_<alignment>_conditioner_input_validation.bin`: rzeczywista kolejność bitów bloku SHA3;
- `dual_weave_<order>_<alignment>_sha3_512.bin`: eksperymentalne wyjście kondycjonowane;
- `binary_geometry_report.html`: diagnostyka 2D najważniejszych plików BIN;
- `dual_weave_stagger2_checkerboard.svg`: generowany opis przepływu C0/C1 i kolejki stagger-2.

## Granica transportu w v7

Dla `source_type=tls-y` próbka fizyczna nadal pochodzi z tych samych bezpośrednich bajtów Y lokalnego YUYV. mTLS, nagłówek ramki i kontrolny SHA-256 są wyłącznie transportem; nie są elementem conditionera ani źródłem dodatkowej entropii.

Dla `source_type=rtsp` model jest inny: bit pochodzi z płaszczyzny Y po ISP kamery, enkoderze, sieci i dekoderze. Takie dane wymagają odrębnej oceny, nawet gdy dalszy pipeline ma identyczne parametry.

Luka w `frame_id` z agenta TLS domyślnie kończy przebieg. Nie wolno po cichu traktować `k` odebranych ramek jako `k` kolejnych ramek fizycznych po wykryciu utraty.
