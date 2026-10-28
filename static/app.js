const state = { reports: [], query: "", supabase: null, adminUser: null };
const $ = (selector, root = document) => root.querySelector(selector);
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]));
const config = window.TERI_SUPABASE || {};
const configured = Boolean(config.url && config.anonKey && window.supabase?.createClient);
if (configured) state.supabase = window.supabase.createClient(config.url, config.anonKey);

const revealObserver = "IntersectionObserver" in window ? new IntersectionObserver(entries => {
  for (const entry of entries) {
    if (entry.isIntersecting) {
      entry.target.classList.add("is-visible");
      revealObserver.unobserve(entry.target);
    }
  }
}, { threshold: 0.12, rootMargin: "0px 0px -36px 0px" }) : null;

function observeReveals(root = document) {
  const targets = root.querySelectorAll("[data-reveal]:not(.is-visible)");
  if (!revealObserver) {
    targets.forEach(target => target.classList.add("is-visible"));
    return;
  }
  targets.forEach(target => revealObserver.observe(target));
}

observeReveals();

function publicPdfUrl(report) {
  return state.supabase.storage.from("reports").getPublicUrl(report.object_path).data.publicUrl;
}

async function loadReports(query = "") {
  state.query = query;
  const grid = $("#report-grid");
  grid.innerHTML = '<div class="loading-state">Searching the report library <span class="spinner" aria-hidden="true"></span></div>';
  if (!configured) {
    grid.innerHTML = '<div class="empty-state"><h3>Reports are temporarily unavailable</h3><p>Please try again later.</p></div>';
    $("#stat-reports").textContent = "—";
    $("#result-count").textContent = "Setup required";
    return;
  }
  try {
    const { data, error } = await state.supabase.from("reports").select("id,filename,title,authors,published,summary,pages,warnings,source_url,object_path,indexed_at").order("published", { ascending: false }).order("title");
    if (error) throw error;
    state.reports = data || [];
    let reports = state.reports;
    if (query) {
      const [searchResult] = await Promise.all([state.supabase.rpc("search_report_chunks", { query_text: query, result_limit: 60 })]);
      if (searchResult.error) throw searchResult.error;
      const ids = [...new Set((searchResult.data || []).map(row => row.report_id))];
      const normalized = query.toLocaleLowerCase();
      const byId = new Map(reports.map(report => [report.id, report]));
      reports = [...ids.map(id => byId.get(id)).filter(Boolean), ...reports.filter(report => !ids.includes(report.id) && [report.title, report.authors, report.summary].some(value => (value || "").toLocaleLowerCase().includes(normalized)))];
    }
    $("#stat-reports").textContent = String(state.reports.length).padStart(2, "0");
    $("#result-count").textContent = query ? `${reports.length} result${reports.length === 1 ? "" : "s"}` : `${reports.length} reports`;
    grid.innerHTML = reports.map((report, index) => `
      <article class="report-card" data-reveal="up" style="--reveal-delay:${(index % 3) * 70}ms">
        <div class="card-meta"><span class="card-type">TERI REPORT</span><span>${escapeHtml(report.published || "Publication date n/a")}</span></div>
        <h3>${escapeHtml(report.title)}</h3>
        <p class="summary">${escapeHtml(report.summary)}</p>
        <div class="card-footer"><span class="page-info">${report.pages} pages${report.authors ? ` · ${escapeHtml(report.authors)}` : ""}</span><button class="card-open" data-open-report="${escapeHtml(report.id)}">View report <span aria-hidden="true">↗</span></button></div>
      </article>`).join("");
    observeReveals(grid);
    const empty = reports.length === 0;
    $("#empty-search").hidden = !empty;
    grid.hidden = empty;
  } catch (error) {
    grid.hidden = false;
    grid.innerHTML = '<div class="empty-state"><h3>Reports are temporarily unavailable</h3><p>Please refresh the page or try again later.</p></div>';
  }
}

