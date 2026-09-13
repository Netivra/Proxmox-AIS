/* Proxmox AIS administration console. No client-side dependencies. */
'use strict';

const icons = {
  dashboard: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  server: '<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01M15 6.5h3M15 17.5h3"/>',
  activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
  workflow: '<rect x="3" y="3" width="6" height="6" rx="1.5"/><rect x="15" y="15" width="6" height="6" rx="1.5"/><path d="M6 9v7a2 2 0 0 0 2 2h7M15 6h6m-3-3v6"/>',
  code: '<path d="m8 6-6 6 6 6m8-12 6 6-6 6m-3-15-2 18"/>',
  disc: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2"/><path d="m7 7 2 2m6 6 2 2"/>',
  shield: '<path d="M12 3 3 7v5c0 5 9 9 9 9s9-4 9-9V7l-9-4Z"/><path d="m8 12 3 3 5-6"/>',
  settings: '<path d="m9 3-1 3-3 1-2 4 2 2v3l3 2 1 3h5l1-3 3-1 2-4-2-2V8l-3-2-1-3Z"/><circle cx="11.5" cy="12" r="3"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  arrow: '<path d="M4 12h15m-6-6 6 6-6 6"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.5 8a7 7 0 0 1 11.8-3L20 8M4 16l2.7 3A7 7 0 0 0 18.5 16"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  alert: '<path d="m12 3 10 18H2L12 3Z"/><path d="M12 9v5m0 3h.01"/>',
  edit: '<path d="m16 3 5 5-12 12-6 1 1-6L16 3Zm-3 3 5 5"/>',
  key: '<circle cx="8" cy="8" r="5"/><path d="m12 12 9 9m-4-4 3-3m-6 0 3-3"/>',
  users: '<circle cx="9" cy="8" r="4"/><path d="M2 21v-3a7 7 0 0 1 14 0v3M17 4a4 4 0 0 1 0 8m3 9v-3a6 6 0 0 0-3-5"/>',
  lock: '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2"/>',
  copy: '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
  back: '<path d="M20 12H5m6-6-6 6 6 6"/>',
  stop: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
  play: '<path d="m7 4 14 8-14 8V4Z"/>',
  file: '<path d="M14 3H5v18h14V8l-5-5Zm0 0v5h5M8 13h8M8 17h5"/>',
};
const svg = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.file}</svg>`;
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const json = value => JSON.stringify(value ?? {}, null, 2);
const arr = value => Array.isArray(value) ? value : (value?.items || []);
const toDate = value => new Date(typeof value==='number' && value<1e12 ? value*1000 : value);
const fmtDate = value => value && !Number.isNaN(toDate(value).getTime()) ? new Intl.DateTimeFormat('de-DE', {dateStyle:'short',timeStyle:'short'}).format(toDate(value)) : 'Noch kein Kontakt';
const shortId = value => String(value || '–').slice(0, 10);
const roleNames = {reader:'Leser',operator:'Operator',author:'Skriptautor',admin:'Administrator',developer:'Entwickler'};
const statusNames = {draft:'Entwurf',published:'Veröffentlicht',discovered:'Entdeckt',ready:'Bereit',prepared:'Freigegeben',approved:'Freigegeben',answer_served:'Antwort ausgeliefert',answer_delivered:'Antwort ausgeliefert',installing:'Installation',installed:'Installiert',installed_reported:'Basisinstallation gemeldet',bootstrapping:'Erster Start',enrolled:'Registriert',runner_ready:'Runner bereit',running:'In Ausführung',postinstall_running:'Nachkonfiguration',postinstalling:'Nachkonfiguration',waiting_retry:'Wiederaufnahme erwartet',reboot_pending:'Neustart erwartet',waiting_reboot:'Neustart erwartet',rebooting:'Neustart',succeeded:'Erfolgreich',failed:'Fehlgeschlagen',needs_review:'Prüfung nötig',unknown:'Kontakt unbekannt',cancelled:'Abgebrochen',canceled:'Abgebrochen',cancel_requested:'Abbruch angefordert',blocked:'Gesperrt',pending:'Ausstehend',checking:'Prüfen',applying:'Anwenden',verifying:'Validieren',skipped:'Übersprungen',active:'Aktiv',passed:'Geprüft',expired:'Abgelaufen',revoked:'Widerrufen'};
const greenStatuses = new Set(['succeeded','passed','published','active','ready']);
const redStatuses = new Set(['failed','needs_review','blocked','revoked']);
const orangeStatuses = new Set(['draft','pending','discovered','unknown','expired','waiting_reboot','waiting_retry','reboot_pending','cancel_requested']);
const blueStatuses = new Set(['approved','prepared','answer_served','answer_delivered','installing','installed','bootstrapping','enrolled','runner_ready','running','postinstall_running','postinstalling','checking','applying','verifying','rebooting']);
const badge = status => `<span class="badge ${greenStatuses.has(status)?'green':redStatuses.has(status)?'red':orangeStatuses.has(status)?'orange':blueStatuses.has(status)?'blue':''}">${esc(statusNames[status] || status || 'Unbekannt')}</span>`;
const state = {me:null,page:'dashboard',id:null,data:null,routeVersion:0,settingsTab:'users',runTab:'steps',refreshing:false};
const canOperate = () => ['admin','developer','operator'].includes(state.me?.role);
const canAuthor = () => ['admin','developer','author'].includes(state.me?.role);
const canAdmin = () => ['admin','developer'].includes(state.me?.role);
const main = document.getElementById('main');
const modal = document.getElementById('modal');
let modalSubmit = null;

function errorMessage(detail) {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(e => `${(e.loc || []).filter(v=>v!=='body').join('.')}: ${e.msg || json(e)}`).join('\n');
  return detail?.message || json(detail);
}
async function api(path, options = {}) {
  const headers = {'Accept':'application/json', ...(options.headers || {})};
  if (options.body !== undefined && typeof options.body !== 'string') {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(options.body);
  }
  if (options.method && options.method !== 'GET') headers['X-CSRF-Token'] = state.me?.csrf_token || '';
  const response = await fetch(path.startsWith('/auth/') ? path : `/api/v1${path}`, {...options, headers, credentials:'same-origin'});
  if (response.status === 401) { window.location.href = '/login'; throw new Error('Ihre Sitzung ist abgelaufen. Bitte erneut anmelden.'); }
  const contentType = response.headers.get('content-type') || '';
  const result = response.status === 204 ? null : contentType.includes('json') ? await response.json() : await response.text();
  if (!response.ok) throw new Error(errorMessage(result?.errors || result?.detail || result?.message || result || `Anfrage fehlgeschlagen (${response.status})`));
  return result;
}
function toast(message, isError=false) {
  const el=document.createElement('div'); el.className=`toast${isError?' error':''}`; el.textContent=message;
  const region=document.getElementById('toast-region');while(region.children.length>=3)region.firstElementChild.remove();region.append(el); setTimeout(()=>el.remove(), isError?9000:4500);
}
function actionButton(label, action, icon='plus', attrs='', primary=false) {
  if(action==='preview-host' && !canOperate() && !canAuthor())return '';
  return `<button type="button" class="button${primary?' primary':''}" data-action="${action}" ${attrs}>${svg(icon)}${esc(label)}</button>`;
}
function header(title, description, actions='', eyebrow='PROVISIONING CONSOLE') {
  return `<div class="page-header"><div><span class="eyebrow">${esc(eyebrow)}</span><h1>${esc(title)}</h1><p>${esc(description)}</p></div><div class="header-actions">${actions}</div></div>`;
}
function empty(title, description, icon='server', action='') {
  return `<div class="empty-state"><div class="empty-icon">${svg(icon)}</div><h3>${esc(title)}</h3><p>${esc(description)}</p>${action}</div>`;
}
function table(headings, rows) {
  return `<div class="table-wrap"><table><thead><tr>${headings.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
}
function toolbar(placeholder='Suchen …', sites=[], statuses=[]) {
  return `<div class="toolbar"><div class="filter-group"><label class="search-field">${svg('search')}<input id="list-search" type="search" aria-label="Liste durchsuchen" placeholder="${esc(placeholder)}"></label>${sites.length?`<select id="site-filter" class="filter-select" aria-label="Nach Standort filtern"><option value="">Alle Standorte</option>${sites.map(s=>`<option>${esc(s)}</option>`).join('')}</select>`:''}${statuses.length?`<select id="status-filter" class="filter-select" aria-label="Nach Status filtern"><option value="">Alle Status</option>${statuses.map(s=>`<option value="${esc(s)}">${esc(statusNames[s]||s)}</option>`).join('')}</select>`:''}</div><span class="toolbar-info" id="filter-count"></span></div>`;
}
const searchAttrs = (value, site='', status='') => `data-search="${esc(String(value).toLowerCase())}" data-site="${esc(site)}" data-status="${esc(status)}"`;
function applyFilters() {
  const query=(document.getElementById('list-search')?.value || '').toLowerCase();
  const site=document.getElementById('site-filter')?.value || '';
  const status=document.getElementById('status-filter')?.value || '';
  let count=0, total=0;
  main.querySelectorAll('[data-search]').forEach(row=>{
    total++; const visible=row.dataset.search.includes(query) && (!site || row.dataset.site===site) && (!status || row.dataset.status===status);
    row.hidden=!visible; if(visible) count++;
  });
  const countEl=document.getElementById('filter-count'); if(countEl) countEl.textContent=`${count} ${count===1?'Eintrag':'Einträge'}${count!==total?` von ${total}`:''}`;
  let noResults=document.getElementById('no-filter-results');
  if(!noResults && total) {noResults=document.createElement('div');noResults.id='no-filter-results';noResults.className='empty-state';noResults.textContent='Keine Einträge für diese Auswahl.';main.querySelector('.filterable')?.append(noResults);}
  if(noResults) noResults.hidden=count!==0;
}
function hostRows(hosts, compact=false) {
  return hosts.map(h=>`<tr ${searchAttrs(`${h.fqdn} ${h.management_ip} ${h.site} ${(h.tags||[]).join(' ')}`,h.site,h.blocked?'blocked':h.status)}><td><div class="table-title"><span class="row-icon">${svg('server')}</span><div><a href="#/hosts/${encodeURIComponent(h.id)}"><strong>${esc(h.fqdn || h.name || `Entdeckter Host ${shortId(h.id)}`)}</strong></a><small>${esc(h.management_ip || 'Keine Management-IP')}</small></div></div></td><td>${esc(h.site || '–')}</td><td>${badge(h.blocked?'blocked':h.status)}</td>${compact?'':`<td>${(h.tags||[]).map(t=>`<span class="tag">${esc(t)}</span>`).join('') || '<span class="muted">–</span>'}</td><td>${esc(fmtDate(h.last_contact || h.last_seen))}</td>`}<td><a class="button-link" href="#/hosts/${encodeURIComponent(h.id)}">Details ${svg('arrow')}</a></td></tr>`);
}
function runRows(runs) {
  return runs.map(r=>{
    const steps=arr(r.steps),finished=steps.filter(s=>['succeeded','failed','skipped'].includes(s.status)).length;
    const waiting={prepared:'Freigabe aktiv · wartet auf ISO',answer_served:'Antwort ausgeliefert',answer_delivered:'Antwort ausgeliefert',installed_reported:'Wartet auf den ersten Start',installing:'Basisinstallation',installed:'Wartet auf den Runner'}[r.status];
    const progress=steps.length?Math.round(finished/steps.length*100):0;
    const progressCell=waiting?esc(waiting):steps.length?`${finished} / ${steps.length} geprüft<progress class="run-progress" max="100" value="${progress}" aria-label="Geprüfte Konfigurationsschritte">${progress}%</progress>`:esc(fmtDate(r.started_at || r.created_at));
    return `<tr ${searchAttrs(`${r.host_fqdn||r.fqdn||r.host_id} ${r.id}`,r.site,r.status)}><td><div class="table-title"><span class="row-icon">${svg('activity')}</span><div><a href="#/runs/${encodeURIComponent(r.id)}"><strong>${esc(r.host_fqdn || r.fqdn || shortId(r.host_id))}</strong></a><small>Lauf ${esc(shortId(r.id))}</small></div></div></td><td>${badge(r.cancel_requested && !['cancelled','succeeded','failed','expired'].includes(r.status)?'cancel_requested':r.status)}${r.contact_status==='unknown'?` ${badge('unknown')}`:''}</td><td>${progressCell}</td><td><a class="button-link" href="#/runs/${encodeURIComponent(r.id)}">Ansehen ${svg('arrow')}</a></td></tr>`;
  });
}
function eventList(events, blank='Noch keine Ereignisse') {
  if(!events.length) return empty(blank,'Ereignisse erscheinen hier, sobald Sie Hosts und Konfigurationen verwalten.','activity');
  return `<div class="activity-list">${events.map(e=>`<div class="activity-item"><span class="event-dot">${svg(e.status==='failed'?'alert':'activity')}</span><div class="activity-copy"><strong>${esc(e.message || e.action || e.type || e.event_type || 'Statusänderung')}</strong><p>${esc(e.host_fqdn || e.actor || e.username || e.object_id || shortId(e.run_id))} · ${esc(fmtDate(e.created_at || e.timestamp))}</p></div></div>`).join('')}</div>`;
}
function dashboard(data) {
  const c=data.counts||{}, hosts=arr(data.hosts), runs=arr(data.runs), events=arr(data.recent_events);
  const metrics=[['Server gesamt',c.hosts||0,'Inventarisierte Hosts','server',''],['Bereit zur Installation',c.ready||0,'Freigegebene Server','shield','orange'],['Aktive Läufe',c.active||0,'Installation & Nachkonfiguration','activity','green'],['Prüfung erforderlich',c.needs_review||0,'Vorgänge mit Handlungsbedarf','alert','red']];
  return header('Infrastruktur im Überblick','Installationen steuern. Konfigurationen prüfen. Den Überblick behalten.',actionButton('Aktualisieren','refresh','refresh')+(canOperate()?actionButton('Server hinzufügen','create-host','plus','',true):''),'WORKSPACE / ÜBERSICHT')+
    `<div class="metrics-grid">${metrics.map(m=>`<div class="metric"><div class="metric-label">${m[0]}<span class="metric-icon ${m[4]}">${svg(m[3])}</span></div><div class="metric-value">${Number(m[1])}</div><div class="metric-note">${m[2]}</div></div>`).join('')}</div><div class="dashboard-grid"><div class="stack"><section class="card"><div class="card-header"><div><h2>Installationsläufe <span class="count-label">${runs.length}</span></h2><p>Die letzten Provisionierungen und ihr aktueller Stand</p></div><a class="button-link" href="#/runs">Alle Läufe ${svg('arrow')}</a></div>${runs.length?table(['SERVER','STATUS','FORTSCHRITT',''],runRows(runs.slice(0,6))):empty('Bereit für den ersten Lauf','Erfassen Sie einen Server, weisen Sie geprüfte Profile zu und erteilen Sie die Installationsfreigabe.','activity',canOperate()?actionButton('Server erfassen','create-host','plus'):'')}<div class="card-footer"><span><span class="status-dot"></span> Status aus persistenten Laufereignissen</span><span>${Number(c.succeeded||0)} erfolgreich abgeschlossen</span></div></section><section class="card"><div class="card-header"><div><h2>Serverinventar <span class="count-label">${Number(c.hosts||0)}</span></h2><p>Ihre zuletzt erfassten Server</p></div><a class="button-link" href="#/hosts">Zum Inventar ${svg('arrow')}</a></div>${hosts.length?table(['SERVER','STANDORT','STATUS',''],hostRows(hosts.slice(0,5),true)):empty('Ihr Inventar beginnt hier','Identitäten, Managementnetz und Profilzuordnungen an einem Ort.','server')}<div class="card-footer"><span>${Number(c.discovered||0)} neu entdeckte Hosts</span><a href="#/hosts">Inventar verwalten →</a></div></section></div><div class="stack"><section class="card intro-card"><div class="card-header"><span class="eyebrow">${c.hosts?'IHR PROVISIONIERUNGSABLAUF':'ERSTE SCHRITTE'}</span>${svg('workflow')}</div><div class="card-content"><h3>Von der ISO zum<br>fertigen Server.</h3><p>Ein klarer Ablauf für jede Installation.</p><div class="setup-steps"><a class="setup-step" href="#/media"><span class="setup-number">01</span><span><strong>Installationsmedium vorbereiten</strong><small>ISO registrieren und Build prüfen</small></span></a><a class="setup-step" href="#/installation"><span class="setup-number">02</span><span><strong>Konfiguration festlegen</strong><small>Profile und Skriptversionen veröffentlichen</small></span></a><a class="setup-step${c.hosts?' complete':''}" href="#/hosts"><span class="setup-number">${c.hosts?'✓':'03'}</span><span><strong>Server erfassen & freigeben</strong><small>Vorschau prüfen, Installation bestätigen</small></span></a></div></div></section><section class="card"><div class="card-header"><div><h2>Letzte Ereignisse</h2><p>Was sich in Ihrem Workspace verändert</p></div></div>${eventList(events.slice(0,5))}<div class="card-footer"><a class="button-link" href="#/audit">Auditprotokoll öffnen ${svg('arrow')}</a></div></section></div></div>`;
}
function hostsPage(hosts, discoveries=[]) {
  const unassigned=discoveries.filter(d=>!hosts.some(h=>arr(h.identities).some(i=>arr(d.identities).some(v=>v.kind===i.kind && v.value.toLowerCase()===i.value.toLowerCase()))));
  return header('Serverinventar','Serveridentitäten, Netzwerke und freigegebene Konfigurationen verwalten.',actionButton('Aktualisieren','refresh','refresh')+(canOperate()?actionButton('JSON importieren','import-hosts','file')+actionButton('Server hinzufügen','create-host','plus','',true):''),'VERWALTUNG / SERVER')+`<section class="card filterable">${toolbar('FQDN, IP-Adresse oder Tag suchen …',[...new Set(hosts.map(h=>h.site).filter(Boolean))],[...new Set(hosts.map(h=>h.blocked?'blocked':h.status).filter(Boolean))])}${hosts.length?table(['SERVER','STANDORT','STATUS','TAGS','LETZTER KONTAKT',''],hostRows(hosts)):empty('Noch keine Server erfasst','Erfassen Sie einen Host anhand seiner UUID, Seriennummer oder MAC-Adresse.','server',canOperate()?actionButton('Ersten Server hinzufügen','create-host','plus','',true):'')}</section>`+(unassigned.length?`<section class="card space-top"><div class="card-header"><div><h2>Entdeckte Hardware <span class="count-label">${unassigned.length}</span></h2><p>Noch nicht zugeordnete Geräte erhalten keine Installationskonfiguration.</p></div>${badge('discovered')}</div>${table(['IDENTITÄTEN','STANDORT / BUILD','ABLEHNUNGSGRUND',''],unassigned.map(d=>`<tr><td>${arr(d.identities).map(i=>`<div class="small-text mono">${esc(i.kind)}: ${esc(i.value)}</div>`).join('')}</td><td>${esc(d.site)}<br><span class="small-text muted">${esc(d.build)}</span></td><td>${esc(d.reason)}</td><td>${canOperate()?`<button class="button small" data-action="assign-discovery" data-id="${esc(d.id)}">Als Server erfassen</button>`:''}</td></tr>`))}</section>`:'');
}
function hostPage(host) {
  const id=esc(host.id), runs=arr(host.runs), identities=arr(host.identities);
  return header(host.fqdn||'Entdeckter Server',`${host.site||'Kein Standort'} · ${host.management_ip||'Keine Management-IP'}`,`<a class="button" href="#/hosts">${svg('back')}Inventar</a>${canOperate()?actionButton('Bearbeiten','edit-host','edit',`data-id="${id}"`)+actionButton('Installation freigeben','approve-host','shield',`data-id="${id}"`,true):''}`,'SERVERDETAIL / '+shortId(host.id))+
    `<div class="detail-grid"><div class="stack"><section class="card"><div class="card-header"><h2>Serverkonfiguration</h2>${badge(host.blocked?'blocked':host.status)}</div><div class="card-content"><dl class="detail-list"><dt>FQDN</dt><dd>${esc(host.fqdn||'–')}</dd><dt>Management-IP</dt><dd class="mono">${esc(host.management_ip||'–')}</dd><dt>Standort</dt><dd>${esc(host.site||'–')}</dd><dt>Tags</dt><dd>${(host.tags||[]).map(t=>`<span class="tag">${esc(t)}</span>`).join('')||'–'}</dd><dt>Letzter Kontakt</dt><dd>${esc(fmtDate(host.last_contact||host.last_seen))}</dd><dt>Versionsstand</dt><dd>${esc(host.version)}</dd></dl><h3 class="section-label">Hardware-Identitäten</h3>${identities.length?`<dl class="detail-list">${identities.map(i=>`<dt>${esc({uuid:'System-UUID',serial:'Seriennummer',mac:'MAC-Adresse'}[i.kind]||i.kind)}</dt><dd class="mono">${esc(i.value)}</dd>`).join('')}</dl>`:'<p class="muted small-text">Noch keine Identitäten hinterlegt.</p>'}</div><div class="card-footer"><span>Erfasst am ${esc(fmtDate(host.created_at))}</span>${canOperate()?`<button class="button-link" data-action="toggle-host" data-id="${id}">${host.blocked?'Host entsperren':'Host sperren'}</button>`:''}</div></section><section class="card"><div class="card-header"><h2>Installationshistorie</h2><span class="count-label">${runs.length}</span></div>${runs.length?table(['LAUF','STATUS','FORTSCHRITT',''],runRows(runs)):empty('Noch keine Installationsläufe','Nach einer Freigabe und dem ersten ISO-Kontakt erscheint hier der zugehörige Lauf.','activity')}</section></div><div class="stack"><section class="card"><div class="card-header"><h2>Profilzuordnung</h2>${svg('layers')}</div><div class="card-content"><dl class="detail-list"><dt>Installation</dt><dd>${esc(host.installation_profile_name||host.installation_profile_id||'Nicht zugewiesen')}</dd><dt>Postinstallation</dt><dd>${esc(host.postinstall_profile_name||host.postinstall_profile_id||'Nicht zugewiesen')}</dd><dt>ISO-Medium</dt><dd>${esc(host.iso_name||host.iso_id||'Nicht zugewiesen')}</dd></dl><p class="small-text muted space-top">Jeder Lauf bindet feste Profil- und Modulversionen. Spätere Änderungen wirken auf neue Läufe.</p>${actionButton('Aufgelöste Vorschau','preview-host','file',`data-id="${id}"`)}</div></section>${host.discovered_data?`<section class="card"><div class="card-header"><h2>Erkannte Systemdaten</h2></div><div class="card-content"><pre class="code-block light">${esc(json(host.discovered_data))}</pre></div></section>`:''}<section class="card"><div class="card-header"><h2>Hostüberschreibungen</h2></div><div class="card-content"><pre class="code-block light">${esc(json(host.overrides))}</pre></div></section></div></div>`;
}
function profilesPage(profiles, kind) {
  const installation=kind==='installation'; const list=profiles.filter(p=>p.kind===kind);
  return header(installation?'Installationsprofile':'Postinstallationsprofile',installation?'Sprache, Managementnetz und explizite Systemdatenträger versioniert definieren.':'Geprüfte Module in einen nachvollziehbaren Ablauf mit festen Versionen bringen.',canAuthor()?actionButton('Profil erstellen','create-profile','plus',`data-kind="${kind}"`,true):'',`KONFIGURATION / ${installation?'INSTALLATION':'POSTINSTALLATION'}`)+
    (list.length?`<div class="card space-bottom">${toolbar('Profilname oder Zielbuild suchen …')}</div><div class="cards-grid filterable">${list.map(p=>`<section class="card profile-card" ${searchAttrs(`${p.name} ${(p.target_builds||[]).join(' ')}`)}><div class="profile-top"><span class="profile-symbol${installation?'':' blue'}">${svg(installation?'layers':'workflow')}</span>${badge(p.status)}</div><h2>${esc(p.name)}</h2><p>Version ${esc(p.version)} · ${installation?'Installationskonfiguration':`${arr(p.steps).length} Konfigurationsschritte`}</p><div class="profile-meta">${(p.target_builds||[]).map(b=>`<span class="tag">PVE ${esc(b)}</span>`).join('')||'<span class="muted small-text">Keine Zielbuilds</span>'}</div><code title="${esc(p.digest||'')}">${p.digest?`SHA256 ${esc(p.digest.slice(0,25))}…`:'Digest nach Veröffentlichung'}</code><div class="profile-actions"><button class="button small" data-action="view-profile" data-id="${esc(p.id)}">${svg('file')}Ansehen</button>${canAuthor()?`<button class="button small" data-action="version-profile" data-id="${esc(p.id)}">${svg('plus')}Neue Version</button>`:''}${canAdmin()&&p.status==='draft'?`<button class="button small primary" data-action="publish-profile" data-id="${esc(p.id)}">Freigeben</button>`:''}</div></section>`).join('')}</div>`:`<section class="card">${empty(installation?'Noch keine Installationsprofile':'Noch keine Postinstallationsprofile',installation?'Legen Sie zuerst ein Root-Geheimnis und die Netzwerk- und Datenträgerkonfiguration Ihres Hardwaretyps an.':'Erstellen und veröffentlichen Sie zunächst Skriptmodule. Fassen Sie diese anschließend zu einem Ablauf zusammen.',installation?'layers':'workflow',canAuthor()?actionButton('Erstes Profil erstellen','create-profile','plus',`data-kind="${kind}"`,true):'')}</section>`);
}
function modulesPage(modules) {
  return header('Skriptmodule','Check, Apply und Verify: versionierte Bausteine für die Nachkonfiguration.',canAuthor()?actionButton('Aus Vorlage','module-catalog','layers')+actionButton('Modul erstellen','create-module','plus','',true):'','KONFIGURATION / SKRIPTE')+`<section class="card filterable">${toolbar('Name oder Zielbuild suchen …',[],['draft','published'])}${modules.length?table(['MODUL','VERSION','STATUS','ZIELBUILDS','WIEDERHOLUNG',''],modules.map(m=>`<tr ${searchAttrs(`${m.name} ${(m.target_builds||[]).join(' ')}`,'',m.status)}><td><div class="table-title"><span class="row-icon">${svg('code')}</span><div><strong>${esc(m.name)}</strong><small>${Number(m.timeout_seconds||300)} s Timeout</small></div></div></td><td>v${esc(m.version)}</td><td>${badge(m.status)}</td><td>${(m.target_builds||[]).map(b=>`<span class="tag">${esc(b)}</span>`).join('')}</td><td>${m.retry_safe?'Explizit erlaubt':'Manuelle Prüfung'}</td><td><div class="table-actions"><button class="button small" data-action="view-module" data-id="${esc(m.id)}">Ansehen</button>${canAuthor()?`<button class="button small" data-action="version-module" data-id="${esc(m.id)}">Neue Version</button>`:''}${canAdmin()&&m.status==='draft'?`<button class="button small primary" data-action="publish-module" data-id="${esc(m.id)}">Freigeben</button>`:''}</div></td></tr>`)):empty('Ihre Konfiguration als Bausteine','Jedes Modul definiert Zustandsprüfung, Änderung und Erfolgskontrolle. Eine Veröffentlichung erfordert einen Testnachweis.','code',canAuthor()?actionButton('Erstes Modul erstellen','create-module','plus','',true):'')}</section>`;
}
function runsPage(runs) {
  return header('Installationsläufe','Installationen und Nachkonfigurationen vom ersten Kontakt bis zur Abschlussprüfung.',actionButton('Aktualisieren','refresh','refresh'),'VERWALTUNG / LÄUFE')+`<section class="card filterable">${toolbar('Host oder Lauf-ID suchen …',[],[...new Set(runs.map(r=>r.status))])}${runs.length?table(['SERVER / LAUF','STATUS','FORTSCHRITT / START',''],runRows(runs)):empty('Noch keine Installationsläufe','Starten Sie einen freigegebenen Server mit dem registrierten Installationsmedium. Der Lauf wird beim Antwortabruf automatisch angelegt.','activity','<a class="button" href="#/hosts">Zum Serverinventar →</a>')}</section>`;
}
function runPage(run) {
  const steps=arr(run.steps), events=arr(run.events), logs=arr(run.logs), terminal=['succeeded','failed','cancelled','canceled','expired'].includes(run.status);
  const resumeAllowed=['needs_review','waiting_retry'].includes(run.status) && !run.cancel_requested;
  const actions=`<a class="button" href="#/runs">${svg('back')}Alle Läufe</a>${canOperate()&&resumeAllowed?actionButton('Wiederaufnehmen','resume-run','play',`data-id="${esc(run.id)}"`,true):''}${canOperate()&&!terminal?actionButton('Abbrechen','cancel-run','stop',`data-id="${esc(run.id)}"`)+actionButton('Lauf abgleichen','reconcile-run','shield',`data-id="${esc(run.id)}"`):''}`;
  let content='';
  if(state.runTab==='steps') content=steps.length?`<ol class="run-steps">${steps.map((s,i)=>`<li class="run-step"><span class="step-number">${s.status==='succeeded'?'✓':i+1}</span><div class="run-step-copy"><strong>${esc(s.name || s.step_id || s.id || `Schritt ${i+1}`)}</strong><p>Modul ${esc(s.module_name || s.module_id || '–')} · Versuch ${Number(s.attempt || s.attempts || 0)}${s.exit_code!=null?` · Exit ${Number(s.exit_code)}`:''}</p>${s.required===false?'<p>Optionaler Schritt</p>':''}${s.error?`<p>${esc(s.error)}</p>`:''}${s.verification&&Object.keys(s.verification).length?`<details><summary>Pr?fergebnis${s.status==='failed'?' / Fehlerursache':''}</summary><pre class="code-block light">${esc(json(s.verification))}</pre></details>`:''}${s.checkpoint?`<details><summary>Checkpoint</summary><pre class="code-block light">${esc(json(s.checkpoint))}</pre></details>`:''}</div>${badge(s.status)}</li>`).join('')}</ol>`:empty('Noch keine Schritte gemeldet','Die fixierten Schritte erscheinen mit dem Start der Nachkonfiguration.','workflow');
  if(state.runTab==='events') content=eventList(events,'Noch keine Laufereignisse');
  if(state.runTab==='logs') content=logs.length?`<div class="card-content"><pre class="code-block">${esc(logs.map(l=>typeof l==='string'?l:`${l.created_at?fmtDate(l.created_at)+' ':''}${l.step_id?'['+l.step_id+'] ':''}${l.content||l.text||l.message||json(l)}`).join('\n'))}</pre></div>`:empty('Noch keine Protokolldaten','Der Runner übermittelt redigierte Protokolle während der Ausführung.','code');
  return header(run.host_fqdn||run.fqdn||`Lauf ${shortId(run.id)}`,`Lauf ${run.id}`,actions,'INSTALLATIONSLAUF')+`<div class="detail-grid"><div class="stack"><section class="card"><div class="card-header"><h2>Ausführungsstatus</h2>${badge(run.status)}</div><div class="card-content"><div class="tabs" role="tablist" aria-label="Laufdetails">${[['steps','Schritte'],['events','Ereignisse'],['logs','Protokolle']].map(([id,label])=>`<button role="tab" type="button" aria-selected="${state.runTab===id}" class="tab${state.runTab===id?' active':''}" data-action="run-tab" data-tab="${id}">${label}${id==='steps'?` (${steps.length})`:''}</button>`).join('')}</div>${run.error||run.error_reason?`<div class="alert alert-danger">${esc(run.error||run.error_reason)}</div>`:''}</div>${content}</section></div><div class="stack"><section class="card"><div class="card-header"><h2>Laufdaten</h2></div><div class="card-content"><dl class="detail-list"><dt>Server</dt><dd><a href="#/hosts/${encodeURIComponent(run.host_id)}">${esc(run.host_fqdn||shortId(run.host_id))} ↗</a></dd><dt>Gestartet</dt><dd>${esc(fmtDate(run.started_at||run.created_at))}</dd><dt>Abgeschlossen</dt><dd>${run.finished_at||run.completed_at?esc(fmtDate(run.finished_at||run.completed_at)):'–'}</dd><dt>Letzter Heartbeat</dt><dd>${esc(fmtDate(run.last_heartbeat||run.last_contact||run.last_seen))}</dd><dt>Versionsstand</dt><dd>${esc(run.version)}</dd><dt>Manifest-Digest</dt><dd class="mono">${esc(run.manifest_digest||'Noch nicht erstellt')}</dd><dt>Antwort-Digest</dt><dd class="mono">${esc(run.answer_digest||run.answer_sha256||'–')}</dd></dl></div></section><section class="card"><div class="card-header"><h2>Fixierte Konfiguration</h2>${svg('lock')}</div><div class="card-content"><p class="small-text muted">Profil- und Skriptversionen dieses Laufs bleiben nach der Reservierung unverändert.</p><pre class="code-block light">${esc(json(run.manifest || run.snapshot || run.profiles || {installation_profile_id:run.installation_profile_id,postinstall_profile_id:run.postinstall_profile_id}))}</pre></div></section></div></div>`;
}
function mediaPage(records, groups=[]) {
  return header('Installationsmedien','Gemeinsame ISO-Medien registrieren, Zugriffe begrenzen und Kompatibilität belegen.',(canAdmin()?actionButton('Gruppentoken erstellen','create-group','key')+actionButton('ISO registrieren','create-iso','plus','',true):''),'KONFIGURATION / MEDIEN')+
    `<div class="alert alert-info">Die ISO wird auf einer Build-Maschine mit dem Proxmox Auto Install Assistant vorbereitet. Registrieren Sie anschließend den konkreten Build mit Prüfsumme und Testnachweis.</div><div class="stack"><section class="card"><div class="card-header"><div><h2>Registrierte ISO-Medien <span class="count-label">${records.length}</span></h2><p>Freigabe ausschließlich für die dokumentierte Build-Kombination</p></div></div>${records.length?table(['MEDIUM','ZIELBUILD','ASSISTANT','TESTSTATUS',''],records.map(r=>`<tr><td><div class="table-title"><span class="row-icon">${svg('disc')}</span><div><strong>${esc(r.name)}</strong><small>${esc(shortId(r.sha256))}…</small></div></div></td><td>${esc(r.build)}</td><td>${esc(r.assistant_version)}</td><td>${badge(r.test_status)}</td><td><button class="button small" data-action="view-iso" data-id="${esc(r.id)}">Details & Buildbefehl</button></td></tr>`)):empty('Noch keine Installationsmedien','Erstellen Sie zuerst eine Bereitstellungsgruppe. Registrieren Sie danach Ihr geprüftes ISO-Medium.','disc',canAdmin()?actionButton('ISO registrieren','create-iso','plus','',true):'')}</section><section class="card groups-card"><div class="card-header"><div><h2>Bereitstellungsgruppen</h2><p>Zeitlich begrenzte Gruppentoken für den Antwortabruf</p></div></div>${groups.length?table(['GRUPPE','STANDORT','GÜLTIG BIS','STATUS'],groups.map(g=>`<tr><td><strong>${esc(g.name)}</strong><br><small class="muted mono">${esc(g.id)}</small></td><td>${esc(g.site||'–')}</td><td>${esc(fmtDate(g.expires_at))}</td><td>${badge(g.revoked?'revoked':toDate(g.expires_at)<new Date()?'expired':'active')}</td></tr>`)):empty('Keine Gruppen vorhanden','Ein Gruppentoken berechtigt zum Antwortabruf für zugeordnete und freigegebene Hosts.','key',canAdmin()?actionButton('Gruppe erstellen','create-group','plus'):'')}</section></div>`;
}
function auditPage(events) {
  return header('Auditprotokoll','Änderungen, Freigaben und privilegierte Zugriffe nachvollziehen.',actionButton('Aktualisieren','refresh','refresh'),'SYSTEM / AUDIT')+`<section class="card filterable">${toolbar('Aktion, Benutzer oder Objekt suchen …')}${events.length?table(['ZEITPUNKT','AKTEUR','AKTION','OBJEKT',''],events.map(e=>`<tr ${searchAttrs(`${e.action} ${e.actor||e.username||e.actor_id} ${e.object_id||e.target_id} ${e.reason||''}`)}><td>${esc(fmtDate(e.created_at||e.timestamp))}</td><td><strong>${esc(e.actor||e.username||e.actor_id||'System')}</strong></td><td>${esc(e.action)}</td><td>${esc(e.object_type||e.target_type||'')} <span class="mono">${esc(shortId(e.object_id||e.target_id))}</span></td><td><button class="button small" data-action="view-audit" data-id="${esc(e.id)}">Details</button></td></tr>`)):empty('Noch keine Auditereignisse','Änderungen und Freigaben werden mit Akteur, Zeitpunkt und Änderungsgrund aufgezeichnet.','shield')}</section>`;
}
function settingsPage(users=[], secrets=[]) {
  if(!canAdmin()) return header('Einstellungen','Ihre Zugriffsrechte im Workspace.','','SYSTEM / EINSTELLUNGEN')+`<section class="card"><div class="card-header"><h2>Ihr Konto</h2></div><div class="card-content"><dl class="detail-list"><dt>Benutzername</dt><dd>${esc(state.me.username)}</dd><dt>Rolle</dt><dd>${esc(roleNames[state.me.role]||state.me.role)}</dd></dl><p class="small-text muted space-top">Benutzer und Geheimnisse werden durch Administratoren verwaltet.</p></div></section>`;
  const tab=state.settingsTab;
  return header('Einstellungen','Benutzerkonten und verschlüsselte Betriebsgeheimnisse verwalten.',tab==='users'?actionButton('Benutzer anlegen','create-user','plus','',true):actionButton('Geheimnis hinterlegen','create-secret','key','',true),'SYSTEM / EINSTELLUNGEN')+`<div class="tabs" role="tablist" aria-label="Einstellungen"><button type="button" role="tab" aria-selected="${tab==='users'}" class="tab${tab==='users'?' active':''}" data-action="settings-tab" data-tab="users">Benutzer & Rollen</button><button type="button" role="tab" aria-selected="${tab==='secrets'}" class="tab${tab==='secrets'?' active':''}" data-action="settings-tab" data-tab="secrets">Geheimnisse</button></div>`+(tab==='users'?`<section class="card"><div class="card-header"><h2>Benutzerkonten <span class="count-label">${users.length}</span></h2></div>${users.length?table(['BENUTZER','ROLLE','ERSTELLT'],users.map(u=>`<tr><td><div class="table-title"><span class="row-icon">${svg('users')}</span><strong>${esc(u.username)}</strong>${u.id===state.me.id?'<span class="tag">Sie</span>':''}</div></td><td>${esc(roleNames[u.role]||u.role)}</td><td>${esc(fmtDate(u.created_at))}</td></tr>`)):empty('Keine Benutzer gefunden','Legen Sie ein Benutzerkonto mit der passenden Rolle an.','users')}</section><div class="alert alert-info space-top">Leser sehen redigierte Daten. Operatoren verwalten Hosts und Läufe. Skriptautoren erstellen Entwürfe. Administratoren verwalten Freigaben, Benutzer und Geheimnisse. Entwickler haben alle Berechtigungen.</div>`:`<div class="alert alert-info">Geheimnisse werden verschlüsselt gespeichert und über ihre ID referenziert. Der gespeicherte Wert wird in der Konsole nicht erneut angezeigt.</div><section class="card"><div class="card-header"><h2>Geheimnisreferenzen <span class="count-label">${secrets.length}</span></h2></div>${secrets.length?table(['NAME','REFERENZ-ID','ERSTELLT'],secrets.map(s=>`<tr><td><div class="table-title"><span class="row-icon">${svg('key')}</span><strong>${esc(s.name)}</strong></div></td><td class="mono">${esc(s.id)}</td><td>${esc(fmtDate(s.created_at))}</td></tr>`)):empty('Noch keine Geheimnisse hinterlegt','Hinterlegen Sie beispielsweise den Root-Passwort-Hash für ein Installationsprofil.','key',actionButton('Geheimnis hinterlegen','create-secret','key','',true))}</section>`);
}

