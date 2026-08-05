# Camera Entropy Distributed 8.0.3

## Naprawiona granica agent Go / worker Python

Agent 8.0.0 potrafił wewnętrznie rozpoznać poprawną ekspozycję, ale wysyłał przez CEYTLS01 surowy tekst `auto_exposure: 1 (Manual Mode)`. Worker porównywał go literalnie z liczbą `1` i uruchamiał fail-closed mimo poprawnego stanu kamery.

W 8.0.3 obie strony są odporne na ten przypadek:

- agent wysyła wartości numeryczne i deklaruje `control_value_schema=integer-v1`;
- worker normalizuje także format historyczny, więc może bezpiecznie współpracować ze starszym agentem;
- nieparsowalna albo naprawdę zmieniona kontrolka nadal powoduje fail-closed.

## Eliminacja starego binarium

`camera-entropy-agent --version` pokazuje wersję wbudowaną w wykonywany plik. `agent/install.sh` usuwa historyczne binaria z drzewa źródeł, buduje nowy plik, instaluje go do `/usr/local/bin`, a następnie sprawdza zgodność obu wersji. Lokalna budowa jest wykonywana przez `agent/build.sh` do `agent/bin/`.

Do poprawnego wdrożenia należy zaktualizować zarówno aplikację główną, jak i agenta. Log workera powinien pokazywać wersję 8.0.3 oraz połączenie zawierające `agent=...8.0.3 controls=integer-v1`.
