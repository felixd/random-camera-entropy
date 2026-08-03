# Camera Entropy Distributed v7.9.2

Wydanie integracyjne zbudowane z przesłanego aktualnego kodu.

## Zintegrowane poprawki

- Agent USB otwiera kamerę przy starcie, stale pobiera klatki i przekazuje czas pracy/warm-up źródła.
- Osobny recorder zapisuje pełne Y8 albo pakowane LSB w chunkach do konfigurowalnego limitu (domyślnie 300 GB).
- `frame-buffer-latest` jest publikowany podczas zapisu, a `dataset-y` może bezpiecznie śledzić rosnący dataset.
- Zatrzymane datasety są prawidłowym źródłem; domyślna ścieżka to względne `data/frame-buffer-latest`.
- Control server wykonuje preflight datasetu, pobiera geometrię z manifestu, nie wywołuje podatnego na wyścig `os.getpgid()` i zwraca błędy JSON zamiast ogólnego HTML 500.
- Pole `Entropy credit` działa poprawnie w polskiej lokalizacji (np. `1,0`) i nie generuje komunikatu o najbliższych wartościach `0,990001` / `1,000001`.

## Zmiana pola liczbowego

HTML używa `step="any"`, ponieważ wcześniejsze połączenie `min="0.000001"` i `step="0.01"` tworzyło bazę kroku od minimum. W konsekwencji dokładne `1.0` nie należało do siatki dozwolonych wartości. Formularz wysyła znormalizowaną wartość z `valueAsNumber`, a backend toleruje przecinek dziesiętny jako zabezpieczenie zgodności.
