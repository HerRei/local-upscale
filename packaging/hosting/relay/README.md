# Mac mini downloads through a small relay server

REVIEW TEMPLATE ONLY. Nothing here is installed or activated automatically.

The Mac mini sits behind two routers: the landlord's router (not to be changed)
and our own GL.iNet router. Incoming connections from the internet cannot reach
it, so the Mac mini **dials out** to a small rented server with a public IPv4
address. That relay terminates HTTPS for the public download hostname and passes
requests through an encrypted WireGuard tunnel to the Mac mini, which keeps
serving the files from its own disk.

```
tester ──HTTPS──▶ relay server (public IP, Caddy) ──WireGuard──▶ Mac mini (Caddy, files)
                                                   ▲
                               outbound UDP from the Mac mini only; no router changes
```

Why this shape:

- no port forwarding on either router and no exposure of the home IP address;
- files stay on the Mac mini (`/mnt/hdd/localsr-downloads/public`);
- the relay stores nothing, so a small plan is enough (for example a Hetzner Cloud
  CX22 with 20 TB traffic per month, about EUR 4–6 per month);
- Tailscale Funnel remains available for quick private tests, but its undisclosed
  bandwidth cap measured about 1.8 MB/s from this network, too slow for 5–6 GB
  GPU packages; Cloudflare's free plan terms exclude serving large non-HTML files
  that are not stored on Cloudflare.

## One-time setup (owner actions)

1. Rent the relay server (Ubuntu LTS) and point a DNS name such as
   `downloads.example.org` at its public IPv4 address.
2. On both machines: `sudo apt install wireguard`. Generate keys on each machine
   with `wg genkey | tee private.key | wg pubkey > public.key` and keep private
   keys out of this repository.
3. Relay: install `wg-relay.conf.example` as `/etc/wireguard/wg-localsr.conf`
   (fill in keys), `sudo systemctl enable --now wg-quick@wg-localsr`, allow
   `51820/udp`, `80/tcp` and `443/tcp` in its firewall.
4. Mac mini: install `wg-macmini.conf.example` as `/etc/wireguard/wg-localsr.conf`
   (fill in keys and the relay's address), then
   `sudo systemctl enable --now wg-quick@wg-localsr`. `PersistentKeepalive`
   keeps the outbound tunnel open through both routers.
5. Mac mini: bind the existing download Caddy to the tunnel address by setting
   `LOCALSR_RELEASE_ADDRESS=http://10.77.0.2:8788` in
   `/etc/localsr-downloads/environment` and restarting `localsr-downloads`.
   Allow that port only on the tunnel interface, for example
   `sudo ufw allow in on wg-localsr to 10.77.0.2 port 8788 proto tcp`.
6. Relay: install Caddy and `Caddyfile.relay` (set `LOCALSR_PUBLIC_HOST`), then
   `sudo systemctl reload caddy`. Caddy obtains the HTTPS certificate itself.
7. From outside the home network verify: HTTPS certificate, a HEAD request,
   an interrupted and resumed download (`curl -C -`), four parallel downloads,
   and a Mac mini reboot (the tunnel and Caddy must come back on their own).

Only after these checks should website download links or update feeds point at
the public hostname.

## Alternative that needs the landlord

If the landlord agrees to forward TCP 80/443 on their router to our router's
WAN address `192.168.50.10`, our own GL.iNet router can forward those ports to
the Mac mini (`192.168.9.134`) and no relay server is needed. This is free and
fastest, but it exposes the shared home IP address and depends on the landlord's
configuration staying unchanged. Never change the landlord's router ourselves.
