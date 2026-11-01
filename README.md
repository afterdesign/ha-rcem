# RCEm (PSE) dla Home Assistant

Integracja pobiera **Rynkową Miesięczną Cenę Energii Elektrycznej (RCEm)** ze strony
[PSE](https://www.pse.pl/oire/rcem-rynkowa-miesieczna-cena-energii-elektrycznej)
i liczy, ile zarobiła energia oddana do sieci. Każda godzina miesiąca ma tę samą cenę RCEm tego miesiąca.

## Instalacja (bez HACS)

1. Dodaj repozytorium aplikacji i zainstaluj aplikację **RCEm Installer**:

   [![Dodaj repozytorium do Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fafterdesign%2Fha-rcem)

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
| Szacowanie nieopublikowanych miesięcy | RCEm za miesiąc pojawia się ok. 11. dnia następnego miesiąca. Do tego czasu zarobek liczony jest szacunkiem, a po publikacji historia jest przeliczana. Do wyboru: **z RCE i generacji PV** (domyślnie), **ostatnia opublikowana RCEm** albo **bez szacowania** |

### Jak szacowana jest RCEm

PSE liczy RCEm jako średnią cen RCE ważoną energią, którą prosumenci wprowadzili do sieci
(art. 4b ustawy o OZE; ujemne RCE liczone jako 0):

> RCEm = Σ(Eₜ · RCEₜ) / ΣEₜ

Wolumen prosumentów (Eₜ) nie jest publiczny, więc integracja pobiera z
[API PSE](https://api.raporty.pse.pl) ceny RCE (`rce-pln`) i krajową generację PV (`his-wlk-cal`)
co 15 minut i liczy średnią RCE ważoną generacją PV. Taka średnia jest systematycznie o ok. 15%
za wysoka (autokonsumpcja „zjada” poranną i wieczorną produkcję, więc do sieci trafia głównie
tańszy szczyt w południe), dlatego wynik mnożony jest przez współczynnik kalibracji: średni
stosunek opublikowanej RCEm do RCE ważonej PV z ostatnich 12 opublikowanych miesięcy.

Walidacja krokowa na 04.2025–09.2026 (współczynnik liczony tylko z wcześniejszych miesięcy):

| Metoda | Średni błąd | Najgorszy miesiąc | lip–wrz 2026 |
|---|---|---|---|
| RCE ważona PV × współczynnik | 7,3% | 20,2% | 2,2% |
| Ostatnia opublikowana RCEm | 29,9% | 72,6% | 9,8% |

Szacunek aktualizuje się co 6 godzin, w miarę dochodzenia danych z kolejnych dni. Miesiąc bez danych
(np. 1. dnia miesiąca) dostaje szacunek poprzedniego nieopublikowanego miesiąca. Gdy API PSE jest
niedostępne, używana jest ostatnia opublikowana RCEm. Atrybuty sensorów `estimate_method`,
`calibration_factor`, `pv_weighted_rce` i `rce_days` pokazują, skąd pochodzi szacunek.

## Co dostajesz

**Encje** (identyfikatory dla HA w języku angielskim; przy polskim języku nazwy i ID będą polskie)

| Encja | Opis |
|---|---|
| `sensor.rcem_pse_rcem_price_net` / `_gross` | cena obowiązująca w bieżącym miesiącu [PLN/kWh]; atrybut `estimated` mówi, czy to szacunek, `prices` zawiera wszystkie miesiące |
| `sensor.rcem_pse_earnings_this_month_net` / `_gross` | zarobek w bieżącym miesiącu [PLN] |
| `sensor.rcem_pse_earnings_last_month_net` / `_gross` | zarobek w poprzednim miesiącu [PLN] |
| `sensor.rcem_pse_earnings_total_net` / `_gross` | zarobek od początku historii licznika; atrybut `months` zawiera rozbicie na miesiące |

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

### Wykres miesięcznego zarobku

Każdy miesiąc liczony jest RCEm tego miesiąca. Miesiące jeszcze nieopublikowane przez PSE
są szacowane ostatnią ceną i przeliczane po publikacji.

```yaml
type: statistics-graph
title: Zarobek miesięczny (RCEm)
chart_type: bar
period: month
days_to_show: 365
stat_types: [change]
entities:
  - entity: rcem:earnings_gross
    name: Brutto
  - entity: rcem:earnings_net
    name: Netto
```

### Tabela rozliczenia miesięcznego

Karta markdown z atrybutu `months` sensora zarobku łącznego:

```yaml
type: markdown
content: |
  {% set months = state_attr('sensor.rcem_pse_earnings_total_gross', 'months') or {} %}
  | Miesiąc | Eksport [kWh] | RCEm netto [zł/kWh] | Netto [zł] | Brutto [zł] |
  |:--|--:|--:|--:|--:|
  {% for k in months | sort(reverse=true) -%}
  {% set m = months[k] -%}
  | {{ k }}{{ ' *(szacunek)*' if m.estimated }} | {{ '%.1f' | format(m.kwh) }} | {{ '%.4f' | format(m.price_net) if m.price_net is not none else '–' }} | {{ '%.2f' | format(m.net) }} | {{ '%.2f' | format(m.gross) }} |
  {% endfor -%}
  | **Razem** | | | **{{ states('sensor.rcem_pse_earnings_total_net') }}** | **{{ states('sensor.rcem_pse_earnings_total_gross') }}** |
```

Przykładowy wynik:

| Miesiąc | Eksport [kWh] | RCEm netto [zł/kWh] | Netto [zł] | Brutto [zł] |
|:--|--:|--:|--:|--:|
| 2026-10 *(szacunek)* | 249.3 | 0.3457 | 86.18 | 106.00 |
| 2026-09 | 448.6 | 0.3457 | 155.07 | 190.74 |
| 2026-08 | 789.4 | 0.2945 | 232.50 | 285.98 |
| 2026-07 | 897.2 | 0.2629 | 235.86 | 290.10 |
| 2026-06 | 945.2 | 0.2732 | 258.23 | 317.62 |

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
