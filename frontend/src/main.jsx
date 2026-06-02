import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const API_BASE = import.meta.env.VITE_API_URL || '';
const steps = ['Ingest', 'Analyze', 'Transform', 'Validate', 'Package'];


function App() {
  const [fromVersion, setFromVersion] = useState('.NET Framework 4.8');
  const [toVersion, setToVersion] = useState('.NET 8');
  const [files, setFiles] = useState([]);
  const [githubUrl, setGithubUrl] = useState('');
  const [githubAuth, setGithubAuth] = useState('public');
  const [githubToken, setGithubToken] = useState('');
  const [uploadMode, setUploadMode] = useState('local');
  const [inventory, setInventory] = useState(null);
  const [job, setJob] = useState(null);
  const [report, setReport] = useState(null);
  const [terminal, setTerminal] = useState('Terminal output will appear here when migration runs...');
  const [busy, setBusy] = useState('');
  const [runtime, setRuntime] = useState(null);
  const [selectedOutput, setSelectedOutput] = useState(null);
  const [tokenStats, setTokenStats] = useState(null);
  const [ollamaStatus, setOllamaStatus] = useState(null);
  const inputRef = useRef(null);

  useEffect(() => {
    fetchJson('/api/ollama/status')
      .then((data) => setOllamaStatus(data))
      .catch(() => setOllamaStatus({ connected: false, status: 'unreachable' }));
  }, []);


  const scopes = useMemo(() => [
    { label: 'Projects',   key: 'project_count',      value: () => inventory?.project_count || 0 },
    { label: 'CS Files',   key: 'source_file_count',  value: () => inventory?.source_file_count || 0 },
    { label: 'Packages',   key: 'packages',           value: () => inventory?.packages?.length || 0 },
    { label: 'Findings',   key: 'patterns',           value: () => inventory?.patterns?.length || 0 },
    { label: 'Complexity', key: 'complexity',         value: () => inventory?.complexity?.level || '—' },
    { label: 'Frameworks', key: 'frameworks',         value: () => inventory?.frameworks?.join(', ') || '—' },
  ], [inventory]);
  const stageIndex = getStageIndex(job);

  async function uploadSelected(event) {
    const selected = [...(event.target.files || [])];
    event.target.value = '';
    if (!selected.length) return;
    const form = new FormData();
    selected.forEach((file) => form.append('files', file));
    setBusy('upload');
    try {
      const data = await postForm('/api/files/upload', form);
      setFiles(data.files || []);
      log(`Uploaded ${data.files?.length || 0} file(s).`);
      await runAnalyze();
    } catch (err) {
      log(`Upload failed: ${err.message}`);
    } finally {
      setBusy('');
    }
  }

  async function fetchGithub() {
    if (!githubUrl.trim()) return;
    setBusy('github');
    try {
      const payload = githubAuth === 'private' && githubToken ? { url: githubUrl, token: githubToken } : { url: githubUrl };
      const data = await postJson('/api/files/upload-github', payload);
      setFiles([{ name: data.repo, type: 'github', size: data.total_files || 0 }]);
      log(`Fetched ${data.repo} from ${data.branch}.`);
      await runAnalyze();
    } catch (err) {
      log(`GitHub fetch failed: ${err.message}`);
    } finally {
      setBusy('');
      // clear token from memory after use
      setGithubToken('');
    }
  }

  async function runAnalyze() {
    setBusy('analyze');
    try {
      const data = await postJson('/api/migration/analyze', { from_version: fromVersion, to_version: toVersion });
      setInventory(data);
      log(`Analyzed ${data.project_count} project(s), ${data.source_file_count} C# file(s).`);
    } catch (err) {
      log(`Analysis failed: ${err.message}`);
    } finally {
      setBusy('');
    }
  }

  async function startMigration() {
    setBusy('migration');
    setReport(null);
    setSelectedOutput(null);
    try {
      const data = await postJson('/api/migration/migrate', {
        from_version: fromVersion,
        to_version: toVersion,
      });
      log(`Migration job queued: ${data.job_id}`);
      poll(data.job_id);
    } catch (err) {
      log(`Migration failed to start: ${err.message}`);
      setBusy('');
    }
  }

  async function poll(jobId) {
    let lastProgress = '';
    for (let i = 0; i < 720; i += 1) {
      const data = await fetchJson(`/api/migration/status/${jobId}`);
      setJob(data);
      const msg = `${data.stage}: ${data.progress}`;
      if (msg !== lastProgress) {
        log(msg);
        lastProgress = msg;
      }
      if (['completed', 'needs_review', 'failed'].includes(data.status)) {
        setBusy('');
        if (data.status === 'failed') {
          log(`Migration failed: ${data.error || 'Unknown error'}`);
        } else {
          try {
            setReport(await fetchJson('/api/migration/report'));
          } catch {
            setReport(data.result || null);
          }
          fetchTokenStats(jobId);
        }
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    setBusy('');
    log('Migration polling timed out.');
  }

  async function loadRuntime() {
    const data = await fetchJson('/health');
    setRuntime(data.runtime ?? null);
  }

  async function fetchTokenStats(jobId) {
    try {
      const data = await fetchJson(`/api/migration/token-stats/${jobId}`);
      setTokenStats(data);
    } catch {
      // silently ignore
    }
  }

  function log(line) {
    setTerminal((prev) => `${prev === 'Terminal output will appear here when migration runs...' ? '' : `${prev}\n`}${line}`);
  }


  return (
    <>
      <section className="ma-hero">
        <div className="ma-hero-content">
          <div className="ma-hero-brand">
            <div className="ma-hero-logo">.N</div>
            <div>
              <div className="ma-hero-title">.NET Migration Agent</div>
              <div className="ma-hero-subtitle">Migrate legacy .NET applications to modern target versions with Microsoft Agent Framework orchestration and LLM-assisted build fixing.</div>
            </div>
          </div>
          <div className="ma-hero-pills">
            <span className="hero-pill"><span className="pill-dot blue"></span>Microsoft Agent Framework</span>
            <span className="hero-pill"><span className="pill-dot purple"></span>Groq LLM</span>
            <span className="hero-pill"><span className="pill-dot green"></span>.NET 8/9/10</span>
            <span className="hero-pill"><span className="pill-dot orange"></span>Build Validation</span>
            <OllamaStatus status={ollamaStatus} />
          </div>
        </div>
      </section>

      <main className="ma-container">
        <section className="ma-card">
          <div className="ma-card-header"><div className="ma-card-title">Migration Path</div><button onClick={loadRuntime}>Runtime Status</button></div>
          <div className="ma-card-body">
            <div className="version-row">
              <Select label="From Version" value={fromVersion} setValue={setFromVersion} values={['.NET Framework 4.5', '.NET Framework 4.6', '.NET Framework 4.7', '.NET Framework 4.8', '.NET Core 3.1', '.NET 5', '.NET 6', '.NET 7']} />
              <div className="version-arrow">to</div>
              <Select label="To Version" value={toVersion} setValue={setToVersion} values={['.NET 8', '.NET 9', '.NET 10']} />
            </div>
            <div className={`compat-matrix ${inventory?.complexity?.level === 'High' ? 'warning' : 'success'}`}>
              <div className="compat-left">
                <div className="compat-status-title">{inventory ? `${inventory.complexity.level} migration complexity` : 'Ready for project analysis'}</div>
                <div className="compat-status-msg">{inventory?.recommended_path || 'Upload a project zip or fetch a GitHub repository to generate the migration plan.'}</div>
              </div>
              <div className="compat-right">
                <Metric label="Projects" value={inventory?.project_count || 0} />
                <Metric label="Files" value={inventory?.source_file_count || 0} />
                <Metric label="Score" value={inventory?.complexity?.score || 0} />
              </div>
            </div>
          </div>
        </section>

        <section className="two-col">
          <article className="ma-card">
            <div className="ma-card-header"><div className="ma-card-title">Upload Source</div></div>
            <div className="ma-card-body">
              <div className="upload-tabs">
                <button className={uploadMode === 'local' ? 'active' : ''} onClick={() => setUploadMode('local')}>Local Files</button>
                <button className={uploadMode === 'github' ? 'active' : ''} onClick={() => setUploadMode('github')}>GitHub URL</button>
              </div>
              {uploadMode === 'local' ? (
                <div className="upload-zone" onClick={() => { inputRef.current?.click(); }}>
                  <div className="upload-icon">ZIP</div>
                  <h3>Drop or browse for .zip, .sln, .csproj, .cs files</h3>
                  <p>The backend ignores .git, bin, obj, packages, and node_modules.</p>
                  <input ref={inputRef} hidden type="file" multiple accept=".zip,.sln,.csproj,.cs,.config,.json,.razor,.cshtml" onChange={uploadSelected} />
                    <button className="browse-btn">{busy === 'upload' ? 'Uploading...' : 'Browse Files'}</button>
                </div>
              ) : (
                <div className="github-input-area">
                  <input className="github-input" value={githubUrl} onChange={(event) => setGithubUrl(event.target.value)} placeholder="https://github.com/owner/repo" />
                  <div className="github-input-row">
                    <select value={githubAuth} onChange={(e) => setGithubAuth(e.target.value)}>
                      <option value="public">Public</option>
                      <option value="private">Private (use PAT)</option>
                    </select>
                    {githubAuth === 'private' && (
                      <input type="password" className="github-input" placeholder="Personal Access Token (PAT)" value={githubToken} onChange={(e) => setGithubToken(e.target.value)} />
                    )}
                    <button className="browse-btn" disabled={busy === 'github'} onClick={fetchGithub}>{busy === 'github' ? 'Fetching...' : 'Fetch Repository'}</button>
                  </div>
                </div>
              )}
              <div className="file-list">{files.map((file) => <div className="file-item" key={file.name}><span>{file.type}</span><span className="fn">{file.name}</span><span className="fs">{file.size}</span></div>)}</div>
            </div>
          </article>

          <article className="ma-card">
            <div className="ma-card-header"><div className="ma-card-title">Migration Controls</div></div>
            <div className="ma-card-body">
              <div className="scope-grid">{scopes.map((s) => <div className={`scope-card ${inventory ? 'on' : ''}`} key={s.key}><div className="si">{String(s.value()).slice(0, 4)}</div><div className="sl">{s.label}</div></div>)}</div>
              <button className="run-btn" disabled={!files.length || busy === 'migration'} onClick={startMigration}>{busy === 'migration' ? 'Migration running...' : 'Run Migration'}</button>
              <button className="secondary-run" disabled={!files.length} onClick={runAnalyze}>Refresh Analysis</button>
              {runtime && <div className="runtime-box">{runtime.agent_framework.detail}<br />LLM: {runtime.llm.provider} / {runtime.llm.model}</div>}
            </div>
          </article>
        </section>

        <section className="ma-card">
          <div className="ma-card-header"><div className="ma-card-title">Execution Progress</div><span className="section-status">{job?.progress || 'Waiting for migration to start'}</span></div>
          <div className="ma-card-body">
            <div className="stepper">
              <div className="stepper-progress" style={{ width: `${job ? Math.min(100, (stageIndex + 1) * 22) : 0}%` }}></div>
              {steps.map((step, index) => {
                const state = getStepState(index, stageIndex, job);
                return <div className="step-item" key={step}><div className={`step-circle ${state}`}>{state === 'completed' ? 'OK' : index + 1}</div><div className={`step-label ${state}`}>{step}</div><div className="step-sub">{state === 'completed' ? 'Done' : state === 'active' ? 'Running' : 'Waiting'}</div></div>;
              })}
            </div>
            <pre className="terminal">{terminal}</pre>
          </div>
        </section>

        <ReadinessScorecard readiness={report?.readiness} inventory={inventory} report={report} />

        <section className="two-col">
          <Findings inventory={inventory} report={report} />
          <Actions job={job} />
        </section>

        <section className="ma-card">
          <div className="ma-card-header"><div className="ma-card-title">Generated Outputs</div><span className="section-status">{report ? 'Ready' : 'Available after migration'}</span></div>
          <div className="ma-card-body outputs-grid">
            <Output title="Migration Summary" ready={!!report} onClick={() => setSelectedOutput(outputContent('Migration Summary', report))} />
            <Output title="Dependency Map" ready={!!report?.dependency_map} onClick={() => setSelectedOutput(outputContent('Dependency Map', report.dependency_map))} />
            <Output title="Validation Report" ready={!!report?.validation} onClick={() => setSelectedOutput(outputContent('Validation Report', report.validation))} />
            <Output title="Migration Diff" ready={!!report?.diff} onClick={() => setSelectedOutput(outputContent('Migration Diff', report.diff))} />
            <Output title="Code Rewrite Preview" ready={!!report?.code_rewrite_previews} onClick={() => setSelectedOutput(outputContent('Code Rewrite Preview', report.code_rewrite_previews))} />
            <Output title="Build Error AI Fixer" ready={!!report?.build_fixer} onClick={() => setSelectedOutput(outputContent('Build Error AI Fixer', report.build_fixer))} />
            <Output title="Dependency Assistant" ready={!!report?.dependency_modernization} onClick={() => setSelectedOutput(outputContent('Dependency Assistant', report.dependency_modernization))} />
            <Output title="Architecture Suggestions" ready={!!report?.architecture_suggestions} onClick={() => setSelectedOutput(outputContent('Architecture Suggestions', report.architecture_suggestions))} />
            <Output title="Test Generation Agent" ready={!!report?.generated_tests} onClick={() => setSelectedOutput(outputContent('Test Generation Agent', report.generated_tests))} />
            <Output title="Executive Report" ready={!!report?.executive_report} onClick={() => setSelectedOutput(outputContent('Executive Report', report.executive_report))} />
            <Output title="Manual Fix List" ready={!!report} onClick={() => setSelectedOutput(outputContent('Manual Fix List', report?.manual_fixes || []))} />
            <Output title="Auth Migration Report" ready={!!report?.auth_migration?.status} onClick={() => setSelectedOutput(outputContent('Auth Migration Report', report.auth_migration))} />
            <Output title="Architecture Guardrails" ready={!!report?.guardrails} onClick={() => setSelectedOutput(outputContent('Architecture Guardrails', report.guardrails))} />
            <Output title="UI Migration Report" ready={!!report} onClick={() => setSelectedOutput(outputContent('UI Migration Report', { view: report?.view_migration, webforms: report?.webforms_migration, blazor: report?.blazor_migration }))} />
            <Output title="Change Log" ready={!!report} onClick={() => setSelectedOutput(outputContent('Change Log', report?.changes || []))} />
            <Output title="Orchestrator Decisions" ready={!!report?.orchestrator?.decision_log} onClick={() => setSelectedOutput(outputContent('Orchestrator Decisions', report.orchestrator))} />
            <Output title="Migrated Project Zip" ready={job?.status === 'completed'} onClick={() => window.location.href = `${API_BASE}/api/files/download`} />
          </div>
          {selectedOutput && <OutputDetail output={selectedOutput} jobId={job?.job_id} />}
        </section>

        {job?.status === 'completed' && <TokenUtilization stats={tokenStats} />}
      </main>
    </>
  );
}

function Select({ label, value, setValue, values }) {
  return <div className="version-group"><label>{label}</label><select className="version-select" value={value} onChange={(event) => setValue(event.target.value)}>{values.map((item) => <option key={item}>{item}</option>)}</select></div>;
}

function Metric({ label, value }) {
  return <div className="compat-metric"><span className="cm-label">{label}</span><div className="cm-bar"><div className="cm-fill effort" style={{ width: `${Math.min(100, Number(value) || 0)}%` }}></div></div><span className="cm-val">{value}</span></div>;
}

function ReadinessScorecard({ readiness, inventory, report }) {
  const fallback = inventory ? {
    score: Math.max(0, 100 - Number(inventory?.complexity?.score || 0)),
    level: 'Pre-migration estimate',
    summary: inventory.recommended_path,
    categories: [
      { name: 'Project Compatibility', score: Math.max(0, 100 - Number(inventory?.complexity?.score || 0)), status: 'Estimate', description: 'Inventory-based readiness estimate' },
      { name: 'Legacy Findings', score: Math.max(0, 100 - (inventory?.patterns?.length || 0) * 8), status: 'Estimate', description: `${inventory?.patterns?.length || 0} migration findings detected` },
    ],
    recommendations: ['Run migration to generate the full readiness scorecard.'],
  } : null;
  const data = readiness || fallback;
  if (!data) return null;
  return (
    <section className="ma-card readiness-card">
      <div className="ma-card-header">
        <div className="ma-card-title">Readiness Scorecard</div>
        <span className="section-status">{data.level}</span>
      </div>
      <div className="ma-card-body readiness-body">
        <div className="readiness-score">
          <div className="score-ring" style={{ '--score': `${data.score}%` }}>
            <span>{data.score}</span>
          </div>
          <div>
            <h3>{data.level}</h3>
            <p>{data.summary}</p>
          </div>
        </div>
        <div className="readiness-grid">
          {(data.categories || []).map((item) => (
            <article className={`readiness-item ${item.status?.toLowerCase()}`} key={item.name}>
              <div className="readiness-item-top"><strong>{item.name}</strong><span>{item.score}</span></div>
              <div className="readiness-bar"><div style={{ width: `${item.score}%` }}></div></div>
              <p>{item.description}</p>
            </article>
          ))}
        </div>
        <UiProfileRow profile={inventory?.ui_profile} report={report} />
        <div className="readiness-recs">{(data.recommendations || []).map((item) => <div key={item}>{item}</div>)}</div>
      </div>
    </section>
  );
}

function UiProfileRow({ profile, report }) {
  const uiType = profile?.ui_type || 'none';
  const view = report?.view_migration;
  const webforms = report?.webforms_migration;
  const blazor = report?.blazor_migration;
  const migrated = report && (view || webforms || blazor);

  const uiLabels = {
    razor_mvc:    'Razor MVC',
    webforms:     'Web Forms',
    blazor:       'Blazor',
    angular:      'Angular',
    react:        'React',
    none:         null,
  };

  const label = uiLabels[uiType];

  return (
    <div style={{ margin: '12px 0 4px', padding: '10px 14px', borderRadius: 8, background: '#f8fafc', border: '1px solid #e2e8f0' }}>
      <div style={{ fontWeight: 700, fontSize: 13, color: '#1e293b', marginBottom: 6 }}>UI Profile</div>
      {!label ? (
        <div style={{ fontSize: 13, color: '#64748b' }}>No UI detected — backend-only project.</div>
      ) : (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'flex-start' }}>
          <div style={{ fontSize: 13 }}>
            <span style={{ fontWeight: 600, color: '#334155' }}>Type: </span>
            <span style={{ color: '#3b82f6', fontWeight: 700 }}>{label}</span>
          </div>
          {!migrated ? (
            <div style={{ fontSize: 13, color: '#64748b' }}>
              {uiType === 'razor_mvc' && `${profile.cshtml_count} view(s) detected — will be automatically migrated to .NET 8.`}
              {uiType === 'webforms' && `${profile.aspx_count} page(s) detected — will be automatically converted to Razor Pages.`}
              {uiType === 'blazor' && `${profile.razor_count} component(s) detected — will be migrated to .NET 8.`}
              {(uiType === 'angular' || uiType === 'react') && 'Frontend detected — backend will be migrated, frontend stays as-is.'}
            </div>
          ) : (
            <div style={{ fontSize: 13, color: '#64748b' }}>
              {uiType === 'razor_mvc' && view && !view.skipped && `${view.views_processed} view(s) migrated — ${view.helpers_replaced} helper(s) replaced${view.manual_review?.length ? `, ${view.manual_review.length} need review` : ', no manual review needed'}.`}
              {uiType === 'webforms' && webforms && !webforms.skipped && `${webforms.pages_processed} page(s) converted — ${webforms.controls_replaced} control(s) replaced${webforms.manual_review?.length ? `, ${webforms.manual_review.length} need review` : ', no manual review needed'}.`}
              {uiType === 'blazor' && blazor && !blazor.skipped && `${blazor.components_processed} component(s) migrated — ${blazor.fixes_applied} fix(es) applied${blazor.manual_review?.length ? `, ${blazor.manual_review.length} need review` : ', no manual review needed'}.`}
              {(uiType === 'angular' || uiType === 'react') && 'Frontend unchanged — backend migrated successfully.'}
              {uiType === 'razor_mvc' && view?.skipped && 'No Razor views found in migrated output.'}
              {uiType === 'webforms' && webforms?.skipped && 'No Web Forms files found in migrated output.'}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Findings({ inventory, report }) {
  const critical = inventory?.patterns?.filter((item) => item.severity === 'High') || [];
  const warnings = inventory?.patterns?.filter((item) => item.severity !== 'High') || [];
  return <article className="ma-card"><div className="ma-card-header"><div className="ma-card-title">Key Findings</div></div><div className="ma-card-body"><div className="findings-summary"><Badge label="Critical" count={critical.length} cls="critical" /><Badge label="Warnings" count={warnings.length} cls="warning" /><Badge label="Manual Fixes" count={report?.manual_fixes?.length || 0} cls="info" /></div><div className="findings-list">{[...critical, ...warnings].map((item, i) => <div className={`f-item ${item.severity === 'High' ? 'critical' : 'warning'}`} key={`${item.title}-${i}`}>{item.title}: {item.action}</div>)}</div></div></article>;
}

function Actions({ job }) {
  const agents = [
    { name: 'Analyzer Agent',       role: 'Scan projects, packages — orchestrator plans from this', stages: ['analyzing'] },
    { name: 'LLM Migration Agent',  role: 'Rewrite source files to target .NET version',            stages: ['migrating'] },
    { name: 'Auth Agent',           role: 'Detect, migrate and verify authentication',               stages: ['auth'] },
    { name: 'View Migration Agent', role: 'Migrate Razor views — HTML Helpers to Tag Helpers',      stages: ['views'] },
    { name: 'Web Forms Agent',      role: 'Convert .aspx/.ascx/.master to Razor Pages',             stages: ['webforms'] },
    { name: 'Blazor Agent',         role: 'Migrate .razor components to .NET 8',                    stages: ['blazor'] },
    { name: 'Fix Agent',            role: 'Apply deterministic structural fixes',                   stages: ['fixing'] },
    { name: 'Guardrail Agent',      role: 'Scan architecture and code quality violations',          stages: ['guardrails'] },
    { name: 'Build Validator',      role: 'Pre-clean, build and auto-fix',                         stages: ['build_validate'] },
    { name: 'LLM Fixer Agent',      role: 'Fix broken files using exact build error context',       stages: ['llm_fixing'] },
    { name: 'Reporter Agent',       role: 'Generate full report from context — no re-running',     stages: ['completed'] },
  ];

  const stage = job?.stage || '';
  const completed = job?.status === 'completed' || job?.status === 'needs_review';

  const stageOrder = ['queued','analyzing','migrating','auth','views','webforms','blazor','fixing','guardrails','build_validate','llm_fixing','completed'];

  function getState(agent) {
    if (!job) return 'waiting';
    if (completed) return 'done';
    if (agent.stages.includes(stage)) return 'active';
    const agentIdx = Math.max(...agent.stages.map(s => stageOrder.indexOf(s)));
    const currentIdx = stageOrder.indexOf(stage);
    if (currentIdx > agentIdx) return 'done';
    return 'waiting';
  }

  const stateStyle = {
    done:    { bg: '#22c55e', color: '#fff', icon: '✓' },
    active:  { bg: '#3b82f6', color: '#fff', icon: '●' },
    waiting: { bg: '#e2e8f0', color: '#94a3b8', icon: '○' },
    skipped: { bg: '#f1f5f9', color: '#94a3b8', icon: '—' },
  };

  return (
    <article className="ma-card">
      <div className="ma-card-header"><div className="ma-card-title">Autonomous Agents</div></div>
      <div className="ma-card-body action-list">
        {agents.map((a) => {
          const s = getState(a);
          const st = stateStyle[s];
          return (
            <div className="a-item" key={a.name}>
              <span className="a-icon" style={{ background: st.bg, color: st.color, borderRadius: '50%', width: 32, height: 32, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, flexShrink: 0 }}>{st.icon}</span>
              <span className="a-text"><strong style={{ color: s === 'active' ? '#3b82f6' : s === 'done' ? '#22c55e' : undefined }}>{a.name}</strong><br />{a.role}</span>
            </div>
          );
        })}
      </div>
    </article>
  );
}

function Badge({ label, count, cls }) {
  return <div className={`f-badge ${cls}`}><div className="fc">{count}</div><div className="fl">{label}</div></div>;
}

function Output({ title, ready, onClick }) {
  return <button className={`out-card ${ready ? '' : 'disabled'}`} onClick={ready ? onClick : undefined}><div className="out-icon">DOC</div><div className="out-title">{title}</div><div className="out-desc">{ready ? 'Open or download' : 'Pending'}</div></button>;
}

function OutputDetail({ output, jobId }) {
  return (
    <section className="output-detail">
      <div className="detail-head">
        <h2>{output.title}</h2>
        <div className="report-actions">
          {jobId && <a href={`${API_BASE}/api/migration/report`} target="_blank" rel="noreferrer">Preview Report</a>}
          {jobId && <a href={`${API_BASE}/api/migration/report`}>Download CSV</a>}
          {jobId && <a href={`${API_BASE}/api/migration/report`}>Download HTML/PDF</a>}
          <button onClick={() => downloadJson(`${output.title.toLowerCase().replaceAll(' ', '-')}.json`, output.data)}>JSON</button>
        </div>
      </div>
      {output.type === 'summary' && <SummaryDetail report={output.data} />}
      {output.type === 'dependency' && <DependencyDetail inventory={output.data} />}
      {output.type === 'depmap' && <DependencyMapDetail depmap={output.data} />}
      {output.type === 'validation' && <ValidationDetail validation={output.data} />}
      {output.type === 'diff' && <DiffDetail diff={output.data} />}
      {output.type === 'rewrite' && <RewritePreviewDetail items={output.data} />}
      {output.type === 'agentReport' && <AgentReportDetail report={output.data} />}
      {output.type === 'list' && <ListDetail items={output.data} />}
      {output.type === 'auth' && <AuthMigrationDetail auth={output.data} />}
      {output.type === 'guardrails' && <GuardrailDetail data={output.data} />}
      {output.type === 'uimigration' && <UiMigrationDetail data={output.data} />}
      {output.type === 'orchestrator' && <OrchestratorDecisionsDetail data={output.data} />}
      <pre className="detail-json">{JSON.stringify(output.data, null, 2)}</pre>
    </section>
  );
}

function SummaryDetail({ report }) {
  const inv = report?.inventory || {};
  return <div className="detail-grid"><Card label="From" value={report?.from_version} /><Card label="To" value={report?.to_version} /><Card label="Projects" value={inv.project_count || 0} /><Card label="Build" value={report?.validation?.success ? 'Passed' : 'Needs Review'} /></div>;
}

function DependencyDetail({ inventory }) {
  const projects = inventory?.projects || [];
  return (
    <div className="dependency-detail">
      {projects.map((project) => (
        <div className="dep-card" key={project.path}>
          <strong>{project.path}</strong>
          <span>{project.target_framework || 'No framework detected'}</span>
          {(project.packages || []).map((pkg) => <p key={`${project.path}-${pkg.name}`}>{pkg.name} {pkg.version}</p>)}
        </div>
      ))}
    </div>
  );
}

function ValidationDetail({ validation }) {
  return <div className={`validation-banner ${validation?.success ? 'success' : 'failed'}`}>{validation?.success ? 'Build succeeded' : 'Build failed or needs review'} at stage {validation?.stage || 'unknown'}</div>;
}

function DiffDetail({ diff }) {
  const summary = diff?.summary || {};
  return (
    <div className="diff-detail">
      <div className="detail-grid">
        <Card label="Added" value={summary.added || 0} />
        <Card label="Modified" value={summary.modified || 0} />
        <Card label="Removed" value={summary.removed || 0} />
        <Card label="Unchanged" value={summary.unchanged || 0} />
      </div>
      <div className="diff-columns">
        <FileList title="Added Files" files={diff?.added || []} />
        <FileList title="Modified Files" files={diff?.modified || []} />
        <FileList title="Removed Files" files={diff?.removed || []} />
      </div>
      {(diff?.previews || []).map((preview) => <pre className="diff-preview" key={preview.path}>{preview.diff}</pre>)}
    </div>
  );
}

function RewritePreviewDetail({ items }) {
  return <div className="rewrite-preview">{(items || []).map((item) => <article key={item.path}><h3>{item.path}</h3><p>{item.explanation}</p><div className="rewrite-columns"><pre>{item.legacy}</pre><pre>{item.proposed}</pre></div></article>)}</div>;
}

function AgentReportDetail({ report }) {
  const items = Array.isArray(report) ? report : report?.items || Object.entries(report || {}).map(([key, value]) => ({ name: key, value }));
  return <div className="agent-report">{items.map((item, index) => <article key={index}>{Object.entries(item).map(([key, value]) => <p key={key}><strong>{key}</strong><span>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</span></p>)}</article>)}</div>;
}

function FileList({ title, files }) {
  return <article className="diff-list"><strong>{title}</strong>{files.length ? files.slice(0, 12).map((file) => <span key={file}>{file}</span>) : <span>None</span>}</article>;
}

function ListDetail({ items }) {
  const list = Array.isArray(items) ? items : [];
  return <div className="detail-list">{list.length ? list.map((item, index) => <div key={index}>{typeof item === 'string' ? item : JSON.stringify(item)}</div>) : <div>No items available.</div>}</div>;
}

function Card({ label, value }) {
  return <article><span>{label}</span><strong>{String(value ?? '')}</strong></article>;
}

function outputContent(title, data) {
  const typeByTitle = {
    'Migration Summary': 'summary',
    'Dependency Map': 'depmap',
    'Validation Report': 'validation',
    'Migration Diff': 'diff',
    'Code Rewrite Preview': 'rewrite',
    'Build Error AI Fixer': 'agentReport',
    'Dependency Assistant': 'agentReport',
    'Architecture Suggestions': 'agentReport',
    'Test Generation Agent': 'agentReport',
    'Executive Report': 'agentReport',
    'Manual Fix List': 'list',
    'Change Log': 'list',
    'Auth Migration Report': 'auth',
    'Architecture Guardrails': 'guardrails',
    'UI Migration Report': 'uimigration',
    'Orchestrator Decisions': 'orchestrator',
  };
  const reportKindByTitle = {
    'Migration Summary': 'executive',
    'Dependency Map': 'dependencies',
    'Validation Report': 'build-fixer',
    'Migration Diff': 'diff',
    'Code Rewrite Preview': 'rewrite',
    'Build Error AI Fixer': 'build-fixer',
    'Dependency Assistant': 'dependencies',
    'Architecture Suggestions': 'architecture',
    'Test Generation Agent': 'tests',
    'Executive Report': 'executive',
    'Manual Fix List': 'build-fixer',
    'Change Log': 'diff',
  };
  return { title, type: typeByTitle[title] || 'json', reportKind: reportKindByTitle[title] || 'executive', data };
}

function getStageIndex(job) {
  if (!job) return -1;
  if (job.status === 'completed' || job.status === 'needs_review') return 4;
  const map = {
    queued: 0, analyzing: 1,
    migrating: 2, auth: 2, views: 2, webforms: 2, blazor: 2,
    fixing: 2, guardrails: 2,
    build_validate: 3, llm_fixing: 3,
    completed: 4, failed: 4,
  };
  return map[job.stage] ?? 0;
}

function getStepState(index, stageIndex, job) {
  if (!job || stageIndex < 0) return 'pending';
  if (job.status === 'completed' || job.status === 'needs_review') return 'completed';
  if (index < stageIndex) return 'completed';
  if (index === stageIndex) return 'active';
  return 'pending';
}

async function postJson(url, payload) {
  const response = await fetch(`${API_BASE}${url}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || data.error || response.statusText);
  return data;
}

async function postForm(url, form) {
  const response = await fetch(`${API_BASE}${url}`, { method: 'POST', body: form });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || response.statusText);
  return data;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(`${API_BASE}${url}`, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || response.statusText);
  return data;
}

function downloadJson(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

function OllamaStatus({ status }) {
  if (!status) return <span className="hero-pill"><span className="pill-dot" style={{ background: '#888' }}></span>Backend: checking...</span>;
  const connected = status.connected;
  return (
    <span className="hero-pill">
      <span className="pill-dot" style={{ background: connected ? '#22c55e' : '#ef4444' }}></span>
      Backend: {connected ? `${status.status} — ${status.model}` : status.status}
    </span>
  );
}

function AppStatusMessage({ runtime, smokeTest }) {
  if (!runtime || runtime.status === 'stopped') return null;

  const statusConfig = {
    starting:    { color: '#f59e0b', icon: '...', text: 'Starting up',                    reason: 'The migrated app is launching. This may take a few seconds.' },
    running:     { color: '#22c55e', icon: 'OK',  text: 'App is running',                  reason: 'The migrated app started and is listening. Run a smoke test to verify the endpoints.' },
    needs_setup: { color: '#f59e0b', icon: '!',   text: 'Setup required before running',   reason: (runtime.logs || []).filter(l => l.trim()).join(' ') },
    exited:      { color: '#ef4444', icon: 'X',   text: 'App exited unexpectedly',          reason: 'The app started but crashed — usually a missing database or misconfigured connection string. Download the zip, fix the config, and run locally.' },
    failed:      { color: '#ef4444', icon: 'X',   text: 'App failed to start',              reason: runtime.logs?.find(l => l.includes('error') || l.includes('Error') || l.includes('Cannot open')) || 'Could not start. Check that .NET SDK is installed and the project builds cleanly.' },
    stopped:     { color: '#6b7280', icon: '-',   text: 'App stopped',                      reason: 'The application was stopped.' },
  };

  const cfg = statusConfig[runtime.status] || { color: '#6b7280', icon: '?', text: runtime.status, reason: '' };

  const smokeColor = !smokeTest ? null
    : smokeTest.status === 'passed'       ? '#22c55e'
    : smokeTest.status === 'needs_review' ? '#f59e0b'
    : '#ef4444';
  const smokeText = !smokeTest ? null
    : smokeTest.status === 'passed'       ? 'Smoke test passed — all required endpoints responded correctly'
    : smokeTest.status === 'needs_review' ? 'Smoke test needs review — some endpoints returned unexpected responses'
    : 'Smoke test failed — app did not respond';

  return (
    <div style={{ margin: '12px 0', padding: '12px 16px', borderRadius: '8px', borderLeft: `4px solid ${smokeColor || cfg.color}`, background: '#f8fafc' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: 700, color: smokeColor || cfg.color }}>
        <span style={{ fontSize: '16px' }}>{cfg.icon}</span>
        <span>{smokeText || cfg.text}</span>
      </div>
      <div style={{ marginTop: '4px', fontSize: '13px', color: '#475569' }}>{cfg.reason}</div>
    </div>
  );
}

function DependencyMapDetail({ depmap }) {
  const entries = Object.entries(depmap || {});
  if (!entries.length) return <div className="detail-list"><div>No dependencies detected in migrated output.</div></div>;
  return (
    <div className="dependency-detail">
      {entries.map(([pkg, version]) => (
        <div className="dep-card" key={pkg}>
          <strong>{pkg}</strong>
          <span>{version}</span>
        </div>
      ))}
    </div>
  );
}

function AuthMigrationDetail({ auth }) {
  if (!auth) return <div className="detail-list"><div>No auth migration data available.</div></div>;
  const statusColor = auth.status === 'passed' ? '#22c55e' : auth.status === 'needs_review' ? '#f59e0b' : '#ef4444';
  return (
    <div style={{ padding: '8px 0' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <span style={{ fontWeight: 700, color: statusColor, fontSize: 15 }}>{auth.summary}</span>
      </div>
      <div style={{ marginBottom: 8 }}><strong>Auth Type Detected:</strong> {auth.auth_type || 'none'}</div>
      {auth.roles?.length > 0 && <div style={{ marginBottom: 8 }}><strong>Roles:</strong> {auth.roles.join(', ')}</div>}
      {auth.protected_files?.length > 0 && <div style={{ marginBottom: 8 }}><strong>Protected Controllers:</strong> {auth.protected_files.length}</div>}
      {auth.checks?.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <strong>Verification Checks:</strong>
          {auth.checks.map((c, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4, fontSize: 13 }}>
              <span style={{ color: c.passed ? '#22c55e' : '#ef4444', fontWeight: 700 }}>{c.passed ? '✓' : '✗'}</span>
              <span>{c.name}</span>
              {!c.passed && <span style={{ color: '#94a3b8', fontSize: 12 }}>— {c.description}</span>}
            </div>
          ))}
        </div>
      )}
      {auth.warnings?.length > 0 && auth.auth_type !== 'none' && (
        <div style={{ background: '#fffbeb', border: '1px solid #fcd34d', borderRadius: 6, padding: '8px 12px' }}>
          <strong style={{ color: '#92400e' }}>Action Required:</strong>
          {auth.warnings.map((w, i) => <div key={i} style={{ fontSize: 13, color: '#78350f', marginTop: 4 }}>⚠ {w}</div>)}
        </div>
      )}
      {auth.changes?.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <strong>Changes Applied:</strong>
          {auth.changes.map((c, i) => <div key={i} style={{ fontSize: 13, color: '#475569', marginTop: 2 }}>• {c}</div>)}
        </div>
      )}
    </div>
  );
}

function UiMigrationDetail({ data }) {
  const view = data?.view;
  const webforms = data?.webforms;
  const blazor = data?.blazor;
  const hasView = view && !view.skipped;
  const hasWebForms = webforms && !webforms.skipped;
  const hasBlazor = blazor && !blazor.skipped;

  if (!hasView && !hasWebForms && !hasBlazor) {
    return <div className="detail-list"><div style={{ color: '#64748b', fontSize: 13 }}>No UI files were found in this project — backend-only migration was performed.</div></div>;
  }

  function Section({ title, items, color }) {
    if (!items?.length) return null;
    return (
      <div style={{ marginBottom: 12, padding: '8px 12px', background: color === 'green' ? '#f0fdf4' : '#fffbeb', borderRadius: 6, borderLeft: `3px solid ${color === 'green' ? '#22c55e' : '#f59e0b'}` }}>
        <strong style={{ fontSize: 13, color: color === 'green' ? '#166534' : '#92400e' }}>{title}</strong>
        {items.map((f, i) => <div key={i} style={{ fontSize: 13, color: color === 'green' ? '#166534' : '#78350f', marginTop: 2 }}>{color === 'green' ? '✓' : '⚠'} {f}</div>)}
      </div>
    );
  }

  return (
    <div style={{ padding: '8px 0' }}>
      {hasView && (
        <div style={{ marginBottom: 20 }}>
          <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 10, color: '#1e293b' }}>Razor MVC Views</div>
          <div className="detail-grid" style={{ marginBottom: 12 }}>
            <article><span>Views Processed</span><strong>{view.views_processed ?? 0}</strong></article>
            <article><span>Helpers Replaced</span><strong>{view.helpers_replaced ?? 0}</strong></article>
            <article><span>LLM Passes</span><strong>{view.llm_passes ?? 0}</strong></article>
            <article><span>Manual Review</span><strong style={{ color: view.manual_review?.length ? '#f59e0b' : '#22c55e' }}>{view.manual_review?.length || 'None'}</strong></article>
          </div>
          <Section title="_ViewImports.cshtml" items={view.viewimports_fixed} color="green" />
          <Section title="Changes Applied" items={view.changes} color="green" />
          <Section title="Needs Manual Review" items={view.manual_review} color="yellow" />
        </div>
      )}
      {hasWebForms && (
        <div style={{ marginBottom: hasBlazor ? 20 : 0 }}>
          <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 10, color: '#1e293b' }}>Web Forms Pages</div>
          <div className="detail-grid" style={{ marginBottom: 12 }}>
            <article><span>Pages Processed</span><strong>{webforms.pages_processed ?? 0}</strong></article>
            <article><span>Controls Replaced</span><strong>{webforms.controls_replaced ?? 0}</strong></article>
            <article><span>LLM Passes</span><strong>{webforms.llm_passes ?? 0}</strong></article>
            <article><span>Manual Review</span><strong style={{ color: webforms.manual_review?.length ? '#f59e0b' : '#22c55e' }}>{webforms.manual_review?.length || 'None'}</strong></article>
          </div>
          <Section title="Structural Fixes" items={webforms.structural_fixes} color="green" />
          <Section title="Changes Applied" items={webforms.changes} color="green" />
          <Section title="Needs Manual Review" items={webforms.manual_review} color="yellow" />
        </div>
      )}
      {hasBlazor && (
        <div>
          <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 10, color: '#1e293b' }}>Blazor Components</div>
          <div className="detail-grid" style={{ marginBottom: 12 }}>
            <article><span>Components</span><strong>{blazor.components_processed ?? 0}</strong></article>
            <article><span>Fixes Applied</span><strong>{blazor.fixes_applied ?? 0}</strong></article>
            <article><span>LLM Passes</span><strong>{blazor.llm_passes ?? 0}</strong></article>
            <article><span>Manual Review</span><strong style={{ color: blazor.manual_review?.length ? '#f59e0b' : '#22c55e' }}>{blazor.manual_review?.length || 'None'}</strong></article>
          </div>
          <Section title="Structural Fixes" items={blazor.structural_fixes} color="green" />
          <Section title="Changes Applied" items={blazor.changes} color="green" />
          <Section title="Needs Manual Review" items={blazor.manual_review} color="yellow" />
        </div>
      )}
    </div>
  );
}

function UiWarningBanner({ profile }) {
  if (!profile || !profile.warning) return null;
  const colors = {
    not_supported: { border: '#ef4444', bg: '#fef2f2', icon: '✕', text: '#991b1b' },
    partial:       { border: '#f59e0b', bg: '#fffbeb', icon: '⚠', text: '#92400e' },
    backend_only:  { border: '#3b82f6', bg: '#eff6ff', icon: 'ℹ', text: '#1e40af' },
  };
  const c = colors[profile.support] || colors.partial;
  return (
    <div style={{ marginTop: 12, padding: '10px 14px', borderRadius: 8, borderLeft: `4px solid ${c.border}`, background: c.bg, display: 'flex', gap: 10, alignItems: 'flex-start' }}>
      <span style={{ fontWeight: 700, color: c.border, fontSize: 16, flexShrink: 0 }}>{c.icon}</span>
      <div>
        <div style={{ fontWeight: 700, color: c.text, fontSize: 13 }}>UI Detected: {profile.ui_type.replace('_', ' ').toUpperCase()}</div>
        <div style={{ fontSize: 12, color: c.text, marginTop: 2 }}>{profile.warning}</div>
        {profile.has_bundling && <div style={{ fontSize: 12, color: c.text, marginTop: 2 }}>Script bundling detected — will be converted to standard script references.</div>}
      </div>
    </div>
  );
}

function TokenUtilization({ stats }) {
  const cards = [
    { label: 'Total Tokens',           value: stats?.total_tokens,            icon: '⚡', accent: 'var(--brand)' },
    { label: 'Total Executions',       value: stats?.total_executions,        icon: '🔁', accent: 'var(--apple-strong)' },
    { label: 'Avg Tokens / Execution', value: stats?.avg_tokens_per_execution, icon: '📊', accent: 'var(--sunset-strong)' },
    { label: 'Total LLM Calls',        value: stats?.total_llm_calls,         icon: '🤖', accent: '#7c3aed' },
    { label: 'Avg Tokens / LLM Call',  value: stats?.avg_tokens_per_llm_call, icon: '📈', accent: 'var(--red)' },
  ];
  const maxVal = Math.max(1, ...cards.map((c) => Number(c.value) || 0));

  // Per-agent breakdown
  const byAgent = stats?.by_agent || {};
  const agentEntries = Object.entries(byAgent).sort((a, b) => b[1] - a[1]);
  const totalTokens = stats?.total_tokens || 1;

  // Context window
  const contextWindow = stats?.context_window || 128000;
  const contextPct = stats?.context_window_pct || 0;
  const contextColor = contextPct < 30 ? 'var(--apple)' : contextPct < 70 ? 'var(--sunset)' : 'var(--red)';
  const contextStatus = contextPct < 30 ? 'Well within limits' : contextPct < 70 ? 'Moderate usage' : 'High usage — monitor closely';

  return (
    <section className="ma-card">
      <div className="ma-card-header">
        <div className="ma-card-title">Token Utilization</div>
        <span className="section-status">{stats ? `${stats.model || 'Groq LLM'} — Live Data` : 'Available after migration'}</span>
      </div>
      <div className="ma-card-body">
        {/* Summary cards */}
        <div className="token-grid">
          {cards.map((c) => (
            <div className={`token-card ${!stats ? 'token-skeleton' : ''}`} key={c.label} style={{ '--accent': c.accent }}>
              <div className="token-icon">{c.icon}</div>
              <div className="token-value">{stats ? Number(c.value).toLocaleString() : '—'}</div>
              <div className="token-label">{c.label}</div>
              <div className="token-bar-track">
                <div className="token-bar-fill" style={{ width: stats ? `${Math.round((Number(c.value) / maxVal) * 100)}%` : '0%' }}></div>
              </div>
            </div>
          ))}
        </div>

        {/* Per-agent breakdown */}
        {stats && agentEntries.length > 0 && (
          <div style={{ marginTop: 24 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 800, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Per-Agent Token Breakdown</div>
              <div style={{ fontSize: 12, fontWeight: 800, color: 'var(--brand-strong)' }}>Total: {totalTokens.toLocaleString()} tokens</div>
            </div>
            <div style={{ display: 'grid', gap: 10 }}>
              {agentEntries.map(([agent, tokens]) => {
                const pct = Math.round((tokens / totalTokens) * 100);
                const barColor = pct > 60 ? 'var(--brand)' : pct > 30 ? 'var(--apple-strong)' : 'var(--apple)';
                return (
                  <div key={agent} style={{ display: 'grid', gridTemplateColumns: '180px 1fr 80px 52px', alignItems: 'center', gap: 12 }}>
                    <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{agent}</div>
                    <div style={{ height: 8, background: 'var(--line)', borderRadius: 999, overflow: 'hidden' }}>
                      <div style={{ height: '100%', width: `${pct}%`, background: barColor, borderRadius: 999, transition: 'width 0.6s ease' }}></div>
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--muted)', textAlign: 'right' }}>{tokens.toLocaleString()} tkns</div>
                    <div style={{ fontSize: 12, fontWeight: 800, color: barColor, textAlign: 'right' }}>{pct}%</div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Context window health */}
        {stats && (
          <div style={{ marginTop: 24, padding: '16px 18px', background: 'var(--alabaster)', border: '1px solid var(--line)', borderRadius: 8, display: 'flex', alignItems: 'center', gap: 24 }}>
            {/* Circle */}
            <div style={{ position: 'relative', width: 72, height: 72, flexShrink: 0 }}>
              <svg width="72" height="72" style={{ transform: 'rotate(-90deg)' }}>
                <circle cx="36" cy="36" r="28" fill="none" stroke="var(--line)" strokeWidth="7" />
                <circle cx="36" cy="36" r="28" fill="none" stroke={contextColor} strokeWidth="7"
                  strokeDasharray={`${2 * Math.PI * 28}`}
                  strokeDashoffset={`${2 * Math.PI * 28 * (1 - contextPct / 100)}`}
                  style={{ transition: 'stroke-dashoffset 0.6s ease' }}
                />
              </svg>
              <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column' }}>
                <span style={{ fontSize: 13, fontWeight: 900, color: contextColor }}>{contextPct}%</span>
              </div>
            </div>
            {/* Info */}
            <div>
              <div style={{ fontSize: 13, fontWeight: 800, color: 'var(--ink)', marginBottom: 4 }}>Context Window Usage per Call</div>
              <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 4 }}>
                Model: <strong style={{ color: 'var(--ink)' }}>{stats.model || 'llama-3.3-70b-versatile'}</strong> &nbsp;·&nbsp; Per-call limit: <strong style={{ color: 'var(--ink)' }}>{(contextWindow).toLocaleString()} tokens</strong>
              </div>
              <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 4 }}>
                Avg per call: <strong style={{ color: 'var(--ink)' }}>{Number(stats.avg_tokens_per_llm_call).toLocaleString()} tokens</strong>
                &nbsp;·&nbsp;
                <span style={{ fontWeight: 800, color: contextColor }}>✓ {contextStatus}</span>
              </div>
              <div style={{ fontSize: 11, color: 'var(--soft)', fontStyle: 'italic' }}>This is the per-call token limit, not a daily quota.</div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

function GuardrailDetail({ data }) {
  if (!data) return <div className="detail-list"><div>No guardrail data available.</div></div>;
  const scoreColor = data.score >= 80 ? '#22c55e' : data.score >= 50 ? '#f59e0b' : '#ef4444';
  const severityColor = { High: '#ef4444', Medium: '#f59e0b', Low: '#3b82f6' };
  const high   = (data.violations || []).filter(v => v.severity === 'High');
  const medium = (data.violations || []).filter(v => v.severity === 'Medium');
  const low    = (data.violations || []).filter(v => v.severity === 'Low');
  return (
    <div style={{ padding: '8px 0' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 16, padding: '14px 16px', background: '#f8fafc', borderRadius: 8, border: '1px solid #e2e8f0' }}>
        <div style={{ width: 64, height: 64, borderRadius: '50%', background: `conic-gradient(${scoreColor} ${data.score}%, #e2e8f0 0)`, display: 'grid', placeItems: 'center', flexShrink: 0, position: 'relative' }}>
          <div style={{ position: 'absolute', inset: 8, background: '#fff', borderRadius: '50%', display: 'grid', placeItems: 'center' }}>
            <span style={{ fontWeight: 900, fontSize: 16, color: scoreColor }}>{data.score}</span>
          </div>
        </div>
        <div>
          <div style={{ fontWeight: 700, fontSize: 15, color: scoreColor }}>{data.summary}</div>
          <div style={{ fontSize: 13, color: '#64748b', marginTop: 4 }}>{data.passed_count} checks passed · {data.violation_count} violation(s) found</div>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 10, marginBottom: 16 }}>
        {[['High', high, '#fef2f2', '#ef4444'], ['Medium', medium, '#fffbeb', '#f59e0b'], ['Low', low, '#eff6ff', '#3b82f6']].map(([label, items, bg, color]) => (
          <div key={label} style={{ padding: '10px 12px', background: bg, borderRadius: 7, border: `1px solid ${color}33` }}>
            <div style={{ fontWeight: 900, color, fontSize: 20 }}>{items.length}</div>
            <div style={{ fontSize: 11, fontWeight: 800, color, textTransform: 'uppercase' }}>{label} Severity</div>
          </div>
        ))}
      </div>
      {data.violations?.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8, color: '#1e293b' }}>Violations</div>
          {data.violations.map((v, i) => (
            <div key={i} style={{ display: 'flex', gap: 10, padding: '10px 12px', marginBottom: 6, background: '#fff', border: '1px solid #e2e8f0', borderLeft: `4px solid ${severityColor[v.severity] || '#94a3b8'}`, borderRadius: 7 }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 700, fontSize: 13, color: '#1e293b' }}>{v.rule}</div>
                <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>{v.file}</div>
                <div style={{ fontSize: 12, color: '#475569', marginTop: 4 }}>💡 {v.suggestion}</div>
              </div>
              <span style={{ fontSize: 11, fontWeight: 800, color: severityColor[v.severity], alignSelf: 'flex-start', padding: '2px 8px', background: `${severityColor[v.severity]}18`, borderRadius: 999 }}>{v.severity}</span>
            </div>
          ))}
        </div>
      )}
      {data.passed?.length > 0 && (
        <div>
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8, color: '#1e293b' }}>Passed Checks</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2,1fr)', gap: 6 }}>
            {data.passed.map((p, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: 7, fontSize: 12 }}>
                <span style={{ color: '#22c55e', fontWeight: 900 }}>✓</span>
                <span style={{ color: '#166534' }}>{p.rule}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function OrchestratorDecisions({ orchestrator }) {
  if (!orchestrator?.decision_log?.length) return null;
  const decisionColor = {
    goal_achieved:          '#22c55e',
    run_llm_fixer:          '#3b82f6',
    run_deterministic_fixer:'#f59e0b',
    skip_build:             '#94a3b8',
    escalate_to_user:       '#ef4444',
    give_up:                '#ef4444',
    abort:                  '#ef4444',
    plan_phase1:            '#8b5cf6',
    run_view_migrator:      '#06b6d4',
    skip_view_migrator:     '#94a3b8',
    run_webforms_migrator:  '#06b6d4',
    skip_webforms_migrator: '#94a3b8',
    run_blazor_migrator:    '#06b6d4',
    skip_blazor_migrator:   '#94a3b8',
  };
  return (
    <section className="ma-card">
      <div className="ma-card-header">
        <div className="ma-card-title">Orchestrator Decision Log</div>
        <span className="section-status">
          {orchestrator.goal_achieved ? '✅ Goal Achieved' : '⚠️ Needs Review'}
          {' — '}{orchestrator.decisions_made} decision(s), {orchestrator.agents_run} agent(s) ran
        </span>
      </div>
      <div className="ma-card-body">
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 10, marginBottom: 20 }}>
          <div style={{ padding: '12px 14px', background: '#f0fdf4', borderRadius: 8, border: '1px solid #bbf7d0' }}>
            <div style={{ fontSize: 22, fontWeight: 900, color: '#22c55e' }}>{orchestrator.agents_run}</div>
            <div style={{ fontSize: 12, color: '#166534', fontWeight: 700 }}>Agents Run</div>
          </div>
          <div style={{ padding: '12px 14px', background: '#eff6ff', borderRadius: 8, border: '1px solid #bfdbfe' }}>
            <div style={{ fontSize: 22, fontWeight: 900, color: '#3b82f6' }}>{orchestrator.decisions_made}</div>
            <div style={{ fontSize: 12, color: '#1e40af', fontWeight: 700 }}>Decisions Made</div>
          </div>
          <div style={{ padding: '12px 14px', background: '#faf5ff', borderRadius: 8, border: '1px solid #e9d5ff' }}>
            <div style={{ fontSize: 22, fontWeight: 900, color: '#8b5cf6' }}>{orchestrator.elapsed_seconds}s</div>
            <div style={{ fontSize: 12, color: '#6d28d9', fontWeight: 700 }}>Elapsed</div>
          </div>
        </div>
        <div style={{ display: 'grid', gap: 8 }}>
          {orchestrator.decision_log.map((d, i) => {
            const color = decisionColor[d.decision] || '#64748b';
            return (
              <div key={i} style={{ display: 'flex', gap: 12, padding: '10px 14px', background: '#f8fafc', borderRadius: 8, borderLeft: `4px solid ${color}` }}>
                <div style={{ flexShrink: 0, marginTop: 2 }}>
                  <span style={{ fontSize: 11, fontWeight: 800, color, background: `${color}18`, padding: '2px 8px', borderRadius: 999 }}>{d.decision}</span>
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 12, color: '#475569', marginBottom: 2 }}><strong style={{ color: '#1e293b' }}>Observed:</strong> {d.observation}</div>
                  <div style={{ fontSize: 12, color: '#64748b' }}><strong style={{ color: '#1e293b' }}>Reason:</strong> {d.reason}</div>
                </div>
                <div style={{ fontSize: 11, color: '#94a3b8', flexShrink: 0 }}>Round {d.round}</div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

function OrchestratorDecisionsDetail({ data }) {
  if (!data) return <div className="detail-list"><div>No orchestrator data available.</div></div>;
  return <OrchestratorDecisions orchestrator={data} />;
}

createRoot(document.getElementById('root')).render(<App />);
