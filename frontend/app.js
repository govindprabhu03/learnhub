"use strict";
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const md = s => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`(.+?)`/g, "<code>$1</code>")
  .replace(/^\s*[-*] (.+)$/gm, "<li>$1</li>").replace(/(<li>.*<\/li>\n?)+/g, m => `<ul>${m}</ul>`)
  .replace(/\n/g, "<br>").replace(/<\/li><br>/g, "</li>").replace(/<ul><br>/g, "<ul>").replace(/<\/ul><br>/g, "</ul>");
const bar = (p, cls = "") => `<div class="bar ${cls}"><i style="width:${Math.max(0, Math.min(100, p || 0))}%"></i></div>`;
const endT = (s, m) => { const t = +s.slice(0, 2) * 60 + +s.slice(3) + m; return String(Math.floor(t / 60) % 24).padStart(2, "0") + ":" + String(t % 60).padStart(2, "0"); };
const isoDay = off => new Date(Date.now() + off * 864e5).toLocaleDateString("en-CA");
const tone = a => (a >= 80 ? "good" : a >= 60 ? "warn" : "bad");
const S = { token: localStorage.getItem("lh_token"), me: null, subjects: [], poll: 0 };

function toast(m) { const t = $("#toast"); t.textContent = m; t.classList.add("on"); setTimeout(() => t.classList.remove("on"), 2600); }