async function openReport(id, updateHash = true) {
  try {
    const report = state.reports.find(item => item.id === id) || (await state.supabase.from("reports").select("*").eq("id", id).single()).data;
    if (!report) throw new Error("Report not found.");
    const { data: sections, error } = await state.supabase.from("report_sections").select("name,first_page").eq("report_id", id).order("first_page");
    if (error) throw error;
    const detail = $("#report-detail");
    const authorLine = [report.authors, report.published, `${report.pages} pages`].filter(Boolean).map(escapeHtml).join(" · ");
    const pdfUrl = publicPdfUrl(report);
    const sectionHtml = sections?.length ? `<h3>Report sections</h3><ul class="section-list">${sections.map(s => `<li><a href="${escapeHtml(`${publicPdfUrl(report)}#page=${s.first_page}`)}" target="_blank" rel="noopener">${escapeHtml(s.name)} <span>p. ${s.first_page} ↗</span></a></li>`).join("")}</ul>` : "";
    detail.innerHTML = `<div class="detail-content"><div class="detail-kicker">TERI REPORT · ${escapeHtml(report.published || "DATE NOT LISTED")}</div><h2 id="detail-title">${escapeHtml(report.title)}</h2><p class="detail-meta">${authorLine || "Metadata available in the report PDF"}</p><h3>About this report</h3><p class="detail-summary">${escapeHtml(report.summary)}</p><h3>Read the report</h3><iframe class="report-preview" src="${escapeHtml(pdfUrl)}#view=FitH" title="PDF preview: ${escapeHtml(report.title)}" loading="lazy"></iframe><form class="scoped-ask-form" data-report-id="${escapeHtml(report.id)}"><label for="report-question">Ask about this report</label><textarea id="report-question" name="question" rows="2" maxlength="1200" placeholder="Ask a question about this report’s findings…" required></textarea><div class="ask-form-bottom"><span>Answers use passages from this report only.</span><button class="button button-ink" type="submit">Find an answer</button></div></form><div class="scoped-answer" aria-live="polite"></div><a class="original-link" href="${escapeHtml(pdfUrl)}" target="_blank" rel="noopener">Open PDF in a new tab <span aria-hidden="true">↗</span></a>${sectionHtml}</div>`;
    const dialog = $("#report-dialog");
    if (!dialog.open) dialog.showModal();
    if (updateHash) history.replaceState(null, "", `#report=${encodeURIComponent(id)}`);
  } catch (error) { toast("We couldn't open this report. Please try again."); }
}

$("#report-detail").addEventListener("submit", async event => {
  const form = event.target.closest(".scoped-ask-form");
  if (!form) return;
  event.preventDefault();
  const answer = form.nextElementSibling;
  const question = form.elements.question.value.trim();
  answer.innerHTML = '<p class="ask-loading">Searching this report <span class="spinner" aria-hidden="true"></span></p>';
  try {
    const { data, error } = await state.supabase.from("report_chunks")
      .select("report_id,page,section,content")
      .textSearch("search_vector", question, { config: "simple", type: "websearch" })
      .eq("report_id", form.dataset.reportId)
      .order("page").limit(12);
    if (error) throw error;
    if (!data?.length) {
      answer.innerHTML = '<p class="scoped-empty">No matching passage was found in this report. Try different wording.</p>';
      return;
    }
    const passages = data.map(item => `<article class="evidence-item"><div class="evidence-top"><span class="evidence-title">${escapeHtml(state.reports.find(report => report.id === item.report_id)?.title || "This TERI report")}</span><a class="evidence-page" href="${escapeHtml(`${publicPdfUrl(state.reports.find(report => report.id === item.report_id))}#page=${item.page}`)}" target="_blank" rel="noopener">p. ${item.page} ↗</a></div>${item.section && item.section !== "Report text" ? `<div class="evidence-section">${escapeHtml(item.section)}</div>` : ""}<p>${escapeHtml(item.content)}</p></article>`).join("");
    answer.innerHTML = `<div class="scoped-answer-heading">Relevant passages from this report</div><div class="evidence-list">${passages}</div>`;
  } catch (error) {
    answer.innerHTML = '<p class="ask-error">We couldn’t search this report right now. Please try again.</p>';
  }
});

function renderAnswer(data) {
  const area = $("#answer-area");
  const evidence = data.evidence || [];
  const items = evidence.map(item => {
    const report = state.reports.find(candidate => candidate.id === item.report_id);
    const file = report ? `${publicPdfUrl(report)}#page=${item.page}` : "#";
    return `<article class="evidence-item"><div class="evidence-top"><a class="evidence-title" href="#report=${encodeURIComponent(item.report_id)}" data-open-report="${escapeHtml(item.report_id)}">${escapeHtml(item.title)}</a><a class="evidence-page" href="${escapeHtml(file)}" target="_blank" rel="noopener">p. ${item.page} ↗</a></div>${item.section && item.section !== "Report text" ? `<div class="evidence-section">${escapeHtml(item.section)}</div>` : ""}<p>${escapeHtml(item.content)}</p></article>`;
  }).join("");
  area.innerHTML = `<section class="answer-block"><div class="answer-header"><h3>From the reports</h3></div><p class="answer-copy">${escapeHtml(data.answer)}</p>${evidence.length ? `<div class="evidence-list">${items}</div>` : ""}</section>`;
}

function toast(text) {
  const el = $("#toast"); el.textContent = text; el.classList.add("show");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => el.classList.remove("show"), 3600);
}

