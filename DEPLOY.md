# Deployment passo per passo

La stessa configurazione Docker Compose gira in locale e sul server: tre container (app Django,
PostgreSQL 16, Caddy per HTTPS). Sul server servono solo Docker, un nome di dominio e il file `.env`.

## 1. Che server serve

| Voce | Minimo consigliato |
|---|---|
| CPU / RAM | 2 vCPU, 4 GB RAM (bastano per una decina di utenti) |
| Disco | 80 GB SSD, ampliabile: i documenti arrivano fino a 100 MB l'uno |
| Sistema | Ubuntu Server 24.04 LTS |
| Rete | IPv4 pubblico fisso, porte 80 e 443 aperte |
| Dove | Data center in Svizzera (Ginevra o Zurigo) |
| Altro | Un sottodominio, per esempio `vc.tuodominio.ch`, e un secondo spazio per i backup in un'altra sede |

### Opzioni

- **Infomaniak** (società svizzera, data center a Ginevra): VPS Cloud con Ubuntu, più Swiss Backup
  per i backup.
- **Exoscale** (zone Ginevra `CH-GVA-2` e Zurigo `CH-DK-2`): Compute instance, più Object Storage
  (compatibile S3) nell'altra zona per i backup.
- **Microsoft Azure** (regione *Switzerland North*, Zurigo): una macchina virtuale Linux con la
  stessa configurazione. Vedi la sezione 9.

## 2. Creare il server

1. Dal pannello del fornitore crea il server: Ubuntu 24.04, 2 vCPU / 4 GB, 80 GB, zona svizzera.
2. Accesso con **chiave SSH**, non con password. Se non hai una chiave, generala sul tuo computer
   con `ssh-keygen -t ed25519` e incolla nel pannello il contenuto di `~/.ssh/id_ed25519.pub`.
3. Nel firewall del fornitore (security group) apri solo queste porte:
   - 22/tcp (SSH), possibilmente solo dal tuo IP;
   - 80/tcp e 443/tcp, più 443/udp (HTTP/3).
4. Prendi nota dell'indirizzo IP pubblico.

## 3. Nome di dominio

Nel DNS del tuo dominio aggiungi un record `A`, ad esempio `vc` → indirizzo IP del server, più un record `AAAA` se il server ha IPv6.
Verifica dal tuo computer con `nslookup vc.tuodominio.ch`.

## 4. Preparare il server (una volta)

```sh
ssh ubuntu@IP_DEL_SERVER          # utente indicato dal fornitore (ubuntu, root, ...)

sudo apt update && sudo apt -y upgrade
sudo apt -y install unattended-upgrades git
sudo dpkg-reconfigure -plow unattended-upgrades     # aggiornamenti di sicurezza automatici

# Docker (repository ufficiale)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
exit                                               # esci e rientra per attivare il gruppo docker
```

Facoltativo ma consigliato: disattiva il login SSH con password
(`PasswordAuthentication no` in `/etc/ssh/sshd_config`, poi `sudo systemctl restart ssh`).

## 5. Installare l'applicazione

Il codice sta in un repository GitHub **privato** (per esempio `giovanni-leo/lgv-manager`). Il server
lo scarica con una *deploy key*: una chiave che dà accesso in sola lettura a quel solo repository.

```sh
ssh ubuntu@IP_DEL_SERVER
ssh-keygen -t ed25519 -f ~/.ssh/github_deploy -N ""
cat ~/.ssh/github_deploy.pub
```

Su GitHub: repository → *Settings* → *Deploy keys* → *Add deploy key*. Incolla la chiave e lascia
**disattivato** "Allow write access". Poi sul server:

```sh
printf 'Host github.com\n  IdentityFile ~/.ssh/github_deploy\n' >> ~/.ssh/config
git clone git@github.com:giovanni-leo/lgv-manager.git vc-manager
cd vc-manager
cp .env.example .env
nano .env
```

Valori da impostare in `.env`:

```ini
DOMAIN=vc.tuodominio.ch
SITE_NAME=Lake Growth Ventures
DJANGO_ALLOWED_HOSTS=vc.tuodominio.ch
DJANGO_CSRF_TRUSTED_ORIGINS=https://vc.tuodominio.ch
DJANGO_SECRET_KEY=...        # genera con: openssl rand -base64 48
POSTGRES_PASSWORD=...        # genera con: openssl rand -base64 24
```

Poi:

```sh
docker compose up -d --build
docker compose ps                     # i tre servizi devono risultare "Up"/"healthy"
docker compose logs -f proxy          # al primo avvio Caddy ottiene il certificato HTTPS
```

## 6. Primo accesso e utenti

```sh
docker compose exec app python manage.py createsuperuser        # il tuo utente (admin)
```

1. Apri `https://vc.tuodominio.ch` e accedi.
2. L'app chiede subito di configurare la **verifica in due passaggi**: inquadra il QR code con
   un'app di autenticazione (Microsoft Authenticator, Google Authenticator, 1Password...).
3. Genera e conserva i **codici di backup** (menu *Security*).
4. In *Investing entities* crea il fondo (per esempio "Lake Growth Ventures", tipo *Fund*). Finché
   c'è una sola entità, l'app la usa da sola e non la chiede negli investimenti.

Altri utenti:

```sh
docker compose exec app python manage.py adduser anna --email anna@tuodominio.ch --role viewer
docker compose exec app python manage.py adduser marc --email marc@tuodominio.ch --role editor
```

Il comando stampa una password monouso da comunicare in modo riservato. Al primo accesso ogni
utente configura la propria 2FA. Ruoli:

