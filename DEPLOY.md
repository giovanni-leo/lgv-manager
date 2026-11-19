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
2. **Casella email (facoltativa).** Serve una casella dedicata con accesso IMAP e password, per
   esempio un account Gmail (con la verifica in due passaggi attiva e una *password per le app*,
   da myaccount.google.com/apppasswords) o una casella Infomaniak. Microsoft 365 richiede OAuth e
   va configurato a parte. Nel `.env`:
   ```ini
   MAIL_IMAP_HOST=imap.gmail.com
   MAIL_IMAP_USER=lakegrowthventuresmanager@gmail.com
   MAIL_IMAP_PASSWORD=abcdefghijklmnop
   MAIL_ALLOWED_SENDERS=@tuodominio.ch,collega@altrodominio.ch
   ```
   Poi `docker compose up -d` e la prova: `docker compose exec app python manage.py check_mailbox`.
   Vengono analizzate solo le email dei mittenti elencati **e** che il server di posta conferma
   autentiche (DMARC/DKIM/SPF, così un mittente falsificato viene scartato); le altre restano
   nell'Inbox senza analisi. Per Gmail il controllo è automatico; per altri fornitori indicare il
   server in `MAIL_AUTH_SERVER`. Il worker legge la casella ogni 2 minuti e segna le email come lette.
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

## 8. Backup

Ogni notte (alle 02:30, ora svizzera) il container `backup` salva database, documenti e `.env`,
li **cifra sul server** e li copia su Google Drive, nella cartella `LGV-Backups`. Se qualcosa va
storto parte un'email a `BACKUP_ALERT_EMAIL`, usando la casella Gmail dell'app.

- **Cifratura:** con [age](https://age-encryption.org). Il server conserva solo la chiave
  *pubblica*, che serve a cifrare. La chiave *segreta* (`AGE-SECRET-KEY-…`), l'unica che può
  leggere i backup, viene mostrata una sola volta durante l'installazione e va custodita in un
  password manager. Se la si perde, i backup non sono più leggibili.
- **Conservazione:** gli ultimi 7 giorni, poi una copia a settimana per 4 settimane e una al mese
  per 6 mesi. Ogni documento viene caricato una volta sola e conservato.
- **Accesso a Google Drive:** rclone, con il solo permesso sui file che crea lui (`drive.file`).
  Non vede il resto del Drive né la posta.

### Installazione (una volta)

Dal PC, apri la sessione SSH con un tunnel, che serve per la pagina di accesso Google:

```sh
ssh -L 53682:127.0.0.1:53682 umbrel@192.168.1.174
cd ~/lgv-manager && sh docker/backup-setup.sh
```

Lo script:
1. costruisce il container;
2. crea la coppia di chiavi e ti mostra la chiave segreta, da salvare (scrivi `SAVED` per
   continuare);
3. mostra un link `http://127.0.0.1:53682/auth…`: aprilo nel browser del PC, accedi con l'account
   Google dei backup e clicca *Consenti*;
4. chiede l'indirizzo a cui mandare gli avvisi;
5. controlla tutto, manda un'email di prova e fa subito il primo backup.

### Comandi utili (sul server, in `~/lgv-manager`)

```sh
sudo docker compose exec backup lgv-backup check     # stato, Google Drive, email di prova
sudo docker compose exec backup lgv-backup list      # backup disponibili
sudo docker compose exec backup lgv-backup run       # un backup adesso
sudo docker compose logs --tail 50 backup            # cosa è successo
```

### Ripristino

```sh
sudo docker compose stop app worker
sudo docker compose exec backup lgv-backup restore            # l'ultimo; oppure: restore 2026-11-19
sudo docker compose start app worker
```

Chiede di scrivere `RESTORE` e di incollare la chiave segreta, che non viene salvata. Il database
viene **sostituito** con quello del backup e i documenti mancanti vengono riscritti. Il `.env` di
quel giorno finisce in `data/backup/` per confrontarlo con quello attuale.

Su un server nuovo: installa l'app (sezione 5), metti nel `.env` la riga `BACKUP_AGE_RECIPIENT=`
con la chiave pubblica di sempre (si ricava dalla chiave segreta: `echo AGE-SECRET-KEY-… | age-keygen -y`),
lancia `sh docker/backup-setup.sh` e poi il ripristino. Finché il database è vuoto il backup non
parte, così non può sostituire quelli buoni.

Una volta ogni tanto conviene provare il ripristino su un computer di prova, per essere sicuri che
la chiave salvata sia quella giusta.

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
