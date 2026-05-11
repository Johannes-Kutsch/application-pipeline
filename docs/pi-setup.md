# Pi 5 Setup Runbook

Brings a fresh Pi 5 8 GB to the point where the cron wrapper (`scripts/pi-tick.sh`) takes over.
Target completion time: under one hour for an attentive operator with no prior knowledge of this project.

Read `CONTEXT.md` for domain glossary and `docs/adr/` for architectural decisions referenced throughout.

---

## 1. Hardware + OS prerequisites

1. Confirm hardware: **Raspberry Pi 5, 8 GB RAM**. Smaller models lack RAM for `qwen3:8b` (see [ADR-0001](adr/0001-local-ollama-as-llm-backend.md)).

2. Flash **Raspberry Pi OS Lite, 64-bit, Bookworm (Debian 12)**. Use Raspberry Pi Imager ≥ 1.8.
   - Enable SSH in Imager's advanced options and set a username (e.g. `pi`).

3. Boot and confirm SSH access from your laptop:
   ```bash
   ssh pi@<pi-ip-address>
   ```
   Expected: shell prompt, no errors.

4. Confirm the Pi is reachable on your local network and can reach the internet:
   ```bash
   ping -c 3 github.com
   ```
   Expected: 3 replies, 0% packet loss.

---

## 2. System packages

5. Update package lists and install required system packages:
   ```bash
   sudo apt-get update
   sudo apt-get install -y git python3 python3-venv flock cron
   ```

6. Verify versions:
   ```bash
   git --version
   python3 --version
   flock --version
   systemctl is-active cron
   ```
   Expected: git ≥ 2.39, python3 ≥ 3.11, flock present, cron `active`.

---

## 3. Ollama installation

Ollama is the local LLM runtime required by the **Relevance Classifier** and **Match Judge** (see [ADR-0001](adr/0001-local-ollama-as-llm-backend.md)). The Pi never calls an external LLM API.

7. Install Ollama:
   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ```

8. Confirm the Ollama service is running:
   ```bash
   systemctl is-active ollama
   ollama --version
   ```
   Expected: `active`, version printed.

9. Pull the model (`qwen3:8b` — do not substitute Qwen 2.5 or another identifier):
   ```bash
   ollama pull qwen3:8b
   ```
   Expected: download progress, then `success`. This downloads ~5 GB; allow 10–20 minutes on a typical home connection.

10. Sanity-check model and RAM headroom:
    ```bash
    ollama run qwen3:8b "reply with the single word ok"
    ```
    Expected: model outputs `ok` (or similar short acknowledgement).

    ```bash
    ollama ps
    ```
    Expected: `qwen3:8b` listed with VRAM/RAM usage under ~6 GB, leaving headroom on the 8 GB Pi 5.

---

## 4. Syncthing installation + folder pairing

Syncthing carries the **Results File** (`current.md`), **Seen State** (`.seen.json`), and **Failure Reports** (`data/results/failures/`) from the Pi to your laptop (see [ADR-0010](adr/0010-pi-pulls-tags-state-via-syncthing.md) and [ADR-0012](adr/0012-failures-as-syncthing-files.md)).

11. Install Syncthing:
    ```bash
    sudo apt-get install -y syncthing
    ```

12. Enable and start Syncthing as a user service (not system-wide):
    ```bash
    systemctl --user enable syncthing
    systemctl --user start syncthing
    systemctl --user status syncthing
    ```
    Expected: `active (running)`.

13. On your **laptop**, open the Syncthing web UI (usually `http://localhost:8384`) and copy the laptop's **Device ID** (shown under Actions → Show ID).

14. On the **Pi**, open the Syncthing web UI via an SSH tunnel:
    ```bash
    # Run this on your laptop in a separate terminal:
    ssh -L 8385:localhost:8384 pi@<pi-ip-address>
    ```
    Then open `http://localhost:8385` in your browser.

15. On the Pi's Syncthing UI:
    - Click **Add Remote Device**.
    - Paste the laptop's Device ID.
    - Click **Save**.

16. Accept the Pi's device pairing request on the **laptop's** Syncthing UI when the notification appears.

17. On the Pi's Syncthing UI:
    - Click **Add Folder**.
    - **Folder Label**: `application-pipeline-results`
    - **Folder Path**: `/home/pi/application-pipeline/data/results`
    - **Folder Type**: Send & Receive
    - Under **Sharing**, tick the laptop device.
    - Click **Save**.

18. Accept the shared folder on the **laptop's** Syncthing UI, choosing a local path (e.g. `~/application-pipeline-results`).

19. Confirm pairing is complete:
    ```bash
    # On the Pi:
    syncthing cli show connections
    ```
    Expected: laptop device listed as `Connected`.

    Note: `.seen.json` and `current.md` will appear on the laptop after the first successful pipeline run (step 37).

---

## 5. Repo bootstrap

The Pi clones the public repo over HTTPS — no SSH key or token required (see [ADR-0010](adr/0010-pi-pulls-tags-state-via-syncthing.md)).

20. Create the top-level layout (see [ADR-0011](adr/0011-atomic-deploy-via-staging-symlink.md)):
    ```bash
    mkdir -p ~/application-pipeline/{releases,data/results/failures,logs}
    ```

21. Clone the repo into `repo/` (bootstrap copy — used only for `git fetch --tags` by the wrapper):
    ```bash
    git clone https://github.com/Johannes-Kutsch/application-pipeline.git \
        ~/application-pipeline/repo
    ```

22. Verify the directory layout:
    ```bash
    ls ~/application-pipeline/
    ```
    Expected:
    ```
    data/  logs/  releases/  repo/
    ```
    The `current` symlink does not exist yet — it is created in step 28.

