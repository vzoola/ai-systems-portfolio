# Architecture: OfficeTechMate, AI teams for small businesses

**Live:** [officetechmate.com](https://officetechmate.com)
**What it is:** done-for-you automation for small offices, framed as "build your AI team" instead of "buy a chatbot." It takes the busywork off the office, starting with the front desk.

## The product surface
- **Interactive AI-team builder:** pick your industry, see a process diagram of how your office runs, toggle modules (front desk, follow-up, reviews, scheduling and more), and watch a **live ROI estimate and monthly price** update. The builder is the visual close in a sales call.
- **Free business audit as the hook:** "show me what your business does, and I'll show you what I can automate."
- **Reception / Front Desk is the flagship:** answers and routes calls, chats and DMs, captures every lead, sends a text back after a missed call, and filters spam.

## Voice agents
- Inbound receptionist and **outbound agent** on Vapi + Twilio. The outbound campaign ran first on a hand-screened 25-number test batch, then the strategy pivoted to the audit-led hook based on the results.
- The prompts carry hard guardrails (real prices only, no capability or compliance over-promises), and every publish is checked with a post-publish grep. The same pattern runs [Patient Catch](architecture-voice-receptionist.md).

## Client portal (multi-tenant)
- Supabase with **row-level security per tenant**, magic-link login (1-hour links), and six migrations written, applied and state-verified.
- **19 canary tests** prove a tenant can never read another tenant's rows. Client #1 is my own directory business, as the first real tenant (dogfooding).
- An owner dashboard with one folder per department, a "needs your word" queue, and approve / decline cards that feed the same approval ledger as [AI Corp](architecture-ai-corp.md).

## Why it's built this way
Small-business owners fear AI for concrete reasons: data theft, losing staff, losing control. Every design choice answers one of those fears. The office keeps its number and its tools, a human approves anything that matters, and every action leaves a receipt.
