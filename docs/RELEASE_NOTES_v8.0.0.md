# Camera Entropy Distributed 8.0.0

## Zmiana architektury

Kod Python został przeniesiony do pakietu `app/` i podzielony według odpowiedzialności: `core`, `sources`, `control`, `reporting`, `qualification`, `tools` oraz `web`. Skrypty operatorskie znajdują się w `scripts/run`, `scripts/qualification`, `scripts/smoke` i `scripts/admin`.

## Agent Go

Pythonowy agent USB został zastąpiony agentem Go zgodnym z protokołem CEYTLS01/mTLS. Agent stale opróżnia YUYV, przekazuje Y8, raportuje rzeczywisty warm-up i automatycznie testuje `/dev/video0..2`. Instalator buduje program, konfiguruje użytkownika/grupę `video`, regułę udev, PKI i systemd.

## Równoległość

Read-only `dataset-y` może być uruchamiany wielokrotnie równolegle na osobnych portach. Jedno zadanie LIVE może działać obok zadań datasetowych. Blokowana jest wyłącznie próba uruchomienia drugiego zadania LIVE. Limit określa `MAX_DATASET_JOBS` lub `--max-dataset-jobs`.

## Panel WWW

Log zadania został przeniesiony do modalnego, pływającego okna otwieranego przyciskiem `Log`. Tabela zadań nie jest już rozpychana przez terminal. Modal ma pauzę, autoscroll, wybór zadania, czyszczenie i zamykanie przez `Esc`.

## Zgodność

Matematyka istniejących pipeline'ów, format datasetu, raporty i protokół TLS-Y pozostają zgodne. Zmieniły się ścieżki plików i entrypointy.
