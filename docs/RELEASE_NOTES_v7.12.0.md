# Camera Entropy Distributed 7.12.0

## Cel wydania

Wydanie porządkuje warstwę operatorską i wprowadza stałą, ostateczną bramkę przedprodukcyjną dla wybranego kandydata: temporalny XOR, 1 LSB, parowanie disjoint z lagiem 4, pełna zamrożona maska, serializacja row-major, entropy credit 0,5 bitu na piksel oraz SHA3-512 z wejściem 2048 bitów.

## Profil `final-preproduction`

Profil wymaga źródła `dataset-y`. W chwili startu wykonuje snapshot aktualnej liczby opublikowanych klatek i uruchamia równolegle pięć przebiegów nad dokładnie tym samym zakresem:

1. bezpieczny kandydat XOR/1 LSB/k=4/input 2048;
2. wariant wydajnościowy XOR/1 LSB/k=4/input 1024;
3. challenger lagu XOR/1 LSB/k=2/input 1024;
4. challenger szerokości XOR/2 LSB/k=4/credit 1,0/input 1024;
5. bezpośredni 1 LSB jako kontrola.

Weryfikowane są sumy SHA-256 wszystkich zamkniętych chunków. Aktywny, poprawnie opublikowany fragment rosnącego chunku może być czytany zgodnie z protokołem datasetu.

Pliki walidacyjne i finalne wyjście są ograniczone domyślnie do 64 MiB, ale statystyki źródła są liczone strumieniowo dla wszystkich zaakceptowanych symboli z całego snapshotu. Raport zawiera wynik zagregowany oraz najgorsze kolejne okno 1024 par.

## Bramka wydania

Bezpieczny kandydat przechodzi wyłącznie wtedy, gdy:

- cały snapshot kończy się przyczyną `dataset-exhausted`;
- nie występują błędy health/fail-closed;
- Hmin na wejściowy bit dla całego przebiegu i najgorszego okna wynosi co najmniej 0,98;
- finalne SHA3 ma Hmin bajtu co najmniej 7,90;
- odchylenie P(1) od 0,5 nie przekracza 0,001;
- bezwzględna korelacja lag-1 nie przekracza 0,005;
- p-value testu chi-square znajduje się w zakresie 0,0001–0,9999.

Wariant 1024-bitowy jest wyłącznie kandydatem do kontrolowanego A/B i nie zastępuje bezpiecznego profilu, dopóki oba nie przejdą bramek.

## Control Panel

Panel startowy zajmuje pełną szerokość i odpowiada kolejności pipeline’u:

1. źródło danych i zakres datasetu;
2. kalibracja oraz maska bad pixels;
3. tworzenie i serializacja bitów;
4. Von Neumann 0–4 razy;
5. conditioner kryptograficzny;
6. health tests i fail-closed;
7. wyniki oraz diagnostyka.

Sticky header zawiera osobny przycisk podglądu aktywnego workera. Nie ma osadzonego iframe. Presety profili ustawiają i podświetlają konkretne pola, a opcje niezgodne lub nieaktywne są blokowane również po stronie API.

## Organizacja kodu

- `profile_catalog.py` — centralny katalog profili i presetów;
- `masking.py` — kalibracja, maska produkcyjna i shadow monitor;
- `entropy_extractors.py` — ekstrakcja Von Neumanna i kolejne przejścia;
- `stream_statistics.py` — statystyki pełnego przebiegu i okien;
- `qualification_final_preproduction.py` — ostateczna kampania i samodzielny raport.

## Równoległość

Źródła LIVE pozostają wyłączne. Zadania `dataset-y` mogą działać równolegle na osobnych portach, ponieważ dataset jest źródłem tylko do odczytu. Limit ustala `MAX_DATASET_JOBS`; profil finalny rezerwuje pulę portów dla swoich pięciu wewnętrznych przebiegów.
