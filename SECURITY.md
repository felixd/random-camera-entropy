# Security notes

- mTLS wymaga TLS 1.3 i certyfikatów obu stron.
- Klucz CA nie powinien znajdować się na hoście USB ani na serwerze obliczeniowym.
- Port agenta należy ograniczyć firewallem do serwera obliczeniowego.
- Klucze prywatne: `0600`; katalog z kluczami: `0700`.
- `ALLOWED_CLIENT_CN` dodatkowo ogranicza akceptowany certyfikat klienta.
- Program nie ma trybu niezabezpieczonego TCP dla zdalnego USB.
- Dane RTSP mogą być nieszyfrowane mimo że samo przetwarzanie jest lokalne. Stosuj izolowaną sieć/VPN/RTSPS zależnie od możliwości kamery.
- Pliki z URL RTSP powinny mieć prawa `0600`. URL jest redagowany w raportach.
- Nie konsumuj plików BIN przed pojawieniem się `READY.json`.

## Panel WWW

- Publiczny reverse proxy powinien kierować wyłącznie do `127.0.0.1:8087`.
- Worker `127.0.0.1:18087` nie może być publikowany w sieci.
- Domyślny `WEB_AUTH_MODE=app` wymaga pliku z hashem hasła oraz sekretu sesji.
- Operacje zmieniające stan wymagają zalogowanej sesji i tokenu CSRF.
- Źródła są definiowane przez administratora w `sources.json`; klient WWW nie może podać dowolnego URL RTSP, ścieżki programu ani zmiennych środowiskowych.
- Pliki spod `/data` są dostępne wyłącznie po uwierzytelnieniu, chyba że jawnie włączono read-only share.
- Token read-only share jest sekretem umieszczonym w ścieżce URL. Powinien być długi, losowy, rotowany i nie może być publikowany.
- `WEB_TRUST_PROXY=1` wolno używać tylko wtedy, gdy aplikacja jest osiągalna wyłącznie przez zaufany lokalny reverse proxy.

## Fail-closed dual weave

- RCT/APT są prowadzone osobno dla A-even, A-odd, B-even, B-odd, C0 i C1.
- Clipping jest egzekwowany osobno dla pełnej maski oraz faz even/odd; alarm dowolnego zakresu zatrzymuje wszystkie outputy.
- `same-group`, `stagger-1` i `stagger-2` mają odrębne separatory domeny SHA3 i osobne pliki wynikowe.
- Publiczny licznik bloku i separator domeny nie są źródłem entropii.
- Wynik stagger nie może być traktowany jako niezależny od baseline ani od innych alignmentów, ponieważ wszystkie warianty są generowane z tych samych ramek.
- `SHA256SUMS` kampanii obejmuje rekursywnie wszystkie pliki BIN w podkatalogach runów.
