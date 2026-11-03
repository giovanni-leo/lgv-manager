# VC Manager

Gestione del pipeline e del portafoglio di un investitore VC (medtech / healthcare).
Django 5.2 LTS + PostgreSQL 16 + HTMX, distribuito con Docker Compose (app, Postgres, Caddy con HTTPS).

Stato: **fase 1, fondamenta** (modelli, migrazioni, funzioni SQL di proprietà, admin, 2FA, Docker, test).

- **Interfaccia in inglese**, con date in formato europeo (3 Nov 2026, ore 24). Le traduzioni italiane
  restano in `locale/` e si possono riattivare aggiungendo la lingua a `LANGUAGES`.
- **Interfaccia essenziale**: ogni scheda mostra solo i campi principali; il resto è in *More details*,
  chiuso finché non lo apri. Le tabelle collegate alla società partono chiuse.
- **Più utenti** con tre ruoli: `viewer` (sola lettura), `editor` (modifica i dati), `admin` (gestisce
  anche gli utenti). 2FA obbligatoria per tutti. Vedi `python manage.py adduser --help`.

Messa in produzione: vedi **[DEPLOY.md](DEPLOY.md)**.

## Avvio con Docker (locale o server)

```sh
cp .env.example .env          # imposta DJANGO_SECRET_KEY e POSTGRES_PASSWORD
docker compose up -d --build
docker compose exec app python manage.py createsuperuser
```

Apri https://localhost. Il certificato locale è firmato dalla CA interna di Caddy. Al primo accesso
l'app chiede di configurare il secondo fattore (TOTP: Google Authenticator, 1Password, ecc.) e
genera i codici di backup. Per il server basta impostare `DOMAIN`, `DJANGO_ALLOWED_HOSTS` e
`DJANGO_CSRF_TRUSTED_ORIGINS` sul dominio pubblico: Caddy ottiene il certificato Let's Encrypt.

SQL libero: `docker compose exec db psql -U vc vc`.

## Sviluppo senza Docker

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export DJANGO_DEBUG=1 POSTGRES_HOST=localhost   # Postgres 16 con utente/db "vc"
python manage.py migrate
python manage.py compilemessages --ignore=.venv
python manage.py runserver
```

Test (servono PostgreSQL e un utente con permesso CREATEDB):

```sh
DJANGO_SETTINGS_MODULE=config.settings_test python manage.py test core
```

## Struttura

| Percorso | Contenuto |
|---|---|
| `core/models/` | modelli, uno per area: `parties` (entità, società, contatti, cambi), `pipeline`, `captable`, `monitoring`, `documents` |
| `core/sql/ownership.sql` | funzioni SQL datate e viste `v_*` dello schema di riferimento |
| `core/db_constraints.py` | ON DELETE a livello di database e FK composite classe↔società |
| `core/services/ownership.py` | accesso Python alle funzioni SQL |
| `core/middleware.py` | 2FA obbligatoria su ogni pagina |
| `locale/` | traduzioni (testi sorgente in inglese; italiano completo ma non attivo) |
| `core/roles.py`, `core/admin_users.py` | ruoli e gestione utenti |
| `docker/` | Caddyfile, entrypoint |

## Calcolo delle quote (SQL)

Tutte le funzioni accettano una data (predefinita: oggi) e, dove serve, un'entità investitrice (`NULL` = tutte).

```sql
SELECT * FROM fn_class_outstanding('2025-12-31');          -- azioni esistenti per classe
SELECT * FROM fn_my_shares('2025-12-31');                  -- mie azioni per società, entità, classe
SELECT * FROM fn_ownership_by_class('2025-12-31', NULL);   -- % per classe e controlli di coerenza
SELECT * FROM fn_ownership_company('2025-12-31', 2);       -- % non diluita e fully diluted, solo entità 2
SELECT * FROM v_ownership_company;                         -- viste dello schema, a oggi
```

Convenzioni:

- **Frazionamento**: un evento `split` con `split_ratio` (2 = 2 per 1, 0,1 = raggruppamento 1 ogni 10)
  moltiplica tutte le posizioni della classe datate *prima* del frazionamento, sia il totale della classe
  sia le mie azioni. Eventi e acquisti con la stessa data del frazionamento sono considerati già post-split.
  Il `shares_delta` di uno split viene compilato in automatico ma è solo informativo.
- **Fully diluted** = azioni as-converted + ultimo pool di opzioni alla data (assegnate + non assegnate,
  non ancora esercitate) + warrant in essere. I miei warrant (investimenti di tipo `warrant` con numero
  di azioni) entrano anche al numeratore.
- **Controlli**: `inconsistent` (mie azioni > azioni esistenti), `missing_issuance` (ho azioni di una classe
  senza eventi di emissione), `negative_position` (cedute più azioni di quelle detenute). Sono calcolati
  sempre su tutte le entità insieme.

Dopo una migrazione che modifica una foreign key, rieseguire `python manage.py sync_db_constraints`.
