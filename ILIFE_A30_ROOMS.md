# ILIFE A30 Pro – sprzątanie pokoi (fork tuya-local)

Ten fork dodaje do pliku `ilife_a30_pro_vacuum.yaml` dwie rzeczy:

| DP  | Encja w HA                          | Co robi |
|-----|-------------------------------------|---------|
| 133 | atrybut `room_list` encji odkurzacza | lista pokoi zgłaszana przez robota (base64) |
| 129 | `text` „Room selection”             | wybór pokoi – **ustawienie od razu uruchamia sprzątanie** |

Fork codziennie scala zmiany z [make-all/tuya-local](https://github.com/make-all/tuya-local)
(workflow `Sync upstream`), więc aktualizacje przez HACS nie kasują tych poprawek.

## Format DP 129 (wybór pokoi)

Base64 z 6 bajtów: `01 01` + 4-bajtowa maska (big-endian). Bit N = pokój o ID N.

| Pokoje (ID)       | Maska  | Wartość DP 129 |
|-------------------|--------|----------------|
| 0                 | `0x01` | `AQEAAAAB` |
| 1                 | `0x02` | `AQEAAAAC` |
| 3                 | `0x08` | `AQEAAAAI` |
| 5                 | `0x20` | `AQEAAAAg` |
| 3 + 5             | `0x28` | `AQEAAAAo` |

Jeśli robot akurat wraca do stacji lub sprząta, najpierw go zatrzymaj (pauza) – inaczej zignoruje wybór.

## Jak ustalić ID i nazwy pokoi

Nazwy pokoi trzyma aplikacja/chmura Tuya, robot zna tylko numery (ID). Są trzy sposoby, żeby je dopasować:

### 1. Atrybut `room_list` (najprościej)

Robot co jakiś czas (m.in. po wczytaniu mapy / przy sprzątaniu) wysyła lokalnie DP 133.
W HA: *Narzędzia deweloperskie → Stany → vacuum.…* → atrybut `room_list`. Odkoduj base64
(np. w *Narzędzia deweloperskie → Szablon*):

```jinja
{{ state_attr('vacuum.robot_vacuum', 'room_list') | base64_decode }}
```

Wynik ma postać `idMapy,mapName,ID,Nazwa,ID,Nazwa,…`, np.:

```
1794503112,mapName,5,Łazienka,0,Salon,3,Korytarz,1,Kuchnia,7,Sypialnia,2,Pokój dziecięcy
```

czyli 5 = Łazienka, 0 = Salon, 3 = Korytarz itd. (ID stoi **przed** nazwą).
Uwaga: atrybut bywa pusty – robot nie zawsze go zgłasza, a pokoje bez nadanej nazwy mogą
w nim nie występować. Po zmianie nazwy w aplikacji lista nie aktualizuje się od razu.

### 2. Podejrzenie wyboru z aplikacji

Uruchom w aplikacji sprzątanie **jednego** pokoju i zajrzyj w HA do encji „Room selection”
(albo w historię jej stanu). Np. `AQEAAAAQ` → bajty `01 01 00 00 00 10` → maska `0x10` → bit 4 → ID 4.
Szybkie odkodowanie w szablonie:

```jinja
{% set a = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/' %}
{% set v = states('text.robot_vacuum_room_selection') %}
{% set m = a.index(v[6]) * 64 + a.index(v[7]) %}
maska={{ m }}, ID: {% for i in range(8) if (m // (2 ** i)) % 2 == 1 %}{{ i }} {% endfor %}
```

### 3. Próba z HA

Ustaw „Room selection” na wartość z jednym bitem (tabela wyżej) i zobacz, dokąd jedzie robot
(aplikacja też podświetli wybrany pokój). Pamiętaj: robot rusza od razu.

## Przykładowy skrypt HA

```yaml
script:
  robot_kuchnia:
    alias: "Robot: Kuchnia"
    sequence:
      - action: text.set_value
        target: { entity_id: text.robot_vacuum_room_selection }
        data: { value: "AQEAAAAC" }   # ID 1
```

Pełny zestaw (skrypt z wyborem wielu pokoi, czujnik z listą, karta z przyciskami) – patrz
`pokoje.yaml` w moim katalogu konfiguracji.