async function api(path, opts = {}) {
  const isForm = opts.body instanceof FormData;
  const r = await fetch(path, {
    method: opts.method || "GET",
    headers: { ...(opts.body && !isForm ? { "Content-Type": "application/json" } : {}), ...(S.token ? { Authorization: "Bearer " + S.token } : {}) },
    body: opts.body && !isForm ? JSON.stringify(opts.body) : opts.body,
  });
  if (r.status === 401 && S.token) { logout(); throw new Error("Session expired"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : data.detail?.[0]?.msg || "Request failed");
  return data;
}
const post = (path, body) => api(path, { method: "POST", body });

function logout() { localStorage.removeItem("lh_token"); S.token = null; S.me = null; location.hash = ""; route(); }

// ---------- auth ----------
function setSession(r) { localStorage.setItem("lh_token", r.token); S.token = r.token; S.me = null; $("#root").innerHTML = ""; location.hash = ""; route(); }

// modes: login | register | forgot | reset (needs opts.token)
async function authView(mode = "login", note = "", opts = {}) {
  S.cfg ||= await api("/api/auth/config").catch(() => ({}));
  const social = (mode === "login" || mode === "register") && S.cfg.google;
  const title = { login: "Log in", register: "Create account", forgot: "Send reset link", reset: "Set new password" }[mode];
  $("#root").innerHTML = `<div class="auth"><div class="card"><div class="logo">🎓 Learn<span>Hub</span></div>
    <div class="muted">${mode === "forgot" ? "Enter your email and we'll send you a link to reset your password." : mode === "reset" ? "Choose a new password for your account." : "Turn your study material into an adaptive learning system."}</div>
    ${note ? `<div class="notice">${esc(note)}</div>` : ""}
    ${social ? `<a class="btn ghost gbtn" href="/api/auth/google/start"><svg width="18" height="18" viewBox="0 0 48 48" style="vertical-align:-3px;margin-right:8px"><path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.5 5.4 2.6 13.2l7.9 6.1C12.4 13.5 17.7 9.5 24 9.5z"/><path fill="#4285F4" d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.7c-.6 3-2.2 5.5-4.7 7.2l7.6 5.9c4.4-4.1 6.9-10.2 6.9-17.6z"/><path fill="#FBBC05" d="M10.5 28.7a14.5 14.5 0 0 1 0-9.4l-7.9-6.1a24 24 0 0 0 0 21.6l7.9-6.1z"/><path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.6-5.9c-2.1 1.4-4.9 2.3-8.3 2.3-6.3 0-11.6-4-13.5-9.8l-7.9 6.1C6.5 42.6 14.6 48 24 48z"/></svg>Continue with Google</a><div class="or">or use email</div>` : ""}
    <form id="af">${mode === "register" ? '<input name="name" placeholder="Your name" required maxlength="60">' : ""}
    ${mode !== "reset" ? '<input name="email" type="email" placeholder="Email" required autocomplete="email">' : ""}
    ${mode === "login" || mode === "register" || mode === "reset" ? `<input name="password" type="password" placeholder="${mode === "login" ? "Password" : "Password (min 8 chars)"}" required minlength="${mode === "login" ? 1 : 8}" autocomplete="${mode === "login" ? "current-password" : "new-password"}">` : ""}
    <div class="err" id="ae"></div><button class="btn">${title}</button></form>
    <p class="small muted" style="margin:14px 0 0">${mode === "login" ? '<a href="#" id="fg">Forgot password?</a> · ' : ""}${mode === "register" ? "Have an account?" : mode === "login" ? "New here?" : "Back to"}
      <a href="#" id="sw">${mode === "register" || mode === "forgot" || mode === "reset" ? "Log in" : "Create an account"}</a></p></div></div>`;
  $("#sw").onclick = e => { e.preventDefault(); authView(mode === "login" ? "register" : "login"); };
  if ($("#fg")) $("#fg").onclick = e => { e.preventDefault(); authView("forgot"); };
  $("#af").onsubmit = async e => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target)), err = $("#ae"); err.textContent = "";
    try {
      if (mode === "forgot") { await post("/api/auth/forgot", f); return authView("login", "If that email has an account, a reset link is on its way."); }
      if (mode === "reset") return setSession(await post("/api/auth/reset", { token: opts.token, password: f.password }));
      const r = await post("/api/auth/" + mode, f);
      if (r.verify) return authView("login", "Account created. Open the link we emailed you to verify your address, then log in.");
      setSession(r);
    } catch (x) {
      err.textContent = x.message;
      if (mode === "login" && /verify/i.test(x.message)) {
        err.innerHTML = `${esc(x.message)} <a href="#" id="rs">Resend link</a>`;
        $("#rs").onclick = async ev => { ev.preventDefault(); await post("/api/auth/resend", { email: f.email }).catch(() => {}); toast("If the account exists, a new link was sent"); };
      }
    }
  };
}

// links from emails and the Google redirect: #/verify?token=…  #/reset?token=…  #/signin?token=…|error=…
async function emailLink(kind, q) {
  history.replaceState(null, "", location.pathname);  // drop the token from the address bar and history
  const token = q.get("token");
  if (kind === "reset") return authView("reset", "", { token });
  if (kind === "signin") return token ? setSession({ token }) : authView("login", q.get("error") || "Sign-in failed");
  try { setSession(await post("/api/auth/verify", { token })); toast("Email verified - welcome!"); }
  catch (x) { authView("login", x.message); }
}

// ---------- shell + routing ----------
function shell() {
  if (!$("#main")) {
    $("#root").innerHTML = `<div class="layout"><aside><div class="logo">🎓 Learn<span>Hub</span></div><nav class="nav" id="nav"></nav>
      <div class="side-foot"><div><b>${esc(S.me.name)}</b></div><div style="margin:4px 0 8px"><span class="pill ${S.me.ai ? "" : "off"}">${S.me.ai ? "AI: Claude" : "Offline mode"}</span> <span class="pill ${S.me.semantic ? "" : "off"}" title="How your material is searched">${S.me.semantic ? "Semantic search" : "Keyword search"}</span></div>
      ${S.me.verified ? "" : '<div class="small" style="color:var(--warn);margin-bottom:6px">Email not verified · <a href="#" id="rv">Resend link</a></div>'}
      <a href="#" id="lo">Log out</a></div></aside><main id="main"></main></div>`;
    $("#lo").onclick = e => { e.preventDefault(); logout(); };
    if ($("#rv")) $("#rv").onclick = async e => { e.preventDefault(); await post("/api/auth/resend", { email: S.me.email }).catch(() => {}); toast("Verification link sent - check your inbox"); };
  }
  const cur = location.hash.split("/")[2];
  $("#nav").innerHTML = `<a href="#/" class="${location.hash.startsWith("#/s/") ? "" : "on"}">🏠 Dashboard</a>
    <div class="navh">Subjects <button id="ns" title="New subject">+</button></div>
    ${S.subjects.map(s => `<a href="#/s/${s.id}" class="${String(s.id) === cur ? "on" : ""}">${esc(s.emoji)} ${esc(s.name)}</a>`).join("") || '<div class="small muted" style="padding:6px 10px">No subjects yet</div>'}`;
  $("#ns").onclick = newSubject;
}

async function route() {
  clearTimeout(S.poll);
  const link = location.hash.match(/^#\/(verify|reset|signin)\?(.*)$/);
  if (link) return emailLink(link[1], new URLSearchParams(link[2]));
  if (!S.token) return authView();
  try {
    if (!S.me) S.me = await api("/api/me");
    S.subjects = await api("/api/subjects");
    shell();
    const [, page, id, tab] = location.hash.split("/");
    const main = $("#main");
    if (page === "s") await subjectView(main, +id, tab || "materials"); else await dashboardView(main);
  } catch (e) { if (S.token) toast(e.message); }
}
window.addEventListener("hashchange", route);

function newSubject() {
  const em = ["📘", "📗", "📙", "📕", "📓", "💻", "🧪", "📐", "🌍", "⚡"]; let pick = em[0];
  const d = $("#dlg");
  d.innerHTML = `<h3>New subject</h3><input id="sn" placeholder="e.g. Artificial Intelligence" maxlength="80" style="width:100%">
    <div class="emojis">${em.map((e, i) => `<button type="button" class="${i ? "" : "on"}">${e}</button>`).join("")}</div>
    <div class="row" style="justify-content:flex-end"><button class="btn ghost" id="cx">Cancel</button><button class="btn" id="ok">Create</button></div>`;
  d.querySelectorAll(".emojis button").forEach(b => b.onclick = () => { d.querySelectorAll(".emojis button").forEach(x => x.classList.remove("on")); b.classList.add("on"); pick = b.textContent; });
  $("#cx").onclick = () => d.close();
  $("#ok").onclick = async () => {
    const name = $("#sn").value.trim(); if (!name) return;
    const r = await post("/api/subjects", { name, emoji: pick }); d.close(); location.hash = `#/s/${r.id}`;
  };
  d.showModal(); $("#sn").focus();
}

