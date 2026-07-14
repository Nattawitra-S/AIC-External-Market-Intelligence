# Deployment Guide -- AIC Market Intelligence Streamlit Dashboard

## Recommended platform

**Self-hosted on a small VM/container in the same private network as
MySQL** (e.g. a DigitalOcean Droplet, AWS Lightsail instance, or an
internal AIC server), behind an nginx (or Caddy) reverse proxy with TLS.

**Why not Streamlit Community Cloud:** the production MySQL database
currently runs on `127.0.0.1` (localhost) with no public endpoint, which
is the correct, secure posture for a database holding government source
data. Streamlit Community Cloud runs your app on Streamlit's own
infrastructure, which would require exposing MySQL to the public internet
(directly or via a tunnel) -- an unnecessary security exposure for a
tool with no public-facing requirement. Self-hosting alongside (or with
private network access to) MySQL avoids that entirely.

If cloud hosting is later required (e.g. for remote team access), the
next-best option is a managed MySQL instance (AWS RDS / DigitalOcean
Managed Database) with the Streamlit app deployed in the **same VPC**,
connecting over private networking -- never over the public internet
without a VPN/SSH tunnel and TLS.

## Steps (self-hosted VM)

1. **Provision a small VM** (2 vCPU / 4GB RAM is comfortable for this
   app's query volume; the largest table is 3.5M rows but every query is
   aggregated, not row-by-row).

2. **Install dependencies**
   ```bash
   sudo apt update && sudo apt install -y python3-pip python3-venv nginx
   git clone <this repo> && cd Gov_ETL_data
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r streamlit_app/requirements.txt
   ```

3. **Configure `.env`** (never commit this file):
   ```bash
   cp .env.example .env
   # Edit .env: MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASS, MYSQL_DB
   # If MySQL is on a different host than this VM, use its private IP or
   # a VPN/SSH-tunnel address here -- never a publicly routable MySQL port.
   ```

4. **Run under a process manager** (systemd shown; supervisor/pm2 also work):
   ```ini
   # /etc/systemd/system/aic-streamlit.service
   [Unit]
   Description=AIC Market Intelligence Streamlit App
   After=network.target

   [Service]
   User=aic
   WorkingDirectory=/opt/Gov_ETL_data
   Environment="PATH=/opt/Gov_ETL_data/.venv/bin"
   ExecStart=/opt/Gov_ETL_data/.venv/bin/streamlit run streamlit_app/Home.py --server.port 8501 --server.address 127.0.0.1
   Restart=on-failure

   [Install]
   WantedBy=multi-user.target
   ```
   ```bash
   sudo systemctl enable --now aic-streamlit
   ```

5. **Reverse proxy with TLS** (nginx + certbot):
   ```nginx
   server {
       listen 443 ssl;
       server_name dashboard.aic.internal;
       ssl_certificate     /etc/letsencrypt/live/dashboard.aic.internal/fullchain.pem;
       ssl_certificate_key /etc/letsencrypt/live/dashboard.aic.internal/privkey.pem;

       location / {
           proxy_pass http://127.0.0.1:8501;
           proxy_http_version 1.1;
           proxy_set_header Upgrade $http_upgrade;
           proxy_set_header Connection "upgrade";
           proxy_set_header Host $host;
       }
   }
   ```
   Streamlit uses WebSockets for live updates -- the `Upgrade`/`Connection`
   headers above are required, not optional.

6. **Restrict access** appropriately for an internal market-intelligence
   tool: put it behind the AIC VPN, an IP allowlist, or nginx
   `auth_basic` / SSO at minimum, since the dashboard surfaces
   competitor/provider data not intended for public access.

## Local development / demo (what was used to build and test this app)

```bash
cd /Users/nattawitrasaengcha/Documents/Gov_ETL_data
streamlit run streamlit_app/Home.py
```

Opens on `http://localhost:8501`. This is sufficient for local development
and single-user use; the systemd + nginx setup above is for shared/team
access.

## Updating after an ETL reload

No redeploy needed -- click **Refresh Data** in the sidebar of any page,
or wait up to 15 minutes for the query cache to expire naturally.