$("#report-search").addEventListener("input", event => {
  clearTimeout(loadReports.timer);
  loadReports.timer = setTimeout(() => loadReports(event.target.value.trim()), 260);
});

$("#report-grid").addEventListener("click", event => {
  const button = event.target.closest("[data-open-report]");
  if (button) openReport(button.dataset.openReport);
});

$("#answer-area").addEventListener("click", event => {
  const link = event.target.closest("[data-open-report]");
  if (!link) return;
  event.preventDefault(); openReport(link.dataset.openReport);
});

$("#ask-form").addEventListener("submit", async event => {
  event.preventDefault();
  const question = $("#question").value.trim();
  const area = $("#answer-area");
  area.innerHTML = '<p class="ask-loading">Looking through the reports <span class="spinner" aria-hidden="true"></span></p>';
  try {
    if (!configured) throw new Error("Question search is temporarily unavailable.");
    const titleMatch = state.reports.find(report => question.toLocaleLowerCase().includes(report.title.toLocaleLowerCase()) && /summari[sz]e|overview|about/i.test(question));
    if (titleMatch) {
      const [{ data: sections, error: sectionError }, { data: passages, error: chunkError }] = await Promise.all([
        state.supabase.from("report_sections").select("name,first_page").eq("report_id", titleMatch.id).order("first_page"),
        state.supabase.from("report_chunks").select("report_id,page,section,content").eq("report_id", titleMatch.id).order("page").limit(24)
      ]);
      if (sectionError) throw sectionError;
      if (chunkError) throw chunkError;
      renderAnswer({ mode: "full_summary", answer: `${titleMatch.summary}${sections?.length ? ` The report includes ${sections.map(s => s.name).join(", ")}.` : ""}`, evidence: passages || [], warnings: titleMatch.warnings || [] });
    } else {
      const { data, error } = await state.supabase.rpc("search_report_chunks", { query_text: question, result_limit: 12 });
      if (error) throw error;
      const evidence = (data || []).map(row => ({ ...row, title: state.reports.find(r => r.id === row.report_id)?.title || "TERI report" }));
      renderAnswer({ answer: evidence.length ? "Here are relevant passages from the reports. Select a page link to read more." : "We couldn't find a relevant passage. Try a different question or search the collection.", evidence });
    }
  } catch (error) { area.innerHTML = '<p class="ask-error">We couldn\'t find an answer right now. Please try again later.</p>'; }
});

document.querySelectorAll("[data-question]").forEach(button => button.addEventListener("click", () => {
  $("#question").value = button.dataset.question;
  $("#question").focus();
}));

const reportDialog = $("#report-dialog");
reportDialog.querySelector(".dialog-close").addEventListener("click", () => { reportDialog.close(); history.replaceState(null, "", location.pathname + location.search); });
reportDialog.addEventListener("click", event => { if (event.target === reportDialog) reportDialog.close(); });
reportDialog.addEventListener("close", () => { if (location.hash.startsWith("#report=")) history.replaceState(null, "", location.pathname + location.search); });

const adminDialog = $("#admin-dialog");
$(".header-admin").addEventListener("click", async event => {
  event.preventDefault(); adminDialog.showModal();
  if (configured) await restoreAdminSession();
});
adminDialog.querySelector(".dialog-close").addEventListener("click", () => adminDialog.close());
adminDialog.addEventListener("click", event => { if (event.target === adminDialog) adminDialog.close(); });

async function restoreAdminSession() {
  const { data } = await state.supabase.auth.getSession();
  if (data.session?.user) await authorizeAdmin(data.session.user, false);
}

async function authorizeAdmin(user, announce = true) {
  const { data: allowed, error } = await state.supabase.rpc("is_library_admin");
  if (error || !allowed) {
    await state.supabase.auth.signOut();
    state.adminUser = null;
    $("#admin-upload-area").hidden = true;
    $("#admin-message").textContent = "This account is not authorized to manage the library.";
    $("#admin-message").classList.add("error");
    return false;
  }
  state.adminUser = user;
  $("#admin-upload-area").hidden = false;
  $("#admin-email").value = user.email || "";
  $("#admin-message").textContent = announce ? `Signed in as ${user.email}.` : "Admin session restored.";
  $("#admin-message").classList.remove("error");
  return true;
}

