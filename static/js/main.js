/**
 * 昆仑哨兵·实验室多模态监控系统
 * 前端主JavaScript文件
 */

// 全局变量
let temperatureChart = null;
let updateInterval = null;
let lastAppliedTs = 0;
let temperatureSeries = [];
let temperatureAnomalies = [];
let latestPollTimer = null;

// 初始化函数
document.addEventListener('DOMContentLoaded', function() {
    console.log('昆仑哨兵系统初始化...');
    
    // 初始化图表
    initCharts();
    
    loadHistoryData();
    loadLatestData();
    
    
    
    // 更新时间显示
    updateDateTime();
    setInterval(updateDateTime, 1000);
    
    // 绑定事件
    bindEvents();
    initTags();
    initScripts();
    initModelOverview();
    initModelCards();
    connectEvents();
});

// 绑定事件
function bindEvents() {
    const captureBtn = document.getElementById('btn-capture');
    if (captureBtn) captureBtn.addEventListener('click', captureData);
    const refreshBtn = document.getElementById('btn-refresh');
    if (refreshBtn) refreshBtn.addEventListener('click', refreshData);
}

// 更新日期时间
function updateDateTime() {
    const now = new Date();
    const dateTimeString = now.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
    
    const datetimeElement = document.getElementById('datetime');
    if (datetimeElement) {
        datetimeElement.textContent = dateTimeString;
    }
}

// 初始化图表
function initCharts() {
    const el = document.getElementById('temperature-chart');
    if (!el) return;
    temperatureChart = echarts.init(el);
    const option = {
        backgroundColor: '#FAFAFA',
        tooltip: { trigger: 'axis' },
        grid: { left: 48, right: 24, top: 24, bottom: 32 },
        xAxis: {
            type: 'time',
            interval: 2 * 3600 * 1000,
            axisLine: { lineStyle: { color: '#E2E8F0' } },
            axisTick: { show: true },
            splitLine: { show: true, lineStyle: { color: '#E2E8F0', type: 'dashed' } },
            axisLabel: {
                color: '#4A5568',
                formatter: function (value) {
                    const d = new Date(value);
                    const h = String(d.getHours()).padStart(2, '0');
                    return h + ':00';
                }
            }
        },
        yAxis: {
            type: 'value', min: 0, max: 50, interval: 10,
            axisLine: { lineStyle: { color: '#E2E8F0' } },
            splitLine: { show: true, lineStyle: { color: '#E2E8F0', type: 'dashed' } },
            axisLabel: { color: '#4A5568' }
        },
        series: [{
            name: '温度', type: 'line', smooth: true, showSymbol: false,
            lineStyle: { color: '#0D9488', width: 2 },
            itemStyle: { color: '#0D9488' },
            areaStyle: {
                color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                    { offset: 0, color: 'rgba(13, 148, 136, 0.10)' },
                    { offset: 1, color: 'rgba(13, 148, 136, 0.02)' }
                ])
            },
            data: []
        },{
            name: '异常点', type: 'scatter', symbolSize: 6,
            itemStyle: { color: '#DD6B20' },
            tooltip: {
                trigger: 'item',
                formatter: function (p) {
                    const delta = p.data && p.data.delta ? p.data.delta : 0;
                    const sign = delta > 0 ? '+' : '';
                    return 'Δ ' + sign + delta.toFixed(1) + '°C';
                }
            },
            data: []
        }]
    };
    temperatureChart.setOption(option);
}

// 加载最新数据
function loadLatestData() {
    fetch('/api/latest')
        .then(response => response.json())
        .then(data => {
            updateLatestDisplay(data);
        })
        .catch(error => {
            console.error('加载最新数据失败:', error);
            showError('加载最新数据失败');
        });
}

// 加载历史数据
function loadHistoryData() {
    fetch('/api/history?hours=24')
        .then(response => response.json())
        .then(data => {
            updateCharts(data);
        })
        .catch(error => {
            console.error('加载历史数据失败:', error);
        });
}

