# Systemd Units — VidOps

Files:
- `storage-broker.service` — runs the VidOps Storage Broker on localhost.

Deploy:
1) Copy the unit to `/etc/systemd/system/`:
   - `sudo cp config/systemd/storage-broker.service /etc/systemd/system/`
2) Reload systemd and enable the service:
   - `sudo systemctl daemon-reload`
   - `sudo systemctl enable --now storage-broker`
3) Check status and logs:
   - `systemctl status storage-broker`
   - `journalctl -u storage-broker -e`

Nginx site (reverse proxy): see `config/nginx/vidops-broker.conf`. After installation:
- `sudo ln -s /path/to/repo/config/nginx/vidops-broker.conf /etc/nginx/sites-enabled/vidops-broker.conf`
- `sudo nginx -t && sudo systemctl reload nginx`