// ---------- dashboard ----------
async function dashboardView(el) {
  const d = await api("/api/dashboard"), h = new Date().getHours();
  const g = d.goal, max = Math.max(1, ...d.weekly.map(w => w.count));
  el.innerHTML = `<h1>Good ${h < 12 ? "morning" : h < 18 ? "afternoon" : "evening"}, ${esc(S.me.name)} 👋</h1><div class="muted">Here's where your learning stands today.</div>
  <div class="grid g3">
    <div class="card"><div class="kpi">🔥 ${d.streak}</div><div class="muted">day streak</div></div>
    <div class="card"><div class="row"><b>Today's goal</b><span class="muted small">${g.done}/${g.target} questions</span></div>${bar(100 * g.done / g.target, g.done >= g.target ? "good" : "")}<div class="small muted">${g.done >= g.target ? "Goal complete 🎉" : `${g.target - g.done} to go`}</div></div>
    <div class="card"><div class="kpi">${d.overall_accuracy ?? "–"}${d.overall_accuracy != null ? "%" : ""}</div><div class="muted">overall quiz accuracy</div></div>
  </div>
  <div class="grid g2">
    <div class="card"><h3>Your subjects</h3>${d.subjects.map(s => `<a href="#/s/${s.id}" style="color:inherit;display:block;margin-bottom:12px">
      <div class="row"><b>${esc(s.emoji)} ${esc(s.name)}</b><span class="muted small">${s.accuracy != null ? s.accuracy + "%" : "not started"}</span></div>${bar(s.accuracy, s.accuracy != null ? tone(s.accuracy) : "")}
      <div class="small muted">${s.docs} document${s.docs === 1 ? "" : "s"}${s.due_cards ? ` · ${s.due_cards} cards due` : ""}</div></a>`).join("") || '<div class="empty"><div class="big">📚</div>Create your first subject with the + in the sidebar.</div>'}</div>
    <div class="card"><h3>Weak topics</h3>${d.weak_topics.map(w => `<div class="row" style="margin-bottom:8px"><span>${esc(w.topic)} <span class="muted small">· ${esc(w.subject)}</span></span><span class="chip weak">${w.accuracy}%</span></div>`).join("") || '<div class="muted">None detected yet — take a few quizzes and LearnHub will find them.</div>'}</div>
  </div>
  <div class="grid g2">
    <div class="card"><h3>Weekly activity</h3><div class="cols">${d.weekly.map(w => `<div><i style="height:${Math.round(w.count / max * 90)}px" title="${w.count}"></i>${w.day}</div>`).join("")}</div></div>
    <div class="card"><h3>Quiz accuracy trend</h3>${d.trend.map(t => `<div class="row small"><span>${t.label}</span><b>${t.accuracy ?? "–"}${t.accuracy != null ? "%" : ""}</b></div>${bar(t.accuracy, t.accuracy != null ? tone(t.accuracy) : "")}`).join("")}</div>
  </div>
  ${d.today_tasks.length ? `<div class="card" style="margin-top:16px"><h3>Today's study plan</h3>${d.today_tasks.map(t => `<div class="task"><input type="checkbox" data-tt="${t.id}" ${t.done ? "checked" : ""}>
    <span class="tm">${t.start}–${endT(t.start, t.minutes)}</span><span class="tl ${t.done ? "dn" : ""}">${esc(t.label)}</span><a class="muted small" href="#/s/${t.subject_id}/planner">${esc(t.subject)}</a></div>`).join("")}</div>` : ""}
  ${d.recommendations.length ? `<div class="card" style="margin-top:16px"><h3>Recommended next</h3>${d.recommendations.map(recHtml).join("")}</div>` : ""}
  <div class="card" style="margin-top:16px"><h3>Recent documents</h3>${d.recent_documents.map(x => `<div class="row" style="margin-bottom:6px"><a href="#/s/${x.subject_id}">📄 ${esc(x.filename)}</a><span class="muted small">${esc(x.subject)} · ${esc(x.status)}</span></div>`).join("") || '<div class="muted">No documents yet.</div>'}</div>`;
  bindRecs(el);
  el.querySelectorAll("[data-tt]").forEach(cb => cb.onchange = async () => { await post(`/api/plans/tasks/${cb.dataset.tt}/toggle`); dashboardView(el); });
}

function recHtml(r) {
  return `<div class="rec"><div class="row"><b>${esc(r.topic)}</b><span class="chip weak">${r.accuracy}% accuracy</span></div>
    <ol>${r.steps.map((s, i) => `<li><button class="chip btnlike" data-act="${s.action}" data-topic="${esc(r.topic)}" data-sid="${r.subject_id}">${i + 1}. ${esc(s.label)}</button></li>`).join("")}</ol></div>`;
}
function bindRecs(el) {
  el.querySelectorAll("[data-act]").forEach(b => b.onclick = async () => {
    const { act, topic, sid } = b.dataset;
    if (act === "tutor") { S.prefill = `Explain "${topic}" in simple terms`; location.hash = `#/s/${sid}/tutor`; }
    else if (act === "cards") location.hash = `#/s/${sid}/cards`;
    else if (act === "quiz") location.hash = `#/s/${sid}/quiz`;
    else {
      b.disabled = true; toast("Writing practice questions…");
      try { await post("/api/quiz/generate", { subject_id: +sid, topic, n: 8 }); S.autoStart = topic; location.hash = `#/s/${sid}/quiz`; }
      catch (e) { toast(e.message); b.disabled = false; }
    }
  });
}

