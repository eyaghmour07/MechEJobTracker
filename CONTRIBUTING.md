# Contributing

This list is generated from public career-board APIs. The file you almost always want to edit is [`scripts/companies.yaml`](scripts/companies.yaml).

## Add a company

1. Find the company's careers page and identify the ATS from the URL.
2. Copy an existing YAML block for that ATS and fill in the fields.
3. Set `category` to one of: `medical`, `automotive`, `aerospace`, `energy`, `robotics`, `other`. Medical device employers belong in `medical` (that section is first on every page).
4. Open a pull request.

### Greenhouse

Careers URL looks like `boards.greenhouse.io/{board}` or `job-boards.greenhouse.io/{board}`.

```yaml
- name: Example
  ats: greenhouse
  board: example
  category: medical
  careers_url: https://boards.greenhouse.io/example
```

### Lever

Careers URL looks like `jobs.lever.co/{board}`.

```yaml
- name: Example
  ats: lever
  board: example
  category: automotive
  careers_url: https://jobs.lever.co/example
```

### Ashby

Careers URL looks like `jobs.ashbyhq.com/{board}`.

```yaml
- name: Example
  ats: ashby
  board: example
  category: robotics
  careers_url: https://jobs.ashbyhq.com/example
```

### SmartRecruiters

Careers URL looks like `careers.smartrecruiters.com/{board}`.

```yaml
- name: Example
  ats: smartrecruiters
  board: Example
  category: other
  careers_url: https://careers.smartrecruiters.com/Example
```

### Workday

Take any job posting URL, for example:

`https://cat.wd5.myworkdayjobs.com/en-US/CaterpillarCareers/job/...`

- `host`: `cat.wd5.myworkdayjobs.com`
- `tenant`: `cat` (the subdomain before `.wdN`)
- `site`: `CaterpillarCareers` (the career-site slug; drop `en-US`)

```yaml
- name: Caterpillar
  ats: workday
  host: cat.wd5.myworkdayjobs.com
  tenant: cat
  site: CaterpillarCareers
  category: energy
  careers_url: https://www.caterpillar.com/en/careers.html
```

### Tesla

Tesla uses a custom careers API. There is already a `ats: tesla` entry; you should not need another.

## Local run

```bash
python3 -m pip install -r requirements.txt
python3 scripts/fetch_jobs.py
```

The script rewrites only the `<!-- TABLE_* -->` and count markers in the markdown files. Do not hand-edit those table bodies; they are overwritten on every run.

## Mark a job as applied

Open roles live in each category table. After you apply, the posting should move into that category's **Applied** subsection (✅ instead of the Apply badge). Applied rows stay even if the company takes the posting down.

Pick one:

1. **GitHub Actions** — Actions → *Update job listings* → Run workflow → paste the posting URL into `apply_url`.
2. **YAML** — add the URL to [`scripts/applied.yaml`](scripts/applied.yaml):

```yaml
applications:
  - url: https://jj.wd5.myworkdayjobs.com/en-US/jj/job/...
```

3. **CLI**

```bash
python3 scripts/fetch_jobs.py --applied 'https://...'
```

## What gets listed

Only mechanical / hardware roles (including biomedical, manufacturing, test, thermal, and device design). Software-only, firmware-only, clinical, nursing, and sales jobs are dropped. Internships are split from new-grad / early-career roles. Postings older than 120 days are dropped.
