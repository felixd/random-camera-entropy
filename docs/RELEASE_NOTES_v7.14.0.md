# Camera Entropy Distributed 7.14.0

Wydanie produkcyjne porządkujące wybór maski, opis pipeline'u oraz koszt diagnostyki.

## Profil produkcyjny

`production-safe` / `run_production.sh` uruchamia: temporal XOR, 1 LSB, disjoint lag 4, pełną zamrożoną maskę, row-major, entropy credit 0,5, SHA3-512 z wejściem 2048 bitów, bez Von Neumanna i bez kosztownych plików diagnostycznych.

## Zgodność raportów

`spatial_sampling` jest oznaczone jako opcja legacy i nie może już sugerować checkerboardu, gdy aktywne `spatial_mask_pattern=full`. Raport pipeline'u odczytuje `0/1`, bool i wartości tekstowe, więc wyłączony VN nie jest prezentowany jako aktywny.
