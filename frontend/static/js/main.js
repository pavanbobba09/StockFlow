const API_BASE = window.location.protocol === 'file:' ? 'http://localhost:8000' : window.location.origin;

let map;
let state = null;
let restaurantLayer = L.layerGroup();
let warehouseLayer = L.layerGroup();
let routeLayer = L.layerGroup();
let autoplayTimer = null;
let liveEventSource = null;
let stateRefreshTimer = null;
let lastLiveSequence = null;

const statusColors = {
    healthy: '#22c55e',
    low: '#f59e0b',
    critical: '#ef4444',
    expiry: '#a855f7',
};

function initMap() {
    map = L.map('map', {
        zoomControl: false,
        attributionControl: false,
    }).setView([40.735, -73.985], 11);

    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        subdomains: 'abcd',
        maxZoom: 19,
    }).addTo(map);

    restaurantLayer.addTo(map);
    warehouseLayer.addTo(map);
    routeLayer.addTo(map);
}

async function fetchJson(url, options = {}) {
    const res = await fetch(url, options);
    if (!res.ok) {
        const body = await res.text();
        throw new Error(`${res.status} ${body}`);
    }
    return res.json();
}

async function loadState() {
    state = await fetchJson(`${API_BASE}/demo/state`);
    renderState();
}

function renderState() {
    if (!state) return;
    renderHeader();
    renderMetrics();
    renderLiveSignals();
    renderAgents();
    renderDecisions();
    renderEvents();
    renderMap();
    syncScenarioButtons();
}

function renderHeader() {
    document.getElementById('scenario-label').textContent = state.scenario.label;
    document.getElementById('sim-day').textContent = `Day ${state.sim_day}`;
    document.getElementById('sim-date').textContent = state.sim_date;
    document.getElementById('autoplay-btn').textContent = state.autoplay ? 'Pause' : 'Auto Play';
}

function renderMetrics() {
    const m = state.metrics;
    document.getElementById('metric-stockouts-avoided').textContent = number(m.stockouts_avoided);
    document.getElementById('metric-waste-reduced').textContent = `${number(m.waste_reduced)} units`;
    document.getElementById('metric-transferred').textContent = `${number(m.units_transferred)} units`;
    document.getElementById('metric-profit').textContent = currency(m.estimated_profit_saved);
    document.getElementById('without-stockouts').textContent = number(m.without_agents.projected_stockouts);
    document.getElementById('with-stockouts').textContent = number(m.with_agents.stockouts);
    document.getElementById('without-waste').textContent = `${number(m.without_agents.projected_waste)} units`;
    document.getElementById('with-waste').textContent = `${number(m.with_agents.waste)} units`;
    document.getElementById('fill-rate').textContent = `Fill rate ${Math.round(m.fill_rate * 100)}%`;
    document.getElementById('pending-count').textContent = `${m.pending_decisions} pending`;
}

function renderLiveSignals() {
    const signals = state.live_signals || {};
    document.getElementById('live-signal-status').textContent = signals.status || 'unknown';
    document.getElementById('live-demand-multiplier').textContent =
        `${Number(signals.demand_multiplier || 1).toFixed(2)}x demand pressure`;

    const reasons = document.getElementById('live-signal-reasons');
    reasons.innerHTML = '';
    (signals.reasons || ['No live signals loaded yet.']).slice(0, 3).forEach(reason => {
        const node = document.createElement('span');
        node.textContent = reason;
        reasons.appendChild(node);
    });
}

function renderAgents() {
    const container = document.getElementById('agent-roster');
    container.innerHTML = '';
    state.agents.forEach(agent => {
        const node = document.createElement('article');
        node.className = 'agent-card';
        node.innerHTML = `
            <div class="agent-pulse"></div>
            <div>
                <h3>${escapeHtml(agent.name)}</h3>
                <p>${escapeHtml(agent.role)}</p>
                <span>${escapeHtml(agent.status)}</span>
            </div>
        `;
        container.appendChild(node);
    });
}

function renderDecisions() {
    const container = document.getElementById('decision-list');
    container.innerHTML = '';
    if (!state.pending_decisions.length) {
        container.innerHTML = '<div class="empty-state">No pending decisions. Run a day or load a scenario.</div>';
        return;
    }

    state.pending_decisions.forEach(decision => {
        const target = decision.target_store_name ? ` -> ${decision.target_store_name}` : '';
        const node = document.createElement('article');
        node.className = `decision-card ${decision.decision_type}`;
        node.innerHTML = `
            <div class="decision-head">
                <span>${escapeHtml(decision.decision_type)}</span>
                <strong>${number(decision.quantity)} units</strong>
            </div>
            <h3>${escapeHtml(decision.item_name || `Item ${decision.item_id}`)}</h3>
            <p class="decision-store">${escapeHtml(decision.store_name || `Store ${decision.store_id}`)}${escapeHtml(target)}</p>
            <p>${escapeHtml(decision.reason)}</p>
            <p class="impact">${escapeHtml(decision.expected_impact)}</p>
            <div class="decision-actions">
                <button class="approve" data-approve="${decision.id}">Approve</button>
                <button class="reject" data-reject="${decision.id}">Reject</button>
            </div>
        `;
        container.appendChild(node);
    });

    container.querySelectorAll('[data-approve]').forEach(button => {
        button.addEventListener('click', () => decide(button.dataset.approve, 'approve'));
    });
    container.querySelectorAll('[data-reject]').forEach(button => {
        button.addEventListener('click', () => decide(button.dataset.reject, 'reject'));
    });
}

