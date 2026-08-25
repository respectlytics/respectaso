"""In-app release notes - the single source of truth for "What's New".

RELEASE RULE (enforced by aso/tests/test_whats_new.py): the NEWEST entry's
version MUST equal core.settings.VERSION. Bumping VERSION without adding
that release's entry fails the test suite, so a release can never ship
without updated notes. The GitHub release body is generated from the same
entry (``manage.py release_notes --markdown``), so the in-app page and the
GitHub release can never drift apart.

Entry schema (newest first):
    version   "2.22.0" - must match settings.VERSION for the newest entry
    date      "YYYY-MM-DD" (release date)
    title     short benefit-first headline (no version prefix)
    kind      "feature" (first release of a minor/major - full card, may
              trigger the update notice) or "patch" (compact one-liner,
              never triggers the notice)
    notice    optional bool - overrides the kind-based notice decision
    sections  [{"heading": str, "intro": [str], "items": [str]}] - values
              may carry minimal inline HTML (<strong>, <em>, <code>, <a>)

Content rules: user-facing-docs.instructions.md applies verbatim - benefit
first, no engineering trivia, no test counts, no internals.

The update notice ("Updated to X - see what's new") shows once after an
update to a new MINOR or MAJOR version and disappears when the user opens
the What's New page or dismisses it. Patch updates are absorbed silently.
Fresh installs never see the notice.
"""

import logging
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

_LAST_SEEN_FILENAME = "whats_new_last_seen.txt"


