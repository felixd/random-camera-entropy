# Camera Entropy Distributed 8.0.1

## Poprawka parametrów źródła

Panel kontrolny traktuje parametry datasetu jako pola ściśle związane ze źródłem `dataset-y`. Po wybraniu `v4l2`, `tls-y` albo `rtsp` cały blok datasetu jest ukrywany, jego kontrolki są wyłączane, a serializer dodatkowo pomija wszystkie klucze `dataset_*` i `final_preprod_*`.

Backend pozostaje odporny na starsze przeglądarki, zapisane formularze i ręczne wywołania API: parametry datasetowe przesłane wraz ze źródłem LIVE są ignorowane, ponieważ nie wpływają na środowisko workera LIVE. Profile, które z definicji wymagają datasetu, nadal walidują typ źródła osobno.