function renderEvents() {
    const container = document.getElementById('event-feed');
    container.innerHTML = '';
    const traceCards = (state.reasoning_traces || []).slice(0, 10).map(trace => ({
        kind: 'trace',
        severity: 'trace',
        agent_name: trace.agent_name,
        label: trace.tool_name,
        message: `${trace.observation} Decision: ${trace.decision}`,
        input: trace.input_summary,
    }));
    const eventCards = (state.events || []).slice(0, 14).map(event => ({
        kind: 'event',
        severity: event.severity,
        agent_name: event.agent_name,
        label: event.event_type.replaceAll('_', ' '),
        message: event.message,
        input: '',
    }));
    const cards = [...traceCards, ...eventCards].slice(0, 20);
    if (!cards.length) {
        container.innerHTML = '<div class="empty-state">LangGraph tool traces and agent events appear here as the simulation runs.</div>';
        return;
    }
    cards.forEach(card => {
        const node = document.createElement('article');
        node.className = `event-card ${card.severity}`;
        node.innerHTML = `
            <div>
                <strong>${escapeHtml(card.agent_name)}</strong>
                <span>${escapeHtml(card.kind === 'trace' ? `tool: ${card.label}` : card.label)}</span>
            </div>
            ${card.input ? `<p class="trace-input">${escapeHtml(card.input)}</p>` : ''}
            <p>${escapeHtml(card.message)}</p>
        `;
        container.appendChild(node);
    });
}

function renderMap() {
    restaurantLayer.clearLayers();
    warehouseLayer.clearLayers();
    routeLayer.clearLayers();

    const bounds = [];

    state.restaurants.forEach(store => {
        const color = statusColors[store.status] || statusColors.healthy;
        const height = Math.max(22, Math.min(96, Math.round(store.inventory_units / 12)));
        const riskPct = Math.round(store.stockout_risk * 100);
        const expiryPct = Math.round(store.expiry_risk * 100);
        const icon = L.divIcon({
            className: 'store-tower-icon',
            html: `
                <div class="tower-wrap">
                    <div class="tower" style="height:${height}px;border-color:${color};box-shadow:0 0 22px ${color}66">
                        <span style="background:${color}"></span>
                    </div>
                    <b>${escapeHtml(shortName(store.name))}</b>
                </div>
            `,
            iconSize: [72, 112],
            iconAnchor: [36, 96],
        });
        const marker = L.marker([store.lat, store.lng], { icon }).addTo(restaurantLayer);
        marker.bindPopup(storePopup(store, riskPct, expiryPct));
        bounds.push([store.lat, store.lng]);
    });

    state.warehouses.forEach(warehouse => {
        const icon = L.divIcon({
            className: 'warehouse-icon',
            html: `
                <div class="warehouse-node">
                    <div></div>
                    <strong>${escapeHtml(shortName(warehouse.name))}</strong>
                </div>
            `,
            iconSize: [86, 72],
            iconAnchor: [43, 54],
        });
        L.marker([warehouse.lat, warehouse.lng], { icon }).addTo(warehouseLayer)
            .bindPopup(`<h3>${escapeHtml(warehouse.name)}</h3><p>${number(warehouse.inventory_units)} supply units</p>`);
        bounds.push([warehouse.lat, warehouse.lng]);
    });

    state.routes.forEach(route => {
        const color = route.type === 'transfer' ? '#38bdf8' : '#f59e0b';
        const line = L.polyline(
            [[route.from.lat, route.from.lng], [route.to.lat, route.to.lng]],
            { color, weight: 4, opacity: 0.9, dashArray: '8 10', className: 'animated-route' }
        ).addTo(routeLayer);
        line.bindPopup(`
            <h3>${escapeHtml(route.type)} proposal</h3>
            <p>${number(route.quantity)} units of ${escapeHtml(route.item_name)}</p>
            <p>${escapeHtml(route.from.name)} to ${escapeHtml(route.to.name)}</p>
        `);
    });

    if (bounds.length) {
        map.fitBounds(bounds, { padding: [30, 30], maxZoom: 11 });
    }
}

