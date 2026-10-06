# Bench Log

Repair tracking for a one-person electronics bench. It logs every device that
comes in, what was tested, what was replaced, how long it took, and what it
earned, and it keeps track of donor boards and the parts taken from them.

It runs as a small web app on a Raspberry Pi or any Linux machine. You use it
from a desktop browser or a phone on the same network. Every device and box
gets a QR label that links straight to its page.

![Bench Log feature overview](docs/features.png)

## What it does

**Devices and testing**
- A unique ID per device, with status: Untested, Needs repair, Repaired, Parts, Sold
- A test checklist for each device type, saved tap by tap
- The as-received result is frozen at intake, so later changes show what was fixed
- Serial numbers, notes, and photos per device

**Repairs and parts**
- A repair log per device: which part, where it came from, what it cost
- A parts catalog with common failure parts for each device type
- Reverse search by part name, part number, or symptom ("drift", "no charge", "M92T36")
- Part names link to a web search, Google Shopping, and eBay buy-it-now
- A saved supplier link per part
- Optional live price check through the eBay Browse API, with the result shown as a suggestion you confirm

**Donor boards**
- A device that cannot be saved becomes a donor with one click
- Each donor lists which parts are good, suspect, or already taken
- Taking a part records which repair it went to, from either side
- Parts on a donor that match what is failing on the current repair are suggested first

**Boxes and lots**
- A box label is printed once and always shows the live count by status
- A lot splits one purchase price across its devices, so each unit has a true cost

**Time and money**
- Start, pause, and resume a bench timer per device. Only one runs at a time
- Sessions can be corrected, and long ones are flagged in case the timer was left on
- Profit per device, what you earned per hour, and profit after paying yourself a target wage
- Per-lot results that count time spent on failures
- Average bench time by repair type, for pricing customer work

**Labels**
- Label images with a QR code, ID, device type, and status letter
- The QR code holds only a link, so a label never goes out of date
- Sized for 62mm Brother QL tape. Direct printing is planned, see Roadmap

**System page**
- Device health: storage, memory, swap, CPU load and temperature, GPU load where the hardware reports it, uptime, and Raspberry Pi power and throttling warnings
- Automatic daily database backups, plus one before every update, restore, and reboot
- One-click restore of any backup, with the previous state saved first
- Check for and install operating system updates, with the package list shown before anything is installed
- Check for and apply Bench Log updates from the git repository, with a database backup first and a button to return to the previous version
- Restart the app or reboot the device
- All of these are locked behind an admin password

**Export**
- Devices, repairs, and time sessions as CSV
- The database, or the database with all photos as one zip

## Supported devices out of the box

The catalog is seeded with checklists and parts for:

- DualSense, DualShock 4
- Xbox Series and Xbox One controllers
- Joy-Con (L/R), Joy-Con 2 (L/R), Switch Pro Controller
- Nintendo Switch (original), Lite, OLED, and Switch 2
- Steam Deck LCD and OLED
- PlayStation 5 and PlayStation 4 consoles

More device types, checklist items, and parts can be added in the app. A new
type can start as a copy of an existing one.

Part costs in the catalog are rough placeholders. Set them to what you really
pay. Part numbers are only filled in where they are well known, so check the
marking on the board before ordering a chip.

## Status letters

| Letter | Status | Meaning |
| --- | --- | --- |
| U | Untested | Just arrived |
| N | Needs repair | Tested, something failed |
| R | Repaired | Works and is ready to sell |
| P | Parts | Donor |
| S | Sold | Gone |

## Install

These steps are for Raspberry Pi OS. Any Debian or Ubuntu system works the
same way.

### 1. Give the Pi read access to this repository

The repository is private, so the Pi needs its own read-only key.

    ssh-keygen -t ed25519 -f ~/.ssh/benchlog_deploy -N ""
    cat ~/.ssh/benchlog_deploy.pub

On GitHub, open the repository, then Settings, Deploy keys, Add deploy key.
Paste the line that was printed and leave "Allow write access" off.

Then tell SSH to use that key for this repository:

    cat >> ~/.ssh/config <<'EOT'
    Host github.com-benchlog
      HostName github.com
      User git
      IdentityFile ~/.ssh/benchlog_deploy
      IdentitiesOnly yes
    EOT