// ---------- subject ----------
async function subjectView(el, sid, tab) {
  let d;
  try { d = await api("/api/subjects/" + sid); } catch { location.hash = "#/"; return; }
  const tabs = [["materials", "📄 Materials"], ["tutor", "🤖 Tutor"], ["quiz", "🧠 Quiz"], ["cards", "🎴 Flashcards"], ["progress", "📊 Progress"], ["planner", "🗓️ Planner"]];
  el.innerHTML = `<div class="head"><div><h1>${esc(d.subject.emoji)} ${esc(d.subject.name)}</h1><div class="muted small">${d.documents.length} documents · ${d.questions} questions · ${d.cards} flashcards</div></div>
    <button class="btn ghost danger sm" id="ds">Delete subject</button></div>
    <nav class="tabs">${tabs.map(([k, l]) => `<a href="#/s/${sid}/${k}" class="${k === tab ? "on" : ""}">${l}</a>`).join("")}</nav><div id="tab"></div>`;
  $("#ds").onclick = async () => { if (confirm("Delete this subject and everything in it?")) { await api("/api/subjects/" + sid, { method: "DELETE" }); location.hash = "#/"; } };
  await ({ materials, tutor, quiz, cards, progress, planner }[tab] || materials)($("#tab"), d, sid);
}