---

## 6. Initial release

This manually performs the first deploy that the cron wrapper (`scripts/pi-tick.sh`) will automate on subsequent runs (see [ADR-0011](adr/0011-atomic-deploy-via-staging-symlink.md)).

23. Choose a release tag (replace `v1.0.0` with the actual latest tag):
    ```bash
    git -C ~/application-pipeline/repo fetch --tags
    git -C ~/application-pipeline/repo tag --sort=-version:refname | head -5
    ```
    Note the highest `v*` tag (e.g. `v1.0.0`). Use that tag in the steps below.

24. Clone the chosen tag into its release directory:
    ```bash
    TAG=v1.0.0   # replace with actual tag
    git clone --branch "$TAG" --depth 1 \
        https://github.com/Johannes-Kutsch/application-pipeline.git \
        ~/application-pipeline/releases/"$TAG"
    ```

25. Create the virtual environment and install the **Pipeline Orchestrator** and its dependencies:
    ```bash
    python3 -m venv ~/application-pipeline/releases/"$TAG"/.venv
    ~/application-pipeline/releases/"$TAG"/.venv/bin/pip install -e \
        ~/application-pipeline/releases/"$TAG"
    ```

26. Smoke-test the installation:
    ```bash
    ~/application-pipeline/releases/"$TAG"/.venv/bin/python \
        -c "import application_pipeline; print('ok')"
    ```
    Expected: `ok` printed, no import errors.

27. Confirm the **Pipeline Orchestrator** entry point is reachable:
    ```bash
    ~/application-pipeline/releases/"$TAG"/.venv/bin/python \
        -m application_pipeline --help
    ```
    Expected: help text or usage message, no traceback.

28. Flip the `current` symlink:
    ```bash
    ln -sfn ~/application-pipeline/releases/"$TAG" \
        ~/application-pipeline/current
    readlink ~/application-pipeline/current
    ```
    Expected: path ends with `releases/v1.0.0` (or chosen tag).

---

## 7. Crontab install

The cron wrapper runs the **Pipeline Orchestrator** four times daily via `flock` to enforce the single-writer invariant (see [ADR-0010](adr/0010-pi-pulls-tags-state-via-syncthing.md)).

29. Copy `crontab.example` from the repo:
    ```bash
    cp ~/application-pipeline/repo/crontab.example ~/crontab.tmp
    ```

30. Replace the `<user>` placeholder with your actual Pi username:
    ```bash
    sed -i "s|<user>|$(whoami)|g" ~/crontab.tmp
    cat ~/crontab.tmp
    ```
    Verify: the path `/home/pi/application-pipeline/...` (or your username) appears, no `<user>` placeholder remains.

31. Install the crontab:
    ```bash
    crontab ~/crontab.tmp
    crontab -l
    ```
    Expected: one line scheduling `pi-tick.sh` at 8, 12, 16, and 20 UTC daily.

32. Remove the temporary file:
    ```bash
    rm ~/crontab.tmp
    ```

---

## 8. Verification

33. Trigger one manual run of the cron wrapper:
    ```bash
    ~/application-pipeline/scripts/pi-tick.sh
    ```
    Watch for: fetch → identify tag → (skip deploy if already current) → pipeline run → exit 0.

34. Confirm the **Results File** was written:
    ```bash
    ls -lh ~/application-pipeline/data/results/current.md
    grep -c "---" ~/application-pipeline/data/results/current.md
    ```
    Expected: file exists, at least one **Run Divider** (`---`) present.

35. Confirm the **Seen State** file was written:
    ```bash
    ls -lh ~/application-pipeline/data/results/.seen.json
    ```
    Expected: file exists, non-zero size.

36. Confirm no **Failure Reports** were created:
    ```bash
    ls ~/application-pipeline/data/results/failures/
    ```
    Expected: empty directory (no `*.md` files).

37. Confirm Syncthing sync to laptop:
    - Within Syncthing's normal sync window (typically under 30 seconds on a local network), open the laptop's paired folder.
    - Expected: `current.md` present with at least one **Run Divider**.
    - Expected: `.seen.json` present alongside `current.md`.
    - Expected: `failures/` directory present and empty.

38. Review the cron log for any warnings:
    ```bash
    tail -40 ~/application-pipeline/logs/cron.log
    ```
    Expected: timestamped log lines, no `ERROR` or `FAILURE` entries.

---

## Disaster recovery: restoring `.seen.json` after Pi disk failure

The **Seen State** file (`.seen.json`) tracks which **Position** URLs have already been shown in the **Results File**. Losing it causes the next pipeline run to treat all previously-seen positions as new (a one-time flood of duplicates). Because `.seen.json` is continuously synced to the laptop via Syncthing (see [ADR-0002](adr/0002-seen-state-durable-via-syncthing.md) and [ADR-0010](adr/0010-pi-pulls-tags-state-via-syncthing.md)), the recovery procedure is:

39. After re-imaging the Pi and completing steps 1–32 above, copy `.seen.json` from the laptop's Syncthing folder back to the Pi **before** the first `pi-tick.sh` run:
    ```bash
    # Run on the Pi, substituting the laptop's IP and your Syncthing folder path:
    scp <laptop-user>@<laptop-ip>:<syncthing-folder>/.seen.json \
        ~/application-pipeline/data/results/.seen.json
    ```
    Or restore via the Syncthing UI: on the Pi, pause and then resume the shared folder — Syncthing will pull the laptop's copy.

40. Verify the restored file is non-empty before triggering a run:
    ```bash
    wc -c ~/application-pipeline/data/results/.seen.json
    ```
    Expected: size > 0 bytes.

41. Trigger a manual run (step 33) and confirm no duplicate flood in `current.md`.