### 2. Install and run

    sudo apt install git python3-venv fonts-dejavu-core
    git clone git@github.com-benchlog:Daggenthal/BenchLog.git ~/benchlog
    cd ~/benchlog
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/python app.py

Open `http://<name-of-your-pi>.local:8080` from any device on the same
network. `hostname` on the Pi prints its name.

### 3. Start at boot

    mkdir -p ~/.config/systemd/user
    cp ~/benchlog/benchlog.service ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now benchlog
    sudo loginctl enable-linger $USER

## First steps

1. System: set an admin password.
2. Settings: set your target hourly rate, and the address to print into QR codes.
3. Boxes: create a box, for example "PS5 controllers".
4. Lots: create a lot with what you paid and how many units, then add its devices.
5. Open a device, run the checklist, and tap "Finish intake".
6. Start the timer, log the parts you replace, and mark it repaired.
7. Enter the sale to see profit and what you earned per hour.

To try scanning without a printer, open a device on a desktop and point a
phone camera at the QR code in its Label section.

## Before printing real labels

The QR code contains the address of the app. In Settings, set "Address printed
into QR codes" to a name that will not change, such as
`http://raspberrypi.local:8080`. If the address changes later, labels that
were already printed stop working.

## Updating

Open System, unlock it, and press "Check for updates" under Bench Log updates.
The changes are listed first. "Back up and update" then:

1. Saves a copy of the database.
2. Applies the new code. Your `data` folder is never touched.
3. Installs any new requirements.
4. Restarts the app.

If a step fails, the code is put back as it was. After an update, "Go back to
the previous version" returns to the version you had before.

To update by hand instead:

    cd ~/benchlog
    git pull
    .venv/bin/pip install -r requirements.txt
    systemctl --user restart benchlog

## Your data

Everything you enter lives in the `data` folder, which git ignores:

- `benchlog.db`: the database
- `photos/`: device photos
- `backups/`: automatic and manual database backups
- `secrets.json`: the admin password hash and eBay keys, readable only by your user
- `logs/`: output of update tasks

The last 14 daily backups are kept. They sit on the same disk as the app, so
download a copy from the System page now and then. Remote backup to another
server is planned.

## Live part prices

This is optional. With a free eBay developer keyset, each part page can show
what the part is selling for right now.

1. Create an account at developer.ebay.com and make a production keyset.
2. In Settings, paste the App ID and Cert ID and pick your marketplace.
3. On any part page, press "Check price on eBay".

The result is a list of fixed-price listings with the lowest, median, and
highest total. Nothing changes until you press "Use". Read the titles first:
cheap results for small parts are often the wrong item or a bulk lot.

## Security

- There is no login for everyday pages. Keep the app on your home network and
  do not forward its port to the internet.
- Reboot, updates, and backups need the admin password. It is stored as a
  salted hash.
- If the system asks for a password for administrator commands, the System
  page asks for it each time, passes it to `sudo` directly, and does not store
  or log it.
- The page is served over plain HTTP, so passwords typed into it can be read
  by other devices on the same network. On a network you do not fully trust,
  do system tasks over SSH instead.

## Roadmap

- Direct printing to a Brother QL label printer
- Customer repair tickets with IDs, shipping details, and an express queue
- Stock counts for purchased parts
- Remote backup to another server
- Customer email notifications

## Development

    .venv/bin/pip install pytest
    .venv/bin/python -m pytest tests -q

| File | Purpose |
| --- | --- |
| `app.py` | Pages and logic |
| `db.py` | Database tables and migrations |
| `catalog.py` | Starting device types, checklists, and parts |
| `labels.py` | Label images |
| `pricing.py` | Part search links and the eBay price check |
| `system.py` | Health, backups, updates, and background tasks |
| `templates/`, `static/` | The pages |
| `tests/` | Automated tests |

`catalog.py` only seeds a new database. After the first run, edit the catalog
in the app.

Set `BENCHLOG_PORT` to change the port and `BENCHLOG_DATA` to move the data
folder.

## License

MIT. See [LICENSE](LICENSE).