$("#admin-auth-form").addEventListener("submit", async event => {
  event.preventDefault();
  const message = $("#admin-message");
  if (!configured) { message.textContent = "Set up Supabase before signing in."; message.classList.add("error"); return; }
  message.textContent = "Signing in…"; message.classList.remove("error");
  const { data, error } = await state.supabase.auth.signInWithPassword({ email: $("#admin-email").value.trim(), password: $("#admin-password").value });
  if (error) { message.textContent = error.message; message.classList.add("error"); return; }
  await authorizeAdmin(data.user);
});

function safeFilename(name) {
  return name.normalize("NFKC").replace(/[^\p{L}\p{N}._() -]/gu, "_").slice(0, 180) || "report.pdf";
}

function splitText(text, max = 1500, overlap = 180) {
  const chunks = [];
  let start = 0;
  while (start < text.length) {
    let end = Math.min(text.length, start + max);
    if (end < text.length) {
      const boundary = text.lastIndexOf(" ", end);
      if (boundary > start + max * 0.65) end = boundary;
    }
    const part = text.slice(start, end).trim();
    if (part) chunks.push(part);
    if (end >= text.length) break;
    start = Math.max(start + 1, end - overlap);
  }
  return chunks;
}

function looksLikeHeading(line) {
  const clean = line.trim();
  return clean.length >= 4 && clean.length <= 100 && clean.split(/\s+/).length <= 13 && (/^(chapter\s+\d+|\d+(?:\.\d+)*\s+[A-Z]|appendix|annexure)/i.test(clean) || (clean === clean.toLocaleUpperCase() && /[A-Z]/.test(clean)));
}

function textLines(items) {
  const lines = [];
  for (const item of items) {
    const value = (item.str || "").trim();
    if (!value) continue;
    const x = item.transform?.[4] || 0, y = item.transform?.[5] || 0;
    let line = lines.find(candidate => Math.abs(candidate.y - y) < 3.5);
    if (!line) { line = { y, parts: [] }; lines.push(line); }
    line.parts.push({ x, value });
  }
  return lines.sort((a, b) => b.y - a.y)
    .map(line => line.parts.sort((a, b) => a.x - b.x).map(part => part.value).join(" ").trim())
    .filter(Boolean).join("\n");
}

async function extractPdf(file) {
  if (!window.pdfjsLib) throw new Error("The PDF text extraction library could not be loaded. Check the network and reload.");
  pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
  const pdf = await pdfjsLib.getDocument({ data: new Uint8Array(await file.arrayBuffer()) }).promise;
  const meta = await pdf.getMetadata().catch(() => ({ info: {} }));
  let section = "Report text", fullText = "", sparse = [], sections = new Map(), chunks = [];
  for (let pageNumber = 1; pageNumber <= pdf.numPages; pageNumber++) {
    const page = await pdf.getPage(pageNumber);
    const content = await page.getTextContent();
    const text = textLines(content.items).replace(/[ \t]+/g, " ").trim();
    if (text.length < 40) sparse.push(pageNumber);
    if (!fullText && text) fullText = text;
    for (const line of text.split("\n")) {
      if (looksLikeHeading(line)) {
        section = line.trim().slice(0, 100);
        sections.set(`${section}\u0000${pageNumber}`, { report_id: "", name: section, first_page: pageNumber });
      }
    }
    const pieces = splitText(text);
    for (const piece of pieces) chunks.push({ report_id: "", page: pageNumber, section, content: piece });
    if (pageNumber <= 3 && text) fullText += ` ${text}`;
  }
  const info = meta.info || {};
  const title = (info.Title || "").trim() || file.name.replace(/\.pdf$/i, "").replace(/[._-]+/g, " ");
  const authoredDate = (info.CreationDate || "").match(/D:(\d{4})/);
  const published = authoredDate?.[1] || "";
  const warnings = sparse.length ? [`Little or no selectable text was extracted from page(s) ${sparse.slice(0, 16).join(", ")}${sparse.length > 16 ? " …" : ""}. Scanned pages may need OCR; diagrams and complex tables may be incomplete.`] : [];
  const summary = fullText.replace(/\s+/g, " ").trim().slice(0, 620) || "No selectable text was extracted from this PDF.";
  return { title, authors: (info.Author || "").trim().slice(0, 200), published, summary, pages: pdf.numPages, warnings, sections: [...sections.values()], chunks };
}

