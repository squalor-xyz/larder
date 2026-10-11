# Research: HTTPS for a LAN-only PWA

Date: 2026-10-11. This research fed into [ADR 0015](../decisions/0015-app-tls-agnostic.md)
(the app is TLS-agnostic) and the deployment spike slice.

Confidence labels: **documented** (official docs), **reported** (forums or
vendor blogs), **inference** (reasoning, not tested).

## Scenario

Household phones reach larder on the home LAN. Some devices may also use a
VPN (e.g., Tailscale); others aren't on the VPN at all. A PWA needs HTTPS to
install, and secure cookies need it too.

## Findings

### 1. Tailscale certificates: getting and renewing them
- `tailscale cert` requests a Let's Encrypt certificate for the machine's
  `*.ts.net` name. MagicDNS and HTTPS must be enabled. **Documented.**
- Certificates expire after 90 days. If you use the files directly, renewing
  them is the user's job. **Documented.**
- Caddy's integration gets the certificate from the local `tailscaled` and
  renews it automatically. If Caddy runs as a non-root user, set
  `TS_PERMIT_CERT_UID`. This implies Caddy runs on the same host as
  `tailscaled`; running it in a container is not covered.
  **Documented / inference.**
- If you hit Let's Encrypt rate limits, you may wait about 34 hours. **Documented.**

### 2. Privacy: Certificate Transparency
- Every certificate goes into the public CT ledger, which publishes machine
  and tailnet names. Don't use sensitive machine names. **Documented.**

### 3. Accepting the certificate when the name resolves to a LAN IP
- TLS checks the name, not the IP, so a valid certificate served from a LAN IP
  should pass. **Inference: test on a device.**

### 4. Local DNS override and phones bypassing it
- Android Private DNS "Automatic" tries DNS-over-TLS to the network's resolver
  and falls back to plain DNS. Either way it asks the network's resolver, so a
  local override applies. **Reported.**
- Risks: Private DNS set to a specific hostname, or a browser's own Secure DNS
  (DoH) setting. Either one bypasses the local resolver. **Reported / inference.**
- Some routers reject public names that resolve to private IPs (DNS-rebinding
  protection). Local overrides are usually exempt, but this varies.
  **Inference.**

### 5. Headscale
- Headscale's feature list doesn't mention HTTPS certificates and marks Serve
  as unsupported. `*.ts.net` certs imply Tailscale's hosted control plane.
  **Partially documented.**

### 6. PWA installability on Android
- Since Chrome 108 (Android), installing from the browser menu doesn't need a
  service worker with a `fetch` handler; the automatic install prompt still
  does. Source: a 2023 post, so it may be outdated. **Documented (dated).**
- Installing requires HTTPS (or localhost); service workers require a secure
  context. **Documented.**
- Chrome DevTools → Application → Manifest lists any install criteria that
  aren't met. **Reported.**

## Deployment options

| | Tailscale cert + local DNS override | Owned domain + ACME DNS-01 + local DNS override |
|---|---|---|
| Name | `<host>.<tailnet>.ts.net` | e.g. `larder.home.example.org` |
| Renewal | Caddy + local tailscaled | ACME client with a DNS-provider API token |
| Control-plane coupling | Tailscale hosted | None |
| CT exposure | Machine + tailnet name | That subdomain |
| Works with Headscale | Unverified / unlikely | Yes |

Both options need a local DNS override for devices that aren't on the VPN.

## Sources
- [Tailscale: Enabling HTTPS](https://tailscale.com/kb/1153/enabling-https)
- [Tailscale: Caddy certificates](https://tailscale.com/kb/1190/caddy-certificates)
- [Headscale feature list](https://github.com/juanfont/headscale/blob/main/docs/about/features.md)
- [Chrome: Update install criteria](https://developer.chrome.com/blog/update-install-criteria)
- [MDN: Making PWAs installable](https://developer.mozilla.org/en-US/docs/Web/Progressive_Web_Apps/Add_to_home_screen)
- [NextDNS forum: Android Private DNS Automatic bypass](https://help.nextdns.io/t/60yzj6g/how-to-prevent-nextdns-bypass-on-android-by-changing-private-dns-to-automatic)
- [Stands: What is Private DNS](https://www.standsapp.org/blog/what-is-private-dns/)
- [ExpressVPN: Android Private DNS](https://expressvpn.com/blog/android-private-dns)