function storePopup(store, riskPct, expiryPct) {
    const rows = store.top_items.slice(0, 5).map(item => `
        <li>
            <span>${escapeHtml(item.name)}</span>
            <strong>${number(item.quantity)}</strong>
            <em>${Math.round(item.risk * 100)}% risk</em>
        </li>
    `).join('');
    return `
        <div class="popup-content">
            <h3>${escapeHtml(store.name)}</h3>
            <p>${number(store.inventory_units)} total units</p>
            <p>Stockout risk: ${riskPct}% | Expiry risk: ${expiryPct}%</p>
            <ul>${rows}</ul>
        </div>
    `;
}

async function runDay() {
    setBusy(true);
    try {
        state = await fetchJson(`${API_BASE}/demo/tick`, { method: 'POST' });
        renderState();
    } catch (err) {
        showError(err);
    } finally {
        setBusy(false);
    }
}

async function resetDemo() {
    setBusy(true);
    try {
        state = await fetchJson(`${API_BASE}/demo/reset`, { method: 'POST' });
        stopLocalAutoplay();
        renderState();
    } catch (err) {
        showError(err);
    } finally {
        setBusy(false);
    }
}

async function toggleAutoplay() {
    if (autoplayTimer) {
        stopLocalAutoplay();
        await fetchJson(`${API_BASE}/demo/autoplay/stop`, { method: 'POST' });
        await loadState();
        return;
    }
    await fetchJson(`${API_BASE}/demo/autoplay/start`, { method: 'POST' });
    autoplayTimer = setInterval(runDay, state?.simulation_speed_ms || 4500);
    await loadState();
}

function stopLocalAutoplay() {
    if (autoplayTimer) {
        clearInterval(autoplayTimer);
        autoplayTimer = null;
    }
}

async function loadScenario(name) {
    setBusy(true);
    try {
        state = await fetchJson(`${API_BASE}/demo/scenario/${name}`, { method: 'POST' });
        renderState();
    } catch (err) {
        showError(err);
    } finally {
        setBusy(false);
    }
}

async function decide(id, action) {
    setBusy(true);
    try {
        await fetchJson(`${API_BASE}/agents/decisions/${id}/${action}`, { method: 'POST' });
        await loadState();
    } catch (err) {
        showError(err);
    } finally {
        setBusy(false);
    }
}

function bindControls() {
    document.getElementById('run-day-btn').addEventListener('click', runDay);
    document.getElementById('reset-btn').addEventListener('click', resetDemo);
    document.getElementById('autoplay-btn').addEventListener('click', toggleAutoplay);
    document.querySelectorAll('.scenario-btn').forEach(button => {
        button.addEventListener('click', () => loadScenario(button.dataset.scenario));
    });
}

function connectLiveEvents() {
    if (!window.EventSource) {
        setLiveStreamStatus('unsupported');
        return;
    }
    if (liveEventSource) {
        liveEventSource.close();
    }

    liveEventSource = new EventSource(`${API_BASE}/live/events`);
    setLiveStreamStatus('connecting');

    liveEventSource.addEventListener('open', () => {
        setLiveStreamStatus('live');
    });

    liveEventSource.addEventListener('stockflow-state', event => {
        setLiveStreamStatus('live');
        try {
            const payload = JSON.parse(event.data);
            if (payload.sequence !== lastLiveSequence) {
                lastLiveSequence = payload.sequence;
                scheduleStateRefresh();
            }
        } catch (err) {
            console.error('Bad live event payload:', err);
        }
    });

    liveEventSource.addEventListener('error', () => {
        setLiveStreamStatus('reconnecting');
    });
}

function scheduleStateRefresh() {
    if (stateRefreshTimer) {
        clearTimeout(stateRefreshTimer);
    }
    stateRefreshTimer = setTimeout(async () => {
        try {
            await loadState();
        } catch (err) {
            showError(err);
        }
    }, 250);
}

function setLiveStreamStatus(value) {
    const status = document.getElementById('live-stream-status');
    status.textContent = `Stream: ${value}`;
    status.dataset.status = value;
}

function syncScenarioButtons() {
    document.querySelectorAll('.scenario-btn').forEach(button => {
        button.classList.toggle('active', state.scenario.name === button.dataset.scenario);
    });
}

function setBusy(isBusy) {
    document.body.classList.toggle('busy', isBusy);
}

function showError(err) {
    console.error(err);
    const feed = document.getElementById('event-feed');
    const node = document.createElement('article');
    node.className = 'event-card critical';
    node.innerHTML = `<div><strong>Demo Error</strong><span>request failed</span></div><p>${escapeHtml(err.message)}</p>`;
    feed.prepend(node);
}

function number(value) {
    return Number(value || 0).toLocaleString();
}

function currency(value) {
    return Number(value || 0).toLocaleString(undefined, {
        style: 'currency',
        currency: 'USD',
        maximumFractionDigits: 0,
    });
}

function shortName(name) {
    return String(name).replace('Distribution Center', 'DC').replace('Logistics', 'Logistics');
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

document.addEventListener('DOMContentLoaded', async () => {
    initMap();
    bindControls();
    connectLiveEvents();
    try {
        await loadState();
    } catch (err) {
        showError(err);
    }
});
