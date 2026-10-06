# Architecture: directory platform (Armenian Listing)

**Live:** [armenianlisting.com](https://armenianlisting.com) · Next.js (App Router) · Supabase Postgres · Vercel · Stripe · Resend
I built it to be reusable: the same stack and pipeline power a second directory ([armeniandaycare.com](https://armeniandaycare.com)) and a local marketplace ([nearbyla.com](https://www.nearbyla.com)).

## Data pipeline
- **Seeder → enrich → dedupe → photos.** Businesses come from Places-style sources, get enriched (emails, socials), deduplicated (phone-number variants, soft-hide rather than delete), and get photos backfilled within a free-tier quota budget.
- **Lesson on provenance:** an AI-extracted URL is not provenance. Name collisions once poisoned both images and links, so every link now needs a source match before it's shown.

## Claim funnel (owners take over their listing)
1. A claim-link email goes out through Resend. Open and click tracking is on, and the sender runs on a schedule.
2. **Safety latch:** if the bounce rate crosses an operator threshold (6.25%), the sender writes a pause flag and stops itself until a human clears it.
3. **Honest metrics:** a self-signup stamps `user_id` at insert, but `claimed` flips to true **only on human approval**. An unreviewed signup can't inflate the conversion number. I found and fixed this metric bug in production.
4. Dead claim links heal themselves: expired tokens re-issue instead of dead-ending.

## Revenue
Free / Verified / Featured memberships and sponsorship boosts (category sponsor, spotlight banner, homepage slider) through Stripe Checkout and webhooks. A monthly spotlight pin is cleared when a business leaves the paid tier.

## Release safety
- A deploy script builds locally and runs a **21-check SEO/safety gate** (sitemaps vs DB counts, canonicals, robots, holiday-banner expiry, cache headers) before it will call `vercel --prod`. A dry-run mode stops just before production.
- A **daily health sweep** across 8 sites checks that sitemap counts match the DB, that canonical and robots are right, and that Search Console has indexed the pages. Problems become tickets, and the sweep reports only real diffs against yesterday.
- Incident on record: a 40-hour outage traced to disk-IO exhaustion on the smallest database tier. The fix was a tier upgrade plus an uptime watch, and the post-mortem went into the gotchas ledger.

## Family tree (Roots)
Private, invite-only, built under an adversarial review loop. See the [case study](case-study-family-tree-privacy.md).
