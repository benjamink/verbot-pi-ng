"""A single-page control surface for bring-up.

Deliberately one self-contained HTML string: no build step, no static
directory to package, no second port. It drives the same endpoints the API
already exposes, so anything it can do is reachable from curl too.

The button list is rendered from the Action enum rather than hard-coded, so a
new gearbox position cannot appear in the API and go missing from the page.
"""

from collections.abc import Iterable

from verbot.actions import Action

_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f4f4f5; --fg: #18181b; --card: #ffffff; --line: #d4d4d8;
  --accent: #2563eb; --stop: #dc2626; --muted: #71717a;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #18181b; --fg: #f4f4f5; --card: #27272a; --line: #3f3f46;
    --accent: #3b82f6; --stop: #ef4444; --muted: #a1a1aa;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 1rem; background: var(--bg); color: var(--fg);
  font: 16px/1.5 system-ui, -apple-system, sans-serif;
  max-width: 46rem; margin-inline: auto;
}
h1 { font-size: 1.25rem; margin: 0 0 1rem; }
section {
  background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 1rem; margin-bottom: 1rem;
}
h2 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: .05em;
     color: var(--muted); margin: 0 0 .75rem; }
button {
  font: inherit; padding: .6rem 1rem; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--line); background: var(--card); color: var(--fg);
}
button:hover { border-color: var(--accent); }
#stop {
  width: 100%; font-size: 1.25rem; font-weight: 600; padding: 1rem;
  background: var(--stop); color: #fff; border: none;
}
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr)); gap: .5rem; }
.row { display: flex; gap: .5rem; }
.row input[type=text] { flex: 1; min-width: 0; }
input[type=text] {
  font: inherit; padding: .6rem; border-radius: 6px;
  border: 1px solid var(--line); background: var(--bg); color: var(--fg);
}
input[type=range] { width: 100%; }
label { display: block; margin-bottom: .75rem; }
label span { font-variant-numeric: tabular-nums; color: var(--muted); }
dl { display: grid; grid-template-columns: auto 1fr; gap: .25rem 1rem; margin: 0; }
dt { color: var(--muted); }
dd { margin: 0; font-weight: 600; }
#log {
  font-family: ui-monospace, monospace; font-size: .8rem; white-space: pre-wrap;
  max-height: 14rem; overflow-y: auto; margin: 0;
}
.err { color: var(--stop); }
.hint { color: var(--muted); font-size: .8rem; margin: .5rem 0 0; }
label.inline { display: flex; gap: .5rem; align-items: flex-start; margin: 0; }
"""

_SCRIPT = """
const log = document.getElementById('log');
function note(line, isError) {
  const t = new Date().toLocaleTimeString();
  const el = document.createElement('div');
  if (isError) el.className = 'err';
  el.textContent = `${t}  ${line}`;
  log.prepend(el);
}

async function call(method, path, body) {
  try {
    const opts = { method };
    if (body !== undefined) {
      opts.headers = { 'content-type': 'application/json' };
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    const text = await res.text();
    note(`${method} ${path} -> ${res.status} ${text}`, !res.ok);
    return res.ok ? JSON.parse(text || '{}') : null;
  } catch (e) {
    note(`${method} ${path} -> ${e}`, true);
    return null;
  }
}

function paint(s) {
  if (!s) return;
  document.getElementById('mode').textContent = s.mode;
  document.getElementById('current').textContent = s.current_action ?? '-';
  document.getElementById('desired').textContent = s.desired_action ?? '-';
}

document.querySelectorAll('[data-action]').forEach(b => {
  b.onclick = async () => paint(await call('POST', `/actions/${b.dataset.action}`));
});
// /halt, not /stop: /stop interrogates for the stop cam and keeps the motor
// running for seconds. A control labelled STOP must cut the motor now.
document.getElementById('stop').onclick = async () => paint(await call('POST', '/halt'));

document.getElementById('say-go').onclick = async () => {
  const el = document.getElementById('say-text');
  if (!el.value.trim()) return;
  const animate = document.getElementById('say-animate').checked;
  paint(await call('POST', '/say', { text: el.value, animate }));
};
document.getElementById('say-text').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('say-go').click();
});

function wireSlider(id) {
  const el = document.getElementById(id);
  const out = document.getElementById(id + '-value');
  el.addEventListener('input', () => { out.textContent = el.value; });
  el.addEventListener('change', async () => {
    await call('PATCH', '/speeds', { [id]: Number(el.value) });
  });
}
wireSlider('interrogation_speed');
wireSlider('action_speed');

async function loadSpeeds() {
  const s = await call('GET', '/speeds');
  if (!s) return;
  for (const k of ['interrogation_speed', 'action_speed']) {
    document.getElementById(k).value = s[k];
    document.getElementById(k + '-value').textContent = s[k];
  }
}

// Poll rather than stream: one client on a bench does not justify websockets.
async function poll() {
  try {
    const res = await fetch('/status');
    if (res.ok) paint(await res.json());
  } catch (e) { /* transient while the service restarts */ }
}
loadSpeeds();
poll();
setInterval(poll, 1000);
"""


def render_index(actions: Iterable[Action]) -> str:
    # Every action, stop included: the oversized red control is /halt, which is
    # a different operation from driving the drum round to the stop cam.
    buttons = "\n".join(
        f'      <button data-action="{a.value}">{a.value.replace("_", " ")}</button>'
        for a in actions
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verbot</title>
<style>{_STYLE}</style>
</head>
<body>
<h1>Verbot</h1>

<section>
  <button id="stop">STOP</button>
  <p class="hint">Cuts the motor immediately. The <em>stop</em> action below
  instead drives the drum round to the stop cam, which takes a few seconds.</p>
</section>

<section>
  <h2>Status</h2>
  <dl id="status-panel">
    <dt>Mode</dt><dd id="mode">-</dd>
    <dt>Current</dt><dd id="current">-</dd>
    <dt>Desired</dt><dd id="desired">-</dd>
  </dl>
</section>

<section>
  <h2>Actions</h2>
  <div class="grid">
{buttons}
  </div>
</section>

<section>
  <h2>Speak</h2>
  <div class="row">
    <input type="text" id="say-text" placeholder="I am Verbot" maxlength="500">
    <button id="say-go">Say</button>
  </div>
  <p class="hint">
    <label class="inline">
      <input type="checkbox" id="say-animate" checked>
      Move the mouth — runs the talk action while the phrase plays, then parks
      at the stop cam. Uncheck to test the speaker alone.
    </label>
  </p>
</section>

<section>
  <h2>Speeds</h2>
  <label>
    Interrogation <span id="interrogation_speed-value">-</span>
    <input type="range" id="interrogation_speed" min="0" max="100" step="1">
  </label>
  <label>
    Action <span id="action_speed-value">-</span>
    <input type="range" id="action_speed" min="-100" max="100" step="1">
  </label>
</section>

<section>
  <h2>Log</h2>
  <div id="log"></div>
</section>

<script>{_SCRIPT}</script>
</body>
</html>
"""
