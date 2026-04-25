# Deployment Guide: TaemDee (Cloudflare + Nginx + systemd)

This guide provides step-by-step instructions for deploying the TaemDee application on a VPS using Cloudflare for SSL/TLS, Nginx as a reverse proxy, and systemd to manage the application process.

## Architecture
`Cloudflare (Edge Cert)` -> `VPS (Nginx)` -> `FastAPI (systemd)`

---

## 1. Cloudflare Configuration

1.  **DNS**: 
    *   Add an `A` record pointing to your VPS IP address.
    *   Set **Proxy status** to **Proxied** (Orange cloud).
2.  **SSL/TLS Settings**:
    *   Set encryption mode to **Full (Strict)**.
3.  **Origin CA Certificate**:
    *   Go to **SSL/TLS** > **Origin Server** > **Create Certificate**.
    *   Generate a private key and CSR with Cloudflare.
    *   Hostnames: `taemdee.com`, `www.taemdee.com`.
    *   Click **Create**.
    *   **Action**: Save the **Origin Certificate** as `taemdee.origin.pem` and the **Private Key** as `taemdee.origin.key`. You will need these for Nginx.

---

## 2. VPS Preparation

Login to your VPS and install dependencies:

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Nginx and Git
sudo apt install -y nginx git curl

# Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.cargo/env
```

---

## 3. Application Setup

1.  **Create application directory**:
    ```bash
    # Choose your deployment path (e.g., /home/pace6/taemdee-prod)
    mkdir -p /path/to/your/app
    cd /path/to/your/app
    ```

2.  **Clone the repository**:
    ```bash
    git clone <your-repo-url> .
    ```

3.  **Setup environment**:
    ```bash
    cp .env.example .env
    # Edit .env with your production database URL and secrets
    nano .env
    ```

4.  **Install dependencies and run migrations**:
    ```bash
    uv sync
    uv run alembic upgrade head
    ```

---

## 4. Systemd Service Setup

Create the service file at `/etc/systemd/system/taemdee.service`:

```bash
sudo nano /etc/systemd/system/taemdee.service
```

Paste the following configuration (Note: Port 91000 is used as requested, but ensure your firewall allows it or consider 9100):

```ini
[Unit]
Description=taemdee
After=network.target

[Service]
Type=notify
User=pace6
Group=pace6
WorkingDirectory=/path/to/your/app
EnvironmentFile=/path/to/your/app/.env
ExecStart=/home/pace6/.local/bin/uv run gunicorn app.main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 127.0.0.1:9100 \
  --timeout 30 \
  --graceful-timeout 30
ExecReload=/bin/kill -HUP $MAINPID
KillMode=mixed
Restart=always
RestartSec=3
Environment=ENV=prod
Environment=APP_NAME=taemdee

[Install]
WantedBy=multi-user.target
```

**Start the service**:
```bash
sudo systemctl daemon-reload
sudo systemctl enable taemdee
sudo systemctl start taemdee

# Check status
sudo systemctl status taemdee
```

### Passwordless Sudo for Management
To allow the `pace6` user to restart the service without a password (useful for deployment scripts), create a sudoers file:

```bash
sudo nano /etc/sudoers.d/pace6
```

Add the following line:
```text
pace6 ALL=(root) NOPASSWD: /usr/bin/systemctl reload taemdee, /usr/bin/systemctl start taemdee, /usr/bin/systemctl stop taemdee, /usr/bin/systemctl status taemdee
```

---

## 5. Nginx Configuration

1.  **Install Cloudflare Certificates**:
    Upload the files you saved in Step 1 to your VPS:
    *   Certificate: `/etc/ssl/certs/taemdee.origin.pem`
    *   Private Key: `/etc/ssl/private/taemdee.origin.key`

    ```bash
    sudo chmod 644 /etc/ssl/certs/taemdee.origin.pem
    sudo chmod 600 /etc/ssl/private/taemdee.origin.key
    ```

2.  **Create Nginx site configuration**:
    ```bash
    sudo nano /etc/nginx/sites-available/taemdee
    ```

    ```nginx
    server {
        listen 80;
        server_name taemdee.com www.taemdee.com;
        return 301 https://$host$request_uri;
    }

    server {
        listen 443 ssl http2;
        server_name taemdee.com www.taemdee.com;

        ssl_certificate /etc/ssl/certs/taemdee.origin.pem;
        ssl_certificate_key /etc/ssl/private/taemdee.origin.key;

        location /static/ {
            alias /path/to/your/app/static/;
            expires 30d;
        }

        location / {
            proxy_pass http://127.0.0.1:9100;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
    }
    ```

3.  **Enable the site**:
    ```bash
    sudo ln -s /etc/nginx/sites-available/taemdee /etc/nginx/sites-enabled/
    sudo nginx -t
    sudo systemctl restart nginx
    ```

---

## 6. Security (Firewall)

Allow only necessary ports:

```bash
sudo ufw allow 'Nginx Full'
sudo ufw allow OpenSSH
sudo ufw enable
```

---

## 7. GitHub Actions (Automated Deployment)

The repository is pre-configured with a GitHub Action in `.github/workflows/deploy.yml`.

1.  **Repository Secrets**: In your GitHub repository, go to **Settings** > **Secrets and variables** > **Actions** and add the following secrets:
    *   `DEPLOY_HOST`: Your VPS IP address.
    *   `DEPLOY_USER`: `pace6`
    *   `DEPLOY_PATH`: The absolute path to your app on the VPS (e.g., `/home/pace6/taemdee-prod`).
    *   `DEPLOY_SSH_KEY`: The **private key** of an SSH key pair.
    *   `DEPLOY_PORT`: `22` (or your custom SSH port).

2.  **Deployment Script**: Ensure the script is executable on the VPS:
    ```bash
    chmod +x /path/to/your/app/scripts/deploy.sh
    ```

3.  **Triggering Deploy**:
    *   Automatic: Push any changes to the `main` branch.
    *   Manual: Go to the **Actions** tab in GitHub, select the **Deploy** workflow, and click **Run workflow**.