// 更新最新数据显示
function updateLatestDisplay(data) {
    try {
        const tsRaw = data && data.timestamp ? String(data.timestamp) : '';
        const ts = tsRaw ? Date.parse(tsRaw.replace(/-/g,'/')) : 0;
        if (ts && lastAppliedTs && ts < lastAppliedTs) return;
        if (ts) lastAppliedTs = ts;
    } catch (_) {}
    // 更新温度
    const tempElement = document.getElementById('temperature');
    if (tempElement && Object.prototype.hasOwnProperty.call(data, 'temperature')) {
        const t = data.temperature;
        if (typeof t === 'number' && t !== 0 && t > -40 && t < 125) {
            tempElement.textContent = t.toFixed(1);
            tempElement.classList.remove('temp-high', 'temp-normal');
            if (t > 35) {
                tempElement.classList.add('temp-high');
            } else {
                tempElement.classList.add('temp-normal');
            }
        }
    }
    
    

    // 更新光敏值
    const lightElement = document.getElementById('light-value');
    if (lightElement && Object.prototype.hasOwnProperty.call(data, 'light')) {
        if (data.light !== null) {
            lightElement.textContent = data.light;
        }
    }
    
    // 更新图像
    const imageElement = document.getElementById('latest-image');
    const noImageElement = document.getElementById('no-image');
    if (imageElement && noImageElement && Object.prototype.hasOwnProperty.call(data, 'image_path')) {
        if (data.image_path) {
            imageElement.src = data.image_path;
            imageElement.style.display = 'block';
            noImageElement.style.display = 'none';
            imageElement.onerror = function(){ imageElement.style.display='none'; noImageElement.style.display='block'; };
        } else {
            imageElement.style.display = 'none';
            noImageElement.style.display = 'block';
        }
    }
    
    // 更新时间文本
    const tsText = data.timestamp || null;
    if (tsText) {
        const tempUpdated = document.getElementById('temp-updated');
        const lightUpdated = document.getElementById('light-updated');
        const imageUpdated = document.getElementById('image-updated');
        if (tempUpdated && Object.prototype.hasOwnProperty.call(data, 'temperature')) tempUpdated.textContent = `更新时间: ${tsText}`;
        if (lightUpdated && Object.prototype.hasOwnProperty.call(data, 'light')) lightUpdated.textContent = `更新时间: ${tsText}`;
        if (imageUpdated && Object.prototype.hasOwnProperty.call(data, 'image_path')) imageUpdated.textContent = `更新时间: ${tsText}`;
    }
    
    // 更新传感器状态
    if (data.sensor_status) {
        updateSensorStatus(data.sensor_status);
    }
}

// 更新传感器状态显示
function updateSensorStatus(status) {
    const statusMap = {
        'ds18b20': 'DS18B20传感器',
        'light': '光敏电阻传感器',
        'camera': 'UVC摄像头',
        'db': '数据库连接'
    };
    
    Object.keys(statusMap).forEach(key => {
        const element = document.getElementById(`status-${key}`);
        if (element && status[key]) {
            const isOnline = status[key] === 'online';
            element.textContent = isOnline ? '在线' : '离线';
            element.className = isOnline ? 'status-value online' : 'status-value offline';
        }
    });
    
    // 更新卡片状态
    const tempStatus = document.getElementById('temp-status');
    const lightStatus = document.getElementById('light-status');
    const cameraStatus = document.getElementById('camera-status');
    const dbValue = document.getElementById('status-db');
    
    if (tempStatus) {
        const online = status.ds18b20 === 'online';
        tempStatus.style.display = online ? '' : 'none';
        if (online) tempStatus.textContent = '传感器正常';
    }

    if (lightStatus) {
        const online = status.light === 'online';
        lightStatus.style.display = online ? '' : 'none';
        if (online) lightStatus.textContent = '传感器正常';
    }

    if (cameraStatus) {
        const online = status.camera === 'online';
        cameraStatus.style.display = online ? '' : 'none';
        if (online) cameraStatus.textContent = '摄像头正常';
    }
    // 数据库连接值显示（页眉状态栏）
    if (dbValue && status.db) {
        const isOnline = status.db === 'online';
        dbValue.textContent = isOnline ? '在线' : '离线';
        dbValue.className = isOnline ? 'status-value online' : 'status-value offline';
    }
}

