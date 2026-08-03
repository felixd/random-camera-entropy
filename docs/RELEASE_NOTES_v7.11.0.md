# Camera Entropy Distributed v7.11.0

Data wydania: 2026-08-03

## Maksymalnie cztery LSB

Aktywne CLI, panel WWW, analizator bitplane i kampania LSB dopuszczają teraz wyłącznie `LSB_BITS=1..4`. Kampania wykonuje 12 profili:

```text
xor    × 1..4 LSB
direct × 1..4 LSB
delta  × 1..4 LSB
```

Usunięcie 5–8 LSB z aktywnych profili wynika z kampanii porównawczej: dodatkowe płaszczyzny nie dawały proporcjonalnego wzrostu empirycznej min-entropii, natomiast zwiększały redundancję i koszt conditioningu.

## Przełączane jednostki przepustowości

Raporty zachowują przepustowość wewnętrznie w `bit/s`. Operator może przełączać wyświetlanie pomiędzy:

- `bit/s`,
- `kbit/s` — dziesiętne,
- `kB/s` — dziesiętne, domyślne,
- `MiB/s` — binarne,
- `MB/s` — dziesiętne.

Zmiana jednostki aktualizuje wartości w tabelach i osie wykresów bez ponownego generowania raportu.

## Kompleksowa ocena produkcyjna

Nowy profil `production-assessment` i skrypt `qualification_production_assessment.py` tworzą faktoryzowaną macierz decyzji. Poziom `full` jest domyślny i obejmuje 75 przypadków. `exhaustive` rozszerza temporalną macierz 1–4 LSB i obejmuje 104 przypadki.

Obszary testowe:

1. `xor/direct/delta × 1..4 LSB`;
2. pairing `disjoint/sliding` oraz lagi `1,2,4,8`;
3. wszystkie publiczne maski, offsety i kolejności serializacji;
4. czułość na przypisany entropy credit;
5. sweep wejścia SHA3-512;
6. dual weave: dwa porządki, trzy alignmenty i lagi `2,4,8`;
7. powtarzalność głównych kandydatów.

Testy używają tego samego źródła lub datasetu. Parametry każdego przypadku są zapisywane osobno i pokazywane w raporcie.

## Jeden plik do końcowej oceny

`production_assessment_report.html` jest samodzielnym artefaktem:

- zawiera lokalnie osadzony runtime Plotly;
- nie wymaga `/static/vendor` do wyświetlenia wykresów;
- zawiera wszystkie najważniejsze tabele i wykresy;
- zawiera pełny JSON w elemencie `#production-assessment-data`;
- pokazuje konfigurację kampanii, źródła i dokładne parametry każdego przypadku;
- może zostać przesłany jako pojedynczy plik albo udostępniony przez link read-only.

Automatyczny shortlist jest wyłącznie screeningiem. Nie zastępuje formalnej estymacji SP 800-90B ani decyzji o produkcyjnym entropy credit.
