# Lokalne PKI mTLS

Ten katalog jest domyślną lokalizacją certyfikatów i kluczy używanych przez transport USB/mTLS.

Utwórz je z katalogu projektu:

```bash
./scripts/admin/generate_mtls_pki.sh
```

Domyślne dane certyfikatu agenta:

- nazwa DNS/SAN: `camera`;
- adres IP/SAN: `192.168.1.2`;
- klient: `camera-compute`.

Pliki prywatne (`*.key`) i certyfikaty (`*.crt`) są ignorowane przez Git. Nie publikuj `ca.key`.