function showModal(title, content, submit=null, eyebrow='PROXMOX AIS') {
  document.getElementById('modal-title').textContent=title;
  document.getElementById('modal-eyebrow').textContent=eyebrow;
  document.getElementById('modal-body').innerHTML=content;
  modalSubmit=submit;
  if(!modal.open) modal.showModal();
}
function closeModal() {modal.close();modalSubmit=null;document.getElementById('modal-body').replaceChildren();}
function field(name,label,value='',options={}) {
  const attrs=`name="${esc(name)}"${options.required?' required':''}${options.placeholder?` placeholder="${esc(options.placeholder)}"`:''}${options.min!==undefined?` min="${esc(options.min)}"`:''}${options.max!==undefined?` max="${esc(options.max)}"`:''}${options.autocomplete?` autocomplete="${esc(options.autocomplete)}"`:''}`;
  let input;
  if(options.type==='textarea'||options.type==='json') input=`<textarea ${attrs} class="${options.type==='json'?'code':''}"${options.rows?` rows="${Number(options.rows)}"`:''}>${esc(typeof value==='object'?json(value):value)}</textarea>`;
  else if(options.type==='select') input=`<select ${attrs}>${(options.options||[]).map(o=>`<option value="${esc(o.value)}"${String(o.value)===String(value)?' selected':''}>${esc(o.label)}</option>`).join('')}</select>`;
  else if(options.type==='checkbox') return `<label class="checkbox${options.full?' full':''}"><input type="checkbox" ${attrs}${value?' checked':''}><span>${esc(label)}${options.hint?`<br><small>${esc(options.hint)}</small>`:''}</span></label>`;
  else input=`<input type="${esc(options.type||'text')}" ${attrs} value="${esc(value)}">`;
  return `<label class="${options.full?'full':''}">${esc(label)}${input}${options.hint?`<small>${esc(options.hint)}</small>`:''}</label>`;
}
function form(fields,label='Speichern',intro='') {
  return `${intro}<form id="modal-form"><div class="form-grid">${fields}</div><div class="form-error alert alert-danger" role="alert"></div><div class="form-actions"><button type="button" class="button" data-action="close-modal">Abbrechen</button><button type="submit" class="button primary">${esc(label)}</button></div></form>`;
}
function parseJSON(data, name, fallback={}) {try{return JSON.parse(data.get(name)||json(fallback));}catch{throw new Error(`Das Feld „${name}“ enthält kein gültiges JSON.`);}}
const split = value => String(value||'').split(/[,\n]/).map(s=>s.trim()).filter(Boolean);
const selectObjects = (objects, emptyLabel='Bitte auswählen') => [{value:'',label:emptyLabel},...objects.map(o=>({value:o.id,label:`${o.name || o.fqdn}${o.version?` · v${o.version}`:''}${o.build?` · ${o.build}`:''}`}))];
async function hostForm(existing=null, discovery=null) {
  const [profiles, isos]=await Promise.all([api('/profiles'),api('/iso-records')]);
  const h=existing||discovery||{};
  const fields=field('fqdn','Vollständiger Hostname (FQDN)',h.fqdn,{required:true,placeholder:'pve-01.example.net'})+field('site','Standort',h.site,{required:true,placeholder:'Rechenzentrum Berlin'})+field('management_ip','Management-IP mit Präfix',h.management_ip,{required:true,placeholder:'192.0.2.10/24'})+field('tags','Tags',arr(h.tags).join(', '),{placeholder:'produktion, rack-a',hint:'Mehrere Tags mit Komma trennen.'})+field('identities','Hardware-Identitäten',arr(h.identities).map(i=>`${i.kind}:${i.value}`).join('\n'),{type:'textarea',required:true,full:true,placeholder:'serial:SERVER-SERIAL\nuuid:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx\nmac:00:11:22:33:44:55',hint:'Eine Identität pro Zeile. Erlaubte Typen: serial, uuid, mac.'})+field('installation_profile_id','Installationsprofil',h.installation_profile_id,{type:'select',options:selectObjects(arr(profiles).filter(p=>p.kind==='installation'&&p.status==='published'),'Noch nicht zuweisen')})+field('postinstall_profile_id','Postinstallationsprofil',h.postinstall_profile_id,{type:'select',options:selectObjects(arr(profiles).filter(p=>p.kind==='postinstall'&&p.status==='published'),'Noch nicht zuweisen')})+field('iso_id','Installationsmedium',h.iso_id,{type:'select',full:true,options:selectObjects(arr(isos),'Noch nicht zuweisen')})+field('overrides','Hostüberschreibungen (JSON)',h.overrides||{},{type:'json',full:true,hint:'Spezifische Werte dieses Hosts. Geheimnisse ausschließlich per Referenz zuweisen.'});
  showModal(existing?'Server bearbeiten':'Server hinzufügen',form(fields,existing?'Änderungen speichern':'Server anlegen'),async data=>{
    const identities=String(data.get('identities')).split('\n').filter(l=>l.trim()).map(line=>{const colon=line.indexOf(':');if(colon<1)throw new Error('Jede Identität benötigt das Format typ:wert.');return {kind:line.slice(0,colon).trim(),value:line.slice(colon+1).trim()};});
    const body={fqdn:data.get('fqdn'),site:data.get('site'),management_ip:data.get('management_ip'),tags:split(data.get('tags')),identities,installation_profile_id:data.get('installation_profile_id')||null,postinstall_profile_id:data.get('postinstall_profile_id')||null,iso_id:data.get('iso_id')||null,overrides:parseJSON(data,'overrides')};
    if(existing){for(const key of Object.keys(body)){if(JSON.stringify(body[key])===JSON.stringify(existing[key]??null))delete body[key];}if(!Object.keys(body).length){closeModal();toast('Keine Änderungen vorhanden.');return;}body.expected_version=existing.version;}
    await api(existing?`/hosts/${encodeURIComponent(existing.id)}`:'/hosts',{method:existing?'PATCH':'POST',body});closeModal();toast(existing?'Server aktualisiert.':'Server wurde angelegt.');await refresh();
  },'INVENTAR');
}
function hostImportForm() {
  const example=[{fqdn:'pve-01.example.net',site:'Berlin',management_ip:'192.0.2.10/24',identities:[{kind:'serial',value:'SERVER-SERIAL'}],tags:[]}];
  showModal('Server aus JSON importieren',form(field('hosts','Serverliste (JSON)',example,{type:'json',full:true,required:true,rows:16,hint:'Liste von Hostobjekten. Die gesamte Liste wird zusammen validiert und gespeichert.'}),'Server importieren'),async data=>{const hosts=parseJSON(data,'hosts',[]);if(!Array.isArray(hosts)||!hosts.length)throw new Error('Eine nicht leere JSON-Liste von Servern ist erforderlich.');await api('/hosts/import',{method:'POST',body:hosts});closeModal();toast(`${hosts.length} Server importiert.`);await refresh();},'INVENTARIMPORT');
}
async function moduleCatalog() {
  const catalog=arr(await api('/modules/builtin'));
  showModal('Basismodul als Entwurf übernehmen',`<div class="alert alert-info">Die Vorlagen sind Ausgangspunkte. Prüfen Sie die Parameter, tragen Sie den konkreten Zielbuild ein und dokumentieren Sie vor Veröffentlichung einen Test.</div>${catalog.map(m=>`<div class="run-step"><span class="step-number">${svg('code')}</span><div class="run-step-copy"><strong>${esc(m.name)}</strong><p>${esc(m.description)}</p></div><button type="button" class="button small" data-action="use-module-template" data-id="${esc(m.id)}">Übernehmen</button></div>`).join('')}<div class="form-actions"><button class="button" data-action="close-modal">Schließen</button></div>`,null,'MODULVORLAGEN');
  state.catalog=catalog;
}
function previewContent(preview) {
  const p=preview||{}, warnings=arr(p.warnings);
  const network=p.resolved?.network||{};
  return `${warnings.length?`<div class="alert">${warnings.map(w=>esc(typeof w==='string'?w:w.message||json(w))).join('<br>')}</div>`:''}<div class="modal-summary"><dl class="detail-list"><dt>ISO / Zielbuild</dt><dd>${esc(p.iso?.name||'–')} · ${esc(p.iso?.build||'–')}</dd><dt>Managementnetz</dt><dd>${esc(network.cidr||'–')}<br>Gateway ${esc(network.gateway||'–')} · DNS ${esc(network.dns||'–')}<br><span class="mono">${esc(JSON.stringify(network.filter||{}))}</span></dd><dt>Zieldatenträger</dt><dd><pre class="code-block light">${esc(json(p.disks || p.resolved?.disk_setup || p.resolved?.['disk-setup'] || {}))}</pre></dd><dt>Profilversionen</dt><dd>${arr(p.profiles).map(profile=>`${esc(profile.name)} · v${esc(profile.version)}`).join('<br>')||'–'}</dd><dt>Skriptversionen</dt><dd>${arr(p.steps).map(step=>`${esc(step.name||step.id)} · v${esc(step.module_version||'–')}`).join('<br>')||'–'}</dd><dt>Konfigurationsdigest</dt><dd class="mono break">${esc(p.digest||'–')}</dd></dl></div><details><summary>Aufgelöste Konfiguration & Herkunft</summary><pre class="code-block light">${esc(json({resolved:p.resolved,provenance:p.provenance,profiles:p.profiles,steps:p.steps}))}</pre></details>`;
}
async function approveHost(id, previewOnly=false) {
  const [host, preview]=await Promise.all([api(`/hosts/${encodeURIComponent(id)}`),api(`/hosts/${encodeURIComponent(id)}/preview`)]);
  if(previewOnly){showModal('Aufgelöste Konfiguration',previewContent(preview)+`<div class="form-actions"><button class="button" data-action="close-modal">Schließen</button></div>`,null,host.fqdn);return;}
  const intro=`<div class="alert alert-danger">Die Installation überschreibt die ausgewählten Systemdatenträger. Prüfen Sie Host, Netzwerk, Zielbuild und Datenträger vor der Freigabe.</div><div class="modal-summary"><dl class="detail-list"><dt>Server</dt><dd><strong>${esc(host.fqdn)}</strong></dd><dt>Management-IP</dt><dd>${esc(host.management_ip)}</dd><dt>Standort</dt><dd>${esc(host.site)}</dd></dl></div>${previewContent(preview)}<hr class="form-divider">`;
  const fields=field('confirmation','Hostnamen zur Bestätigung eingeben','',{required:true,full:true,placeholder:host.fqdn})+field('valid_minutes','Freigabefenster in Minuten',30,{type:'number',required:true,min:5,max:240})+field('reason','Freigabegrund','',{required:true,placeholder:'Geplante Erstinstallation'})+field('disks_confirmed','Ich habe die Zieldatenträger geprüft und bestätige, dass diese überschrieben werden dürfen.',false,{type:'checkbox',required:true,full:true});
  showModal('Installation freigeben',form(fields,'Verbindlich freigeben',intro),async data=>{
    if(data.get('confirmation')!==host.fqdn)throw new Error('Der eingegebene Hostname stimmt nicht mit dem Server überein.');
    await api(`/hosts/${encodeURIComponent(id)}/approve-install`,{method:'POST',body:{expected_version:host.version,valid_minutes:Number(data.get('valid_minutes')),confirmation:data.get('confirmation'),disks_confirmed:data.get('disks_confirmed')==='on',reason:data.get('reason')}});
    closeModal();toast('Installationsfreigabe erteilt. Der Server kann mit der zugewiesenen ISO gestartet werden.');await refresh();
  },'ZEITLICH BEGRENZTE INSTALLATIONSFREIGABE');
}
const installationExample = {
  global:{keyboard:'de',country:'de',timezone:'Europe/Berlin',mailto:'admin@example.net'},
  network:{source:'from-answer',gateway:'192.0.2.1',dns:'192.0.2.53',filter:{ID_NET_NAME_MAC:'enx001122334455'}},
  disk_setup:{filesystem:'ext4',filter:{ID_SERIAL:'EXPLICIT_DISK_SERIAL'},expected_count:1,expected_serials:['EXPLICIT_DISK_SERIAL'],inventory_evidence:'Referenz zur geprüften Hardwareinventarisierung'},
  root_secret_id:'ID_DES_ROOT_PASSWORT_HASHES'
};
async function profileForm(kind, existing=null) {
  const p=existing||{}, install=kind==='installation';
  let info='';
  if(!install){const modules=arr(await api('/modules')).filter(m=>m.status==='published');info=`<div class="alert alert-info">Verfügbare veröffentlichte Module: ${modules.length?modules.map(m=>`${esc(m.name)} v${esc(m.version)}: <code>${esc(m.id)}</code>`).join('<br>'):'Noch keine. Erstellen und veröffentlichen Sie zuerst ein Skriptmodul.'}</div>`;}
  const fields=field('name','Profilname',p.name,{required:true,full:true,placeholder:install?'PVE · Standardserver':'PVE · Basiskonfiguration',hint:'Die nächste Versionsnummer wird automatisch für diesen Namen vergeben.'})+field('target_builds','Unterstützte Zielbuilds',arr(p.target_builds).join(', '),{required:true,full:true,placeholder:'z. B. 9.1-1',hint:'Nur tatsächlich geprüfte Builds eintragen. Mehrere Werte mit Komma trennen.'})+field('values',install?'Installationskonfiguration (JSON)':'Profilparameter (JSON)',p.values||(install?installationExample:{}),{type:'json',full:true,rows:install?17:6,hint:install?'Beispielwerte an Ihr Netz und Ihre geprüfte Hardware anpassen. FQDN und Management-CIDR kommen vom Host.':'Parameter werden mit den Einstellungen der einzelnen Schritte aufgelöst.'})+(!install?field('steps','Geordnete Schritte (JSON)',p.steps||[{id:'final-check',module_id:'VEROEFFENTLICHTE_MODUL_ID',parameters:{},secret_refs:{},required:true}],{type:'json',full:true,rows:10,hint:'Jeder Schritt verweist auf eine veröffentlichte Modulversion. Die Listenreihenfolge ist die Ausführungsreihenfolge.'})+field('reboot_budget','Maximale geplante Neustarts',p.reboot_budget??1,{type:'number',min:0,max:5,full:true}):'')+field('reason','Änderungsgrund','',{full:true,placeholder:'Grund für diesen Profilstand'});
  showModal(existing?'Neue Profilversion':'Profil erstellen',form(fields,'Entwurf speichern',info),async data=>{
    await api('/profiles',{method:'POST',body:{name:data.get('name'),kind,target_builds:split(data.get('target_builds')),values:parseJSON(data,'values'),steps:install?[]:parseJSON(data,'steps',[]),reason:data.get('reason')||'',...(!install?{reboot_budget:Number(data.get('reboot_budget'))}:{})}});
    closeModal();toast('Profilentwurf gespeichert. Eine Veröffentlichung benötigt einen Testnachweis.');await refresh();
  },install?'INSTALLATIONSPROFIL':'POSTINSTALLATIONSPROFIL');
}
const moduleExample = '#!/usr/bin/env bash\nset -euo pipefail\n\ncheck() {\n  systemctl is-active --quiet pveproxy\n}\n\napply() {\n  # Nur erforderliche, geprüfte Änderungen ausführen.\n  return 0\n}\n\nverify() {\n  systemctl is-active --quiet pveproxy\n}\n\ncase "${1:-}" in\n  check) check ;;\n  apply) apply ;;\n  verify) verify ;;\n  *) echo "Usage: $0 {check|apply|verify}" >&2; exit 2 ;;\nesac\n';
function moduleForm(existing=null) {
  const m=existing||{};
  const fields=field('name','Modulname',m.name,{required:true,full:true,placeholder:'PVE-Dienste prüfen',hint:'Die nächste Versionsnummer wird automatisch für diesen Namen vergeben.'})+field('target_builds','Unterstützte Zielbuilds',arr(m.target_builds).join(', '),{required:true,placeholder:'z. B. 9.1-1'})+field('timeout_seconds','Timeout in Sekunden',m.timeout_seconds||300,{type:'number',required:true,min:1,max:7200})+field('source','Bash-Quelltext',m.source||moduleExample,{type:'json',required:true,full:true,rows:17,hint:'Aufruf: bash modul.sh check|apply|verify parameter.json. Parameter werden als JSON-Datei übergeben.'})+field('parameters_schema','Parameterschema (JSON Schema)',m.parameters_schema||{type:'object',properties:{},additionalProperties:false},{type:'json',full:true,rows:6})+field('dependencies','Abhängige Modulnamen',arr(m.dependencies).join(', '),{full:true,placeholder:'Optional: exakte Modulnamen, durch Komma getrennt'})+field('retry_safe','Apply darf nach Zustandsprüfung wiederholt werden.',m.retry_safe||false,{type:'checkbox',full:true,hint:'Nur aktivieren, wenn die Wiederholbarkeit im Test nachgewiesen wurde.'})+field('reason','Änderungsgrund','',{full:true,placeholder:'Grund für diesen Modulstand'});
  showModal(existing?'Neue Modulversion':'Skriptmodul erstellen',form(fields,'Entwurf speichern'),async data=>{
    await api('/modules',{method:'POST',body:{name:data.get('name'),source:data.get('source'),parameters_schema:parseJSON(data,'parameters_schema'),dependencies:split(data.get('dependencies')),target_builds:split(data.get('target_builds')),timeout_seconds:Number(data.get('timeout_seconds')),retry_safe:data.get('retry_safe')==='on',reason:data.get('reason')||''}});closeModal();toast('Modulentwurf gespeichert.');await refresh();
  },'VERSIONIERTE SKRIPTMODULE');
}
async function publishObject(type, id) {
  const item=arr(state.data).find(x=>x.id===id) || await api(`/${type}/${encodeURIComponent(id)}`);
  const intro=`<div class="alert alert-info">${esc(item.name)} · Version ${esc(item.version)}<br>Veröffentlichten Inhalt können Sie nicht mehr ändern. Dokumentieren Sie den praktischen Test auf einem passenden Testhost. Im Vieraugenmodus muss eine andere Person den Entwurf veröffentlichen.</div>`;
  showModal(type==='profiles'?'Profil veröffentlichen':'Modul veröffentlichen',form(field('test_evidence','Praktischer Testnachweis','',{type:'textarea',required:true,full:true,placeholder:'Testhost, Build, Datum, Ergebnisse und Referenz zum Prüfprotokoll'})+field('reason','Änderungsgrund','',{type:'textarea',required:true,full:true}),'Veröffentlichen',intro),async data=>{
    await api(`/${type}/${encodeURIComponent(id)}/publish`,{method:'POST',body:{test_evidence:data.get('test_evidence'),reason:data.get('reason')}});closeModal();toast('Version veröffentlicht.');await refresh();
  },'VERÖFFENTLICHUNG');
}
function inspectProfile(p) {
  showModal(p.name,`<div class="modal-summary"><dl class="detail-list"><dt>Version</dt><dd>v${esc(p.version)} ${badge(p.status)}</dd><dt>Zielbuilds</dt><dd>${esc((p.target_builds||[]).join(', ')||'–')}</dd><dt>Profil-ID</dt><dd class="mono">${esc(p.id)}</dd><dt>Digest</dt><dd class="mono">${esc(p.digest||'Noch nicht veröffentlicht')}</dd></dl></div><pre class="code-block light">${esc(json({values:p.values,steps:p.steps}))}</pre><div class="form-actions">${canAuthor()?`<button class="button" data-action="version-profile" data-id="${esc(p.id)}">Neue Version</button>`:''}<button class="button" data-action="close-modal">Schließen</button></div>`,null,'PROFILDETAILS');
}
function inspectModule(m) {
  showModal(m.name,`<div class="modal-summary"><dl class="detail-list"><dt>Version / Status</dt><dd>v${esc(m.version)} ${badge(m.status)}</dd><dt>Modul-ID</dt><dd class="mono">${esc(m.id)}</dd><dt>Zielbuilds</dt><dd>${esc((m.target_builds||[]).join(', '))}</dd><dt>Digest</dt><dd class="mono">${esc(m.digest||'Noch nicht veröffentlicht')}</dd><dt>Testnachweis</dt><dd>${esc(m.test_evidence||'Noch nicht hinterlegt')}</dd></dl></div>${m.source?`<pre class="code-block">${esc(m.source)}</pre>`:'<div class="alert alert-info">Der Quelltext ist für Skriptautoren und Administratoren sichtbar.</div>'}<details><summary>Parameterschema & Abhängigkeiten</summary><pre class="code-block light">${esc(json({parameters_schema:m.parameters_schema,dependencies:m.dependencies,retry_safe:m.retry_safe,timeout_seconds:m.timeout_seconds}))}</pre></details><div class="form-actions"><button class="button" data-action="close-modal">Schließen</button></div>`,null,'MODULDETAILS');
}
async function isoForm() {
  const groups=arr(await api('/groups'));
  const fields=field('name','Medienname','',{required:true,placeholder:'Berlin · Proxmox VE'})+field('build','Exakter Proxmox ISO-Build','',{required:true,placeholder:'z. B. 9.1-1'})+field('sha256','SHA256-Prüfsumme des ISO-Mediums','',{required:true,full:true,placeholder:'64 hexadezimale Zeichen'})+field('assistant_version','Version des Auto Install Assistant','',{required:true,placeholder:'Exakte Paketversion'})+field('group_id','Bereitstellungsgruppe','',{required:true,type:'select',options:selectObjects(groups.filter(g=>!g.revoked))})+field('fingerprint','SHA256-Zertifikatsfingerprint','',{required:true,full:true,placeholder:'Fingerprint des HTTPS-Zertifikats des Antwortdienstes'})+field('test_status','Prüfstatus','draft',{type:'select',options:[{value:'draft',label:'Entwurf – noch nicht freigegeben'},{value:'passed',label:'Geprüft – Nachweis liegt vor'}]})+field('native_token_support','Native Unterstützung von --answer-auth-token ist nachgewiesen.',false,{type:'checkbox',required:true,full:true})+field('test_evidence','Kompatibilitätsnachweis','',{type:'textarea',full:true,placeholder:'Antwortschema, Token-Header, First Boot, Startnetz und Bootverfahren: Testhost, Datum und Prüfprotokoll.'});
  showModal('ISO-Medium registrieren',form(fields,'Medium registrieren',groups.length?'':'<div class="alert">Erstellen Sie zuerst eine Bereitstellungsgruppe im Bereich Installationsmedien.</div>'),async data=>{
    await api('/iso-records',{method:'POST',body:{name:data.get('name'),build:data.get('build'),sha256:data.get('sha256'),assistant_version:data.get('assistant_version'),group_id:data.get('group_id'),fingerprint:data.get('fingerprint'),test_status:data.get('test_status'),native_token_support:data.get('native_token_support')==='on',test_evidence:data.get('test_evidence')}});closeModal();toast('ISO-Medium registriert.');await refresh();
  },'INSTALLATIONSMEDIUM');
}
function buildCommand(iso, token='<gruppenname>:<secret>') {
  if(iso.command)return iso.command;
  const shellQuote = value => "'"+String(value).replace(/'/g,"'\\''")+"'";
  const base=(iso.answer_url||`${window.location.origin}/installer/v1/answer`);
  return `proxmox-auto-install-assistant prepare-iso SOURCE.iso \\\n  --fetch-from http \\\n  --url ${shellQuote(base)} \\\n  --cert-fingerprint ${shellQuote(iso.fingerprint||'<SHA256-FINGERPRINT>')} \\\n  --answer-auth-token ${shellQuote(token)}`;
}
function inspectISO(iso) {
  showModal(iso.name,`<div class="modal-summary"><dl class="detail-list"><dt>ISO-ID</dt><dd class="mono">${esc(iso.id)}</dd><dt>Zielbuild / Status</dt><dd>${esc(iso.build)} ${badge(iso.test_status)}</dd><dt>Assistant-Version</dt><dd>${esc(iso.assistant_version)}</dd><dt>SHA256</dt><dd class="mono">${esc(iso.sha256)}</dd><dt>Fingerprint</dt><dd class="mono">${esc(iso.fingerprint)}</dd><dt>Gruppe</dt><dd class="mono">${esc(iso.group_id)}</dd><dt>Testnachweis</dt><dd>${esc(iso.test_evidence||'Noch nicht hinterlegt')}</dd></dl></div><h3 class="section-label">Vorbereiteter Buildbefehl</h3><p class="small-text muted">SOURCE.iso und den Token-Platzhalter durch Ihre Eingaben ersetzen. Die URL muss aus dem Provisionierungsnetz per HTTPS erreichbar sein.</p><textarea class="code" id="copy-value" readonly aria-label="Buildbefehl">${esc(buildCommand(iso))}</textarea><div class="form-actions"><button class="button" data-action="copy-value">${svg('copy')}Befehl kopieren</button><button class="button" data-action="close-modal">Schließen</button></div>`,null,'ISO-DETAILS');
}
function groupForm() {
  showModal('Bereitstellungsgruppe erstellen',form(field('name','Gruppenname','',{required:true,placeholder:'berlin-rack-a'})+field('site','Standort','',{required:true,placeholder:'Rechenzentrum Berlin'})+field('valid_hours','Gültigkeit in Stunden',24,{type:'number',required:true,min:1,max:8760,full:true}),'Token erstellen'),async data=>{
    const result=await api('/groups',{method:'POST',body:{name:data.get('name'),site:data.get('site'),valid_hours:Number(data.get('valid_hours'))}});
    showModal('Gruppentoken erstellt',`<div class="alert">Der vollständige Token wird nur jetzt angezeigt. Speichern Sie ihn für die ISO-Vorbereitung. Er ist auf die Bereitstellungsgruppe und deren Gültigkeitsfenster beschränkt.</div><dl class="detail-list"><dt>Gruppe</dt><dd>${esc(result.name)}</dd><dt>Referenz-ID</dt><dd class="mono">${esc(result.id)}</dd><dt>Gültig bis</dt><dd>${esc(fmtDate(result.expires_at))}</dd></dl><h3 class="section-label">Token für --answer-auth-token</h3><textarea id="copy-value" class="code token-value" readonly aria-label="Einmalig angezeigter Gruppentoken">${esc(result.token)}</textarea>${result.command?`<h3 class="section-label">Buildbefehl</h3><pre class="code-block light">${esc(result.command)}</pre>`:''}<div class="form-actions"><button class="button" data-action="copy-value">${svg('copy')}Token kopieren</button><button class="button primary" data-action="close-modal">Token gesichert</button></div>`,null,'EINMALIGE TOKENAUSGABE');
    await refresh();
  },'ISO-ZUGRIFF');
}
function userForm() {
  showModal('Benutzer anlegen',form(field('username','Benutzername','',{required:true,autocomplete:'off'})+field('role','Rolle','reader',{type:'select',options:Object.entries(roleNames).map(([value,label])=>({value,label}))})+field('password','Initiales Passwort','',{required:true,type:'password',full:true,autocomplete:'new-password',hint:'Mindestens 12 Zeichen verwenden.'}),'Benutzer anlegen'),async data=>{
    await api('/users',{method:'POST',body:{username:data.get('username'),password:data.get('password'),role:data.get('role')}});closeModal();toast('Benutzerkonto angelegt.');await refresh();
  },'BENUTZER & ROLLEN');
}
function secretForm() {
  showModal('Geheimnis hinterlegen',form(field('name','Bezeichnung','',{required:true,full:true,placeholder:'Root-Hash · PVE Berlin'})+field('value','Geheimniswert','',{required:true,type:'password',full:true,autocomplete:'new-password',hint:'Für den Root-Zugang einen von Ihrem Zielbuild unterstützten Passwort-Hash verwenden.'}),'Verschlüsselt speichern','<div class="alert alert-info">Verwenden Sie die nach dem Speichern angezeigte Referenz-ID im Profil. Der Geheimniswert wird nicht erneut ausgegeben.</div>'),async data=>{
    const result=await api('/secrets',{method:'POST',body:{name:data.get('name'),value:data.get('value')}});closeModal();toast('Geheimnis verschlüsselt gespeichert.');state.settingsTab='secrets';await refresh();
    if(result?.id)showModal('Geheimnis gespeichert',`<p class="small-text muted">Referenz für <code>root_secret_id</code> oder einen Schritt:</p><input id="copy-value" value="${esc(result.id)}" readonly aria-label="Geheimnisreferenz"><div class="form-actions"><button class="button" data-action="copy-value">${svg('copy')}ID kopieren</button><button class="button primary" data-action="close-modal">Fertig</button></div>`);
  },'BETRIEBSGEHEIMNIS');
}
async function runAction(id, action) {
  const run=await api(`/runs/${encodeURIComponent(id)}`), resume=action==='resume';
  showModal(resume?'Lauf wiederaufnehmen':'Lauf abbrechen',form(field('reason',resume?'Begründung und durchgeführte Prüfung':'Abbruchgrund','',{type:'textarea',full:true,required:true}),resume?'Wiederaufnahme anfordern':'Abbruch anfordern',`<div class="alert${resume?' alert-info':''}">${resume?'Der Runner prüft gespeicherte Checkpoints und Modulzustände vor weiteren Änderungen. Unklare, nicht wiederholbare Schritte benötigen eine manuelle Klärung.':'Der Runner stoppt am nächsten sicheren Übergang. Eine bereits gestartete Datenträgeroperation oder ein Paketmanager wird dadurch nicht rückgängig gemacht.'}</div>`),async data=>{
    await api(`/runs/${encodeURIComponent(id)}/${action}`,{method:'POST',body:{reason:data.get('reason'),expected_version:run.version}});closeModal();toast(resume?'Wiederaufnahme angefordert.':'Abbruch angefordert.');await refresh();
  },`LAUF ${shortId(id)}`);
}
async function reconcileRun(id) {
  const run=await api(`/runs/${encodeURIComponent(id)}`);
  const host=await api(`/hosts/${encodeURIComponent(run.host_id)}`);
  const fields=field('reason','Durchgeführte Prüfung und Abgleichgrund','',{type:'textarea',full:true,required:true})+field('confirmation','Hostnamen zur Bestätigung eingeben','',{required:true,full:true,placeholder:host.fqdn})+field('execution_stopped','Ich habe am Host geprüft, dass Installer und Runner gestoppt sind.',false,{type:'checkbox',full:true,required:true});
  showModal('Lauf manuell abgleichen',form(fields,'Lauf als abgebrochen schließen',`<div class="alert">Dieser Abgleich schließt einen aufgegebenen oder nach Wiederherstellung unklaren Lauf. Prüfen Sie zuvor direkt am Host, dass keine Ausführung mehr stattfindet. Bereits erteilte Laufberechtigungen und das Antwortfenster müssen abgelaufen sein. Für eine neue Installation ist eine neue Freigabe erforderlich.</div>`),async data=>{
    if(data.get('confirmation')!==host.fqdn)throw new Error('Der eingegebene Hostname stimmt nicht mit dem Server überein.');
    await api(`/runs/${encodeURIComponent(id)}/reconcile`,{method:'POST',body:{expected_version:run.version,reason:data.get('reason'),confirmation:data.get('confirmation'),execution_stopped:data.get('execution_stopped')==='on'}});closeModal();toast('Lauf abgeglichen und als abgebrochen geschlossen.');await refresh();
  },host.fqdn);
}

async function renderRoute(showLoading=true) {
  const route=window.location.hash.replace(/^#\/?/,'').split('/');
  const pages=['dashboard','hosts','installation','postinstall','modules','runs','media','audit','settings'];
  state.page=pages.includes(route[0])?route[0]:'dashboard';state.id=route[1]?decodeURIComponent(route[1]):null;
  const version=++state.routeVersion;
  const labels={dashboard:'Übersicht',hosts:'Serverinventar',installation:'Installationsprofile',postinstall:'Postinstallation',modules:'Skriptmodule',runs:'Installationsläufe',media:'Installationsmedien',audit:'Auditprotokoll',settings:'Einstellungen'};
  document.getElementById('breadcrumb').textContent=labels[state.page];document.title=`${labels[state.page]} · Proxmox AIS`;
  document.querySelectorAll('[data-nav]').forEach(a=>{a.classList.toggle('active',a.dataset.nav===state.page);if(a.dataset.nav===state.page)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
  if(showLoading)main.innerHTML='<div class="loading-screen"><span class="spinner"></span> Daten werden geladen …</div>';
  try {
    let output, data;
    if(state.page==='dashboard'){data=await api('/dashboard');output=dashboard(data);}
    if(state.page==='hosts'){if(state.id){data=await api(`/hosts/${encodeURIComponent(state.id)}`);output=hostPage(data);}else{const results=await Promise.all([api('/hosts'),api('/discoveries')]);data={hosts:arr(results[0]),discoveries:arr(results[1])};output=hostsPage(data.hosts,data.discoveries);}}
    if(['installation','postinstall'].includes(state.page)){data=arr(await api('/profiles'));output=profilesPage(data,state.page);}
    if(state.page==='modules'){data=arr(await api('/modules'));output=modulesPage(data);}
    if(state.page==='runs'){data=await api(state.id?`/runs/${encodeURIComponent(state.id)}`:'/runs');output=state.id?runPage(data):runsPage(arr(data));}
    if(state.page==='media'){const result=await Promise.all([api('/iso-records'),(canOperate()||canAuthor())?api('/groups'):Promise.resolve([])]);data={records:arr(result[0]),groups:arr(result[1])};output=mediaPage(data.records,data.groups);}
    if(state.page==='audit'){data=arr(await api('/audit'));output=auditPage(data);}
    if(state.page==='settings'){data=canAdmin()?await Promise.all([api('/users'),api('/secrets')]):[[],[]];output=settingsPage(arr(data[0]),arr(data[1]));}
    if(version!==state.routeVersion)return;
    state.data=data; main.innerHTML=output;
    if(state.page==='media'&&!canOperate()&&!canAuthor())main.querySelector('.groups-card')?.remove();
    applyFilters();
    document.getElementById('connection').className='connection';document.getElementById('connection').innerHTML='<span class="status-dot"></span> Verbunden';
  }catch(error){
    if(version!==state.routeVersion)return;
    document.getElementById('connection').className='connection disconnected';document.getElementById('connection').innerHTML='<span class="status-dot"></span> Abruf fehlgeschlagen';
    if(showLoading)main.innerHTML=header('Daten konnten nicht geladen werden','Prüfen Sie Ihre Verbindung und Zugriffsrechte.',actionButton('Erneut versuchen','refresh','refresh'))+`<div class="alert alert-danger" role="alert">${esc(error.message)}</div>`;
    else throw error;
  }
}
async function refresh() {
  if(state.refreshing)return;
  state.refreshing=true;
  const filters=['list-search','site-filter','status-filter'].map(id=>[id,document.getElementById(id)?.value]);
  try{await renderRoute(false);for(const [id,value]of filters){const el=document.getElementById(id);if(el&&value!==undefined)el.value=value;}applyFilters();}finally{state.refreshing=false;}
}
async function handleAction(button) {
  const action=button.dataset.action,id=button.dataset.id;
  if(action==='close-modal'){closeModal();return;}
  if(action==='menu'){const open=document.getElementById('sidebar').classList.toggle('open');button.setAttribute('aria-expanded',String(open));return;}
  if(action==='logout'){await api('/auth/logout',{method:'POST'});window.location.href='/login';return;}
  if(action==='refresh'){await refresh();return;}
  if(action==='create-host'){await hostForm();return;}
  if(action==='import-hosts'){hostImportForm();return;}
  if(action==='assign-discovery'){await hostForm(null,state.data.discoveries.find(d=>d.id===id));return;}
  if(action==='edit-host'){await hostForm(await api(`/hosts/${encodeURIComponent(id)}`));return;}
  if(action==='approve-host'||action==='preview-host'){await approveHost(id,action==='preview-host');return;}
  if(action==='toggle-host'){
    const host=await api(`/hosts/${encodeURIComponent(id)}`);
    await api(`/hosts/${encodeURIComponent(id)}`,{method:'PATCH',body:{expected_version:host.version,blocked:!host.blocked}});toast(host.blocked?'Host entsperrt.':'Host gesperrt.');await refresh();return;
  }
  if(action==='create-profile'){await profileForm(button.dataset.kind);return;}
  if(action==='version-profile'){const p=arr(state.data).find(x=>x.id===id)||await api(`/profiles/${encodeURIComponent(id)}`);await profileForm(p.kind,p);return;}
  if(action==='view-profile'){const p=arr(state.data).find(x=>x.id===id)||await api(`/profiles/${encodeURIComponent(id)}`);inspectProfile(p);return;}
  if(action==='publish-profile'){await publishObject('profiles',id);return;}
  if(action==='create-module'){moduleForm();return;}
  if(action==='module-catalog'){await moduleCatalog();return;}
  if(action==='use-module-template'){const selected=state.catalog.find(m=>m.id===id);moduleForm({...selected,dependencies:selected.dependencies.map(name=>state.catalog.find(m=>m.id===name)?.name||name),version:0});return;}
  if(action==='version-module'||action==='view-module'){const m=arr(state.data).find(x=>x.id===id)||await api(`/modules/${encodeURIComponent(id)}`);if(action==='version-module')moduleForm(m);else inspectModule(m);return;}
  if(action==='publish-module'){await publishObject('modules',id);return;}
  if(action==='create-iso'){await isoForm();return;}
  if(action==='view-iso'){inspectISO(state.data.records.find(x=>x.id===id));return;}
  if(action==='create-group'){groupForm();return;}
  if(action==='create-user'){userForm();return;}
  if(action==='create-secret'){secretForm();return;}
  if(action==='settings-tab'){state.settingsTab=button.dataset.tab;await renderRoute(false);return;}
  if(action==='run-tab'){state.runTab=button.dataset.tab;main.innerHTML=runPage(state.data);return;}
  if(action==='resume-run'||action==='cancel-run'){await runAction(id,action==='resume-run'?'resume':'cancel');return;}
  if(action==='reconcile-run'){await reconcileRun(id);return;}
  if(action==='copy-value'){
    const el=document.getElementById('copy-value');
    try{await navigator.clipboard.writeText(el.value);toast('In die Zwischenablage kopiert.');}catch{el.focus();el.select();toast('Text markiert. Mit Strg+C kopieren.');}return;
  }
  if(action==='view-audit'){const e=arr(state.data).find(x=>String(x.id)===id);showModal('Auditereignis',`<pre class="code-block light">${esc(json(e))}</pre><div class="form-actions"><button class="button" data-action="close-modal">Schließen</button></div>`,null,e?.action||'AUDIT');}
}
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-action]');
  if(button){event.preventDefault();if(button.disabled)return;button.disabled=true;try{await handleAction(button);}catch(error){toast(error.message,true);}finally{button.disabled=false;}}
  if(event.target.closest('[data-nav]')){document.getElementById('sidebar').classList.remove('open');document.querySelector('[data-action="menu"]').setAttribute('aria-expanded','false');}
});
document.addEventListener('input',event=>{if(event.target.id==='list-search')applyFilters();});
document.addEventListener('change',event=>{if(['site-filter','status-filter'].includes(event.target.id))applyFilters();});
document.addEventListener('submit',async event=>{
  if(event.target.id!=='modal-form')return;
  event.preventDefault();if(!modalSubmit)return;
  const formEl=event.target, submit=formEl.querySelector('[type="submit"]'),errorEl=formEl.querySelector('.form-error');
  submit.disabled=true;errorEl.textContent='';
  try{await modalSubmit(new FormData(formEl));}catch(error){errorEl.textContent=error.message;errorEl.scrollIntoView({block:'nearest'});}finally{submit.disabled=false;}
});
modal.addEventListener('cancel',()=>{modalSubmit=null;document.getElementById('modal-body').replaceChildren();});
window.addEventListener('hashchange',()=>{renderRoute();});
async function init() {
  document.querySelectorAll('[data-icon]').forEach(el=>el.insertAdjacentHTML('afterbegin',svg(el.dataset.icon)));
  try{
    state.me=await api('/me');
    document.getElementById('username').textContent=state.me.username;
    document.getElementById('user-role').textContent=roleNames[state.me.role]||state.me.role;
    document.getElementById('avatar').textContent=state.me.username.slice(0,2).toUpperCase();
    await renderRoute();
    setInterval(async()=>{
      if(document.hidden||modal.open||state.refreshing||['INPUT','SELECT','TEXTAREA'].includes(document.activeElement?.tagName)||!['dashboard','runs','hosts'].includes(state.page))return;
      try{await refresh();}catch{/* The connection indicator reports refresh failures. */}
    },15000);
  }catch(error){main.innerHTML=`<div class="alert alert-danger" role="alert">${esc(error.message)}</div>`;}
}
init();
