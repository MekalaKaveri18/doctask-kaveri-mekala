import React, { useEffect, useState } from "react";

const api = (path, opts) =>
  fetch(`/api${path}`, {
    headers: {
      ...(opts?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(opts?.headers || {}),
    },
    ...opts,
  }).then(async (r) => {
    const text = await r.text();
    const data = text ? JSON.parse(text) : {};
    if (!r.ok) throw new Error(data.detail || text || r.statusText);
    return data;
  });

function statusDot(status) {
  if (status === "committed") return "dot";
  if (status === "awaiting_review") return "dot warn";
  if (status === "paused" || status === "rejected") return "dot bad";
  return "dot warn";
}

export default function App() {
  const [piles, setPiles] = useState([]);
  const [docs, setDocs] = useState([]);
  const [pileId, setPileId] = useState("");
  const [run, setRun] = useState(null);
  const [items, setItems] = useState([]);
  const [choices, setChoices] = useState({});
  const [register, setRegister] = useState(null);
  const [cost, setCost] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState("pile");
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem("va-theme") === "light" ? "light" : "dark";
    } catch {
      return "dark";
    }
  });

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("va-theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  const refreshPiles = () => api("/piles").then(setPiles).catch((e) => setError(e.message));

  useEffect(() => {
    refreshPiles();
  }, []);

  useEffect(() => {
    if (!pileId) {
      setDocs([]);
      return;
    }
    api(`/piles/${pileId}/documents`).then(setDocs).catch((e) => setError(e.message));
  }, [pileId]);

  const loadRun = async (id) => {
    const r = await api(`/runs/${id}`);
    setRun(r);
    const rev = await api(`/runs/${id}/review`);
    setItems(rev);
    setChoices(Object.fromEntries(rev.map((i) => [i.id, i.status === "pending" ? "" : i.status])));
    setRegister(null);
    setCost(await api(`/runs/${id}/cost`));
    if (r.status === "committed") {
      setRegister(await api(`/runs/${id}/register`));
      setView("register");
    } else if (rev.length) {
      setView("review");
    } else {
      setView("pile");
    }
  };

  const seed = async (seedName, name) => {
    setBusy(true);
    setError("");
    try {
      const p = await api("/piles", { method: "POST", body: JSON.stringify({ name, seed: seedName }) });
      setPileId(p.id);
      await refreshPiles();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const start = async (trigger = "full") => {
    if (!pileId) return;
    setBusy(true);
    setError("");
    try {
      const r = await api(`/piles/${pileId}/runs`, { method: "POST", body: JSON.stringify({ trigger }) });
      await loadRun(r.id);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const kill = async () => {
    if (!run) return;
    await api(`/runs/${run.id}/kill`, { method: "POST" });
    await loadRun(run.id);
  };

  const resume = async () => {
    if (!run) return;
    setBusy(true);
    try {
      const r = await api(`/runs/${run.id}/resume`, { method: "POST" });
      await loadRun(r.id);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const upload = async (file) => {
    if (!pileId || !file) return;
    setBusy(true);
    setError("");
    try {
      const body = new FormData();
      body.append("file", file);
      await fetch(`/api/piles/${pileId}/documents`, { method: "POST", body }).then(async (r) => {
        const text = await r.text();
        const data = text ? JSON.parse(text) : {};
        if (!r.ok) throw new Error(data.detail || text);
        return data;
      });
      setDocs(await api(`/piles/${pileId}/documents`));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const watch = async () => {
    if (!pileId) return;
    setBusy(true);
    try {
      const r = await api(`/piles/${pileId}/watch-ingest`, { method: "POST" });
      if (r.id) await loadRun(r.id);
      else setError(r.reason || "no new files in the watch folder");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const submitReview = async () => {
    if (!run) return;
    const pending = items.filter((i) => i.status === "pending");
    const missing = pending.filter((i) => !choices[i.id]);
    if (missing.length) {
      setError("Decide every pending item. Rejecting one does not discard the rest.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const decisions = Object.fromEntries(pending.map((i) => [i.id, choices[i.id]]));
      await api(`/runs/${run.id}/review`, { method: "POST", body: JSON.stringify({ decisions }) });
      await loadRun(run.id);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="shell">
      <header className="nav">
        <div className="brand">Vendor Analyst</div>
        <div className="nav-links">
          <span onClick={() => setView("pile")}>Pile</span>
          <span onClick={() => setView("review")}>Review</span>
          <span onClick={() => setView("register")}>Register</span>
          <span onClick={() => setView("cost")}>Cost</span>
        </div>
        <div className="nav-actions">
          <button
            type="button"
            className="theme-toggle"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            aria-pressed={theme === "dark"}
          >
            <span className="track">
              <span className="knob" />
            </span>
            {theme === "dark" ? "Dark" : "Light"}
          </button>
          <button className="ghost" disabled={busy || !pileId} onClick={() => start("full")}>
            Run agent
          </button>
        </div>
      </header>

      <section className="hero">
        <div className="eyebrow">Human-gated vendor register · cited, not invented</div>
        <h1>
          Own the pile. <em>Gate the claims.</em>
        </h1>
        <p className="lede">
          Contracts, amendments, and invoices into one grounded register. Disagreements stay visible.
          Nothing is success until a person commits it.
        </p>
        <div className="cta-row">
          <button disabled={busy} onClick={() => seed("acme", "Acme vendor pile")}>
            Seed Acme pile →
          </button>
          <button className="secondary" disabled={busy} onClick={() => seed("clean", "Clean vendor pile")}>
            Seed clean pile →
          </button>
        </div>
      </section>

      <div className="workspace">
        {error ? <div className="banner">{error}</div> : null}

        <div className="grid">
          <section className="panel">
            <div className="kicker">Workspace</div>
            <h2>Documents in, stages watched</h2>
            <p className="muted">
              Seed a synthetic pile, or upload your own. Kill and resume at a stage boundary. A second
              file is an update, not a rewrite.
            </p>
            <div className="row">
              <select value={pileId} onChange={(e) => setPileId(e.target.value)}>
                <option value="">Select pile</option>
                {piles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} v{p.version}
                  </option>
                ))}
              </select>
              <button className="primary" disabled={busy || !pileId} onClick={() => start("full")}>
                Full run
              </button>
              <button disabled={busy || !pileId} onClick={() => start("incremental")}>
                Incremental
              </button>
              <button className="danger" disabled={busy || !run} onClick={kill}>
                Kill
              </button>
              <button disabled={busy || !run} onClick={resume}>
                Resume
              </button>
              <button disabled={busy || !pileId} onClick={watch}>
                Watch folder
              </button>
              <label className="file">
                Upload
                <input type="file" disabled={busy || !pileId} onChange={(e) => upload(e.target.files?.[0])} />
              </label>
            </div>
            {docs.length ? (
              <div className="docs">
                {docs.map((d) => (
                  <span className="chip" key={d.id}>
                    {d.filename}
                    {d.kind ? ` · ${d.kind}` : ""}
                  </span>
                ))}
              </div>
            ) : (
              <p className="cite">No documents yet. Seed Acme to see conflicts, or seed clean for an honest empty findings report.</p>
            )}

            {run ? (
              <>
                <h3>Live run</h3>
                <div className="status">
                  <span className={statusDot(run.status)} />
                  {run.status} · stage {run.current_stage} · {run.trigger}
                </div>
                <ul className="timeline">
                  {(run.path_decisions || []).map((d, i) => (
                    <li key={i}>
                      <code>{d.stage}</code>
                      <span>
                        {d.decision || "continue"}
                        {d.document ? ` · ${d.document}` : ""} — {d.reason || d.kind || ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
          </section>

          <section className="panel">
            <div className="kicker">Human gate</div>
            <h2>Approve item by item</h2>
            <p className="muted">Rejecting one finding does not discard the rest. The register stays uncommitted until you say so.</p>

            {view === "cost" && cost ? (
              <>
                <h3>Where the time went</h3>
                <p>
                  {cost.total_ms?.toFixed?.(1) || cost.total_ms} ms · {cost.total_llm_calls} model calls · $
                  {cost.total_usd}
                </p>
                <ul className="timeline">
                  {(cost.stages || []).map((s) => (
                    <li key={s.stage}>
                      <code>{s.stage}</code>
                      <span>
                        {s.ms.toFixed(1)} ms · {s.llm_calls} calls
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}

            {view === "register" && register ? (
              <>
                <h3>Committed register</h3>
                {register.map((s) => (
                  <article key={s.section_id} className="card">
                    <div className="meta">
                      {s.section_id}
                      {s.unchanged ? " · unchanged hash" : ""}
                    </div>
                    <h3>{s.title}</h3>
                    <pre>{s.body}</pre>
                    <p className="cite">hash {s.content_hash.slice(0, 12)}</p>
                  </article>
                ))}
              </>
            ) : null}

            {(view === "review" || view === "pile") && items.length ? (
              <>
                {items.map((item) => (
                  <article key={item.id} className="card">
                    <div className="meta">
                      {item.type} · {item.status}
                    </div>
                    <h3>{item.title}</h3>
                    <pre>{item.body}</pre>
                    {item.locator ? <p className="cite">{item.locator}</p> : null}
                    {item.type === "conflict" ? (
                      <div className="compare">
                        <div className="box">Without a gate, this would be silently merged.</div>
                        <div className="box live">With the gate, both values stay until you pick.</div>
                      </div>
                    ) : null}
                    {item.status === "pending" ? (
                      <div className="row">
                        <button
                          className={choices[item.id] === "approved" ? "on" : ""}
                          onClick={() => setChoices({ ...choices, [item.id]: "approved" })}
                        >
                          Approve
                        </button>
                        <button
                          className={choices[item.id] === "rejected" ? "danger" : ""}
                          onClick={() => setChoices({ ...choices, [item.id]: "rejected" })}
                        >
                          Reject
                        </button>
                      </div>
                    ) : null}
                  </article>
                ))}
                <button className="primary" disabled={busy} onClick={submitReview}>
                  Submit review →
                </button>
              </>
            ) : null}

            {!items.length && !register ? (
              <p className="cite">Run a pile to open the review queue. A clean corpus can honestly produce no findings.</p>
            ) : null}
          </section>
        </div>
      </div>
    </div>
  );
}