// 更新图表
function updateCharts(data) {
    if (!temperatureChart) return;
    const raw = (data && data.temperature_data) ? data.temperature_data : [];
    temperatureSeries = raw
        .filter(it => typeof it.value === 'number' && it.value !== 0 && it.value > -40 && it.value < 125)
        .map(it => [new Date(it.timestamp), it.value]);
    temperatureAnomalies = [];
    for (let i = 1; i < raw.length; i++) {
        const prev = raw[i - 1];
        const cur = raw[i];
        const dt = new Date(cur.timestamp) - new Date(prev.timestamp);
        const dv = cur.value - prev.value;
        if (Math.abs(dv) >= 5 && dt <= 10 * 60 * 1000) {
            temperatureAnomalies.push({ value: [new Date(cur.timestamp), cur.value], delta: dv });
        }
    }
    const now = Date.now();
    temperatureChart.setOption({
        xAxis: { type: 'time', min: now - 24 * 3600 * 1000, max: now, interval: 2 * 3600 * 1000 },
        series: [ { data: temperatureSeries }, { data: temperatureAnomalies } ]
    });
}

function initTags() {
    const sel = document.getElementById('tag-select');
    const cur = document.getElementById('current-tag');
    const btnSet = document.getElementById('btn-set-tag');
    const btnAdd = document.getElementById('btn-add-tag');
    const newInput = document.getElementById('new-tag-input');
    if (!sel || !cur) return;
    fetch('/api/tags/current').then(r=>r.json()).then(j=>{ cur.textContent = j.name || '未设置'; });
    fetch('/api/tags').then(r=>r.json()).then(list=>{ sel.innerHTML = list.map(n=>`<option value="${n}">${n}</option>`).join(''); });
    if (btnSet) btnSet.onclick = ()=>{
        const name = sel.value;
        fetch('/api/tags/current', { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name}) })
            .then(r=>r.json()).then(_=>{ cur.textContent = name; showSuccess('标签已更新'); });
    };
    if (btnAdd) btnAdd.onclick = ()=>{
        const name = (newInput.value||'').trim();
        if (!name) return;
        fetch('/api/tags', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name}) })
            .then(_=>fetch('/api/tags')).then(r=>r.json()).then(list=>{ sel.innerHTML = list.map(n=>`<option value="${n}">${n}</option>`).join(''); newInput.value=''; showSuccess('标签已新增'); });
    };
}

