/**
 * Demo Runner — State machine driving the guided walkthrough.
 *
 * Plays through 8 steps of the advisory flow with animations,
 * narration, and interactive controls. Zero backend calls.
 */

var DemoRunner = (function () {
    var STEPS = ['intro', 'idea', 'questions', 'design', 'capabilities', 'results', 'simulation', 'cta'];
    var state = 'idle'; // idle | playing | paused | complete
    var currentStep = -1;
    var timers = [];
    var cancelled = 0; // incremented on skip/replay to invalidate stale async chains
    var data = null;
    var graphAPI = null;
    var resumeResolve = null;

    // ── Utilities ──────────────────────────────────────────

    function sleep(ms) {
        var gen = cancelled;
        return new Promise(function (resolve) {
            var id = setTimeout(function () {
                timers = timers.filter(function (t) { return t !== id; });
                resolve(gen === cancelled);
            }, ms);
            timers.push(id);
        });
    }

    function clearTimers() {
        timers.forEach(clearTimeout);
        timers = [];
    }

    function alive() {
        return state === 'playing';
    }

    async function waitIfPaused() {
        if (state === 'paused') {
            await new Promise(function (resolve) { resumeResolve = resolve; });
        }
    }

    function trackDemo(name, props) {
        try { if (window.trackBookingEvent) window.trackBookingEvent(name, props || {}); } catch (e) { }
    }

    // ── DOM Helpers ────────────────────────────────────────

    function $(id) { return document.getElementById(id); }

    function showStep(name) {
        document.querySelectorAll('.demo-step').forEach(function (el) { el.style.display = 'none'; });
        var el = $('step-' + name);
        if (el) el.style.display = '';
        // Update dots
        document.querySelectorAll('.step-dot').forEach(function (dot, i) {
            dot.classList.toggle('active', i === currentStep);
            dot.classList.toggle('done', i < currentStep);
        });
    }

    function setNarration(text) {
        var el = $('narrationText');
        if (el) el.textContent = text;
    }

    // ── Typing Animation ──────────────────────────────────

    async function typeText(el, text, speed) {
        speed = speed || 35;
        el.value = '';
        for (var i = 0; i < text.length; i++) {
            if (!alive()) return;
            await waitIfPaused();
            el.value += text[i];
            el.scrollTop = el.scrollHeight;
            await sleep(speed);
        }
    }

    // ── Chat Bubbles ──────────────────────────────────────

    async function appendBotBubble(container, html) {
        // Typing dots first
        var dots = document.createElement('div');
        dots.className = 'msg msg-bot';
        dots.innerHTML = '<div class="msg-avatar bg-primary-subtle text-primary"><i class="bi bi-cpu"></i></div>' +
            '<div class="bubble"><div class="typing-dots"><span></span><span></span><span></span></div></div>';
        container.appendChild(dots);
        container.scrollTop = container.scrollHeight;
        await sleep(800);
        if (!alive()) return;
        // Replace with actual message
        dots.querySelector('.bubble').innerHTML = '<strong>' + html + '</strong>';
        container.scrollTop = container.scrollHeight;
    }

    function appendUserBubble(container, text) {
        var msg = document.createElement('div');
        msg.className = 'msg msg-user';
        msg.innerHTML = '<div class="bubble">' + text + '</div>' +
            '<div class="msg-avatar bg-primary text-white"><i class="bi bi-person"></i></div>';
        container.appendChild(msg);
        container.scrollTop = container.scrollHeight;
    }

    // ── Card Click Animation ──────────────────────────────

    function clickCard(el) {
        el.classList.add('selected');
        el.style.transform = 'scale(1.03)';
        el.style.boxShadow = '0 0 20px rgba(59,130,246,0.3)';
        setTimeout(function () {
            el.style.transform = '';
            el.style.boxShadow = '';
        }, 400);
        var cb = el.querySelector('input[type="checkbox"]');
        if (cb) cb.checked = true;
    }

    // ── Count Up ──────────────────────────────────────────

    function countUp(el, target, prefix, suffix, duration) {
        prefix = prefix || '';
        suffix = suffix || '';
        duration = duration || 1500;
        var start = 0;
        var startTime = null;
        function step(ts) {
            if (!startTime) startTime = ts;
            var p = Math.min((ts - startTime) / duration, 1);
            var eased = 1 - Math.pow(1 - p, 3);
            var cur = Math.floor(eased * target);
            el.textContent = prefix + cur.toLocaleString() + suffix;
            if (p < 1) requestAnimationFrame(step);
        }
        requestAnimationFrame(step);
    }

    // ── D3 Network Graph ──────────────────────────────────

    function buildDemoGraph(containerId, agents) {
        var container = $(containerId);
        if (!container) return { highlight: function () { }, destroy: function () { } };
        container.innerHTML = '';
        var w = container.clientWidth || 600;
        var h = 400;

        var deptColors = {
            'Executive': '#1a1a2e', 'Operations': '#f59e0b', 'Customer Support': '#6f42c1',
            'Sales': '#4361ee', 'Finance': '#dc3545', 'Marketing': '#198754',
            'HR': '#0dcaf0', 'Logistics': '#fd7e14', 'Engineering': '#6366f1'
        };

        var nodes = [];
        var links = [];
        var deptMap = {};

        agents.forEach(function (a) {
            nodes.push({
                id: a.name, name: a.name, dept: a.department,
                r: a.is_cory ? 28 : (a.is_primary_focus ? 16 : 12),
                color: deptColors[a.department] || '#64748b',
                isCory: a.is_cory || false
            });
            if (!a.is_cory) {
                if (!deptMap[a.department]) deptMap[a.department] = [];
                deptMap[a.department].push(a.name);
            }
        });

        // Link all agents to Control Tower
        var cory = nodes.find(function (n) { return n.isCory; });
        if (cory) {
            nodes.forEach(function (n) {
                if (!n.isCory) links.push({ source: cory.id, target: n.id });
            });
        }
        // Link agents within same dept
        Object.values(deptMap).forEach(function (arr) {
            for (var i = 0; i < arr.length - 1; i++) {
                links.push({ source: arr[i], target: arr[i + 1] });
            }
        });

        var svg = d3.select('#' + containerId).append('svg').attr('width', w).attr('height', h);
        var defs = svg.append('defs');
        var filter = defs.append('filter').attr('id', 'demo-glow');
        filter.append('feGaussianBlur').attr('stdDeviation', '3').attr('result', 'blur');
        var merge = filter.append('feMerge');
        merge.append('feMergeNode').attr('in', 'blur');
        merge.append('feMergeNode').attr('in', 'SourceGraphic');

        var sim = d3.forceSimulation(nodes)
            .force('link', d3.forceLink(links).id(function (d) { return d.id; }).distance(80))
            .force('charge', d3.forceManyBody().strength(-250))
            .force('center', d3.forceCenter(w / 2, h / 2))
            .force('collision', d3.forceCollide(25));

        var link = svg.append('g').selectAll('line').data(links).enter().append('line')
            .attr('stroke', '#cbd5e1').attr('stroke-width', 1.5).attr('stroke-dasharray', '4,3');

        var node = svg.append('g').selectAll('g').data(nodes).enter().append('g');

        node.append('circle')
            .attr('r', function (d) { return d.r; })
            .attr('fill', function (d) { return d.color; })
            .attr('stroke', 'white').attr('stroke-width', 2)
            .attr('filter', function (d) { return d.isCory ? 'url(#demo-glow)' : null; });

        node.append('text')
            .text(function (d) { return d.name.substring(0, 2).toUpperCase(); })
            .attr('text-anchor', 'middle').attr('dy', '0.35em')
            .attr('fill', 'white').attr('font-size', '9px').attr('font-weight', '700')
            .style('pointer-events', 'none');

        sim.on('tick', function () {
            link.attr('x1', function (d) { return d.source.x; }).attr('y1', function (d) { return d.source.y; })
                .attr('x2', function (d) { return d.target.x; }).attr('y2', function (d) { return d.target.y; });
            node.attr('transform', function (d) {
                d.x = Math.max(30, Math.min(w - 30, d.x));
                d.y = Math.max(30, Math.min(h - 30, d.y));
                return 'translate(' + d.x + ',' + d.y + ')';
            });
        });

        function highlight(agentName) {
            node.select('circle').transition().duration(200)
                .attr('r', function (d) { return d.name === agentName ? d.r + 8 : d.r; })
                .attr('stroke', function (d) { return d.name === agentName ? '#facc15' : 'white'; })
                .attr('stroke-width', function (d) { return d.name === agentName ? 4 : 2; });
            link.transition().duration(200)
                .attr('stroke', function (d) {
                    return (d.source.name === agentName || d.target.name === agentName) ? '#facc15' : '#cbd5e1';
                })
                .attr('stroke-width', function (d) {
                    return (d.source.name === agentName || d.target.name === agentName) ? 3 : 1.5;
                });
            setTimeout(function () {
                node.select('circle').transition().duration(500)
                    .attr('r', function (d) { return d.r; }).attr('stroke', 'white').attr('stroke-width', 2);
                link.transition().duration(500).attr('stroke', '#cbd5e1').attr('stroke-width', 1.5);
            }, 1500);
        }

        function destroy() {
            sim.stop();
            svg.remove();
        }

        return { highlight: highlight, destroy: destroy };
    }

    // ── Step Runners ──────────────────────────────────────

    async function runIntro() {
        setNarration('');
        showStep('intro');
        trackDemo('demo_started');
        if (!await sleep(2500)) return;
        nextStep();
    }

    async function runIdea() {
        setNarration(data.narration.idea);
        showStep('idea');
        var ta = $('demo-idea-textarea');
        if (!ta) return;
        if (!await sleep(800)) return;
        await typeText(ta, data.company.idea, 40);
        if (!alive()) return;
        var btn = $('demo-idea-btn');
        if (btn) { btn.classList.add('btn-success'); btn.classList.remove('btn-primary'); }
        if (!await sleep(1200)) return;
        nextStep();
    }

    async function runQuestions() {
        setNarration(data.narration.questions);
        showStep('questions');
        var chat = $('demo-chat-messages');
        var bar = $('demo-chat-progress');
        if (!chat) return;
        chat.innerHTML = '';

        for (var i = 0; i < data.questions.length; i++) {
            if (!alive()) return;
            await waitIfPaused();
            var q = data.questions[i];

            // Update progress
            var pct = Math.round(((i + 1) / data.questions.length) * 100);
            if (bar) bar.style.width = pct + '%';

            // Bot asks
            await appendBotBubble(chat, q.question);
            if (!alive()) return;
            await sleep(600);

            // Show options if present
            if (q.options && q.answer_method === 'chip') {
                var chipRow = document.createElement('div');
                chipRow.className = 'd-flex flex-wrap gap-2 ms-5 mb-2';
                q.options.forEach(function (opt) {
                    var btn = document.createElement('button');
                    btn.className = 'btn btn-sm btn-outline-primary rounded-pill';
                    btn.textContent = opt;
                    chipRow.appendChild(btn);
                });
                chat.appendChild(chipRow);
                chat.scrollTop = chat.scrollHeight;
                await sleep(500);
                if (!alive()) return;

                // Click the answer chip(s)
                var answers = q.answer.split(', ');
                var chips = chipRow.querySelectorAll('button');
                chips.forEach(function (chip) {
                    if (answers.indexOf(chip.textContent) !== -1) {
                        chip.classList.add('btn-primary', 'active');
                        chip.classList.remove('btn-outline-primary');
                    }
                });
                await sleep(400);
            }

            // User answers
            if (!alive()) return;
            appendUserBubble(chat, q.answer);
            await sleep(800);
        }
        if (!alive()) return;
        if (!await sleep(800)) return;
        nextStep();
    }

    async function runDesign() {
        setNarration(data.narration.design);
        showStep('design');
        if (!await sleep(800)) return;

        // Click outcomes
        for (var i = 0; i < data.design.selected_outcomes.length; i++) {
            if (!alive()) return;
            await waitIfPaused();
            var card = $('demo-outcome-' + data.design.selected_outcomes[i]);
            if (card) clickCard(card);
            await sleep(700);
        }

        if (!await sleep(400)) return;

        // Click systems
        for (var j = 0; j < data.design.selected_systems.length; j++) {
            if (!alive()) return;
            await waitIfPaused();
            var card = $('demo-system-' + data.design.selected_systems[j]);
            if (card) clickCard(card);
            await sleep(700);
        }

        if (!await sleep(1200)) return;
        nextStep();
    }

    async function runCapabilities() {
        setNarration(data.narration.capabilities);
        showStep('capabilities');
        if (!await sleep(600)) return;

        var count = 0;
        for (var d = 0; d < data.capabilities.departments.length; d++) {
            if (!alive()) return;
            var dept = data.capabilities.departments[d];

            // Activate tab
            document.querySelectorAll('.demo-dept-tab').forEach(function (t) { t.classList.remove('active'); });
            var tab = $('demo-tab-' + dept.id.replace(/ /g, '-'));
            if (tab) tab.classList.add('active');

            document.querySelectorAll('.demo-dept-pane').forEach(function (p) { p.style.display = 'none'; });
            var pane = $('demo-pane-' + dept.id.replace(/ /g, '-'));
            if (pane) pane.style.display = '';

            await sleep(400);

            for (var c = 0; c < dept.capabilities.length; c++) {
                if (!alive()) return;
                await waitIfPaused();
                var cap = dept.capabilities[c];
                if (data.capabilities.selected.indexOf(cap.id) !== -1) {
                    var el = $('demo-cap-' + cap.id);
                    if (el) clickCard(el);
                    count++;
                    var counter = $('demo-cap-count');
                    if (counter) counter.textContent = count + ' selected';
                    await sleep(500);
                }
            }
        }

        if (!await sleep(1000)) return;
        nextStep();
    }

    function _agentCardHTML(agent) {
        var deptColors = {'Executive':'dark','Operations':'warning','Customer Support':'info','Sales':'primary','Finance':'danger','Marketing':'success','HR':'info','Compliance':'success','Field Services':'success','Production':'warning','Quality':'success','Supply Chain':'info','Curriculum':'primary','Student Support':'info','Claims':'primary','Underwriting':'warning','Leasing':'primary','Property Management':'primary','Tenant Relations':'info','Maintenance':'warning','Customer Service':'info','Engineering':'secondary'};
        var c = deptColors[agent.department] || 'secondary';
        return '<div style="border:2px solid #facc15;border-radius:10px;padding:14px;background:#fffbeb;animation:msgIn .3s ease;">' +
            '<div class="d-flex align-items-center gap-2 mb-2">' +
            '<i class="bi ' + (agent.is_cory ? 'bi-cpu' : 'bi-robot') + ' fs-5 text-' + c + '"></i>' +
            '<div><strong style="font-size:.9rem;">' + agent.name + '</strong>' +
            '<span class="badge bg-' + c + '-subtle text-' + c + ' ms-2" style="font-size:.6rem;">' + agent.department + '</span></div></div>' +
            '<div style="font-size:.8rem;color:#475569;line-height:1.5;">' + (agent.role || agent.name + ' agent') + '</div></div>';
    }

    async function runResults() {
        setNarration(data.narration.results);
        showStep('results');
        if (!await sleep(400)) return;

        // Count up KPIs
        var kpis = data.results.kpis;
        var el;
        el = $('demo-kpi-savings');
        if (el) countUp(el, parseInt(kpis.cost_savings.replace(/[^0-9]/g, '')), '$', 'K');
        el = $('demo-kpi-revenue');
        if (el) countUp(el, parseFloat(kpis.revenue_impact.replace(/[^0-9.]/g, '')), '$', 'M');
        el = $('demo-kpi-time');
        if (el) countUp(el, parseInt(kpis.time_saved.replace(/[^0-9]/g, '')), '', 'h/wk');
        el = $('demo-kpi-roi');
        if (el) countUp(el, parseInt(kpis.three_year_roi.replace(/[^0-9]/g, '')), '', '%');
        el = $('demo-kpi-agents');
        if (el) countUp(el, kpis.total_agents, '', '');

        await sleep(1800);
        if (!alive()) return;

        // Rebuild results section with graph + agent card side by side
        var resultsGraph = $('demo-results-graph');
        if (resultsGraph) {
            var wrapper = resultsGraph.parentElement;
            resultsGraph.remove();
            var row = document.createElement('div');
            row.className = 'row g-3';
            row.innerHTML = '<div class="col-lg-7"><div class="demo-graph" id="demo-results-graph" style="min-height:400px;"></div></div>' +
                '<div class="col-lg-5"><div class="small text-muted mb-2"><i class="bi bi-robot me-1"></i>Agent Details</div><div id="demo-agent-card"></div></div>';
            wrapper.appendChild(row);
        }

        // Build D3 graph
        if (graphAPI) graphAPI.destroy();
        graphAPI = buildDemoGraph('demo-results-graph', data.results.agents);

        await sleep(2000);
        if (!alive()) return;

        // Cycle through agents with detail card
        var agents = data.results.agents;
        var cardEl = $('demo-agent-card');
        for (var i = 0; i < agents.length; i++) {
            if (!alive()) return;
            await waitIfPaused();
            var agent = agents[i];
            graphAPI.highlight(agent.name);
            if (cardEl) cardEl.innerHTML = _agentCardHTML(agent);
            setNarration(agent.name + ': ' + (agent.role || ''));
            await sleep(1800);
        }

        if (!await sleep(800)) return;
        nextStep();
    }

    async function runSimulation() {
        setNarration(data.narration.simulation);
        showStep('simulation');
        if (!await sleep(400)) return;

        // Build sim graph
        if (graphAPI) graphAPI.destroy();
        graphAPI = buildDemoGraph('demo-sim-graph', data.results.agents);
        await sleep(1500);
        if (!alive()) return;

        var feed = $('demo-sim-feed');
        var evCount = 0;

        for (var i = 0; i < data.simulation.events.length; i++) {
            if (!alive()) return;
            await waitIfPaused();
            var ev = data.simulation.events[i];

            // Update sub-narration
            var subNarr = $('demo-sim-narration');
            if (subNarr) subNarr.textContent = ev.narration || '';

            // Highlight agent
            graphAPI.highlight(ev.agent);

            // Add to feed
            evCount++;
            var evEl = $('demo-sim-count');
            if (evEl) evEl.textContent = evCount + ' events';

            if (feed) {
                var item = document.createElement('div');
                item.className = 'feed-item' + (ev.agent === 'AI Control Tower' ? ' cory' : '');
                item.innerHTML = '<strong style="font-size:0.8rem;">' + ev.agent + '</strong>' +
                    '<div style="font-size:0.75rem;">' + ev.action + '</div>';
                feed.insertBefore(item, feed.firstChild);
            }

            await sleep(ev.delay || 2000);
        }

        if (!alive()) return;
        setNarration('Simulation complete. Your AI workforce handled everything autonomously.');
        if (!await sleep(2000)) return;
        nextStep();
    }

    async function runCTA() {
        setNarration(data.narration.cta);
        showStep('cta');
        trackDemo('demo_completed');
        state = 'complete';
        $('btnPause').style.display = 'none';
        $('btnSkip').style.display = 'none';
        $('btnReplay').classList.remove('d-none');
    }

    var stepRunners = [runIntro, runIdea, runQuestions, runDesign, runCapabilities, runResults, runSimulation, runCTA];

    // ── Core Control ──────────────────────────────────────

    function nextStep() {
        currentStep++;
        if (currentStep < stepRunners.length) {
            trackDemo('demo_step_viewed', { step: STEPS[currentStep], step_index: currentStep });
            stepRunners[currentStep]();
        }
    }

    function normalizeData(d) {
        // Normalize scenario format to runner format
        // Questions: q→question, a→answer, method→answer_method, chips→options, multi→multi_select
        if (d.questions && d.questions[0] && d.questions[0].q) {
            d.questions = d.questions.map(function(q) {
                return {
                    question: q.q || q.question,
                    answer: q.a || q.answer,
                    answer_method: q.method || q.answer_method || 'type',
                    options: q.chips || q.options || null,
                    multi_select: q.multi || q.multi_select || false
                };
            });
        }
        // Design: sel→rec, build selected lists
        if (d.design && d.design.outcomes && d.design.outcomes[0] && 'sel' in d.design.outcomes[0]) {
            d.design.selected_outcomes = d.design.outcomes.filter(function(o){return o.sel;}).map(function(o){return o.id;});
            d.design.selected_systems = d.design.systems.filter(function(s){return s.sel;}).map(function(s){return s.id;});
            d.design.outcomes.forEach(function(o){ o.rec = o.sel; });
            d.design.systems.forEach(function(s){ s.rec = s.sel; });
        }
        // Results: agents at top level → results.agents; kpis at top level → results.kpis
        if (d.agents && !d.results) {
            d.results = {
                agents: d.agents.map(function(a) {
                    return { name: a.name, department: a.dept || a.department, is_cory: a.cory || a.is_cory || false, is_primary_focus: a.primary || a.is_primary_focus || false };
                }),
                kpis: d.kpis ? {
                    cost_savings: '$' + d.kpis.savings + (d.kpis.savings_suf || 'K'),
                    revenue_impact: '$' + d.kpis.revenue + (d.kpis.revenue_suf || 'M'),
                    time_saved: '120h',
                    three_year_roi: d.kpis.roi + '%',
                    total_agents: d.kpis.agents
                } : {}
            };
        }
        // Simulation: sim→simulation.events
        if (d.sim && !d.simulation) {
            d.simulation = { events: d.sim.map(function(e) {
                return { agent: e.agent, action: e.action, narration: e.narr || e.narration, delay: e.delay || 2000 };
            })};
        }
        // Narration: narr→narration
        if (d.narr && !d.narration) {
            d.narration = d.narr;
        }
        // Build capabilities from agents if missing
        if (!d.capabilities && d.agents) {
            var deptMap = {};
            var deptIcons = {'Operations':'bi-gear','Customer Support':'bi-headset','Sales':'bi-currency-dollar','Marketing':'bi-megaphone','Finance':'bi-cash-stack','HR':'bi-people','Compliance':'bi-shield-check','Field Services':'bi-truck','Production':'bi-gear-wide-connected','Quality':'bi-check-circle','Supply Chain':'bi-truck','Curriculum':'bi-mortarboard','Student Support':'bi-chat-dots','Claims':'bi-file-earmark-check','Underwriting':'bi-clipboard-data','Customer Service':'bi-headset','Leasing':'bi-building','Property Management':'bi-building','Tenant Relations':'bi-people','Maintenance':'bi-wrench'};
            var deptColors = {'Operations':'warning','Customer Support':'info','Sales':'primary','Marketing':'success','Finance':'danger','HR':'info','Compliance':'success','Field Services':'success','Production':'warning','Quality':'success','Supply Chain':'info','Curriculum':'primary','Student Support':'info','Claims':'primary','Underwriting':'warning','Leasing':'primary','Property Management':'primary','Tenant Relations':'info','Maintenance':'warning'};
            (d.agents || []).forEach(function(a) {
                var dept = a.dept || a.department;
                if (!dept || dept === 'Executive') return;
                if (!deptMap[dept]) deptMap[dept] = [];
                deptMap[dept].push({id: a.name.toLowerCase().replace(/\s+/g,'_'), name: a.name, desc: a.role || 'AI-powered automation'});
            });
            var depts = [];
            var selected = [];
            Object.keys(deptMap).forEach(function(dept) {
                depts.push({id: dept, icon: deptIcons[dept] || 'bi-gear', color: deptColors[dept] || 'primary', capabilities: deptMap[dept]});
                deptMap[dept].forEach(function(c) { selected.push(c.id); });
            });
            d.capabilities = {departments: depts, selected: selected};
        }
        // Ensure narration has capabilities key
        if (d.narration && !d.narration.capabilities) {
            d.narration.capabilities = 'Now we select the specific AI capabilities each department needs.';
        }
        if (d.narration && !d.narration.cta) {
            d.narration.cta = 'This was just a demo. Ready to design an AI organization for YOUR business?';
        }
        return d;
    }

    return {
        init: function () {
            var raw = $('demo-data');
            if (!raw) return;
            data = normalizeData(JSON.parse(raw.textContent));
            state = 'playing';
            currentStep = -1;
            nextStep();
        },

        pause: function () {
            if (state === 'playing') {
                state = 'paused';
                $('btnPause').innerHTML = '<i class="bi bi-play-fill"></i> Resume';
                $('demoPulse').classList.remove('active');
                trackDemo('demo_paused', { step: STEPS[currentStep] });
            } else if (state === 'paused') {
                state = 'playing';
                $('btnPause').innerHTML = '<i class="bi bi-pause-fill"></i> Pause';
                $('demoPulse').classList.add('active');
                if (resumeResolve) { resumeResolve(); resumeResolve = null; }
            }
        },

        skip: function () {
            cancelled++;
            clearTimers();
            if (resumeResolve) { resumeResolve(); resumeResolve = null; }
            state = 'playing';
            $('btnPause').innerHTML = '<i class="bi bi-pause-fill"></i> Pause';
            $('demoPulse').classList.add('active');
            trackDemo('demo_skipped', { from_step: STEPS[currentStep] });
            nextStep();
        },

        replay: function () {
            cancelled++;
            clearTimers();
            if (resumeResolve) { resumeResolve(); resumeResolve = null; }
            if (graphAPI) { graphAPI.destroy(); graphAPI = null; }
            state = 'playing';
            currentStep = -1;
            $('btnPause').style.display = '';
            $('btnSkip').style.display = '';
            $('btnReplay').classList.add('d-none');
            $('btnPause').innerHTML = '<i class="bi bi-pause-fill"></i> Pause';
            $('demoPulse').classList.add('active');
            trackDemo('demo_replayed');
            nextStep();
        }
    };
})();

// Auto-start on page load
document.addEventListener('DOMContentLoaded', function () {
    DemoRunner.init();
});

// Keyboard shortcuts
document.addEventListener('keydown', function (e) {
    if (e.code === 'Space') { e.preventDefault(); DemoRunner.pause(); }
    if (e.code === 'ArrowRight') { e.preventDefault(); DemoRunner.skip(); }
    if (e.code === 'Escape') { window.location.href = '/advisory/'; }
});
