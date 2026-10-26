# Encore marketing site

Static, dependency-free marketing site for encoreclip.ai: `index.html`, `styles.css`, `app.js` and `assets/`
(fonts, favicon and link preview image from the Encore brand kit). Any static host works (Cloudflare Pages,
Netlify, Vercel, GitHub Pages, a GCS bucket). Upload the folder as is; there's no build step.

Preview locally:

```bash
cd web && python3 -m http.server 8000   # then open http://localhost:8000
```

## Before launch

| What | Where | Status |
|---|---|---|
| Waitlist endpoint | `window.ENCORE.waitlistEndpoint` in `index.html` | Empty. The form validates, then says sign-ups aren't connected. Any endpoint that accepts a JSON POST `{email, source}` and returns 2xx works (Formspree, Loops, Tally, a small Cloud Run function). |
| NEAR Agent Market link | `window.ENCORE.marketUrl` | Points at market.near.ai. Swap in the live Encore listing URL once the listing is out of draft. |
| Subscription prices and hours | Pricing section (`$—`, `[X] hours`, `[Y] hours`) | Placeholders, labelled on the page. |
| Planned features | Pricing section, tagged "Planned" | Saved topic focus per show, seats. Remove or ship. |
| Privacy and terms pages | Footer | Not written yet. Needed before collecting emails at scale. |
| Analytics | none | Add a privacy-friendly counter (e.g. Plausible) and track the events listed below. |

Events worth tracking: hero form submit, closing form submit, market link clicks (hero, pricing, closing),
demo interactions (clip tab, format switch, each change request), FAQ opens, pricing section reached.

## Design system

Everything uses the brand kit tokens (`/brand/encore-brand-kit/colors/tokens.css`).

- **Colour roles.** Ink for text and primary buttons; white or near-black surfaces; raised grey for panels.
  Amber `#ffb224` appears only where the product found something: replay peaks, the spoken caption word, the
  key-line rule. Amber is never text on white (use `--spotlight-text`). No gradients, no shadows.
- **Type.** Geist for everything, Geist Mono for labels, timestamps, clip IDs and anything the agent "says".
  Display sizes use tight tracking (-0.035 to -0.045em).
- **Shape.** 6 / 10 / 16 px radii (tags / controls / cards), 1px hairline borders, pill chips for actions you
  can send to the agent.
- **The demo panel** is always dark, like a monitor, in both themes. It is the one place with motion: a scan
  across the replay graph on first view, word-by-word captions, and re-renders on change requests. All motion
  stops under `prefers-reduced-motion`.
- **Layout.** One 1180px column, 16px gutters on phones and 40px from 720px. Grids collapse to one column on
  phones; the demo stacks graph, clip tabs, preview, reason, change requests.
- **Honesty rules.** Demo data is labelled illustrative. Placeholder prices are labelled. No testimonials, logos
  or usage numbers until they're real.