function docHtml(x) {
  const st = x.status === "processing" ? `<span class="muted small"><span class="spin"></span>${x.note ? esc(x.note) : "Processing…"}</span>`
    : x.status === "failed" ? '<span class="chip weak">Failed</span>' : '<span class="chip strong">Ready</span>';
  return `<details class="card doc" ${x.status === "ready" && x.__open ? "open" : ""}><summary><span>📄 ${esc(x.filename)} <span class="muted small">${x.pages ? x.pages + " pages" : ""}</span></span>${st}</summary>
    <div class="body">${x.status === "failed" ? `<div class="err">${esc(x.error)}</div>` : x.status === "processing" ? `<div class="muted">${esc(x.note || "Extracting text, summarising and generating study material…")}</div>` : `
    ${x.note ? `<div class="notice" style="margin:0">ℹ️ ${esc(x.note)}</div>` : ""}
    <div><b>Summary</b><div>${md(x.summary)}</div></div>
    <div><b>Key concepts</b><div>${x.concepts.map(c => `<span class="chip">${esc(c)}</span>`).join("")}</div></div>
    <div><b>Important questions</b><ol>${x.important_qs.map(q => `<li>${esc(q)}</li>`).join("")}</ol></div>`}
    <div><button class="btn ghost danger sm" data-del="${x.id}">Delete document</button></div></div></details>`;
}

async function materials(el, d, sid) {
  el.innerHTML = `<label class="drop" id="drop"><input type="file" id="fi" accept="application/pdf" multiple hidden>
    <div style="font-size:30px">📤</div><b>Drop PDFs here or click to upload</b><div class="small">Up to 25 MB each. Text is extracted, summarised and turned into a quiz and flashcards.</div></label><div id="docs"></div>`;
  const list = $("#docs");
  const draw = docs => {
    list.innerHTML = docs.map(docHtml).join("") || '<div class="empty"><div class="big">📄</div>No documents yet — upload your first PDF.</div>';
    list.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => { if (confirm("Delete this document and its quiz/flashcards?")) { await api("/api/documents/" + b.dataset.del, { method: "DELETE" }); route(); } });
  };
  draw(d.documents);
  const poll = async () => {
    const cur = await api("/api/subjects/" + sid);
    if (!$("#docs")) return;
    if (cur.documents.some(x => x.status === "processing")) { draw(cur.documents); S.poll = setTimeout(poll, 2500); }
    else route();
  };
  if (d.documents.some(x => x.status === "processing")) S.poll = setTimeout(poll, 2500);
  const upload = async files => {
    for (const f of files) {
      const fd = new FormData(); fd.append("file", f);
      try { await api(`/api/subjects/${sid}/documents`, { method: "POST", body: fd }); } catch (e) { toast(`${f.name}: ${e.message}`); }
    }
    route();
  };
  $("#fi").onchange = e => upload([...e.target.files]);
  const drop = $("#drop");
  drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); upload([...e.dataTransfer.files]); };
}

