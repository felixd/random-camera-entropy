# Camera Entropy Distributed 8.0.5

## Poprawka warm-upu źródła TLS-Y

W wersji 8.0.4 agent Go poprawnie raportował rzeczywisty czas działania kamery w komunikacie `hello`, lecz worker odczytywał `source_warmup_seconds` wyłącznie z nagłówków ramek. Nagłówki ramek nie zawierały tego pola, więc worker uruchamiał własny pełny timer od zera.

Wersja 8.0.5 naprawia cały przepływ:

- worker zalicza czas raportowany w `hello`, dzięki czemu współpracuje także z agentem 8.0.4;
- agent 8.0.5 umieszcza `source_warmup_seconds` w każdej ramce CEYTLS01;
- aktualna wartość z ramki koryguje deadline workera;
- brak metadanych nadal uruchamia lokalny, fail-closed timer;
- reconnect przed produkcją zeruje stan parowania i kalibracji, ale zalicza wiek tej samej ciągle przechwytywanej kamery;
- WWW pokazuje efektywny czas pozostały oraz `wiek źródła`.

Przykład: przy wymaganym warm-upie 3600 s i wieku źródła 2400 s worker odlicza jeszcze około 1200 s, a nie pełne 3600 s.
