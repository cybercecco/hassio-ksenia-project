# Ksenia Lares 4.0 Gateway for Home Assistant

[![hassfest](https://github.com/cybercecco/hassio-ksenia-project/actions/workflows/hassfest.yml/badge.svg)](https://github.com/cybercecco/hassio-ksenia-project/actions/workflows/hassfest.yml)
[![HACS](https://github.com/cybercecco/hassio-ksenia-project/actions/workflows/hacs.yml/badge.svg)](https://github.com/cybercecco/hassio-ksenia-project/actions/workflows/hacs.yml)
[![GitHub release](https://img.shields.io/github/v/release/cybercecco/hassio-ksenia-project?include_prereleases)](https://github.com/cybercecco/hassio-ksenia-project/releases)

Un'integrazione custom (HACS-compatibile) che agisce da gateway fra una
centrale di allarme **Ksenia Lares 4.0** e Home Assistant. Sfrutta il
protocollo WebSocket ufficiale esposto dalla centrale (lo stesso usato
dall'app Ksenia Pro, da SecureWeb e dai driver Control4/Milestone) e
mantiene una connessione continua per ricevere gli aggiornamenti in
tempo reale.

## Caratteristiche

- Connessione WebSocket persistente (TLS o in chiaro) con riconnessione
  automatica ed esponenziale backoff.
- Login/logout con PIN utente e CRC-16 del protocollo Ksenia.
- Snapshot iniziale di zone, partizioni, scenari e stato di sistema.
- Aggiornamenti in tempo reale tramite broadcast `REALTIME` della
  centrale: niente polling.
- Entità esposte:
  - `alarm_control_panel.alarm` — inserimento/disinserimento totale e
    parziale guidato dagli scenari `CAT=DISARM/ARM/PARTIAL`.
  - `binary_sensor.*` — una per ogni zona (stato aperto/chiuso,
    con `device_class` dedotta dalla categoria della zona).
  - `sensor.*` — stato di ogni partizione (ARM/allarme/manomissione)
    più lo stato globale della centrale.
  - `button.*` — un pulsante per ogni scenario configurato
    (inclusi scenari personalizzati).

## Requisiti

- Home Assistant >= 2024.1.
- Centrale Ksenia Lares 4.0 raggiungibile via rete locale.
- Un utente dedicato sulla centrale con PIN e i permessi necessari per
  eseguire gli scenari che si vogliono richiamare da Home Assistant.

## Installazione

### Tramite HACS (consigliato)

1. Apri HACS → Integrazioni → ⋮ → *Custom repositories*.
2. Aggiungi `https://github.com/cybercecco/hassio-ksenia-project` come
   tipo *Integration*.
3. Installa **Ksenia Lares 4.0 Gateway** e riavvia Home Assistant.

### Manuale

Copia la cartella `custom_components/ksenia_lares4` in
`<config>/custom_components/ksenia_lares4` e riavvia Home Assistant.

## Configurazione

Impostazioni → Dispositivi e servizi → *Aggiungi integrazione* → cerca
**Ksenia Lares 4.0 Gateway** e compila:

- **URL della centrale**: accetta `https://192.168.1.10`,
  `http://lares:80`, oppure solo `192.168.1.10` (in questo caso viene
  assunto HTTPS sulla porta 443).
- **Nome utente** (facoltativo): mostrato nel titolo dell'integrazione
  per identificare più facilmente la centrale.
- **PIN utente**: il PIN dell'utente Home Assistant configurato sulla
  centrale.

L'integrazione verifica la connessione durante il wizard: in caso di
errore mostra un messaggio specifico (PIN errato, rete non
raggiungibile, timeout…).

## Come funziona

1. All'avvio apre una WebSocket verso `wss://<host>/KseniaWsock`
   (`ws://` se SSL disattivato) con sub-protocollo `KS_WSOCK`.
2. Invia un `LOGIN` con PIN, riceve l'`ID_LOGIN` e si iscrive ai
   broadcast `REALTIME` per `STATUS_ZONES`, `STATUS_PARTITIONS` e
   `STATUS_SYSTEM`.
3. Fa un `READ` iniziale di `ZONES`, `PARTITIONS`, `SCENARIOS` e dei
   rispettivi stati per popolare le entità.
4. Un task *supervisor* mantiene la connessione aperta: se cade, tenta
   la riconnessione con backoff esponenziale (max 60s) finché non viene
   ripristinata o l'integrazione non viene rimossa.
5. I comandi di arm/disarm sono implementati come `CMD_USR` →
   `CMD_EXE_SCENARIO` richiamando lo scenario con la categoria
   appropriata; il PIN inserito nell'alarm panel viene sempre
   rimandato alla centrale per validazione.

## Struttura del progetto

```
custom_components/ksenia_lares4/
├── __init__.py            # entrypoint HA / setup/unload
├── api.py                 # WebSocket client + supervisor di riconnessione
├── crc.py                 # CRC-16 Ksenia
├── const.py               # enum e costanti di protocollo
├── gateway.py             # bridge fra client e Home Assistant
├── entity.py              # entità base + helper unique_id
├── alarm_control_panel.py # entità allarme
├── binary_sensor.py       # zone
├── sensor.py              # partizioni + stato centrale
├── button.py              # scenari
├── config_flow.py         # wizard di configurazione
├── manifest.json
├── strings.json
└── translations/
    ├── en.json
    └── it.json
```

## Sicurezza

- Il PIN è utilizzato esclusivamente per autenticare la sessione
  WebSocket e per validare gli scenari che richiedono conferma.
- La libreria è compatibile con i certificati TLS legacy usati dalle
  centrali Ksenia (`OP_LEGACY_SERVER_CONNECT` + `CERT_NONE`). Per
  ambienti in cui la centrale pubblica un certificato valido è
  possibile modificare facilmente il contesto SSL in
  `api.py::_connect_and_prime`.

## Disclaimer

Integrazione non ufficiale. "Ksenia" e "Lares" sono marchi di Ksenia
Security S.p.A. Questo progetto non è affiliato a Ksenia né certificato
dal produttore.

## Licenza

MIT.
