# TERI Reports Library

A public site deployed with GitHub Pages. Supabase stores report PDFs and searchable report text and runs an Edge Function for report-grounded explanations. The function retrieves relevant page passages, sends only those passages and the question to the Gemini API, and returns an explanation with source-page citations. Admins sign in with Supabase Auth before uploading. PDF text extraction for new uploads happens in the admin's browser.

## Project layout

- `static/` — GitHub Pages site.
- `supabase/schema.sql` — database tables, search function, Row Level Security policies, and the public PDF bucket.
- `supabase/migrations/20261028_report_grounded_ai.sql` — scoped passage search and private hourly AI request limits.
- `supabase/functions/report-answer/` — server-side retrieval and grounded explanation endpoint.
- `scripts/sync_supabase.py` — one-time import of the existing local PDF collection and SQLite index.
- `scripts/reindex_reports.py` — rebuilds searchable passages from the original local PDFs.
- `data/reports/` — local source copies for migration; `.gitignore` keeps PDFs out of GitHub.
- `server.py` — original local indexer and development server, used to generate the SQLite index for the one-time import.
- `.github/workflows/pages.yml` — deploys the static site after pushes to `main`.

## 1. Create the Supabase project

1. Create a Supabase project.
2. In **SQL Editor**, run all of `supabase/schema.sql`.
3. In **Authentication**, create the account(s) that can manage uploads. Disable public sign-ups if only invited admins should have accounts.
4. In SQL Editor, authorize each admin account by its Auth user ID:

   ```sql
   insert into public.library_admins (user_id)
   values ('SUPABASE_AUTH_USER_UUID');
   ```

The schema creates a public `reports` Storage bucket for public PDFs. The policies allow anonymous visitors to read reports and searchable text, and restrict report, text, and PDF changes to listed admin accounts. The bucket limit is 50 MiB per PDF. Keep the service-role key private; it is only used by the migration script and must never be added to the site configuration.

After the first GitHub Pages deployment, add the site URL shown by GitHub under **Authentication → URL Configuration → Redirect URLs**. Set the Supabase Site URL to that same URL.

## 2. Move the current library into Supabase

Before migrating, make sure this local folder contains the generated `data/library.sqlite3` and the PDF copies in `data/reports/`. These source files are ignored by `.gitignore` and are not published to GitHub. If regenerating the index, install Python 3.10+ and run `start.ps1` once; stop the local server after it finishes indexing. Then run the migration from PowerShell in this folder:

```powershell
$env:SUPABASE_URL = 'https://YOUR_PROJECT_REF.supabase.co'
python scripts/sync_supabase.py
```

The script prompts for the Supabase service-role key without echoing it. Enter the key directly at that prompt; do not put it in a command, paste it into chat, commit it, add it to `static/config.js`, or save it as a GitHub variable. The script uploads the PDF files and copies report metadata, sections and page passages from SQLite. Verified cover-page titles and authors in `scripts/metadata_overrides.json` take precedence over embedded PDF metadata. Run `python scripts/apply_metadata_overrides.py` to apply those corrections to the local index; the sync script also applies them when publishing metadata to Supabase. It is safe to rerun: it replaces the stored object and refreshes passages for each report.

To rebuild the searchable index from PDFs already in `data/reports/` without uploading the PDFs again, run `python scripts/reindex_reports.py` and then `python scripts/sync_supabase.py --index-only`. This re-index pass uses plain PDF text extraction, removes common cover/contents noise, and clears automatically generated summaries. The public interface does not show unverified report summaries.

## 3. Deploy the site with GitHub Pages

1. Create a GitHub repository and push this project to its `main` branch. The report PDFs and local SQLite database are ignored by `.gitignore`; do not force-add them.
2. Put the Supabase project URL and public anon/publishable key in `static/config.js`. This browser-facing key is public by design; the schema's RLS and Storage policies restrict changes to authorized admins. Never put the service-role key in this file.
3. Open **Settings → Pages** and choose **GitHub Actions** as the build and deployment source.
4. Push to `main` or run **Deploy TERI Reports Library** from the Actions tab. GitHub Pages will show the public site URL in the deployment result. For a project repository it is usually `https://YOUR_GITHUB_NAME.github.io/REPOSITORY_NAME/`.

The workflow copies `static/config.js` into the published site. The Supabase anon key is public by design; database and Storage policies enforce read-only public access and admin-only changes.

## Report-grounded explanations

1. In Supabase **SQL Editor**, run `supabase/migrations/20261028_report_grounded_ai.sql` once. This adds a report-filtered search function and a private per-IP quota table/function. The Edge Function hashes the client IP before storing it and allows up to 12 explanation requests per IP per hour.
2. Create a Gemini API key in [Google AI Studio](https://aistudio.google.com/apikey). In the Supabase project dashboard, open **Edge Functions → Secrets** and add `GEMINI_API_KEY`. Do not put this key in GitHub, `static/config.js`, or chat. Optionally set `GEMINI_MODEL`; the default is `gemini-3.8-flash`.
3. From this repository, install the Supabase CLI and link it to project `rgrzlzvhbxywbuvifrfi`, then deploy the function:

   ```powershell
   supabase link --project-ref rgrzlzvhbxywbuvifrfi
   supabase functions deploy report-answer
   ```

   The function configuration disables the gateway's legacy JWT check for this public endpoint; the handler restricts browser origins, checks the project's publishable key, and enforces its own hourly quota. Keep the database migration and function deployed before publishing the frontend change.

Each answer retrieves at most 10 matching passages, uses only those as model context, and cites the source labels with links to the cited PDF pages. It returns a no-evidence message instead of calling the model when search finds nothing. Report-level questions are filtered to that report in the database. This is retrieval at question time, not model training or persistent learning. Relevant report excerpts and the question are sent to Google Gemini for each answer. Scanned/image-only pages remain outside the text index. Gemini API usage may be billable; the per-IP limit is a basic abuse guard, so set any available project usage limits in Google AI Studio as well. Google documents that free-tier inputs may be used to improve its products, while paid-tier inputs are not; choose a paid tier if you don't want requests used for product improvement. See [Gemini API pricing and data-use terms](https://ai.google.dev/gemini-api/docs/pricing).

## Admin uploads

Use **Admin** on the deployed site and sign in with an account listed in `public.library_admins`. New PDFs are limited to 50 MiB by the bucket. The browser extracts text page by page, uploads the unchanged PDF to Storage, and saves searchable passages to Postgres. PDF uploads are not automatically sent to Gemini; only passages retrieved for a visitor's explanation request are sent. Scanned pages without selectable text, charts and complex tables may not be fully searchable; OCR is not included.

## Search and answers

Search uses PostgreSQL full-text search over page passages. Question forms return a concise model-written explanation constrained to the retrieved report text, followed by the exact supporting passages and page links. Model citations link to those pages. The explanation may still omit nuance or misinterpret extraction, so readers should open cited PDF pages for important claims. Report PDFs remain intact.

## Local indexer API

The original Python server remains available for local indexing and the initial migration. The public site itself is static and uses Supabase directly; the Python server is not required after the collection has been synced.
