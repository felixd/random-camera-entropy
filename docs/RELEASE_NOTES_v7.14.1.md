# Camera Entropy Distributed 8.0.0

Wydanie poprawkowe naprawiające ponowne hashowanie niezmienionych datasetów.

## Przyczyna

Moduł integralności poprawnie obsługiwał cache, a kwalifikacja finalna go zapisywała, lecz zwykłe źródło `dataset-y` wywoływało `verify_dataset_chunks(..., use_cache=False)` i nie przekazywało ścieżki cache. Każdy przebieg produkcyjny czytał więc i hashował wszystkie chunki od nowa.

## Zachowanie po poprawce

Dla zatrzymanego lub ukończonego datasetu źródło oblicza stabilną ścieżkę `data/.integrity-cache/<24-znakowy-klucz>.json`. Cache jest uznawany tylko wtedy, gdy pełny fingerprint jest identyczny: ścieżka datasetu, SHA-256 `checksums.sha256`, lista i oczekiwane hashe chunków, rozmiary oraz nanosekundowe czasy modyfikacji. Zmiana choć jednego elementu wymusza ponowną fizyczną weryfikację.

Dla aktywnie rosnącego datasetu cache nie jest używany, ponieważ lista zamkniętych chunków może się zmieniać.

## CLI

```bash
--dataset-verify-cache          # domyślnie włączone
--no-dataset-verify-cache       # wymusza świeżą weryfikację
--dataset-verify-cache-dir DIR  # opcjonalny wspólny katalog cache
```