function initScripts() {
    const tbody = document.getElementById('script-table-body');
    const logs = document.getElementById('script-logs');
    const btnCreate = document.getElementById('btn-create-script');
    const btnRefresh = document.getElementById('btn-refresh-scripts');
    const nameEl = document.getElementById('sc-name');
    const langEl = document.getElementById('sc-lang');
    const authorEl = document.getElementById('sc-author');
    const orgEl = document.getElementById('sc-org');
    const licEl = document.getElementById('sc-license');
    const contentEl = document.getElementById('sc-content');
    if (!tbody) return;
    const loadList = ()=>{
        fetch('/api/scripts').then(r=>r.json()).then(list=>{
            tbody.innerHTML = list.map(it=>{
                return `<tr><td>${it.id}</td><td>${it.name}</td><td>${it.lang}</td><td>${it.author||''}</td><td>${it.org||''}</td><td>${it.license||''}</td><td>${it.created_at}</td><td><button class='btn btn-secondary' data-id='${it.id}'>执行</button></td></tr>`;
            }).join('');
            Array.from(tbody.querySelectorAll('button[data-id]')).forEach(btn=>{
                btn.onclick = ()=>{
                    const id = btn.getAttribute('data-id');
                    fetch(`/api/scripts/run/${id}`, { method:'POST' }).then(r=>r.json()).then(res=>{
                        logs.textContent = res.output || JSON.stringify(res);
                        showSuccess('脚本执行完成');
                        fetch(`/api/scripts/logs?script_id=${id}`).then(r=>r.json()).then(ls=>{
                            logs.textContent = (ls[0] && ls[0].output) || logs.textContent;
                        });
                    });
                };
            });
        });
    };
    loadList();
    if (btnRefresh) btnRefresh.onclick = loadList;
    if (btnCreate) btnCreate.onclick = ()=>{
        const payload = { name: nameEl.value, lang: langEl.value, author: authorEl.value, org: orgEl.value, license: licEl.value, content: contentEl.value };
        fetch('/api/scripts', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) }).then(r=>r.json()).then(_=>{ showSuccess('脚本已提交'); loadList(); });
    };
}

function initModelOverview() {
    const overview = document.getElementById('model-overview-list');
    if (!overview) return;
    const renderList = (list) => {
        overview.innerHTML = '';
        list.forEach(m=>{
            const row = document.createElement('div');
            row.className = 'script-item';
            const left = document.createElement('div');
            const nameEl = document.createElement('span');
            nameEl.textContent = `${m.title || m.name}`;
            const descEl = document.createElement('span');
            descEl.style.marginLeft = '0.5rem';
            descEl.textContent = `${m.description||''}`;
            const statusEl = document.createElement('span');
            const st = String(m.status||'').toLowerCase();
            let pillClass = 'status-value';
            let pillText = '未启动';
            if (st === 'running') { pillClass = 'status-value online'; pillText = '运行中'; }
            else if (st === 'finished') { pillClass = 'status-value online'; pillText = '已完成'; }
            else if (st === 'stopped') { pillClass = 'status-value idle'; pillText = '未启动'; }
            statusEl.className = pillClass;
            statusEl.style.marginLeft = '0.75rem';
            statusEl.textContent = pillText;
            left.appendChild(nameEl);
            left.appendChild(descEl);
            left.appendChild(statusEl);
            const right = document.createElement('div');
            right.className = 'script-actions';
            const btnDownload = document.createElement('button');
            btnDownload.className = 'btn btn-secondary';
            btnDownload.textContent = '下载';
            btnDownload.onclick = ()=>{
                fetch(`/api/models/download/${encodeURIComponent(m.name)}`).then(r=>r.blob()).then(blob=>{
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url; a.download = `${m.name}`;
                    document.body.appendChild(a); a.click();
                    setTimeout(()=>{ URL.revokeObjectURL(url); a.remove(); }, 0);
                });
            };
            const running = String(m.status||'').toLowerCase() === 'running';
            const btnToggle = document.createElement('button');
            btnToggle.className = running ? 'btn btn-secondary' : 'btn btn-primary';
            btnToggle.textContent = running ? '停止' : '启动';
            btnToggle.onclick = ()=>{
                const act = running ? 'stop' : 'start';
                btnToggle.disabled = true;
                btnToggle.textContent = running ? '停止中...' : '启动中...';
                fetch('/api/models/command', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ action:act, name:m.name }) })
                    .then(r=>r.json()).then(_=>{ 
                        showSuccess(running ? '已停止' : '已启动');
                    })
                    .catch(_=>{ showError('操作失败'); })
                    .finally(()=>{ btnToggle.disabled = false; });
            };
            const btnDelete = document.createElement('button');
            btnDelete.className = 'btn btn-danger';
            btnDelete.textContent = '删除';
            btnDelete.onclick = ()=>{
                fetch('/api/models/command', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ action:'delete', name:m.name }) }).then(r=>r.json()).then(_=>{ showSuccess('已删除'); });
            };
            const btnAuto = document.createElement('button');
            btnAuto.className = 'btn btn-secondary';
            btnAuto.textContent = m.autostart ? '移除开机' : '加入开机';
            btnAuto.onclick = ()=>{
                const act = m.autostart ? 'remove_autostart' : 'add_autostart';
                btnAuto.disabled = true;
                btnAuto.textContent = m.autostart ? '移除中...' : '加入中...';
                fetch('/api/models/command', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ action:act, name:m.name }) })
                  .then(r=>r.json()).then(_=>{ 
                    showSuccess('已更新开机项');
                  })
                  .catch(_=>{ showError('操作失败'); })
                  .finally(()=>{ btnAuto.disabled = false; });
            };
            right.appendChild(btnDownload);
            right.appendChild(btnToggle);
            right.appendChild(btnAuto);
            right.appendChild(btnDelete);
            row.appendChild(left);
            row.appendChild(right);
            overview.appendChild(row);
        });
    };
    window.renderModels = renderList;
    fetch('/api/models').then(r=>r.json()).then(list=>{ renderList(list); }).catch(()=>{});
}

