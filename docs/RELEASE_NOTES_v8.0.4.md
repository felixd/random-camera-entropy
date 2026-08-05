# Camera Entropy Distributed 8.0.4

## Lokalna kompilacja, uprzywilejowana instalacja

Kompilator Go nie musi i nie powinien być dostępny w środowisku `sudo`. Wydanie 8.0.4 rozdziela te dwa etapy:

```bash
./agent/build.sh
sudo ./agent/install.sh
```

`agent/build.sh`:

- odmawia działania jako root;
- sprawdza Go 1.22+ w `PATH` lokalnego użytkownika;
- wykonuje testy Go i statyczną kompilację do `agent/bin/camera-entropy-agent`;
- porównuje wbudowaną wersję binarium z głównym plikiem `VERSION`;
- opcjonalnie wypisuje SHA-256 gotowego pliku.

`agent/install.sh`:

- nie wywołuje `go`, `go test` ani `go build`;
- wymaga wcześniej zbudowanego pliku `agent/bin/camera-entropy-agent`;
- odrzuca binarium z inną wersją niż bieżący projekt;
- kopiuje zweryfikowany plik atomowo do `/usr/local/bin/camera-entropy-agent`;
- dopiero potem wykonuje operacje wymagające roota: użytkownik systemowy, grupa `video`, udev, PKI i systemd.

Instalator nie usuwa ani nie przebudowuje plików w drzewie źródłowym należącym do lokalnego użytkownika.