async function tutor(el, d, sid) {
  const hist = await api("/api/chat/" + sid), ready = d.documents.some(x => x.status === "ready");
  el.innerHTML = `<div class="card chat"><div id="msgs"></div>
    <form id="cf"><select id="mode" title="Explain gives answers; Socratic guides you with questions"><option value="explain">Explain</option><option value="socratic">Socratic</option></select>
    <input id="ci" placeholder="${ready ? "Ask about your material…" : "Upload a document first"}" autocomplete="off" maxlength="2000" ${ready ? "" : "disabled"}><button class="btn" ${ready ? "" : "disabled"}>Send</button></form>
    <div class="row small muted" style="margin-top:8px"><span>Answers come only from your uploaded material, with sources.</span><a href="#" id="clr">Clear chat</a></div></div>`;
  const box = $("#msgs");
  const add = (role, text, sources = []) => {
    const m = document.createElement("div"); m.className = "msg " + role;
    m.innerHTML = (role === "user" ? esc(text) : md(text)) + (sources.length ? `<details class="src"><summary>Sources (${sources.length})</summary>${sources.map(s => `<div>[${s.n}] <b>${esc(s.doc)}</b>, p.${s.page}<br>${esc(s.snippet)}…</div>`).join("")}</details>` : "");
    box.appendChild(m); box.scrollTop = box.scrollHeight; return m;
  };
  hist.forEach(m => add(m.role, m.content, m.sources));
  if (!hist.length && ready) box.innerHTML = `<div class="empty"><div class="big">🤖</div>Ask anything about this subject. Try:<div style="margin-top:10px">${["Summarise the key ideas", "Ask me 5 questions", "Give me a real-world example"].map(q => `<button class="chip btnlike" data-q="${q}">${q}</button>`).join("")}</div></div>`;
  const send = async text => {
    if (!text.trim()) return;
    if (!hist.length && box.querySelector(".empty")) box.innerHTML = "";
    hist.push(1); add("user", text); const wait = add("assistant", "Thinking…");
    try { const r = await post("/api/chat", { subject_id: sid, message: text, mode: $("#mode").value }); wait.remove(); add("assistant", r.answer, r.sources); }
    catch (e) { wait.remove(); toast(e.message); }
  };
  $("#cf").onsubmit = e => { e.preventDefault(); const t = $("#ci").value; $("#ci").value = ""; send(t); };
  box.querySelectorAll("[data-q]").forEach(b => b.onclick = () => send(b.dataset.q));
  $("#clr").onclick = async e => { e.preventDefault(); await api("/api/chat/" + sid, { method: "DELETE" }); route(); };
  if (S.prefill) { $("#ci").value = S.prefill; S.prefill = null; $("#ci").focus(); }
}

async function quiz(el, d, sid) {
  const weak = d.topics.filter(t => t.status === "weak");
  if (!d.questions) { el.innerHTML = '<div class="empty card"><div class="big">🧠</div>No questions yet. Upload a document and LearnHub will generate them.</div>'; return; }
  const topics = [...new Set(d.topics.map(t => t.topic))];
  const intro = () => {
    el.innerHTML = `<div class="card"><h3>Adaptive quiz</h3><p class="muted">Questions are picked by your performance: weak topics and unseen questions come first.</p>
      ${weak.length ? `<p>Currently weak: ${weak.map(w => `<span class="chip weak">${esc(w.topic)} ${w.accuracy}%</span>`).join("")}</p>` : ""}
      <div class="row" style="justify-content:flex-start;flex-wrap:wrap"><select id="qt"><option value="">All topics (adaptive)</option>${topics.map(t => `<option>${esc(t)}</option>`).join("")}</select>
      <button class="btn" id="go">Start 10 questions</button><button class="btn ghost" id="more">Generate more questions</button></div></div>`;
    $("#go").onclick = () => start($("#qt").value);
    $("#more").onclick = async e => { e.target.disabled = true; toast("Writing new questions…"); try { await post("/api/quiz/generate", { subject_id: sid, topic: $("#qt").value || null }); toast("Added new questions"); route(); } catch (x) { toast(x.message); e.target.disabled = false; } };
  };
  const start = async topic => {
    const qs = (await post("/api/quiz/next", { subject_id: sid, n: 10, topic: topic || null })).questions;
    if (!qs.length) return toast("No questions for that topic yet");
    let i = 0; const res = [];
    const show = () => {
      if (i >= qs.length) return done();
      const q = qs[i];
      el.innerHTML = `<div class="card"><div class="row small muted"><span>Question ${i + 1} of ${qs.length} · ${esc(q.difficulty)}</span><span class="chip">${esc(q.topic)}</span></div>${bar(100 * i / qs.length)}
        <h3 style="margin-top:14px">${esc(q.question)}</h3>${q.options.map((o, k) => `<button class="opt" data-k="${k}">${"ABCD"[k]}. ${esc(o)}</button>`).join("")}<div id="fb"></div></div>`;
      el.querySelectorAll(".opt").forEach(b => b.onclick = async () => {
        const r = await post("/api/quiz/answer", { question_id: q.id, chosen: +b.dataset.k });
        el.querySelectorAll(".opt").forEach(x => { x.disabled = true; if (+x.dataset.k === r.answer) x.classList.add("ok"); });
        if (!r.correct) b.classList.add("no");
        res.push({ topic: q.topic, ok: r.correct });
        $("#fb").innerHTML = `<p><b>${r.correct ? "✅ Correct" : "❌ Not quite"}</b> ${esc(r.explanation)}</p><button class="btn" id="nx">${i + 1 < qs.length ? "Next" : "Finish"}</button>`;
        $("#nx").onclick = () => { i++; show(); }; $("#nx").focus();
      });
    };
    const done = () => {
      const by = {}; res.forEach(r => { (by[r.topic] ||= [0, 0])[0] += r.ok; by[r.topic][1]++; });
      const sc = res.filter(r => r.ok).length;
      el.innerHTML = `<div class="card"><div class="kpi">${sc}/${res.length}</div><div class="muted">${Math.round(100 * sc / res.length)}% correct</div><h3 style="margin-top:16px">By topic</h3>
        ${Object.entries(by).map(([t, [c, n]]) => `<div class="row small"><span>${esc(t)}</span><b>${c}/${n}</b></div>${bar(100 * c / n, tone(100 * c / n))}`).join("")}
        <div style="margin-top:16px"><button class="btn" id="again">Another round</button> <a class="btn ghost" href="#/s/${sid}/progress">See progress</a></div></div>`;
      $("#again").onclick = () => route();
    };
    show();
  };
  if (S.autoStart) { const t = S.autoStart; S.autoStart = null; return start(t); }
  intro();
}

