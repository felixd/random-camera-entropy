# Camera Entropy Distributed 7.13.0

Wydanie naprawia brak informacji podczas wielominutowej weryfikacji ponad 200 GB danych oraz porządkuje tabele Control Panelu.

## Najważniejsze zmiany

- `qualification_final_preproduction.py --verify-workers N` — równoległe hashowanie chunków;
- `--workers N` — liczba jednocześnie uruchomionych wariantów analizy;
- postęp SHA-256: procent, chunki, bajty, przepustowość i ETA;
- heartbeat trwających wariantów;
- opcjonalny cache pełnej weryfikacji niezmienionego datasetu;
- `dataset_integrity.py` jako jedna implementacja walidacji ścieżek i SHA-256;
- pola workerów w panelu WWW;
- poprawione sticky headers i szerokości kolumn tabel zadań/raportów.

## Przykład

```bash
SOURCE_TYPE=dataset-y \
DATASET_DIR=data/frame-buffer-latest \
DATASET_VERIFY_HASHES=1 \
./qualification_final_preproduction.py --workers 5 --verify-workers 8
```

Na pojedynczym HDD użyj zwykle 1–2 workerów weryfikacji. Na NVMe warto zacząć od 4–8 i obserwować rzeczywistą przepustowość.
