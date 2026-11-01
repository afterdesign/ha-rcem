# RCEm (PSE) dla Home Assistant

Integracja pobiera **Rynkową Miesięczną Cenę Energii Elektrycznej (RCEm)** ze strony
[PSE](https://www.pse.pl/oire/rcem-rynkowa-miesieczna-cena-energii-elektrycznej)
i liczy, ile zarobiła energia oddana do sieci. Każda godzina miesiąca ma tę samą cenę RCEm tego miesiąca.

## Instalacja (bez HACS)

1. Dodaj repozytorium aplikacji i zainstaluj aplikację **RCEm Installer**:

   [![Dodaj repozytorium do Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FOWNER%2Fha-rcem)

2. Uruchom aplikację (**Start**). Skopiuje integrację do `custom_components/rcem` i wyświetli powiadomienie.
3. Zrestartuj Home Assistant.
4. Dodaj integrację:

   [![Dodaj integrację](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=rcem)

Aktualizacja: zaktualizuj aplikację, uruchom ją ponownie i zrestartuj HA.
Aplikacja wymaga Home Assistant OS lub Supervised. Na innych instalacjach skopiuj
`custom_components/rcem` ręcznie albo dodaj to repozytorium w HACS jako repozytorium niestandardowe (custom repository).

## Konfiguracja

| Pole | Opis |
|---|---|
| Sensor energii oddanej do sieci | licznik kWh eksportu (np. ten sam co w panelu Energia). Bez niego integracja daje tylko ceny |
| Stawka VAT | domyślnie 23% (cena brutto = netto × 1,23) |
| Używaj skorygowanej RCEm | PSE publikuje korekty po kilku miesiącach; brana jest najnowsza |
| Szacuj niepublikowane miesiące | RCEm za miesiąc pojawia się ok. 11. dnia następnego miesiąca. Do tego czasu zarobek liczony jest ostatnią opublikowaną ceną, a po publikacji historia jest przeliczana |

## Co dostajesz

**Encje**

| Encja | Opis |
|---|---|
| `sensor.rcem_pse_cena_rcem_netto` / `_brutto` | cena obowiązująca w bieżącym miesiącu [PLN/kWh]; atrybut `estimated` mówi, czy to szacunek, `prices` zawiera wszystkie miesiące |
| `sensor.rcem_pse_zarobek_biezacy_miesiac_netto` / `_brutto` | zarobek w bieżącym miesiącu [PLN] |
| `sensor.rcem_pse_zarobek_poprzedni_miesiac_netto` / `_brutto` | zarobek w poprzednim miesiącu [PLN] |
| `sensor.rcem_pse_zarobek_lacznie_netto` / `_brutto` | zarobek od początku historii licznika; atrybut `months` zawiera rozbicie na miesiące |

**Statystyki długoterminowe** (do wykresów; godzinowe, agregowane do dnia, tygodnia i miesiąca):

| Statystyka | Opis |
|---|---|
| `rcem:earnings_net`, `rcem:earnings_gross` | zarobek [PLN], suma narastająca: każda godzina eksportu × RCEm jej miesiąca |
| `rcem:price_net`, `rcem:price_gross` | cena RCEm [PLN/kWh] dla każdej godziny |

Statystyki są liczone wstecz z historii sensora eksportu, więc od razu po instalacji widać zarobek za miniony okres.

### Wykres dziennego zarobku

```yaml
type: statistics-graph
title: Zarobek dzienny (RCEm)
chart_type: bar
period: day
days_to_show: 30
stat_types: [change]
entities:
  - rcem:earnings_gross
  - rcem:earnings_net
```

### Panel Energia

W ustawieniach sieci (Grid) w panelu Energia, w polu rekompensaty za eksport, można wybrać
„Use an entity tracking the total amount” → `rcem:earnings_gross`. Wtedy panel Energia pokazuje
przychód liczony po RCEm, również przeliczony wstecz po publikacji ceny.

## Rozwój

```bash
python3 -m venv .venv && .venv/bin/pip install pytest-homeassistant-custom-component
.venv/bin/pytest tests
./scripts/sync_installer.sh   # kopiuje integrację do aplikacji instalacyjnej, przed każdym wydaniem
```