# Newest first. See the module docstring for the schema and rules.
RELEASES = [{'version': '2.24.0',
  'date': '2026-08-25',
  'title': 'Invite a friend, queue your AI runs, use any AI service',
  'kind': 'feature',
  'sections': [
      {'heading': 'Invite a friend, and both of you get 3 months',
       'intro': ['Settings \u2192 License has a new Invite a friend panel. Generate a code, '
                 'send it to someone who does ASO, and when they buy Pro your license '
                 'grows by three months.'],
       'items': [
           '<strong>Three months per friend, with no limit.</strong> Every friend who buys '
           'Pro with one of your codes adds three months to your license. Invite as many '
           'people as you like.',
           '<strong>Your friend gets 15 months instead of 12.</strong> A first RespectASO '
           'Pro license bought with an invite code runs fifteen months for the price of '
           'twelve.',
           '<strong>Codes work once and last 90 days.</strong> Generate up to five at a '
           'time, copy a code or a ready-made message with one click, and see at a glance '
           'which ones have been used.',
       ]},
      {'heading': 'Line up as many runs as you like',
       'intro': ['The input form no longer disappears while a run is in progress. Start '
                 'another one and it waits its turn, so you can set up a whole afternoon '
                 'of research in a couple of minutes and come back to the results.'],
       'items': [
           '<strong>Add to Queue.</strong> While something is running, the button on every '
           'AI tab reads "Add to Queue" - fill in the form, submit, tweak a country or a '
           'title, submit again.',
           '<strong>One run at a time.</strong> Runs from the AI Niche Researcher, the AI '
           'Competitor Analyzer and the ASO Score Simulator all share one queue and run in '
           'the order you started them, which keeps every run comfortably inside the App '
           "Store's request limits instead of two analyses competing for them.",
           '<strong>See and change what is up next.</strong> Every AI tab shows the run '
           'that is executing (and which tab it belongs to) plus everything waiting behind '
           'it. Remove a single run, or clear the queue, from wherever you happen to be.',
           '<strong>Results find you.</strong> If you are still on the tab when a run '
           'finishes, its results open as before. If you moved on to something else, a '
           'short notice tells you it finished and takes you straight to it.',
       ]},
      {'heading': 'Retry a failed run with one click',
       'items': [
           '<strong>Same inputs, one click.</strong> A failed or cancelled run on any AI '
           'tab can be retried without re-typing the title, subtitle, keywords, country '
           'or language. Failed and cancelled runs in your history get a Retry button too.',
           '<strong>One card, not two.</strong> A retried run updates its card in place - '
           'in queue, running, then done - instead of leaving the failed card next to a '
           'new one.',
           'A retry uses your current AI settings, so switching provider or model and '
           'clicking Retry is the way to try again with a different model.',
       ]},
      {'heading': 'Connect any OpenAI-compatible AI service',
       'intro': ['Settings \u2192 AI has a new Custom endpoint option next to the existing '
                 'providers.'],
       'items': [
           "<strong>Base URL, API key, model ID.</strong> Enter the service's Base URL, "
           'your key and a model ID, run the built-in compatibility test once, and it is '
           'active. Works with OpenCode Zen, Groq, a LiteLLM proxy or any other service '
           'that speaks the OpenAI API.',
           'Your requests go only to the service whose Base URL you entered, and your key '
           'stays on your computer - RespectASO never sees it.',
       ]},
      {'heading': 'Nothing gets stuck any more',
       'items': [
           'A run that was in progress when you closed RespectASO now shows up as failed '
           'with a Retry button, instead of looking like it is still running forever. '
           'Anything that was waiting in the queue picks up again when you reopen the app.',
           'The run timer counts only the time a run was actually working, not the time it '
           'spent waiting for its turn.',
       ]},
      {'heading': 'Quality of life',
       'items': [
           'Your license extension arrives automatically - there is no key to paste.',
           'The Respectlytics banner no longer shows while you have an active Pro '
           'license, and without one you can hide it with one click.',
       ]},
  ]},
 {'version': '2.22.0',
  'date': '2026-08-19',
  'title': "Apple's official search data, a smarter estimate, and Top Search Terms",
  'kind': 'feature',
  'sections': [
      {'heading': "Apple's search data, now through Apple's official API",
       'intro': ["The Apple Ads popularity source has been rebuilt on Apple's new official "
                 'Apple Ads Platform API. The sign-in window is gone: connecting is a '
                 'one-time, five-minute API key setup - no campaigns, no spend, no payment '
                 'method - and your key never leaves your computer.'],
       'items': [
           "<strong>Official weekly data.</strong> RespectASO syncs Apple's official top "
           'search terms per storefront and category every week, automatically. Keywords '
           "Apple reports get Apple's own 1–100 value; the rest are covered by the "
           'estimate, clearly labeled.',
           '<strong>Works everywhere.</strong> The Apple source now works in the desktop '
           'app, the browser edition, and Docker - it was native-Mac-only before.',
           '<strong>Faster scoring.</strong> After the weekly sync, every popularity '
           'lookup is instant and local - scans and analyses no longer wait on Apple.',
       ]},
      {'heading': 'Top Search Terms - a new Pro tab',
       'intro': ["Browse what people actually search on the App Store: Apple's official "
                 'most-searched terms per storefront and category, with rank, popularity, '
                 "and Apple's 1–5 tier."],
       'items': [
           'See the weekly climbers, fallers, and brand-new terms at a glance.',
           "Follow any term's week-by-week history with sparklines and a full chart.",
           'Track any term for the app you choose, or send it straight to the AI '
           'Researcher - one click each.',
           "If you run Apple Ads campaigns, your ads' impression share per search "
           "term appears on the dashboard.",
       ]},
      {'heading': 'A smarter, measured estimate',
       'intro': ['The built-in popularity estimate has been recalibrated against hundreds '
                 "of Apple's official values, so both sources now speak the same 1–100 "
                 'scale and popular brand keywords are recognized far more reliably. Your '
                 'keyword history is re-scored automatically so everything stays '
                 'comparable - expect the numbers to shift once, by design. Every '
                 'popularity badge now explains itself on hover: which source produced the '
                 'number, and exactly how it was derived.'],
       'items': []},
      {'heading': 'Use any AI model, from any provider',
       'intro': ['OpenAI, Anthropic, and Google Gemini now work like OpenRouter already '
                 'did: type any model ID the provider offers, run the built-in test, and '
                 "it's ready - new models work the day they ship, no app update needed. "
                 'A new AI response deadline (Settings, default 60 minutes) stops a '
                 'stalled model from eating your afternoon, and the progress view now '
                 'shows elapsed time and warns when a model is responding unusually '
                 'slowly.'],
       'items': []},
      {'heading': 'Quality of life',
       'items': [
           "This What's New page - the full release history, always available from the "
           'version badge in the footer and under Settings.',
           'Weekly Apple trend arrows on your keywords, plus an Apple popularity trend '
           'column in CSV exports.',
           'Top Search Terms remembers your storefront and category between visits, and '
           'warns you when you are viewing an earlier week.',
           'Settings: the Save button moved to the top, every unsaved change shows a '
           'clear warning, and a passed model test tells you when Save is still needed.',
           'MCP grows to 23 tools (top search terms and impression share are new), and '
           'the setup instructions for VS Code, ChatGPT Desktop, Claude Code, and Codex '
           'CLI were corrected and expanded.',
           'The Apple Ads connection wizard has a Start over button, so a mis-click no '
           'longer locks you on step 2.',
       ]},
      {'heading': 'Fixes',
       'items': [
           'Saving AI settings no longer resets your Apple Ads connection.',
           'The Apple Ads setup guide page could fail to open - fixed.',
           'Tracked keywords are now marked the same way everywhere on Top Search Terms.',
       ]},
      {'heading': 'For existing Apple Ads users',
       'intro': ['Because the old sign-in mechanism no longer exists, the app will ask '
                 'you to reconnect once using the new key setup - about five minutes, '
                 'guided step by step. Your last-synced Apple data keeps serving until '
                 'the new connection takes over.'],
       'items': []},
  ]},
 {'version': '2.21.1',
  'date': '2026-07-23',
  'title': 'Choose your keyword popularity source: Apple Ads or the RespectASO estimate',
  'kind': 'feature',
  'sections': [{'heading': 'Choose your keyword popularity source: Apple Ads or the RespectASO '
                           'estimate',
                'intro': ["You can now power all your scores with <strong>Apple's own search "
                          'popularity</strong>, fetched directly from your Apple Ads account, '
                          "as an alternative to RespectASO's built-in estimate. Sign in once "
                          "from Settings, and every keyword you track gets Apple's granular "
                          'popularity number next to the RespectASO estimate.'],
                'items': ['<strong>You decide which source drives your scores.</strong> '
                          'Popularity, opportunity, classifications, and download estimates '
                          'all follow your selection, and switching is one click and fully '
                          'reversible, including your full history and trends.',
                          '<strong>Both values, always visible.</strong> Wherever popularity '
                          'appears, a small badge shows which source produced it, with the '
                          "other source's value right beside it. Nothing hidden, nothing to "
                          'hover for.',
                          '<strong>New keywords get their Apple value instantly</strong>, the '
                          'moment they are scored.',
                          '<strong>Automatic, visible fallback.</strong> When Apple has no '
                          'value for a keyword, the estimate steps in and is clearly marked, '
                          'so your scores never go missing.',
                          '<strong>Free for everyone</strong>, with a built-in step-by-step '
                          'setup guide. No campaigns or ad spend required, and your keyword '
                          'data never leaves your Mac.']},
               {'heading': 'Smarter, more honest AI analyses',
                'items': ['Every AI report now states which popularity source it used, both in '
                          'the report and in your saved-runs list.',
                          'When Apple reports its minimum value for many of your keywords, the '
                          'Simulator now explains what that means for your score and shows '
                          'what the same metadata would rate under the estimate, so a low '
                          'number never sends you in the wrong direction.',
                          'Score verdicts now describe what was measured, and the Simulator is '
                          'explicit that the readiness score measures search traffic '
                          'potential, not how well your metadata converts.']},
               {'heading': 'MCP: your AI assistant gets more capable',
                'intro': ['The MCP server, part of RespectASO Pro, now includes tools to check '
                          'and switch your popularity source from Claude, Cursor, or any MCP '
                          'assistant, 21 tools in total. App references now accept the App '
                          'Store ID too, and we fixed an issue where some assistants could not '
                          'pass app ids at all.']},
               {'heading': 'Also in this release',
                'items': ['The update banner now stays visible until you actually update, so '
                          "new versions can't slip by unnoticed.",
                          'CSV exports now include both popularity values and the source per '
                          'keyword. The existing Popularity column follows your selected '
                          'source.']}]},
 {'version': '2.20.0',
  'date': '2026-07-18',
  'title': 'License renewals now activate themselves',
  'kind': 'feature',
  'sections': [{'heading': 'Highlights',
                'items': ['<strong>License renewals now activate themselves.</strong> Renew '
                          'your Pro license on respectaso.com, open the app, and Pro is active '
                          'again - no need to find the email or paste a new key. If the app is '
                          'already open, a new <strong>Check for renewed license</strong> '
                          'button on Settings -&gt; License fetches your renewal on the '
                          'spot.']},
               {'heading': 'Quality of Life',
                'items': ['Pasting a license key now gives clear, specific feedback: the app '
                          "tells you if you pasted the key that's already installed, and "
                          'expired keys show their exact expiry date along with where to find '
                          'your new one.',
                          'The license page now explains that renewals come with a new key, so '
                          "there's no more guessing why the old one stopped working."]}]},
 {'version': '2.19.0',
  'date': '2026-07-15',
  'title': 'Keyword multi-select with bulk actions',
  'kind': 'feature',
  'sections': [{'heading': 'Highlights',
                'items': ['<strong>Work with many keywords at once.</strong> You can now '
                          'select multiple keywords on the Dashboard using checkboxes — and '
                          'your selection is remembered as you move between pages. Gather '
                          'keywords from anywhere in your list, then delete them or copy them '
                          'to your clipboard in a single action.']},
               {'heading': 'Fixes',
                'items': ['<strong>The app always loads fully styled.</strong> Some users saw '
                          'a plain, unstyled page instead of the normal dark interface — '
                          'especially without an internet connection (#13). Styling is now '
                          'built directly into the app, so it looks right every time, online '
                          'or offline.']},
               {'heading': 'Quality of Life',
                'items': ['The App and Countries selectors on the Dashboard now line up '
                          'cleanly.']}]},
 {'version': '2.18.0',
  'date': '2026-07-12',
  'title': 'Use the AI model you want - DeepSeek, free models, and hundreds more',
  'kind': 'feature',
  'sections': [{'heading': 'Use the AI model you want - DeepSeek, free models, and hundreds '
                           'more',
                'intro': ['RespectASO now connects to <strong>OpenRouter</strong>, giving you '
                          'access to practically any AI model on the market with a single key '
                          '- DeepSeek, Qwen, Llama, and hundreds of others, <strong>including '
                          'models that are completely free to use</strong>. Pick the model '
                          'that fits your budget and quality bar, and run the AI Researcher, '
                          'AI Competitor, and ASO Simulator on it.']},
               {'heading': 'Highlights',
                'items': ['<strong>OpenRouter support.</strong> Add your OpenRouter key in '
                          'Settings → AI, type in any model from their catalog, and click '
                          '<strong>Test model</strong>. The built-in check confirms the model '
                          "handles RespectASO's analyses before you commit to a full run - "
                          "once it passes, you're ready to go.",
                          '<strong>Choose your keyword lengths.</strong> All three AI tabs '
                          '(and the MCP tools) now let you pick which keyword lengths to work '
                          'with - 1, 2, 3, and now also 4 words. Hunting for long-tail '
                          'phrases? Uncheck the short lengths and the full research effort '
                          'goes into multi-word keywords instead.',
                          '<strong>Copy keywords straight from the dashboard.</strong> Every '
                          'keyword in your Search History now has a one-click copy button, and '
                          'you can show up to <strong>200 keywords per page</strong> - so your '
                          'whole list fits on one screen. Your page-size choice is remembered. '
                          'For grabbing everything at once, the CSV export is still there.']},
               {'heading': 'Quality of Life',
                'items': ['The <strong>ASO Simulator</strong> now proposes <strong>up to 3 '
                          'alternative titles and subtitles</strong>, ordered best-first, each '
                          'explaining which keyword opportunity it targets - and if your '
                          'current metadata is already strong, it tells you to keep it rather '
                          'than change for the sake of change.',
                          '<strong>Apply Suggestions &amp; Re-Simulate</strong> keeps your '
                          'keyword-length selection, so before/after scores always compare '
                          'like with like.',
                          'Each analysis now shows <strong>which keyword lengths it '
                          'used</strong>, in the results and in your session history - and the '
                          'tabs explain why the words already in your title and subtitle are '
                          'always scored, whatever lengths you select.',
                          'The AI provider list in Settings has a <strong>cleaner '
                          "layout</strong> that's easier to scan now that there are five "
                          'providers to choose from.']},
               {'heading': 'Fixed',
                'intro': ['Thanks to <strong>@gerasimoph</strong> and '
                          '<strong>@foxfortmobile</strong> - both of the headline improvements '
                          'in this release started as your requests (#11, #12).'],
                'items': ['A successful OpenRouter model test now takes effect immediately - '
                          'previously you also had to click Save Settings, which was easy to '
                          'miss.',
                          'Flipping dashboard pages no longer drops an active keyword search.',
                          'The AI tabs no longer show rough per-run dollar estimates - with so '
                          'many models to choose from, they could mislead. The actual token '
                          'usage after each run remains your reliable cost signal.']}]},
 {'version': '2.17.1',
  'date': '2026-07-03',
  'title': 'Opportunity Search is working again',
  'kind': 'patch',
  'sections': [{'heading': 'Opportunity Search is working again',
                'intro': ['This release fixes a bug that made the <strong>Opportunity '
                          'Search</strong> tool unusable when you drive RespectASO from an MCP '
                          'client such as Claude Desktop. Every call failed with an internal '
                          'error, no matter which keyword you tried. It now runs correctly, '
                          'scanning opportunities across all supported countries as '
                          'intended.']},
               {'heading': 'Fixed',
                'intro': ['Thanks to <strong>@imtomdev</strong> for the clear bug report that '
                          'made this quick to pin down.'],
                'items': ['<strong>Opportunity Search (MCP) restored.</strong> The '
                          '<code>opportunity_search</code> tool no longer errors out on every '
                          'call - multi-country opportunity scans work again.']}]},
 {'version': '2.17.0',
  'date': '2026-06-07',
  'title': 'Run AI on your own Mac — introducing Local AI (Beta)',
  'kind': 'feature',
  'sections': [{'heading': 'Run AI on your own Mac — introducing Local AI (Beta)',
                'intro': ['RespectASO now works with <strong>local AI models running right on '
                          'your own machine</strong> — through Ollama, LM Studio, or any '
                          'compatible local server. Point RespectASO at your local model and '
                          'the AI Researcher, AI Competitor, and ASO Simulator run entirely on '
                          'your own hardware, with no cloud API costs.']},
               {'heading': 'Highlights',
                'items': ['<strong>Bring your own model.</strong> Connect Ollama or LM Studio '
                          "in Settings → AI, choose your model, and you're ready. A one‑click "
                          '<strong>Detect</strong> finds the models you already have '
                          'installed.',
                          '<strong>Check before you commit.</strong> A built‑in <strong>Test '
                          'Local AI</strong> runs a quick, representative check so you know '
                          'your setup is ready before you start a full analysis.',
                          '<strong>See it working.</strong> Every AI tab now shows live '
                          'progress while your model thinks — how much it has written and how '
                          'fast — so a longer run never feels stuck.',
                          '<strong>Stop means stop.</strong> Cancelling an analysis now halts '
                          'the work right away and frees your machine.',
                          '<strong>Clearer guidance.</strong> Friendlier, more helpful '
                          'messages when something needs your attention, with practical next '
                          'steps instead of cryptic errors.']},
               {'heading': 'Good to know',
                'intro': ['Local AI is labelled <strong>Beta</strong> while we keep refining '
                          'it across the wide range of local models out there.'],
                'items': ["RespectASO's analyses involve <strong>demanding "
                          "processing</strong>, and local AI models <strong>won't perform "
                          'equally well on every device</strong> — how smoothly things run '
                          "depends on your Mac's hardware.",
                          "<strong>Bigger models handle RespectASO's multi‑step workflows far "
                          'better</strong>, returning more robust and more relevant '
                          'suggestions. If a smaller model feels slow or its results feel '
                          'thin, a larger one is well worth it.',
                          'Prefer the cloud? Your existing OpenAI, Anthropic, and Gemini '
                          'options are unchanged — Local AI is simply a new, cost‑free '
                          'choice.']}]},
 {'version': '2.16.0',
  'date': '2026-06-03',
  'title': 'Sharper keyword suggestions across every AI tab',
  'kind': 'feature',
  'sections': [{'heading': 'Highlights',
                'items': ['<strong>Sharper keyword suggestions across every AI tab.</strong> '
                          'The Score Simulator, Niche Researcher, and Competitor Analyzer now '
                          'suggest keywords that genuinely fit your app — every word earns its '
                          'place instead of padding the list.',
                          '<strong>Your keyword field is held to a higher standard.</strong> '
                          "It's now judged for relevance just like your title and subtitle, so "
                          'the keywords you copy into App Store Connect actually match what '
                          'your app does — better for ranking <em>and</em> conversion.',
                          '<strong>No more wasted keyword space.</strong> Suggestions no '
                          'longer include both a word and its plural (like "hour" and "hours") '
                          '— that freed-up space goes to keywords that broaden your reach.',
                          '<strong>Long-tail phrases that people actually search.</strong> '
                          'Awkward, nonsensical keyword combinations are gone. You get more '
                          'real, natural search phrases to choose from — and far less noise to '
                          'sift through.']}]},
 {'version': '2.15.1',
  'date': '2026-06-02',
  'title': 'Smoother, more reliable AI. Setting up your AI provider is now simpler and more '
           'dependable…',
  'kind': 'patch',
  'sections': [{'heading': 'Highlights',
                'intro': ['<strong>Smoother, more reliable AI.</strong> Setting up your AI '
                          'provider is now simpler and more dependable. The model list in '
                          'Settings shows a curated set of current, ready-to-use models — so '
                          'the AI Niche Researcher, AI Competitor Analyzer, and ASO Score '
                          'Simulator just work, across OpenAI, Anthropic, and Google Gemini.']},
               {'heading': 'Quality of Life',
                'items': ['If a provider ever updates or retires one of its models, RespectASO '
                          'now keeps going automatically instead of stopping with an error — '
                          'your AI features stay available.',
                          'Cleaner, more predictable choices when picking your AI model.']}]},
 {'version': '2.15.0',
  'date': '2026-06-02',
  'title': 'Keep your app details in sync with the App Store Renamed your app, refreshed its '
           'icon, or …',
  'kind': 'feature',
  'sections': [{'heading': 'Highlights',
                'intro': ['<strong>Keep your app details in sync with the App Store</strong> '
                          'Renamed your app, refreshed its icon, or updated your developer '
                          'name? Open the <strong>Apps</strong> tab and click the new refresh '
                          'button beside any app — RespectASO pulls the current title, icon, '
                          'and developer name straight from the App Store, so the right '
                          'details follow you everywhere you work.',
                          '<strong>Always the latest AI models</strong> The model picker in '
                          '<strong>AI Settings</strong> now keeps itself current. When your AI '
                          'provider ships a newer model, it shows up automatically — no app '
                          "update required. And if a model you'd chosen is ever retired, "
                          'RespectASO seamlessly moves you to a current one, so AI Researcher, '
                          'Competitor analysis, and the Simulator keep running without a '
                          'hitch.',
                          '---',
                          '<strong>Requires:</strong> macOS on Apple Silicon (M1 or '
                          'later).']}]},
 {'version': '2.14.0',
  'date': '2026-05-21',
  'title': 'See your whole ASO picture at a glance',
  'kind': 'feature',
  'sections': [{'heading': 'See your whole ASO picture at a glance',
                'intro': ['The Dashboard now opens with a brand-new <strong>App '
                          'Summary</strong> — an instant, at-a-glance read on how your app is '
                          'really doing for the keywords you track.',
                          "For each app you'll see:",
                          "It's collapsible whenever you want more room, and it stays in sync "
                          'automatically as you add keywords or refresh your rankings.'],
                'items': ['<strong>Daily downloads now</strong> and your <strong>growth '
                          'potential</strong> if you reached the top spots',
                          '<strong>Where you rank</strong> and which keyword is driving the '
                          'most traffic',
                          'Your single <strong>biggest opportunity</strong> — the keyword with '
                          'the most untapped upside',
                          'A clear breakdown <strong>by country</strong>, so multi-market apps '
                          'can spot exactly where to focus']},
               {'heading': 'Bring keywords over, ready to act on',
                'intro': ['Keywords you add from the AI tools now land in your Dashboard fully '
                          'scored — complete with download estimates — so you can analyze and '
                          'prioritize them immediately.']},
               {'heading': 'A clearer, smoother Dashboard',
                'intro': ['---',
                          '<em>RespectASO runs entirely on your Mac. No accounts, no API keys, '
                          'no data leaves your machine.</em>'],
                'items': ['Friendlier labels and helpful explanations throughout, so every '
                          'number is easy to understand',
                          'Tidier hover details that appear right where you expect them',
                          'A more intuitive flow when switching between apps and filtering '
                          'your keywords']}]},
 {'version': '2.13.0',
  'date': '2026-05-11',
  'title': 'Compatibility',
  'kind': 'feature',
  'sections': [{'heading': "What's new",
                'intro': ['<strong>See when your rankings were last refreshed.</strong> A live '
                          '"Rankings auto-refreshed X ago" indicator now sits right under your '
                          'Search History heading — at a glance, you know whether the numbers '
                          "you're looking at are fresh."]},
               {'heading': 'Compatibility', 'items': ['macOS 11 or later (unchanged).']}]},
 {'version': '2.12.0',
  'date': '2026-05-10',
  'title': 'Compatibility',
  'kind': 'feature',
  'sections': [{'heading': 'Highlights',
                'items': ['<strong>Title and subtitle suggestions stay true to what your app '
                          "actually does.</strong> The AI now treats relevance to your app's "
                          'core functionality as the first filter — a high-opportunity keyword '
                          "that doesn't describe your app gets rejected, even if it ranks at "
                          'the top of the opportunity table. You get titles users will tap on, '
                          'not just rank for.',
                          '<strong>Localized metadata reads as a real app name in every '
                          'supported language.</strong> Generating metadata for any of the 30 '
                          'supported App Store storefronts now produces something a native '
                          'speaker would actually write — proper prepositions, particles, '
                          'compounding, and word order — instead of a flat list of keywords '
                          'translated word-for-word from English. The same standard applies to '
                          'Spanish, French, German, Japanese, Korean, Chinese, Arabic, '
                          'Russian, Turkish and every other locale we ship.',
                          "<strong>Runs no longer get stuck when Apple's App Store API "
                          'misbehaves.</strong> RespectASO now detects when Apple slows down '
                          'or starts rate-limiting, paces requests adaptively, and — if things '
                          'stay rough — finishes gracefully with partial results plus a clear '
                          'recommendation to re-run in a few minutes. No more multi-minute '
                          'hangs on a single keyword.']},
               {'heading': 'Quality of Life',
                'items': ['<strong>Live status when things slow down.</strong> A subtle banner '
                          "appears in the AI tab progress panel if Apple's API starts "
                          "throttling, so you know what's happening and roughly how much "
                          'longer to expect.',
                          '<strong>Cleaner non-English runs.</strong> When you target a '
                          'non-English App Store, the AI Simulator, Niche Researcher, and '
                          'Competitor Analyzer now stay strictly within the target language '
                          'end-to-end — keyword scoring no longer drifts into English near the '
                          'end of a run.',
                          "<strong>Clearer guidance when a run can't complete.</strong> If a "
                          "run had to stop early because Apple's API was unreliable, the "
                          'result page now shows a clear amber banner above your readiness '
                          'score with a specific "re-run in a few minutes" recommendation — no '
                          'more guessing whether the result is complete or not.']},
               {'heading': 'Compatibility', 'items': ['macOS 11 or later (unchanged).']}]},
 {'version': '2.11.0',
  'date': '2026-05-08',
  'title': 'RespectASO v2.11.0',
  'kind': 'feature',
  'sections': [{'heading': 'RespectASO v2.11.0',
                'intro': ['Multi-language metadata — pick any App Store storefront, generate '
                          'native target-language versions.']},
               {'heading': 'Highlights',
                'items': ['<strong>Suggestions language picker on AI Researcher, AI '
                          'Competitor, and ASO Score Simulator.</strong> Choose any of 30+ App '
                          'Store storefronts (English, French, German, Spanish, Japanese, '
                          'Korean, Chinese, Hindi, Arabic, Portuguese, and many more) and get '
                          'suggestions written natively for that market.',
                          '<strong>New Localize mode in ASO Score Simulator.</strong> Hand '
                          'over your existing metadata in any language and get a brand-new '
                          "title, subtitle, and keyword field in your target storefront's "
                          'language — native search phrases real local users actually type, '
                          'never machine translation.',
                          '<strong>Localize works across regional variants.</strong> Take your '
                          'en-US metadata to en-GB ("soccer" → "football"), es-ES to es-MX, '
                          'pt-BR to pt-PT, zh-Hans to zh-Hant, and more. The Simulator detects '
                          'when source and target match and offers a quick confirm-or-switch '
                          'prompt.',
                          '<strong>Bilingual output by design.</strong> Suggested titles, '
                          'subtitles, and keyword fields come back in your target language; AI '
                          'feedback and analysis stay in English so your decisions stay easy '
                          'to read.',
                          '<strong>Title pre-fill from the App Store.</strong> Pick your '
                          'tracked app and the App Title field auto-fills from the App '
                          'Store.']},
               {'heading': 'Quality of Life',
                'items': ['<strong>Cleaner Simulator results.</strong> Title, subtitle, and '
                          'keyword field each get their own clearly-labelled row with a '
                          'one-click copy button — paste straight into App Store Connect.',
                          '<strong>Mode-aware results heading.</strong> Score mode says '
                          '"Evaluated Metadata"; Localize mode says "Generated [Language] '
                          'Metadata" so you always know what you\'re looking at.',
                          '<strong>"Source (your English)" sub-panel</strong> on Localize '
                          'results so you can compare side-by-side.',
                          '<strong>Refreshed locked AI Simulator preview</strong> matches the '
                          'real form pixel-for-pixel — no surprise after you activate Pro.']}]},
 {'version': '2.10.2',
  'date': '2026-05-06',
  'title': 'See your download potential at a glance — every saved keyword in the dashboard now '
           'shows t…',
  'kind': 'patch',
  'sections': [{'heading': 'Highlights',
                'intro': ['<strong>See your download potential at a glance</strong> — every '
                          'saved keyword in the dashboard now shows the estimated daily '
                          "downloads you'd capture if you ranked #1, with a hover breakdown "
                          'across rank #1, #5, and #10. Sort the column to surface the '
                          'keywords with the biggest upside.']}]},
 {'version': '2.10.1',
  'date': '2026-04-29',
  'title': 'RespectASO 2.10.1 — Save every term to Search History',
  'kind': 'patch',
  'sections': [{'heading': 'RespectASO 2.10.1 — Save every term to Search History',
                'intro': ['A small follow-up to 2.10.0 that closes a gap in the ASO Score '
                          "Simulator's Metadata Coverage Analysis."]},
               {'heading': "What's fixed",
                'items': ['"Covered" terms whose source is a cross-field combination (for '
                          'example <em>KF + Title</em>, <em>Title Phrase</em>, <em>Keyword '
                          'Field Combo</em>) now consistently show the "track this term" '
                          'checkbox alongside every other term — so you can save them to '
                          'Search History with a single click.',
                          'These rows now carry the same complete score data as every other '
                          'tracked keyword, so once saved they appear in the Dashboard with '
                          'full popularity, difficulty, opportunity, and competitor '
                          'information.']}]},
 {'version': '2.10.0',
  'date': '2026-04-29',
  'title': 'RespectASO 2.10.0 — Stronger metadata recommendations',
  'kind': 'feature',
  'sections': [{'heading': 'RespectASO 2.10.0 — Stronger metadata recommendations',
                'intro': ['This release significantly broadens the keyword opportunity space '
                          'our agentic engine considers when crafting metadata recommendations '
                          'across the AI Niche Researcher, AI Competitor Analyzer, and ASO '
                          'Score Simulator.']},
               {'heading': 'Highlights',
                'items': ['Recommendations are now built on a substantially larger and more '
                          'diverse pool of keyword candidates, surfacing stronger niche and '
                          'long-tail opportunities that earlier versions could overlook.',
                          'More consistent coverage across all three AI tools, so the quality '
                          'of suggestions no longer depends on which entry point you start '
                          'from.',
                          'Improved balance between high-volume head terms and natural '
                          'multi-word phrases, leading to more competitive title, subtitle, '
                          'and keyword field suggestions.']},
               {'heading': 'Why it matters',
                'intro': ['A wider opportunity pool means each metadata suggestion is grounded '
                          'in more evidence — increasing the chance of finding the right fit '
                          "for your app's niche."]}]},
 {'version': '2.9.1',
  'date': '2026-04-28',
  'title': 'Compatibility',
  'kind': 'patch',
  'sections': [{'heading': 'Highlights',
                'items': ['<strong>Improved metadata suggestion generation</strong> — title, '
                          'subtitle, and keyword-field recommendations across the AI tabs are '
                          'now better tuned to help your app capture more organic search '
                          'traffic.']},
               {'heading': 'Compatibility',
                'items': ['macOS 11+ on Apple Silicon (M1 or later).']}]},
 {'version': '2.9.0',
  'date': '2026-04-27',
  'title': 'RespectASO v2.9.0',
  'kind': 'feature',
  'sections': [{'heading': 'RespectASO v2.9.0',
                'intro': ['A bigger, more polished AI workflow — and a smoother bridge from '
                          'your AI simulations into the Dashboard you already track keywords '
                          'with.']},
               {'heading': 'Highlights'},
               {'heading': '🆕 Send AI Simulator keywords straight into your tracked Search '
                           'History',
                'intro': ['The ASO Simulator now lets you tick the keywords (and metadata '
                          "terms) that look most promising and add them to the Dashboard's "
                          'Search History for the same app — in one click. No re-searching, no '
                          're-scoring. The popularity, difficulty, opportunity, download '
                          'estimates, app rank, and competitor list are reused exactly as '
                          'shown, so the Dashboard sees the same scores you just looked at. '
                          'Keywords you already track are skipped automatically, which '
                          'preserves your existing rank history.']},
               {'heading': '📊 Live progress counter that survives tab switches',
                'intro': ['The "keywords scored" counter on AI Researcher, AI Competitor, and '
                          'ASO Simulator no longer resets to zero when you switch tabs and '
                          'come back. The number simply continues from where it was, in every '
                          'AI tab, every time.']},
               {'heading': '📋 Copy and Export now work reliably in the native Mac app',
                'intro': ['The <strong>Copy</strong> button on every AI tab finally just works '
                          'in the native Mac app. Exports prefetch in the background so the '
                          'moment you click Copy or Export, the content is already there — no '
                          'waiting, no silent failures.']},
               {'heading': '💡 Honest "your description is limiting your suggestions" guidance',
                'intro': ['When ASO Simulator detects that your live App Store description is '
                          'shorter than what Apple allows (4,000 characters), it now shows a '
                          'clear amber notice explaining <em>why</em> the suggestions feel '
                          'safer than they could be — and gives you two concrete options to '
                          'unlock richer ideas, including a one-click jump to the Refine '
                          'tab.']},
               {'heading': '🧭 Dashboard keyword table: rank column always visible',
                'intro': ["The Dashboard's keyword table now always shows the "
                          '<strong>Rank</strong> column, even when "All apps" is selected. '
                          'Keywords tied to a tracked app show your real rank; keywords '
                          'without an app show "—". You can scan rank performance across your '
                          'portfolio at a glance.']},
               {'heading': '✨ Cleaner sticky result headers across all 3 AI tabs',
                'intro': ['The result headers on AI Researcher, AI Competitor, and ASO '
                          'Simulator have been reorganised into a calmer, more breathable '
                          'two-row layout. The badges (keywords analysed, chain total, elapsed '
                          'time) sit lightly under the title; the action buttons line up '
                          'neatly on the right.']},
               {'heading': 'Quality of Life',
                'items': ['<strong>Dashboard layout fix</strong> — restored the spacing '
                          'between the Filters, Search, and Scoring Guide cards that was '
                          'visually cramped in the previous build.',
                          '<strong>Re-simulations stay instant</strong> — adding keywords from '
                          'a re-simulation into the Dashboard is just as instant as the '
                          'simulation itself.',
                          '<strong>Friendlier handling of older simulations</strong> — '
                          'simulations from earlier versions show a clear notice instead of a '
                          'broken state when you try to add their keywords to the Dashboard.']},
               {'heading': 'Compatibility',
                'intro': ['Apple Silicon (M1 or later) macOS. Free edition still ships via '
                          'Docker; Pro AI features require the native .dmg and a Pro '
                          'license.']}]},
 {'version': '2.8.0',
  'date': '2026-04-26',
  'title': 'RespectASO v2.8.0',
  'kind': 'feature',
  'sections': [{'heading': 'RespectASO v2.8.0'},
               {'heading': 'See your work happen in real time',
                'intro': ['AI features now show <strong>live progress</strong> while running:',
                          'When the run finishes, the results header tells you exactly how '
                          'much work was done:',
                          'No more wondering whether the AI is still working or how thorough '
                          'the analysis was.'],
                'items': ['An elapsed-time clock ticking up',
                          'A live counter of how many keywords have been scored so far',
                          '<strong>X unique keywords analyzed</strong> in this run',
                          '<strong>How long it took</strong>',
                          'For refinements: <strong>total unique keywords across all '
                          'iterations</strong> in the chain']},
               {'heading': 'Sortable Opportunity column on the Dashboard',
                'intro': ['You can now sort your keyword history by '
                          '<strong>Opportunity</strong> — quickly surface your best targets. '
                          'Numeric columns (Popularity, Difficulty, Opportunity, Rank, '
                          'Competitors) now sort highest-first on the first click, which is '
                          'what you actually want.']},
               {'heading': 'Get your app featured on the ASO Review Show',
                'intro': ['We added a Settings card linking to <strong>The Creator '
                          "Behind</strong>'s <a "
                          'href="https://www.youtube.com/playlist?list=PLvEPSrgsfJC2HeA1mFsAQ0nHtG1wPqOqG" '
                          'target="_blank" rel="noopener">ASO Review Show</a> — a YouTube '
                          "series that breaks down real apps' metadata using RespectASO. "
                          "Submit your app via the card and we'll consider featuring it in an "
                          'upcoming episode.']},
               {'heading': 'Fixes',
                'intro': ['---',
                          '<strong>Free vs Pro:</strong> All features above are available in '
                          'the free edition unless noted. The AI features (Researcher, '
                          'Competitor, Simulator) require a Pro license.',
                          '<strong>Compatibility:</strong> Apple Silicon Macs (M1 or later) '
                          'running macOS 11+. Internet required.',
                          '<strong>Upgrade:</strong> Download the new DMG, drag to '
                          'Applications. Your settings, license, and tracked apps carry over '
                          'automatically.'],
                'items': ['<strong>Settings:</strong> Changing the AI model (not just the '
                          'provider) now correctly shows the "unsaved changes" reminder.',
                          '<strong>External links</strong> in the native Mac app (email, web '
                          'links) now open reliably in your default browser or mail app.']}]},
 {'version': '2.7.4',
  'date': '2026-04-17',
  'title': 'Complete AI Tab Exports',
  'kind': 'patch',
  'sections': [{'heading': "What's New"},
               {'heading': 'Complete AI Tab Exports',
                'intro': ['All three AI feature exports (Researcher, Competitor, Simulator) '
                          'now include the <strong>full report data</strong> — matching '
                          'exactly what you see in the app.',
                          '<strong>Previously missing, now included:</strong>'],
                'items': ['<strong>Estimated downloads</strong> column in all keyword tables',
                          '<strong>All metadata title/subtitle variants</strong> with '
                          'character counts, recommended badges, and overlap warnings',
                          '<strong>Metadata Coverage Analysis</strong> table with opportunity '
                          'scores and estimated downloads',
                          '<strong>Token usage</strong> statistics (input/output/total)',
                          '<strong>Simulator:</strong> ranking effectiveness score, readiness '
                          'warnings, ranking insights (download capture %, untapped keywords, '
                          'bonus rankings)',
                          '<strong>Competitor:</strong> full competitor profile (seller name, '
                          'version, release dates, current title/subtitle)',
                          '<strong>Researcher:</strong> keyword source column, "Research Next" '
                          'suggestions',
                          '<strong>No more keyword caps</strong> — all keywords are exported '
                          '(previously limited to 30)']}]},
 {'version': '2.7.3',
  'date': '2026-04-17',
  'title': "What's New in v2.7.3",
  'kind': 'feature',
  'sections': [{'heading': "What's New in v2.7.3"},
               {'heading': 'Resilient Data Collection',
                'intro': ['The iTunes Search API occasionally returns incomplete data for '
                          'certain country/keyword combinations. RespectASO now automatically '
                          'detects this and falls back to a secondary Apple endpoint '
                          '(Server-Side Rendering), ensuring you always get complete results — '
                          'even in edge-case markets.']},
               {'heading': 'MCP Server — 19 Tools for AI Assistant Integration',
                'intro': ['RespectASO Pro now includes a built-in MCP (Model Context Protocol) '
                          'server that lets AI assistants like Claude Desktop and Cursor '
                          'perform ASO tasks directly. 19 tools cover the full workflow: '
                          'keyword research, difficulty/popularity scoring, opportunity '
                          'analysis, competitor analysis, app tracking, metadata simulation, '
                          'and more. Configure it in Settings → MCP Integration.']},
               {'heading': 'Export Competitor Apps from Dashboard',
                'intro': ['The CSV export now includes the top competitor apps for each '
                          'keyword — with their names, ratings, review counts, and prices. See '
                          "exactly who you're competing against without leaving your "
                          'spreadsheet.']},
               {'heading': 'Better Support for Non-English Markets',
                'intro': ['Keyword extraction and n-gram analysis now properly handle accented '
                          'characters (é, ñ, ü, etc.) and language-specific word boundaries, '
                          'improving accuracy for French, Spanish, German, Portuguese, and '
                          'other non-English App Stores.']},
               {'heading': 'Session Timestamps in Local Timezone',
                'intro': ['AI Researcher, AI Competitor, and ASO Simulator sessions now '
                          'display timestamps in your local timezone instead of UTC.']},
               {'heading': 'Update Notifications on Every Tab',
                'intro': ['The version update banner now appears on all tabs (Dashboard, Apps, '
                          'Opportunity, Methodology, and Pro features), not just the '
                          'Dashboard.',
                          '---',
                          '<strong>Download:</strong> Mount the DMG, drag RespectASO to '
                          'Applications, and launch. Requires macOS with Apple Silicon (M1 or '
                          'later).']}]},
 {'version': '2.6.0',
  'date': '2026-04-15',
  'title': 'Native Apple Silicon Support',
  'kind': 'feature',
  'sections': [{'heading': '',
                'intro': ['Native Apple Silicon Support',
                          'RespectASO now runs natively on Apple Silicon Macs (M1, M2, M3, M4) '
                          '— no Rosetta 2 translation needed. The app launches faster and uses '
                          'less memory.',
                          'Improved Update Notifications',
                          'Release notes in the update banner now display with clean '
                          'formatting instead of showing raw text symbols.',
                          'Requirements',
                          'macOS 12 (Monterey) or later, Apple Silicon Mac (M1 or later).']}]},
 {'version': '2.5.0',
  'date': '2026-04-13',
  'title': "What's New in v2.5.0",
  'kind': 'feature',
  'sections': [{'heading': "What's New in v2.5.0"},
               {'heading': 'Smarter AI Keyword Suggestions',
                'items': ['<strong>Better titles and subtitles</strong> — The AI now generates '
                          'more natural, human-readable app names instead of keyword salad. '
                          'Titles like "AI Grammar Checker Keyboard" instead of "Grammar Check '
                          'Fix Tool App". Every word must dual-purpose: sound natural AND '
                          'target a high-opportunity keyword.',
                          '<strong>Better use of every character</strong> — Title and subtitle '
                          'now target 25–30 characters (previously could be much shorter), and '
                          'the keyword field targets 97+ characters. The AI works harder to '
                          'fill every available character with meaningful keywords instead of '
                          'leaving space unused.',
                          '<strong>No more abbreviations</strong> — The AI no longer shortens '
                          'words like "Keyboard" to "Keybd" or "Manager" to "Mgr". Only '
                          'universally recognized abbreviations (AI, GPS, PDF, etc.) are '
                          'allowed.',
                          '<strong>Smarter keyword field design</strong> — The keyword field '
                          'is now treated as a complement to the title and subtitle, not a '
                          'dumping ground. The AI considers which cross-field combinations '
                          'each keyword field word creates with title/subtitle words, '
                          'prioritizing words that form phrases people actually search.']},
               {'heading': 'Improved Cross-Field Combination Discovery',
                'items': ['<strong>Better combination quality</strong> — All three AI tabs '
                          '(Simulator, Researcher, Competitor) now share the same quality gate '
                          'for cross-field keyword combinations. The AI filters out '
                          'nonsensical word pairs (like "tracker running") and keeps natural '
                          'search phrases (like "running tracker").',
                          '<strong>Both word orderings considered</strong> — The AI now '
                          'evaluates phrases in both directions (e.g., "budget planner" and '
                          '"planner budget") and picks the one people actually search.',
                          '<strong>3-word long-tail combinations</strong> — In addition to '
                          '2-word pairs, the AI now discovers valuable 3-word search phrases '
                          '(e.g., "daily calorie tracker") by mixing words across metadata '
                          'fields.']},
               {'heading': 'Faster Re-Simulations and Refinements',
                'items': ['<strong>Keyword score caching</strong> — When you re-simulate or '
                          'refine suggestions, previously-scored keywords are reused instantly '
                          'instead of being re-fetched from the App Store. This makes '
                          'follow-up runs significantly faster.',
                          '<strong>Clearer refinement process</strong> — The Refine panel now '
                          'explains that it regenerates suggestions using existing keyword '
                          'data without re-scoring, so you know what to expect.']},
               {'heading': 'More Accurate Ranking Effectiveness Score',
                'items': ['<strong>Honest scoring for unranked keywords</strong> — The ranking '
                          'effectiveness score no longer gives free points for keywords you '
                          "don't rank on. Previously, an app with zero rankings on hard "
                          'keywords could still show a score of 20–30; now it correctly shows '
                          'near zero. You earn ranking effectiveness by actually ranking, not '
                          'just by targeting difficult keywords.']},
               {'heading': 'Better Competitor Analysis',
                'items': ['<strong>Nonsense keyword filtering</strong> — Multi-word n-grams '
                          'extracted from competitor metadata are now validated by the AI, '
                          'removing meaningless phrases that would waste API calls and clutter '
                          'results.',
                          '<strong>App context in results</strong> — Competitor analysis now '
                          'shows app rating, release date, last update, and version info, '
                          'giving you more context about each competitor.',
                          '<strong>Source labels show field origins</strong> — Combination '
                          'keywords now show exactly which metadata fields they come from '
                          '(e.g., "Title + KF", "Subtitle + Title") instead of a generic '
                          '"Combination" label.']},
               {'heading': 'UI Improvements',
                'items': ['<strong>Copy any keyword</strong> — Every keyword in the results '
                          'tables now has a copy button so you can quickly grab keywords for '
                          'your metadata.',
                          '<strong>Clearer section descriptions</strong> — The "Keyword '
                          'Opportunities" and "Metadata Coverage Analysis" sections now have '
                          'better descriptions explaining what each section shows and how to '
                          'use it.',
                          '<strong>More visible selection borders</strong> — Selected '
                          'title/subtitle combo cards now have more visible borders so you can '
                          'clearly see which combination is being analyzed.',
                          '<strong>App context display</strong> — The Simulator now shows your '
                          "app's rating, release date, last update, version, and seller below "
                          'the app selector, so you have full context without leaving the '
                          'page.']}]},
 {'version': '2.4.0',
  'date': '2026-04-09',
  'title': 'Smarter AI Keyword Discovery',
  'kind': 'feature',
  'sections': [{'heading': "What's New"},
               {'heading': 'Smarter AI Keyword Discovery',
                'intro': ['All three AI features (Researcher, Competitor Analyzer, Simulator) '
                          'now use a more intelligent approach to discover keywords. The AI '
                          'receives richer context about competitor apps, resulting in '
                          'higher-quality keyword suggestions that better reflect real user '
                          'search behavior.']},
               {'heading': 'Source Tooltips',
                'intro': ['Hovering over any source badge in keyword tables now shows a '
                          'helpful explanation of where that keyword came from and what it '
                          'means — making results easier to understand at a glance.']},
               {'heading': 'Scrollable Keyword Tables',
                'intro': ['Keyword tables in all AI tabs now scroll vertically with sticky '
                          'column headers, keeping long result lists manageable without losing '
                          'track of which column is which.']},
               {'heading': 'More Keywords Generated',
                'intro': ['All AI features now discover more keyword opportunities per '
                          'analysis, giving you a broader view of your niche.']},
               {'heading': 'Improvements',
                'items': ['Improved tooltip descriptions to be more accurate and intuitive',
                          'Cleaned up internal code for better performance']}]},
 {'version': '2.3.2',
  'date': '2026-04-08',
  'title': 'Scoring Consistency',
  'kind': 'patch',
  'sections': [{'heading': 'Scoring Consistency',
                'intro': ['All four core metrics — popularity, difficulty, opportunity, and '
                          'estimated downloads — now produce identical results across every '
                          "tab and table in the application. Whether you're viewing a keyword "
                          'on the Dashboard, in Opportunity analysis, or through any of the '
                          'AI-powered tools (Researcher, Competitor Analyzer, Simulator), '
                          "you'll see the same scores for the same keyword in the same "
                          'localization.']},
               {'heading': 'What changed',
                'items': ['<strong>Consistent download estimates across all views.</strong> '
                          'Previously, AI tab tables could display slightly different download '
                          'ranges than the Dashboard for the same keyword. All views now use '
                          'the same per-position data source and formatting.',
                          '<strong>Decimal precision in download estimates.</strong> Download '
                          'ranges now display with consistent decimal precision across all '
                          'tabs (e.g., "0.9–3.7/day" instead of rounded "&lt;1–4/day").',
                          '<strong>Unified scoring pipeline enforcement.</strong> The metadata '
                          'coverage analysis engine now uses the same search parameters as the '
                          'Dashboard, ensuring popularity, difficulty, and opportunity scores '
                          'match exactly.']},
               {'heading': 'Quality assurance',
                'intro': ['This release adds 23 automated regression tests that verify scoring '
                          'consistency across all scoring paths, download estimate identity, '
                          'and template display formatting. These tests run before every '
                          'future release to prevent scoring drift.']}]},
 {'version': '2.3.1',
  'date': '2026-04-06',
  'title': 'New',
  'kind': 'patch',
  'sections': [{'heading': 'Fixes',
                'items': ['Fix: Bulk refresh no longer creates phantom keyword results across '
                          'wrong countries (#3)',
                          'Fix: Bulk refresh respects country filter from Search History '
                          'section',
                          'Fix: Bulk refresh now runs in background — no more blocking '
                          'requests or broken navigation',
                          'Fix: "All Apps" refresh now correctly includes all tracked '
                          'keywords']},
               {'heading': 'New',
                'items': ['X (@sinecanswork) link added to footer',
                          'YouTube feature request card on Settings tabs (Pro)']}]},
 {'version': '2.3.0',
  'date': '2026-04-06',
  'title': 'YouTube demos and polish across the Pro pages',
  'kind': 'feature',
  'sections': [{'heading': "What's New",
                'items': ['YouTube demo links on all Pro feature promo pages',
                          'Prominent "Download Pro" button on free/Docker promo pages',
                          'Direct .dmg download (no more redirect to GitHub releases page)',
                          'Docker warning made more visible on Pro promo tabs',
                          'Dead code cleanup (unused model, workflow, static file removed)',
                          'Improved release testing documentation']}]},
 {'version': '2.2.1',
  'date': '2026-03-27',
  'title': 'Update banner fixed in the native Mac app',
  'kind': 'patch',
  'sections': [{'heading': 'Fixes',
                'items': ['<strong>Fix update banner not appearing in native macOS '
                          'app</strong> — the in-app update checker was silently failing due '
                          'to missing SSL certificates in the PyInstaller bundle. Bundled '
                          '<code>certifi</code> CA certificates so HTTPS calls to the GitHub '
                          'API work correctly.',
                          '<strong>Add visible error hint</strong> when the update check fails '
                          '— Dashboard now shows a subtle "Unable to check for updates" '
                          'message instead of silent failure.',
                          '<strong>Add file logging for native app</strong> — errors are '
                          'logged to <code>~/Library/Application '
                          'Support/RespectASO/respectaso.log</code> for diagnostics.']}]},
 {'version': '2.2.0',
  'date': '2026-03-27',
  'title': 'Keyword text search in your dashboard history',
  'kind': 'feature',
  'sections': [{'heading': "What's New",
                'items': ['<strong>Keyword text search</strong> in dashboard history — type a '
                          'word and press Enter to filter results across all pages. Works with '
                          'all existing filters (insight, popularity, difficulty, country), '
                          'pagination, sorting, and CSV export.']},
               {'heading': 'Fixes',
                'items': ['Fixed About dialog showing hardcoded version instead of actual app '
                          'version',
                          'Fixed download estimates chart rendering',
                          'Fixed country filter dropdown behavior']}]},
 {'version': '2.1.1',
  'date': '2026-03-26',
  'title': 'Bug Fixes',
  'kind': 'patch',
  'sections': [{'heading': 'Bug Fixes',
                'items': ['<strong>Country filter</strong>: Fixed the country dropdown in '
                          'Search History not responding after performing a new search. The '
                          "filter's event listener was lost when the history section "
                          'refreshed.',
                          '<strong>Download estimates chart</strong>: Fixed the "Estimated '
                          'Downloads by Position" chart not appearing when expanding keyword '
                          'details. The chart rendering function was called before it was '
                          'defined due to script ordering.']},
               {'heading': 'Full Changelog',
                'intro': ['https://github.com/respectlytics/respectaso/compare/v2.1.0...v2.1.1']}]},
 {'version': '2.1.0',
  'date': '2026-03-26',
  'title': 'Server-Side Dashboard Filters',
  'kind': 'feature',
  'sections': [{'heading': "What's New"},
               {'heading': 'Server-Side Dashboard Filters',
                'items': ['Insight, popularity, and difficulty filters now work across all '
                          'pages (not just the first 25 results)',
                          'Filter count shows "X of Y matching" for clarity',
                          'Filters persist when switching tabs (via sessionStorage)']},
               {'heading': 'CSV Export Fix',
                'items': ['Export now correctly deduplicates results (latest per '
                          'keyword+country), matching what the dashboard shows',
                          'Export respects all active filters']},
               {'heading': 'Opportunity Tab Persistence',
                'items': ['Partial results are saved incrementally during multi-country scans, '
                          'so switching tabs mid-search no longer loses completed country '
                          'results']},
               {'heading': 'Other',
                'items': ['Native macOS app improvements (.gitignore cleanup, version bump)',
                          'Docker version remains fully functional']}]},
 {'version': '1.5.1',
  'date': '2026-03-14',
  'title': 'Bug Fixes',
  'kind': 'patch',
  'sections': [{'heading': 'Bug Fixes'},
               {'heading': 'Critical: Auto-refresh daily updates silently failing',
                'intro': ['The background scheduler that refreshes keyword data daily was '
                          'silently failing on every keyword. The progress bar showed 100% '
                          'completion, but no data was actually updated.',
                          '<strong>Root cause:</strong> A regression in v1.5.0 where '
                          '<code>_refresh_pair()</code> passed <code>len(competitors)</code> '
                          '(an integer like 25) as the <code>country</code> parameter to '
                          '<code>DownloadEstimator.estimate()</code>. This caused an '
                          '<code>AttributeError</code> on every refresh attempt, which was '
                          'caught by the per-keyword error handler — making the failure '
                          'invisible.',
                          '<strong>Fix:</strong> Corrected the call to '
                          '<code>download_est.estimate(popularity, country=country)</code>.']},
               {'heading': 'Performance: N+1 query optimization in scheduler',
                'intro': ['<code>_needs_refresh_today()</code> and '
                          '<code>_get_pairs_to_refresh()</code> each performed 1+N database '
                          'queries (one per keyword+country pair). Replaced with single '
                          'annotated queries using <code>Max("searched_at")</code>, reducing '
                          'database load during the daily refresh check.']},
               {'heading': 'How to Update',
                'intro': ['``<code>bash docker compose pull docker compose up -d </code>``',
                          'Or if you built from source:',
                          '``<code>bash git pull docker compose build docker compose up -d '
                          '</code>``',
                          'After updating, your daily auto-refresh will start working '
                          'correctly. You should see new data points appearing in trend charts '
                          'within 24 hours.']}]},
 {'version': '1.5.0',
  'date': '2026-03-12',
  'title': 'Brand Keyword Detection',
  'kind': 'feature',
  'sections': [{'heading': "What's New"},
               {'heading': 'Brand Keyword Detection',
                'items': ['<strong>Automatic brand identification</strong> — RespectASO now '
                          'detects brand keywords using two signals: seller-name matching '
                          '(Signal A) and review disparity analysis with same-seller exclusion '
                          '(Signal B)',
                          '<strong>Brand badge</strong> — Brand keywords are clearly labeled '
                          'in search results on both Dashboard and Opportunity pages',
                          '<strong>Smarter difficulty scoring</strong> — Brand keywords '
                          'correctly bypass the Weak Leader Cap and Backfill Discount, since '
                          "brand dominance is expected and shouldn't inflate difficulty",
                          '<strong>Methodology documentation</strong> — Full explanation of '
                          'brand detection added to the Methodology page']},
               {'heading': 'Download Model Recalibration',
                'items': ['<strong>Three-component model</strong> — Downloads = Searches x '
                          'Tap-Through Rate x Conversion Rate, providing more realistic '
                          'estimates',
                          '<strong>Realistic conversion rates</strong> — CVR narrowed to 5–20% '
                          '(previously 35–55%), better reflecting real-world App Store install '
                          'behavior',
                          '<strong>Position-based tap-through</strong> — Position #1 receives '
                          '~30% of taps, dropping to ~1.3% by position #10',
                          '<strong>Market-size scaling</strong> — Download estimates now '
                          'adjust for country market size with ~55 country multipliers (e.g., '
                          'US = 1.0x baseline, smaller markets scale down proportionally)']},
               {'heading': 'Algorithm Improvements',
                'items': ['<strong>Better title matching</strong> — Refactored into a '
                          'dedicated function with exact phrase, all-words, and partial '
                          'overlap detection plus a finance-intent guard to reduce false '
                          'positives',
                          '<strong>7 difficulty sub-scores</strong> — Rating Volume (30%), '
                          'Dominant Players (20%), Rating Velocity (10%), Keyword in Titles '
                          '(10%), Star Ratings (10%), Market Maturity (10%), Publisher '
                          'Diversity (10%)',
                          '<strong>Cleaner download charts</strong> — Removed the "Raw: X → '
                          'adjusted" override note from download estimate displays']},
               {'heading': 'YouTube Channel Visibility',
                'items': ['<strong>Navigation link</strong> — "The Creator Behind" YouTube '
                          'channel link added to the nav bar with a YouTube icon',
                          '<strong>Methodology callout</strong> — New section on the '
                          'Methodology page highlighting the YouTube channel',
                          '<strong>Bolder footer</strong> — YouTube pill in the footer now has '
                          'a red glow for better visibility']},
               {'heading': 'Upgrade',
                'intro': ['``<code>bash docker compose pull &amp;&amp; docker compose up -d '
                          '</code>``']}]},
 {'version': '1.4.1',
  'date': '2026-03-11',
  'title': 'Patch release with tested UX and search improvements.\\n\\n- Footer: YouTube link '
           'ordering/s…',
  'kind': 'patch',
  'sections': [{'heading': '',
                'intro': ['Patch release with tested UX and search improvements.\\n\\n- '
                          'Footer: YouTube link ordering/spacing polish\\n- Dashboard: '
                          'server-side history sorting across full paginated dataset\\n- '
                          'Search: duplicate-today keywords now skip pre-call rate-limit '
                          'delay\\n\\nCredit: rate-limit ordering proposal originally surfaced '
                          'by @4mitabh in PR #2.']}]},
 {'version': '1.4.0',
  'date': '2026-02-27',
  'title': 'Search History Filtering',
  'kind': 'feature',
  'sections': [{'heading': "What's New"},
               {'heading': 'Search History Filtering',
                'items': ['Insight filter: multi-select dropdown to filter by Sweet Spot, Good '
                          'Target, Hidden Gem, etc.',
                          'Popularity and Difficulty threshold dropdowns',
                          'Match counter and Clear All button']},
               {'heading': 'Search Results UX Redesign',
                'items': ['Tabbed view for multi-country search results',
                          'Auto-clear search field, dismiss button, Escape key support',
                          'Streamlined single ad placement (banner only)']},
               {'heading': 'Data Integrity',
                'items': ['One result per keyword per day: refreshing replaces existing entry '
                          'instead of creating duplicates',
                          'Auto-refresh scheduler no longer runs redundant cycles',
                          'Opportunity save preserves historical trend data']},
               {'heading': 'UX Polish',
                'items': ['Global fixed-position tooltip system (no more clipping)',
                          'Filter state resets cleanly on page refresh',
                          'Improved banner copy and CSV attribution']},
               {'heading': 'Bug Fixes',
                'items': ['Fix _needs_refresh_today() always returning true',
                          'Fix search skip check blocking re-searches from previous days',
                          'Fix template syntax error in filter bar',
                          'Fix filter row counter matching nested competitor rows']}]},
 {'version': '1.3.0',
  'date': '2026-02-26',
  'title': 'Country Filter on Search History',
  'kind': 'feature',
  'sections': [{'heading': "What's New"},
               {'heading': 'Country Filter on Search History',
                'intro': ['You can now filter your search history by country using a dropdown '
                          'in the history toolbar.'],
                'items': ['<strong>Country dropdown</strong> appears on the right side of the '
                          'Search History header, alongside Reload, Export CSV, and other '
                          'actions',
                          '<strong>Smart filtering</strong> — only shows countries that '
                          'actually have search results',
                          '<strong>Persistent selection</strong> — your chosen country filter '
                          'is remembered across sessions via localStorage',
                          '<strong>Works with all features</strong> — pagination, CSV export, '
                          'and app filter all respect the active country selection',
                          'Changing the app filter preserves your country selection',
                          'Selecting a country resets pagination to page 1']},
               {'heading': 'How to Update',
                'intro': ['``<code>bash docker compose pull docker compose up -d </code>``']}]},
 {'version': '1.2.1',
  'date': '2026-02-25',
  'title': 'Bug Fix',
  'kind': 'patch',
  'sections': [{'heading': 'Bug Fix',
                'items': ['<strong>Fix table sorting for columns with trend arrows</strong> — '
                          'Sorting the Rank, Popularity, and Difficulty columns now uses the '
                          "raw numeric value instead of parsing the cell's text content. "
                          'Previously, a rank like <code>#4 (↑4)</code> was parsed as '
                          '<code>44</code> instead of <code>4</code>, causing incorrect sort '
                          'order. The same issue affected Popularity and Difficulty columns '
                          'when they had delta indicators.']}]},
 {'version': '1.2.0',
  'date': '2026-02-25',
  'title': 'Trend Chart Improvements',
  'kind': 'feature',
  'sections': [{'heading': "What's New"},
               {'heading': 'Trend Chart Improvements',
                'items': ['Full trend chart with proper Y-axis labels, horizontal grid lines, '
                          'and auto-scaled ranges',
                          'Rank plotted on chart as a secondary Y-axis (right side) with '
                          'inverted scale',
                          'Vertical axis titles: Popularity / Difficulty (left) and Rank '
                          '(right)',
                          'All dates shown on X-axis (up to 10 smart ticks for longer '
                          'histories)',
                          'Bigger chart and history table for better readability',
                          'Sticky table headers with backdrop blur']},
               {'heading': 'UI Polish',
                'items': ['Close button (X) on trend panels instead of auto-close',
                          'Version display in footer next to the GitHub link',
                          'Trend arrow formatting: cleaner format with parentheses',
                          'Zero-delta values no longer show a distracting equals sign',
                          'Fixed alignment of rank, popularity, and difficulty values with '
                          'trend arrows']},
               {'heading': 'Under the Hood',
                'items': ['New context processor exposes VERSION to all templates',
                          'VERSION bumped to 1.2.0']}]},
 {'version': '1.1.0',
  'date': '2026-02-25',
  'title': 'Automatic Version Update Check',
  'kind': 'feature',
  'sections': [{'heading': "What's New"},
               {'heading': 'Automatic Version Update Check',
                'intro': ['RespectASO now automatically checks GitHub for newer releases when '
                          'you open the Dashboard.'],
                'items': ['<strong>Update banner</strong> — If a newer version is available, a '
                          'subtle purple banner appears at the top of the Dashboard showing '
                          'your current version and the latest available version.',
                          '<strong>One-click instructions</strong> — The banner links directly '
                          'to the "Updating to a New Version" section on the Setup page with '
                          'step-by-step commands.',
                          '<strong>Non-intrusive</strong> — The check runs silently on page '
                          'load. If GitHub is unreachable or the check fails, nothing is '
                          'shown. No disruption to normal use.']},
               {'heading': 'Technical Details',
                'items': ['Added <code>VERSION</code> constant to '
                          '<code>core/settings.py</code> for tracking the current version',
                          'Added <code>/version-check/</code> API endpoint that queries the '
                          'GitHub Releases API',
                          'Added <code>#updating</code> anchor to the Setup page for deep '
                          'linking']}]},
 {'version': '1.0.3',
  'date': '2026-02-25',
  'title': 'Bug Fix',
  'kind': 'patch',
  'sections': [{'heading': 'Bug Fix',
                'items': ['<strong>Removed orphaned <code>/history/</code> page</strong> — The '
                          'standalone Search History page was previously merged into the '
                          'Dashboard, but the old URL route, redirect stub, and template were '
                          'never cleaned up. A user navigating to <code>/history/</code> would '
                          'see a stale, incomplete view. This has been fixed by removing the '
                          'dead code entirely. (Fixes #1)']},
               {'heading': 'What changed',
                'items': ['Deleted <code>aso/templates/aso/history.html</code>',
                          'Removed <code>/history/</code> route from <code>aso/urls.py</code>',
                          'Removed <code>history_view</code> redirect stub from '
                          '<code>aso/views.py</code>',
                          'The CSV export (<code>/export/history.csv</code>) is unaffected — '
                          'it continues to work from the Dashboard']},
               {'heading': 'Upgrade',
                'intro': ['Pull the latest image and restart: ``<code>bash docker compose pull '
                          '&amp;&amp; docker compose up -d </code>``']}]},
 {'version': '1.0.2',
  'date': '2026-02-23',
  'title': 'Quick Start and setup fixes',
  'kind': 'patch',
  'sections': [{'heading': 'Fixes',
                'items': ['<strong>Quick Start uses <code>-d</code> flag</strong> — runs in '
                          "background, doesn't block the terminal",
                          '<strong>Added explicit "Open in your browser → http://localhost" '
                          'step</strong> — no guessing needed',
                          '<strong>Update instructions fixed</strong> — now uses proper '
                          '<code>down → build --no-cache → up -d</code> flow (matches the '
                          'in-app setup page)']}]},
 {'version': '1.0.1',
  'date': '2026-02-23',
  'title': 'UX Fix',
  'kind': 'patch',
  'sections': [{'heading': 'UX Fix',
                'items': ['<strong>Quick Start now uses <code>docker compose up</code> '
                          '(foreground)</strong> instead of <code>docker compose up -d</code> '
                          '— the ready message with <code>http://localhost</code> appears '
                          'directly in your terminal',
                          'Simplified startup banner to make the URL unmissable',
                          'Background mode (<code>-d</code>) documented as optional '
                          'alternative']}]},
 {'version': '1.0.0',
  'date': '2026-02-23',
  'title': 'RespectASO v1.0.0 — Initial Release',
  'kind': 'feature',
  'sections': [{'heading': 'RespectASO v1.0.0 — Initial Release',
                'intro': ['<strong>Free, open-source, self-hosted ASO keyword research '
                          'tool.</strong>']},
               {'heading': 'Features',
                'items': ['<strong>Keyword Research</strong> — Search any keyword across 40+ '
                          'App Store countries',
                          '<strong>Popularity Score</strong> (0–100) — 6-signal estimation '
                          "using Apple's Search Ads framework",
                          '<strong>Difficulty Score</strong> (0–100) — 7-factor competitive '
                          'analysis',
                          '<strong>Download Estimates</strong> — 3-stage pipeline: popularity '
                          '→ search volume → per-position downloads',
                          '<strong>Country Opportunity Finder</strong> — Compare keyword '
                          'performance across all supported countries',
                          '<strong>Keyword History</strong> — Save and revisit past research '
                          'with full result snapshots',
                          '<strong>CSV Export</strong> — Export results for external analysis',
                          '<strong>Multi-Country Search</strong> — Analyze keywords in '
                          'multiple markets simultaneously']},
               {'heading': 'Privacy',
                'items': ["<strong>No API keys required</strong> — Uses Apple's public iTunes "
                          'Search API',
                          '<strong>Self-hosted</strong> — All data stays on your machine',
                          '<strong>No tracking</strong> — Zero analytics, no external requests '
                          "beyond Apple's API"]},
               {'heading': 'Quick Start',
                'intro': ['```bash git clone https://github.com/respectlytics/respectaso.git '
                          'cd respectaso docker compose up -d']},
               {'heading': 'Open http://localhost in your browser', 'intro': ['```']},
               {'heading': 'Tech Stack',
                'items': ['Django 5.x + SQLite',
                          'Docker (Python 3.12-slim)',
                          'Tailwind CSS (CDN)']},
               {'heading': 'License', 'intro': ['AGPL-3.0']}]}]