- `viewer`: sola lettura;
- `editor`: inserisce e modifica i dati;
- `admin`: come editor, e in più gestisce gli utenti.

Ruoli e azzeramento della 2FA (per esempio dopo la perdita del telefono) si gestiscono anche da
*Users* nell'interfaccia.

## Da iPad, senza computer

Su iPad Docker non gira, ma tutto il deployment si fa dall'iPad stesso.

1. Installa **Termius** dall'App Store: fa da terminale SSH e da trasferimento file (SFTP).
2. In Termius: *Keychain* → *+* → *Generate Key* (tipo ED25519). Copia la chiave pubblica.
3. Da Safari crea il server (punto 2), incollando la chiave pubblica di Termius, e il record DNS
   (punto 3).
4. Salva `lgv-manager.zip` in *File* sull'iPad.
5. In Termius aggiungi il server (*Hosts* → *+*: indirizzo IP, utente, chiave). Apri *SFTP*,
   collegati al server e carica lo zip nella cartella home.
6. Apri il terminale sul server e lancia, sostituendo il dominio:

```sh
sudo apt update && sudo apt -y install unzip
unzip lgv-manager.zip && cd lgv-manager
sh docker/install.sh vc.tuodominio.ch "Lake Growth Ventures"
sudo docker compose exec app python manage.py createsuperuser
```

`install.sh` installa Docker, crea il file `.env` con chiavi e password casuali e avvia l'app.
Puoi rilanciarlo senza rischi: un `.env` già esistente resta com'è, e i dati non vengono toccati.
7. Apri `https://vc.tuodominio.ch` in Safari e configura la 2FA (punto 6).

Per aggiornare l'app: carica il nuovo zip con SFTP, poi
`unzip -o lgv-manager.zip && cd lgv-manager && sh docker/install.sh vc.tuodominio.ch`.

## Claude: ricerca web e lettura delle email

Le due funzioni usano l'API di Anthropic e un contenitore separato, il `worker`, che parte da solo
con `docker compose up`.

1. **Chiave API.** Crea un account su **console.anthropic.com**, aggiungi un metodo di pagamento e un
   limite di spesa mensile (*Settings → Limits*), poi crea una chiave (*API keys*). Mettila nel `.env`:
   `ANTHROPIC_API_KEY=sk-ant-...`, e riavvia con `docker compose up -d`.
   Costo indicativo: una ricerca web va da qualche decina di centesimi a qualche franco, a seconda di
   quante pagine Claude consulta; la lettura di un'email con un pitch deck in genere meno. Il costo approssimativo di ogni lavoro è visibile in *Data → Claude jobs*.
2. **Casella email (facoltativa).** Crea una casella dedicata, per esempio `deals@tuodominio.ch`,
   presso un fornitore che offre IMAP con password (Infomaniak sì; Microsoft 365 richiede OAuth e va
   configurato a parte). Nel `.env`:
   ```ini
   MAIL_IMAP_HOST=mail.infomaniak.com
   MAIL_IMAP_USER=deals@tuodominio.ch
   MAIL_IMAP_PASSWORD=...
   MAIL_ALLOWED_SENDERS=@lakegrowth.ch,collega@altrodominio.ch
   ```
   Solo i mittenti elencati vengono analizzati; gli altri restano nell'Inbox come "Sender not allowed".
   Senza casella si possono comunque caricare file `.eml` dalla pagina *Inbox*.
3. **Riservatezza.** Testo delle email e allegati PDF vengono inviati all'API di Anthropic per
   l'analisi. Verifica che sia compatibile con gli accordi di riservatezza e con la nLPD; con
   `AI_READ_ATTACHMENTS=0` Claude legge solo il testo dell'email e gli allegati restano sul server.

## 7. Aggiornare l'applicazione

```sh
cd ~/vc-manager
git pull
docker compose up -d --build          # le migrazioni del database partono da sole
```

## 8. Backup (da completare nella fase 5)

I backup notturni cifrati verso un'altra sede arrivano con la fase 5. Fino ad allora:

- attiva gli **snapshot automatici** del server dal pannello del fornitore;
- prima di caricare dati importanti, esegui ogni tanto un dump manuale:

```sh
docker compose exec -T db pg_dump -U vc -Fc vc > vc-$(date +%F).dump
```

## 9. Variante Microsoft Azure

Un abbonamento Microsoft 365 da solo non basta: serve un **abbonamento Azure**, che si può creare
con lo stesso account aziendale ed è fatturato a parte, a consumo.

1. Portale Azure → *Virtual machines* → *Create*:
   - Region **Switzerland North**;
   - Image **Ubuntu Server 24.04 LTS**;
   - Size **B2s** (2 vCPU, 4 GB) o **B2ms** (2 vCPU, 8 GB);
   - Authentication: *SSH public key*;
   - Disk: Standard SSD da 64–128 GB.
2. *Networking*:
   - IP pubblico **statico**;
   - nel *Network security group* consenti 22 (solo dal tuo IP), 80 e 443.
3. Per i backup attiva **Azure Backup** sulla VM. In alternativa, nella fase 5 si può puntare a uno
   *Storage account* in Switzerland West.
4. Da qui in poi segui le sezioni 3–7: sono identiche.

Sul piano legale i dati restano fisicamente in Svizzera, ma Microsoft è una società statunitense
soggetta al CLOUD Act. La nLPD lo consente, ma se l'obiettivo è tenere i dati sotto sola
giurisdizione svizzera, Infomaniak o Exoscale sono più coerenti.
