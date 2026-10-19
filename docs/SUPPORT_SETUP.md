# Support setup — obtain each link (step by step)

Goal: get a real URL/address for every method, then paste them into
`config/support.json`. Empty entries are hidden, so do them in any order.

App: **BlackHorse** · Company/brand: **CryptoBoqor** (`cryptoboqor.com`)
GitHub: `<your-username>` (TBD — the account that will own the sponsors page)

How modern apps do it (Blender's model): the app only holds **links + addresses**
and opens them in the browser. Payment happens on the platform's own page — no
card data ever touches this machine. That is why this is safe to ship.

---

## 1. GitHub Sponsors  → `config/support.json → links[0].url`

1. Sign in to your GitHub account (the one that will own the sponsors page).
2. Go to `https://github.com/sponsors` → **Get started** (or "Join the waitlist"
   if your country/account isn't enabled yet).
3. You must have **2FA enabled** and a verified email — do that first if prompted:
   `https://github.com/settings/security` and `https://github.com/settings/emails`.
4. Choose **Individual** (or organization).
5. Connect a payout account (**Stripe Connect**) — legal name, address, bank.
6. Complete the tax form (**W-9** US / **W-8BEN** non-US).
7. Set **monthly tiers** (e.g. $3 / $10 / $50) and one-time amounts.
8. Submit and wait for review (usually same day to a few days).
9. Once approved, your public page is:
   `https://github.com/sponsors/<your-username>`
10. Paste that into `links[0].url`.

## 2. Ko-fi  → `links[1].url`

1. Go to `https://ko-fi.com` → **Sign up** (Google/email).
2. Pick username `cryptoboqor` → page becomes `https://ko-fi.com/cryptoboqor`.
3. Settings → connect **Stripe** and/or **PayPal** to receive.
4. Test the tip button on your own page.
5. Paste `https://ko-fi.com/cryptoboqor` into `links[1].url`.

## 3. Buy Me a Coffee  → `links[2].url`

1. `https://buymeacoffee.com` → **Sign up**, choose username `cryptoboqor`.
2. Connect **Stripe** (payouts).
3. Optional: set a default "coffee" price.
4. Paste `https://buymeacoffee.com/cryptoboqor` into `links[2].url`.

## 4. Patreon  → `links[3].url`

1. `https://patreon.com` → **Create on Patreon**.
2. Choose **Creator**, pick a category (Software/Tech).
3. Username `cryptoboqor` → page `https://patreon.com/cryptoboqor`.
4. Create tiers (join for free / $5 / $20) and connect **Stripe** payout.
5. Paste the page URL into `links[3].url`.

## 5. PayPal  → `links[4].url`

1. Log in at `https://www.paypal.com`.
2. Easiest: claim a **PayPal.Me** name → `https://paypal.me/cryptoboqor`.
3. Or (business account) **Pay & Get Paid → Donate buttons** → create, then copy
   the donate link. Prefer the donate button so the page shows a preset amount.
4. Paste it into `links[4].url`.

## 6. Liberapay  → `links[5].url`

1. `https://liberapay.com` → **Sign up** (recurring donations, non-profit-friendly).
2. Create your profile/team, username `cryptoboqor`.
3. Connect a payout method (Stripe or PayPal) under **Settings → Payouts**.
4. Paste `https://liberapay.com/cryptoboqor` into `links[5].url`.

## 7. Open Collective  → `links[6].url`

1. `https://opencollective.com` → **Create a Collective**.
2. Pick a **fiscal host** (they handle money + receipts; good for transparency).
3. Slug `cryptoboqor` → page `https://opencollective.com/cryptoboqor`.
4. Paste the URL into `links[6].url`.

## 8. Website / own donate page  → `links[7].url`

Your single "fund" landing page (Blender uses `fund.blender.org`).
1. Stand up the page on the cryptoboqor.com site (CryptoBoqor brand).
2. It should list all the links above in one place.
3. Paste the page URL into `links[7].url` — and set `site` at the top of the file.

## 9. Crypto  → `crypto[].address`

Rules (important):
- Use a **fresh, dedicated** receive-only wallet — never a withdrawal-enabled
  exchange API key (BlackHorse rejects those for good reason).
- Crypto donations are **irreversible**; double-check the network.

- **Polkadot (DOT)** — Talisman or Polkadot.js extension → copy the `1...` address.
- **Kusama (KSM)** — same wallet, Kusama network → `C...` address.
- **Bitcoin (BTC)** — Sparrow/BlueWallet → prefer a **bc1...** (bech32) address.
- **Ethereum / USDC** — MetaMask/Rabby → `0x...` address, network **Ethereum mainnet**.

Paste each into the matching `crypto[].address`. Leave any blank to hide it.

---

## After you have the links

Fill `config/support.json`, then tell me — I'll wire the **"❤ Support"** button on
the Dashboard to render exactly the non-empty links/addresses (open in browser /
copy address). Nothing else to configure.

## Safety notes
- Keep the Support dialog honest: say it's a donation, not a subscription to
  BlackHorse (BlackHorse is local/free). Nobody gains features by paying.
- Don't put a payment link behind any agent action or "unlock" — that would be a
  scam surface.