// 采集数据
function captureData() {
    showLoading(true);
    
    fetch('/api/capture', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
    .then(response => response.json())
    .then(data => {
        showLoading(false);
        
        if (data.error) {
            showError(data.error);
        } else {
            showSuccess('数据采集成功！');
            // 更新显示
            updateLatestDisplay(data);
            // 刷新历史数据
            loadHistoryData();
        }
    })
    .catch(error => {
        showLoading(false);
        console.error('数据采集失败:', error);
        showError('数据采集失败，请检查硬件连接');
    });
}

// 刷新数据
function refreshData() {
    showSuccess('数据已刷新');
}

// 显示加载状态
function showLoading(show) {
    const loading = document.getElementById('loading');
    if (loading) {
        loading.style.display = show ? 'flex' : 'none';
    }
}

// 显示成功消息
function showSuccess(message) {
    showNotification(message, 'success');
}

// 显示错误消息
function showError(message) {
    showNotification(message, 'error');
}

// 显示通知
function showNotification(message, type) {
    // 创建通知元素
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.textContent = message;
    
    // 添加样式
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 15px 20px;
        border-radius: 5px;
        color: white;
        font-weight: bold;
        z-index: 1000;
        max-width: 300px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        transition: all 0.3s ease;
    `;
    
    if (type === 'success') {
        notification.style.backgroundColor = '#059669';
    } else {
        notification.style.backgroundColor = '#DC2626';
    }
    
    // 添加到页面
    document.body.appendChild(notification);
    
    // 3秒后移除
    setTimeout(() => {
        notification.style.opacity = '0';
        setTimeout(() => {
            if (notification.parentNode) {
                notification.parentNode.removeChild(notification);
            }
        }, 300);
    }, 3000);
}

// 页面卸载时清理
window.addEventListener('beforeunload', function() {
    if (updateInterval) {
        clearInterval(updateInterval);
    }
});

function initModelCards() {
    const grid = document.getElementById('models-grid');
    if (!grid) return;
    let models = generateFakeModels(20);
    const render = () => {
        grid.innerHTML = '';
        models.forEach((m, idx) => {
            const card = document.createElement('article');
            card.className = 'model-card';
            card.setAttribute('role', 'listitem');
            card.dataset.idx = String(idx);
            card.dataset.name = m.name || '';
            const thumb = document.createElement('div');
            thumb.className = 'model-thumbnail';
            const ph = document.createElement('div');
            ph.className = `thumbnail-placeholder ${m.thumbnail || 'color-analysis'}`;
            const icon = document.createElement('div');
            icon.className = 'thumbnail-icon';
            icon.textContent = '🔬';
            const txt = document.createElement('div');
            txt.className = 'thumbnail-text';
            txt.textContent = m.name || '';
            ph.appendChild(icon);
            ph.appendChild(txt);
            thumb.appendChild(ph);
            const info = document.createElement('div');
            info.className = 'model-info';
            const title = document.createElement('h3');
            title.className = 'model-name';
            title.textContent = m.name || '';
            const desc = document.createElement('p');
            desc.className = 'model-description';
            desc.textContent = m.description || '';
            const meta = document.createElement('div');
            meta.className = 'model-meta';
            const author = document.createElement('span');
            author.className = 'model-author';
            author.textContent = ((m.org && (m.org.university || '')) + (m.org && m.org.lab ? ' · ' + m.org.lab : '')).trim();
            const lic = document.createElement('span');
            lic.className = 'model-license';
            lic.textContent = '开源分享';
            const tags = document.createElement('div');
            tags.className = 'tag-list';
            (m.tags || []).forEach(t => {
                const s = document.createElement('span');
                s.className = 'tag tag-topic';
                s.textContent = t;
                tags.appendChild(s);
            });
            meta.appendChild(author);
            meta.appendChild(lic);
            info.appendChild(title);
            info.appendChild(desc);
            info.appendChild(meta);
            info.appendChild(tags);
            const actions = document.createElement('div');
            actions.className = 'model-actions';
            const btnLike = document.createElement('button');
            btnLike.className = 'btn btn-secondary action-like';
            btnLike.textContent = `👍 赞 ${(m.likes || 0)}`;
            const btnShare = document.createElement('button');
            btnShare.className = 'btn btn-secondary action-share';
            btnShare.textContent = '分享';
            const btnDown = document.createElement('button');
            btnDown.className = 'btn btn-primary action-download';
            btnDown.textContent = '下载';
            actions.appendChild(btnLike);
            actions.appendChild(btnShare);
            actions.appendChild(btnDown);
            const details = document.createElement('div');
            details.className = 'model-details';
            card.appendChild(thumb);
            card.appendChild(info);
            card.appendChild(actions);
            grid.appendChild(card);
        });
    };
    render();
    grid.addEventListener('click', e => {
        const btn = e.target;
        const card = btn.closest('.model-card');
        if (!card) return;
        if (btn.classList.contains('action-share')) {
            const title = card.dataset.name || document.title;
            const text = getModelText(card);
            const url = location.href;
            if (navigator.share) {
                navigator.share({ title, text, url }).catch(() => {});
            } else {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(url).then(() => showSuccess('已复制链接'));
                } else {
                    showSuccess('请手动分享');
                }
            }
        } else if (btn.classList.contains('action-download')) {
            const idx = Number(card.dataset.idx || -1);
            if (idx >= 0) downloadModelJSON(models[idx]);
        } else if (btn.classList.contains('action-like')) {
            const idx = Number(card.dataset.idx || -1);
            if (idx >= 0) {
                models[idx].likes = (models[idx].likes || 0) + 1;
                btn.textContent = `👍 赞 ${models[idx].likes}`;
            }
        }
    });
}

function getModelText(card) {
    const name = card.dataset.name || '';
    const desc = (card.querySelector('.model-description') && card.querySelector('.model-description').textContent) || '';
    const tags = Array.from(card.querySelectorAll('.tag-list .tag')).map(el => el.textContent).join(',');
    return `${name}\n${desc}\n标签: ${tags}`;
}

function downloadModelJSON(model) {
    const data = JSON.stringify(model, null, 2);
    const blob = new Blob([data], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${(model.name || 'model').replace(/\s+/g,'_')}.json`;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 0);
}