async function cards(el, d, sid) {
  let due = await api("/api/flashcards/due?subject_id=" + sid), flipped = false, total = due.length;
  const show = () => {
    if (!due.length) { el.innerHTML = `<div class="empty card"><div class="big">🎉</div>${d.cards ? "You're all caught up — no cards due right now." : "No flashcards yet. Upload a document to generate some."}</div>`; return; }
    const c = due[0];
    el.innerHTML = `<div class="small muted" style="margin-bottom:8px">${total - due.length + 1} of ${total} due · <span class="chip">${esc(c.topic || "General")}</span></div>
      <div class="card fc" id="fc"><div>${esc(c.front)}</div>${flipped ? `<div class="ans">${esc(c.back)}</div>` : "<small>Tap to reveal</small>"}</div>
      ${flipped ? `<div class="row" style="margin-top:12px;justify-content:center">${[["again", "Again"], ["hard", "Hard"], ["good", "Good"], ["easy", "Easy"]].map(([g, l]) => `<button class="btn ${g === "again" ? "ghost danger" : g === "hard" ? "ghost" : ""}" data-g="${g}">${l}</button>`).join("")}</div>` : ""}`;
    $("#fc").onclick = () => { if (!flipped) { flipped = true; show(); } };
    el.querySelectorAll("[data-g]").forEach(b => b.onclick = async () => {
      await post(`/api/flashcards/${c.id}/review`, { grade: b.dataset.g });
      due.shift(); if (b.dataset.g === "again") due.push(c);  // lapsed cards come back this session
      flipped = false; show();
    });
  };
  show();
}

async function progress(el, d, sid) {
  el.innerHTML = `<div class="grid g2" style="margin-top:0"><div class="card"><h3>Topic mastery</h3>${d.topics.map(t => `<div class="row"><b>${esc(t.topic)}</b><span class="chip ${t.status}">${t.status === "new" ? "new" : t.accuracy + "%"}</span></div>${bar(t.accuracy, tone(t.accuracy))}`).join("") || '<div class="muted">Take a quiz to see your mastery per topic.</div>'}</div>
    <div class="card"><h3>Recommended study</h3>${d.recommendations.map(recHtml).join("") || '<div class="muted">No weak topics right now — keep practising to stay sharp.</div>'}</div></div>`;
  bindRecs(el);
}