def latest() -> dict:
    """The newest release entry (the running version's notes)."""
    return RELEASES[0]


def _minor(version: str) -> tuple:
    try:
        parts = [int(x) for x in version.split(".")]
        return tuple(parts[:2])
    except (ValueError, AttributeError):
        return ()


def _last_seen_path() -> Path:
    return Path(settings.DATA_DIR) / _LAST_SEEN_FILENAME


def get_last_seen_version() -> str:
    try:
        return _last_seen_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def mark_seen(version: str | None = None) -> None:
    """Record that the user has seen the notes for `version` (default: now)."""
    try:
        _last_seen_path().write_text(
            (version or settings.VERSION) + "\n", encoding="utf-8"
        )
    except OSError as e:  # A failed write must never break a page render.
        logger.debug("Could not persist whats-new state: %s", e)


def should_show_notice() -> bool:
    """One-time "see what's new" notice, tiered by version bump.

    Shows only after an update to a new minor/major version (or when the
    newest entry sets ``notice: True``), and only for EXISTING installs -
    a fresh install has nothing "new" to announce. Patch updates and fresh
    installs are absorbed silently by recording the current version.
    """
    current = settings.VERSION
    last = get_last_seen_version()
    if last == current:
        return False
    if not last:
        # No state yet: an existing install (settings.json present from
        # earlier use) gets the notice for this update; a genuinely fresh
        # install does not.
        if not (Path(settings.DATA_DIR) / "settings.json").exists():
            mark_seen(current)
            return False
        last = "0.0.0"
    entry = latest()
    override = entry.get("notice")
    if override is not None:
        show = bool(override)
    else:
        show = _minor(current) != _minor(last)
    if not show:
        mark_seen(current)
    return show