// 已移除悬浮窗口相关逻辑

function generateFakeModels(n) {
    const names = ['液位估计模型','气泡检测模型','晶体识别模型','颜色分析模型','沉淀识别模型','光敏评估模型','温度异常模型','蓝牙定位模型','WiFi指纹模型','红外测温模型','声学检测模型','加速度识别模型','气压趋势模型','湿度监测模型','陀螺姿态模型'];
    const thumbs = ['bubble-detection','level-detection','crystal-detection','color-analysis','sediment-detection'];
    const sensors = ['摄像','温度','光敏','蓝牙','WiFi','NFC','红外','声学','加速度','气压','湿度','陀螺仪'];
    const techFront = ['VanillaJS@1','Vue@3'];
    const techBack = ['Flask@2'];
    const techAlgo = ['OpenCV@4','NumPy@1','PyTorch@2'];
    const dataTypes = ['图像','光敏','蓝牙','温度'];
    const arr = [];
    for (let i = 0; i < n; i++) {
        const fn = [ '功能A', '功能B', '功能C', '功能D', '功能E' ];
        const pick = (list, count) => {
            const s = new Set();
            while (s.size < count) s.add(list[Math.floor(Math.random()*list.length)]);
            return Array.from(s);
        };
        const tagCount = 1 + Math.floor(Math.random() * dataTypes.length);
        arr.push({
            name: names[Math.floor(Math.random()*names.length)] + ` #${i+1}`,
            description: '用于实验场景的数据处理与识别。',
            functions: pick(fn, 3),
            tags: pick(dataTypes, tagCount),
            tech: { frontend: pick(techFront, 1), backend: pick(techBack, 1), algo: pick(techAlgo, 1) },
            org: { university: '北京大学', lab: '昆仑哨兵实验室', site: 'https://www.pku.edu.cn' },
            thumbnail: thumbs[Math.floor(Math.random()*thumbs.length)],
            sensors: pick(sensors, 3),
            likes: Math.floor(Math.random()*500)+10
        });
    }
    return arr;
}
function connectEvents() {
    try {
        const es = new EventSource('/api/events');
        es.onmessage = function(e) {
            const data = JSON.parse(e.data || '{}');
            if (data && Object.keys(data).length) {
                updateLatestDisplay(data);
                if (Object.prototype.hasOwnProperty.call(data, 'temperature') && data.timestamp) {
                    appendTemperaturePoint(data.timestamp, data.temperature);
                }
                if (Object.prototype.hasOwnProperty.call(data, 'models') && Array.isArray(data.models)) {
                    if (typeof window.renderModels === 'function') {
                        window.renderModels(data.models);
                    }
                }
            }
        };
        es.onopen = function() { if (latestPollTimer) { clearInterval(latestPollTimer); latestPollTimer = null; } };
        es.onerror = function() { if (!latestPollTimer) { latestPollTimer = setInterval(loadLatestData, 5000); } };
    } catch (err) {}
}

function appendTemperaturePoint(tsText, t) {
    try {
        if (typeof t !== 'number' || t === 0 || t <= -40 || t >= 125) return;
        const ts = new Date(tsText);
        temperatureSeries.push([ts, t]);
        const cutoff = Date.now() - 24 * 3600 * 1000;
        temperatureSeries = temperatureSeries.filter(p => (new Date(p[0]).getTime()) >= cutoff);
        const n = temperatureSeries.length;
        if (n >= 2) {
            const prev = temperatureSeries[n - 2];
            const cur = temperatureSeries[n - 1];
            const dt = new Date(cur[0]).getTime() - new Date(prev[0]).getTime();
            const dv = cur[1] - prev[1];
            if (Math.abs(dv) >= 5 && dt <= 10 * 60 * 1000) {
                temperatureAnomalies.push({ value: [cur[0], cur[1]], delta: dv });
            }
        }
        if (temperatureChart) {
            const now = Date.now();
            temperatureChart.setOption({
                xAxis: { type: 'time', min: now - 24 * 3600 * 1000, max: now, interval: 2 * 3600 * 1000 },
                series: [ { data: temperatureSeries }, { data: temperatureAnomalies } ]
            });
        }
    } catch (_) {}
}