async function planner(el, d, sid) {
  const p = await api("/api/plans/" + sid), live = p && p.days_left > 0;
  const dayName = iso => iso === isoDay(0) ? "Today" : iso === isoDay(1) ? "Tomorrow" : new Date(iso + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  const taskHtml = t => `<div class="task"><input type="checkbox" data-t="${t.id}" ${t.done ? "checked" : ""}><span class="tm">${t.start}–${endT(t.start, t.minutes)}</span>
    <span class="tl ${t.done ? "dn" : ""}">${esc(t.label)}</span><span class="muted small">${t.minutes} min</span><button class="btn ghost sm" data-go="${t.kind}" data-topic="${esc(t.topic)}">Start</button></div>`;
  const dayHtml = x => `<div class="dayh">${dayName(x.date)}${x.date === isoDay(0) ? ' <span class="chip">now</span>' : ""}</div>${x.tasks.map(taskHtml).join("")}`;
  const form = `<div class="card" style="margin-top:16px"><h3>${live ? "Change plan" : "Plan your exam prep"}</h3>
    <p class="muted small" style="margin-top:0">LearnHub schedules weak topics first, repeats them later (spaced), keeps the last day for revision, and re-plans as you finish sessions or fall behind.</p>
    <form id="pf" style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end">
      <label class="small muted">Exam date<br><input type="date" name="exam_date" required min="${isoDay(1)}" value="${live ? p.exam_date : ""}"></label>
      <label class="small muted">Study time per day<br><select name="daily_minutes">${[30, 45, 60, 90, 120, 180, 240].map(m => `<option value="${m}" ${m === (p?.daily_minutes || 90) ? "selected" : ""}>${m >= 60 ? m / 60 + " h" : m + " min"}</option>`).join("")}</select></label>
      <label class="small muted">Start time<br><input type="time" name="start_time" required value="${p?.start_time || "09:00"}"></label>
      <button class="btn">${live ? "Rebuild plan" : "Generate plan"}</button></form><div class="err" id="pe"></div></div>`;
  if (!live) { el.innerHTML = (p ? '<div class="card"><b>Your exam date has passed.</b> <span class="muted">Set a new date to plan again.</span></div>' : "") + form; }
  else {
    const first = p.days.slice(0, 7), more = p.days.slice(7), pct = p.total ? Math.round(100 * p.done / p.total) : 0;
    el.innerHTML = `<div class="card"><div class="row"><h3 style="margin:0">Exam in ${p.days_left} day${p.days_left === 1 ? "" : "s"} · ${new Date(p.exam_date + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "long" })}</h3><button class="btn ghost danger sm" id="dp">Delete plan</button></div>
      ${bar(pct, pct >= 60 ? "good" : "")}<div class="small muted">${p.done} of ${p.total} sessions done · ${p.daily_minutes >= 60 ? p.daily_minutes / 60 + " h" : p.daily_minutes + " min"}/day</div>
      ${p.missed ? `<div class="small" style="color:var(--warn);margin-top:8px">↻ ${p.missed} missed session${p.missed === 1 ? " was" : "s were"} rescheduled automatically.</div>` : ""}
      ${p.dropped ? `<div class="small" style="color:var(--bad);margin-top:8px">⚠ ${p.dropped} planned sessions don't fit before the exam. Add study time or move the exam date.</div>` : ""}</div>
      <div class="card" style="margin-top:16px">${first.map(dayHtml).join("") || '<div class="muted">Nothing scheduled.</div>'}${more.length ? `<details style="margin-top:14px"><summary class="muted">Show ${more.length} more days</summary>${more.map(dayHtml).join("")}</details>` : ""}</div>` + form;
    el.querySelectorAll("[data-t]").forEach(cb => cb.onchange = async () => { await post(`/api/plans/tasks/${cb.dataset.t}/toggle`); planner(el, d, sid); });
    el.querySelectorAll("[data-go]").forEach(b => b.onclick = () => {
      const { go, topic } = b.dataset;
      if (go === "study") { S.prefill = `Explain "${topic}" in simple terms`; location.hash = `#/s/${sid}/tutor`; }
      else if (go === "practice") { S.autoStart = topic; location.hash = `#/s/${sid}/quiz`; }
      else location.hash = `#/s/${sid}/${go === "flashcards" ? "cards" : "quiz"}`;
    });
    $("#dp").onclick = async () => { if (confirm("Delete this study plan?")) { await api("/api/plans/" + sid, { method: "DELETE" }); planner(el, d, sid); } };
  }
  $("#pf").onsubmit = async e => {
    e.preventDefault();
    try { await post("/api/plans", { subject_id: sid, ...Object.fromEntries(new FormData(e.target)), daily_minutes: +e.target.daily_minutes.value }); planner(el, d, sid); }
    catch (x) { $("#pe").textContent = x.message; }
  };
}

route();