$("#upload-form").addEventListener("submit", async event => {
  event.preventDefault();
  const fileInput = $("#upload-file"), message = $("#admin-message"), file = fileInput.files[0];
  if (!file) return;
  if (file.size > 50_000_000) { message.textContent = "This Supabase bucket accepts PDFs up to 50 MB."; message.classList.add("error"); return; }
  if (!file.name.toLowerCase().endsWith(".pdf")) { message.textContent = "Choose a PDF file."; message.classList.add("error"); return; }
  let reportId = crypto.randomUUID(), objectPath = `${reportId}/${safeFilename(file.name)}`, recordCreated = false;
  message.textContent = "Extracting PDF text in this browser…"; message.classList.remove("error");
  try {
    const extracted = await extractPdf(file);
    const { error: uploadError } = await state.supabase.storage.from("reports").upload(objectPath, file, { contentType: "application/pdf", upsert: false });
    if (uploadError) throw uploadError;
    const sections = extracted.sections.map(section => ({ ...section, report_id: reportId }));
    const chunks = extracted.chunks.map(chunk => ({ ...chunk, report_id: reportId }));
    const report = { id: reportId, filename: safeFilename(file.name), title: extracted.title, authors: extracted.authors, published: extracted.published, summary: extracted.summary, pages: extracted.pages, warnings: extracted.warnings, source_url: "", object_path: objectPath };
    let result = await state.supabase.from("reports").insert(report);
    if (result.error) throw result.error;
    recordCreated = true;
    for (let start = 0; start < sections.length; start += 200) {
      result = await state.supabase.from("report_sections").insert(sections.slice(start, start + 200));
      if (result.error) throw result.error;
    }
    for (let start = 0; start < chunks.length; start += 200) {
      result = await state.supabase.from("report_chunks").insert(chunks.slice(start, start + 200));
      if (result.error) throw result.error;
      message.textContent = `Indexing report text… ${Math.min(start + 200, chunks.length)} / ${chunks.length} passages`;
    }
    message.textContent = `Added “${report.title}” · ${report.pages} pages.${report.warnings.length ? ` Content note: ${report.warnings.join(" ")}` : ""}`;
    fileInput.value = "";
    await loadReports($("#report-search").value.trim());
  } catch (error) {
    if (recordCreated) await state.supabase.from("reports").delete().eq("id", reportId);
    await state.supabase.storage.from("reports").remove([objectPath]);
    message.textContent = error.message || "Upload or indexing failed."; message.classList.add("error");
  }
});

$("#admin-signout").addEventListener("click", async () => {
  if (configured) await state.supabase.auth.signOut();
  state.adminUser = null;
  $("#admin-upload-area").hidden = true;
  $("#admin-password").value = "";
  $("#admin-message").textContent = "Signed out.";
});

document.addEventListener("keydown", event => {
  if (event.key === "/" && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName) && !reportDialog.open && !adminDialog.open) { event.preventDefault(); $("#report-search").focus(); }
  if (event.key === "Escape" && location.hash.startsWith("#report=")) history.replaceState(null, "", location.pathname + location.search);
});

document.querySelectorAll("[data-nav]").forEach(link => link.addEventListener("click", () => {
  document.querySelectorAll("[data-nav]").forEach(item => item.classList.toggle("active", item === link));
}));

const siteHeader = $(".site-header");
let scrollUpdatePending = false;
window.addEventListener("scroll", () => {
  if (scrollUpdatePending) return;
  scrollUpdatePending = true;
  requestAnimationFrame(() => {
    siteHeader.classList.toggle("scrolled", window.scrollY > 24);
    scrollUpdatePending = false;
  });
}, { passive: true });

if ("IntersectionObserver" in window) {
  const sectionObserver = new IntersectionObserver(entries => {
    const visible = entries.filter(entry => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (!visible) return;
    const activeId = visible.target.id;
    document.querySelectorAll("[data-nav]").forEach(link => link.classList.toggle("active", link.dataset.nav === activeId));
  }, { rootMargin: "-28% 0px -58% 0px", threshold: [0, .15, .35] });
  document.querySelectorAll("#library, #ask").forEach(section => sectionObserver.observe(section));
}

loadReports().then(() => {
  const match = location.hash.match(/^#report=(.+)$/);
  if (match && configured) openReport(decodeURIComponent(match[1]), false);
});
