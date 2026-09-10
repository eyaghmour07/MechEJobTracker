const STORAGE_KEY = "me-jobs-applied";

const CATEGORIES = [
  ["medical", "Medical Devices"],
  ["automotive", "Automotive & EV"],
  ["aerospace", "Aerospace & Defense"],
  ["energy", "Energy & Industrial"],
  ["robotics", "Robotics & Hardware"],
  ["other", "Other"],
];

const TABS = [
  { level: "intern", geo: "usa", key: "intern-usa" },
  { level: "coop", geo: "usa", key: "coop-usa" },
  { level: "intern", geo: "intl", key: "intern-intl" },
  { level: "coop", geo: "intl", key: "coop-intl" },
];

const SITE_LEVELS = new Set(["intern", "coop"]);

const state = {
  jobs: [],
  updatedAt: "",
  level: "intern",
  geo: "usa",
  query: "",
  applied: new Set(),
};

function normalizeUrl(url) {
  return String(url || "")
    .trim()
    .split("#")[0]
    .split("?")[0]
    .replace(/\/+$/, "")
    .toLowerCase();
}

function loadApplied() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    state.applied = new Set((raw || []).map(normalizeUrl).filter(Boolean));
  } catch {
    state.applied = new Set();
  }
}

function saveApplied() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify([...state.applied]));
}

function isApplied(job) {
  return state.applied.has(normalizeUrl(job.url));
}

function setApplied(job, applied) {
  const key = normalizeUrl(job.url);
  if (applied) state.applied.add(key);
  else state.applied.delete(key);
  saveApplied();
  render();
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function classifySiteLevel(job) {
  const title = job.title || "";
  if (/\bco[-\s]?op\b/i.test(title)) return "coop";
  if (job.level === "coop" || job.level === "intern") return job.level;
  return null;
}

function matchesQuery(job, query) {
  if (!query) return true;
  const hay = `${job.company} ${job.title} ${job.location}`.toLowerCase();
  return hay.includes(query);
}

function jobsForTab(level, geo) {
  return state.jobs.filter((job) => classifySiteLevel(job) === level && job.geo === geo);
}

function updateTabCounts() {
  for (const tab of TABS) {
    const jobs = jobsForTab(tab.level, tab.geo);
    const open = jobs.filter((job) => !isApplied(job)).length;
    const applied = jobs.filter((job) => isApplied(job)).length;
    const el = document.querySelector(`[data-count="${tab.key}"]`);
    if (el) el.textContent = applied ? `${open} · ${applied}` : String(open);
  }
}

function sortJobs(jobs) {
  return [...jobs].sort((a, b) => {
    const pa = a.priority ?? 999;
    const pb = b.priority ?? 999;
    if (pa !== pb) return pa - pb;
    const byName = (a.company || "").localeCompare(b.company || "");
    if (byName) return byName;
    const byTitle = (a.title || "").localeCompare(b.title || "");
    if (byTitle) return byTitle;
    const byAge = (a.age ?? 999) - (b.age ?? 999);
    if (byAge) return byAge;
    return (a.location || "").localeCompare(b.location || "");
  });
}

function groupByCompany(jobs) {
  const groups = [];
  const index = new Map();
  for (const job of sortJobs(jobs)) {
    const name = job.company || "Unknown";
    if (!index.has(name)) {
      index.set(name, groups.length);
      groups.push({
        company: name,
        careers: job.careers_url,
        priority: job.priority ?? 999,
        jobs: [],
      });
    }
    groups[index.get(name)].jobs.push(job);
  }
  return groups;
}

function renderTable(jobs, applied) {
  if (!jobs.length) {
    return `<div class="empty">${applied ? "Nothing applied yet." : "No open roles in this section."}</div>`;
  }
  return groupByCompany(jobs)
    .map((group) => {
      const rows = group.jobs
        .map((job) => {
          const age = job.age == null ? "?" : `${job.age}d`;
          return `<tr class="${applied ? "applied-row" : ""}">
            <td class="check">
              <input type="checkbox" ${applied ? "checked" : ""} data-url="${escapeHtml(job.url)}" aria-label="Mark ${escapeHtml(job.title)} as applied" />
            </td>
            <td class="title">${escapeHtml(job.title)}</td>
            <td class="loc">${escapeHtml(job.location)}</td>
            <td class="posting"><a href="${escapeHtml(job.url)}" target="_blank" rel="noreferrer">${escapeHtml(job.url)}</a></td>
            <td class="age">${escapeHtml(age)}</td>
          </tr>`;
        })
        .join("");
      return `<article class="company-block">
        <h3 class="company-head">
          <a href="${escapeHtml(group.careers)}" target="_blank" rel="noreferrer">${escapeHtml(group.company)}</a>
          <span class="company-count">${group.jobs.length}</span>
        </h3>
        <div class="table-wrap"><table>
          <thead>
            <tr>
              <th class="check"></th>
              <th>Position</th>
              <th>Location</th>
              <th>Application</th>
              <th>Age</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table></div>
      </article>`;
    })
    .join("");
}

function render() {
  const query = state.query.trim().toLowerCase();
  const tabJobs = jobsForTab(state.level, state.geo).filter((job) => matchesQuery(job, query));
  const board = document.getElementById("board");
  const sections = CATEGORIES.map(([id, label]) => {
    const inCat = tabJobs.filter((job) => (job.category || "other") === id);
    const open = inCat.filter((job) => !isApplied(job));
    const applied = inCat.filter((job) => isApplied(job));
    return `<section class="section" id="${id}">
      <h2>${label}</h2>
      ${renderTable(open, false)}
      <h3>Applied</h3>
      ${renderTable(applied, true)}
    </section>`;
  });
  board.innerHTML = sections.join("");
  board.querySelectorAll("input[type=checkbox][data-url]").forEach((box) => {
    box.addEventListener("change", () => {
      setApplied({ url: box.dataset.url }, box.checked);
    });
  });
  updateTabCounts();
}

function bind() {
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.level = btn.dataset.level;
      state.geo = btn.dataset.geo;
      document.querySelectorAll(".tab").forEach((el) => el.classList.toggle("is-active", el === btn));
      render();
    });
  });
  document.getElementById("search").addEventListener("input", (event) => {
    state.query = event.target.value;
    render();
  });
  document.getElementById("export-btn").addEventListener("click", () => {
    const blob = new Blob([JSON.stringify([...state.applied], null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "me-jobs-applied.json";
    a.click();
    URL.revokeObjectURL(a.href);
  });
  document.getElementById("import-file").addEventListener("change", async (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      const urls = Array.isArray(data) ? data : data.urls || data.applications || [];
      for (const item of urls) {
        const url = typeof item === "string" ? item : item && item.url;
        if (url) state.applied.add(normalizeUrl(url));
      }
      saveApplied();
      render();
    } catch {
      window.alert("Could not read that file. Use the Export applied JSON.");
    }
    event.target.value = "";
  });
}

async function init() {
  loadApplied();
  bind();
  const res = await fetch("jobs.json", { cache: "no-store" });
  if (!res.ok) {
    document.getElementById("updated").textContent = "Could not load listings.";
    return;
  }
  const data = await res.json();
  state.jobs = (data.jobs || []).filter((job) => SITE_LEVELS.has(classifySiteLevel(job)));
  state.updatedAt = data.updated_at || "";
  document.getElementById("updated").textContent = state.updatedAt
    ? `Last updated ${state.updatedAt}`
    : "Listings loaded";
  render();
}

init();
