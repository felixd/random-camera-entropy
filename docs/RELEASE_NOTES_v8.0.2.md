# Camera Entropy Distributed 8.0.2

## Poprawka komunikacji z agentem Go

Kamery UVC mogą zwracać kontrolkę menu w postaci:

```text
auto_exposure: 1 (Manual Mode)
```

Wersja 8.0.1 próbowała zamienić cały fragment `1 (Manual Mode)` na liczbę. Konwersja nie powiodła się, więc poprawny tryb manualny został błędnie zakwalifikowany jako zmiana ustawień. Agent przechodził w stan fatal i zamykał połączenie TLS przed wysłaniem `hello`.

W 8.0.2 parser wyodrębnia wartość numeryczną, sprawdza obie kontrolki typowo i w razie rzeczywistej zmiany najpierw próbuje przywrócić konfigurację. Klient otrzymuje też strukturalny komunikat CEYTLS01 zamiast anonimowego EOF.

Po wdrożeniu należy zrestartować usługę agenta, ponieważ agent 8.0.1 pozostaje w stanie fatal do restartu procesu.
