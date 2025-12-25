// ========== 全局变量 ==========
const API_BASE = '';
let activeTasks = {};
let transcribeTaskId = null;
let currentTranscriptData = null;
let selectedBvid = null;

// ========== 页面初始化 ==========
document.addEventListener('DOMContentLoaded', function() {
    // 加载已下载列表
    loadDownloads();

    // 绑定事件监听器
    bindEventListeners();
});

function bindEventListeners() {
    // 刷新按钮
    document.getElementById('refreshBtn').addEventListener('click', loadDownloads);

    // 下载按钮
    document.getElementById('startDownloadBtn').addEventListener('click', startDownload);
    document.getElementById('clearTasksBtn').addEventListener('click', clearTasks);

    // 转写和总结按钮
    document.getElementById('transcribeBtn').addEventListener('click', startTranscribe);
    document.getElementById('summarizeBtn').addEventListener('click', summarizeText);

    // 复制按钮
    document.getElementById('copyBtn').addEventListener('click', copyTranscript);

    // 格式切换标签
    document.getElementById('formatTabs').addEventListener('click', function(e) {
        if (e.target.classList.contains('format-tab')) {
            const format = e.target.dataset.format;
            switchFormat(format);
        }
    });
}

// ========== 下载列表功能 ==========
async function loadDownloads() {
    const refreshBtn = document.getElementById('refreshBtn');
    refreshBtn.classList.add('loading');
    refreshBtn.textContent = '⏳ 加载中...';

    try {
        const response = await fetch(`${API_BASE}/api/downloads`);
        const data = await response.json();
        renderDownloadList(data.downloads);
    } catch (error) {
        console.error('加载下载列表失败:', error);
        document.getElementById('downloadList').innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">❌</div>
                <p>加载失败: ${error.message}</p>
            </div>
        `;
    } finally {
        refreshBtn.classList.remove('loading');
        refreshBtn.textContent = '🔄 刷新';
    }
}

function renderDownloadList(downloads) {
    const container = document.getElementById('downloadList');

    if (!downloads || downloads.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">📭</div>
                <p>暂无已下载内容</p>
                <p style="font-size: 12px; margin-top: 8px;">在上方输入BV号开始下载</p>
            </div>
        `;
        return;
    }

    container.innerHTML = downloads.map(item => {
        const sizeStr = formatFileSize(item.total_size);

        const tags = [];
        if (item.has_audio) tags.push('<span class="download-tag tag-audio">🎵 音频</span>');
        if (item.has_video) tags.push('<span class="download-tag tag-video">🎥 视频</span>');
        if (item.has_transcript) tags.push('<span class="download-tag tag-transcript">📝 转写</span>');

        return `
            <div class="download-item ${selectedBvid === item.bvid ? 'selected' : ''}"
                 data-bvid="${item.bvid}">
                <div class="download-item-header">
                    <span class="download-item-title">${escapeHtml(item.title)}</span>
                    <span class="download-item-bvid">${item.bvid}</span>
                </div>
                <div class="download-item-meta">
                    <span>📁 ${item.file_count} 个文件</span>
                    <span>💾 ${sizeStr}</span>
                </div>
                <div class="download-item-tags">
                    ${tags.join('')}
                </div>
                <div class="download-item-actions">
                    <button class="btn btn-secondary btn-small" data-action="files" data-bvid="${item.bvid}">📂 查看文件</button>
                    <button class="btn btn-primary btn-small" data-action="select" data-bvid="${item.bvid}">✅ 选择处理</button>
                    <button class="btn btn-danger btn-small" data-action="delete" data-bvid="${item.bvid}">🗑️ 删除</button>
                </div>
            </div>
        `;
    }).join('');

    // 绑定下载列表事件（使用事件委托）
    container.addEventListener('click', handleDownloadListClick);
}

function handleDownloadListClick(e) {
    const target = e.target;
    const bvid = target.dataset.bvid;
    const action = target.dataset.action;

    if (action === 'files') {
        e.stopPropagation();
        showFiles(bvid);
    } else if (action === 'select') {
        e.stopPropagation();
        selectForProcess(bvid);
    } else if (action === 'delete') {
        e.stopPropagation();
        deleteDownload(bvid);
    } else {
        // 点击整个卡片选中
        const downloadItem = target.closest('.download-item');
        if (downloadItem) {
            const itemBvid = downloadItem.dataset.bvid;
            selectDownloadItem(itemBvid);
        }
    }
}

function selectDownloadItem(bvid) {
    selectedBvid = bvid;
    document.querySelectorAll('.download-item').forEach(el => {
        el.classList.toggle('selected', el.dataset.bvid === bvid);
    });
    document.getElementById('processBvid').value = bvid;
}

async function showFiles(bvid) {
    try {
        const response = await fetch(`${API_BASE}/api/files/${bvid}`);
        const data = await response.json();

        let filesHtml = data.files.map(file => {
            const sizeStr = formatFileSize(file.size);
            return `${file.name} (${sizeStr})`;
        }).join('\n');

        alert(`📁 ${bvid} 的文件:\n\n${filesHtml}`);
    } catch (error) {
        alert('获取文件列表失败: ' + error.message);
    }
}

async function deleteDownload(bvid) {
    if (!confirm(`确定要删除 ${bvid} 及其所有文件吗？`)) {
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/api/delete/${bvid}`, {
            method: 'DELETE'
        });
        const data = await response.json();

        if (data.success) {
            loadDownloads();
            if (selectedBvid === bvid) {
                selectedBvid = null;
                document.getElementById('processBvid').value = '';
            }
        } else {
            alert('删除失败: ' + data.error);
        }
    } catch (error) {
        alert('删除失败: ' + error.message);
    }
}

// ========== 下载功能 ==========
function getSelectedTypes() {
    const types = [];
    if (document.getElementById('typeAudio').checked) types.push('audio');
    if (document.getElementById('typeVideoOnly').checked) types.push('video_only');
    if (document.getElementById('typeMerged').checked) types.push('merged');
    if (document.getElementById('typeDanmaku').checked) types.push('danmaku');
    return types;
}

function getSelectedFormats() {
    const formats = ['txt'];
    if (document.getElementById('formatTimestamped').checked) formats.push('timestamped');
    if (document.getElementById('formatSrt').checked) formats.push('srt');
    if (document.getElementById('formatVtt').checked) formats.push('vtt');
    if (document.getElementById('formatJson').checked) formats.push('json');
    return formats;
}

async function startDownload() {
    const bvidText = document.getElementById('bvidInput').value;
    const bvids = bvidText.split('\n').map(b => b.trim()).filter(b => b);
    const types = getSelectedTypes();

    if (bvids.length === 0) {
        alert('请输入至少一个BV号');
        return;
    }

    if (types.length === 0) {
        alert('请选择至少一种下载类型');
        return;
    }

    for (const type of types) {
        try {
            const response = await fetch(`${API_BASE}/api/download`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ bvids, type })
            });
            const data = await response.json();

            for (const taskId of data.task_ids) {
                activeTasks[taskId] = { bvid: taskId.split('_')[0], type };
                addTaskToList(taskId);
                pollTaskStatus(taskId);
            }
        } catch (error) {
            console.error('下载请求失败:', error);
            alert('下载请求失败: ' + error.message);
        }
    }
}

function addTaskToList(taskId) {
    const taskList = document.getElementById('taskList');
    const task = activeTasks[taskId];

    const taskHtml = `
        <div class="task-item" id="task-${taskId}">
            <div class="task-header">
                <span class="task-bvid">${task.bvid} (${getTypeLabel(task.type)})</span>
                <span class="task-status status-downloading" id="status-${taskId}">下载中</span>
            </div>
            <div class="progress-bar">
                <div class="progress-fill" id="progress-${taskId}" style="width: 0%"></div>
            </div>
            <div class="task-message" id="message-${taskId}">准备中...</div>
            <div class="task-actions" id="actions-${taskId}"></div>
        </div>
    `;
    taskList.insertAdjacentHTML('beforeend', taskHtml);
}

function getTypeLabel(type) {
    const labels = {
        'audio': '音频',
        'video_only': '纯视频',
        'merged': '合成视频',
        'danmaku': '弹幕'
    };
    return labels[type] || type;
}

async function pollTaskStatus(taskId) {
    const poll = async () => {
        try {
            const response = await fetch(`${API_BASE}/api/status/${taskId}`);
            const status = await response.json();

            const statusEl = document.getElementById(`status-${taskId}`);
            const progressEl = document.getElementById(`progress-${taskId}`);
            const messageEl = document.getElementById(`message-${taskId}`);
            const actionsEl = document.getElementById(`actions-${taskId}`);

            if (status.status === 'downloading') {
                statusEl.textContent = '下载中';
                statusEl.className = 'task-status status-downloading';
                progressEl.style.width = `${status.progress || 0}%`;
                messageEl.textContent = status.message || '下载中...';
                setTimeout(poll, 1000);
            } else if (status.status === 'completed') {
                statusEl.textContent = '完成';
                statusEl.className = 'task-status status-completed';
                progressEl.style.width = '100%';
                messageEl.textContent = status.message || '下载完成';

                const bvid = taskId.split('_')[0];
                actionsEl.innerHTML = `
                    <button class="btn btn-secondary btn-small" onclick="loadFilesForTask('${bvid}', '${taskId}')">📁 查看文件</button>
                    <button class="btn btn-primary btn-small" onclick="selectForProcess('${bvid}')">✅ 选择处理</button>
                `;

                loadDownloads();
            } else if (status.status === 'error') {
                statusEl.textContent = '失败';
                statusEl.className = 'task-status status-error';
                messageEl.textContent = status.message || '下载失败';
            }
        } catch (error) {
            console.error('获取状态失败:', error);
        }
    };
    poll();
}

function selectForProcess(bvid) {
    selectedBvid = bvid;
    document.getElementById('processBvid').value = bvid;
    document.getElementById('processBvid').scrollIntoView({ behavior: 'smooth' });

    document.querySelectorAll('.download-item').forEach(el => {
        el.classList.toggle('selected', el.dataset.bvid === bvid);
    });
}

async function loadFilesForTask(bvid, taskId) {
    try {
        const response = await fetch(`${API_BASE}/api/files/${bvid}`);
        const data = await response.json();

        const actionsEl = document.getElementById(`actions-${taskId}`);
        const oldFileList = actionsEl.querySelector('.file-list');
        if (oldFileList) oldFileList.remove();

        let filesHtml = '<div class="file-list">';
        for (const file of data.files) {
            const sizeStr = formatFileSize(file.size);
            filesHtml += `
                <div class="file-item">
                    <span class="file-name">${escapeHtml(file.name)}</span>
                    <span class="file-size">${sizeStr}</span>
                </div>
            `;
        }
        filesHtml += '</div>';

        actionsEl.insertAdjacentHTML('beforeend', filesHtml);
    } catch (error) {
        console.error('获取文件列表失败:', error);
    }
}

function clearTasks() {
    document.getElementById('taskList').innerHTML = '';
    activeTasks = {};
}

// ========== 转写功能 ==========
async function startTranscribe() {
    const bvid = document.getElementById('processBvid').value.trim();
    if (!bvid) {
        alert('请输入BV号');
        return;
    }

    const formats = getSelectedFormats();
    console.log('输出格式:', formats);

    const btn = document.getElementById('transcribeBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="loading"></span> 转写中...';

    const progressDiv = document.getElementById('transcribeProgress');
    const progressBar = document.getElementById('transcribeProgressBar');
    const messageEl = document.getElementById('transcribeMessage');

    progressDiv.classList.remove('hidden');
    progressBar.style.width = '0%';
    messageEl.textContent = '正在启动转写任务...';

    document.getElementById('transcriptResult').classList.add('hidden');

    try {
        const response = await fetch(`${API_BASE}/api/transcribe`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                bvid,
                formats: formats
            })
        });
        const data = await response.json();

        if (data.error) {
            alert('转录失败: ' + data.error);
            progressDiv.classList.add('hidden');
            btn.disabled = false;
            btn.innerHTML = '🎤 音频转文本 (Whisper)';
            return;
        }

        if (data.status === 'completed' && data.text) {
            displayTranscriptResult(data);
            progressDiv.classList.add('hidden');
            btn.disabled = false;
            btn.innerHTML = '🎤 音频转文本 (Whisper)';
            return;
        }

        transcribeTaskId = data.task_id;
        pollTranscribeStatus(transcribeTaskId);

    } catch (error) {
        alert('转录请求失败: ' + error.message);
        progressDiv.classList.add('hidden');
        btn.disabled = false;
        btn.innerHTML = '🎤 音频转文本 (Whisper)';
    }
}

async function pollTranscribeStatus(taskId) {
    const btn = document.getElementById('transcribeBtn');
    const progressDiv = document.getElementById('transcribeProgress');
    const progressBar = document.getElementById('transcribeProgressBar');
    const messageEl = document.getElementById('transcribeMessage');

    const poll = async () => {
        try {
            const response = await fetch(`${API_BASE}/api/transcribe/status/${taskId}`);
            const status = await response.json();

            progressBar.style.width = `${status.progress || 0}%`;
            messageEl.textContent = status.message || '处理中...';

            if (status.status === 'starting' || status.status === 'loading_model') {
                messageEl.textContent = '⏳ ' + (status.message || '正在加载 Whisper 模型...');
                setTimeout(poll, 1000);
            } else if (status.status === 'transcribing') {
                messageEl.textContent = '🎤 ' + (status.message || '正在转写...');
                setTimeout(poll, 2000);
            } else if (status.status === 'completed') {
                progressBar.style.width = '100%';
                messageEl.textContent = '✅ 转写完成！';

                displayTranscriptResult(status);
                loadDownloads();

                setTimeout(() => {
                    progressDiv.classList.add('hidden');
                }, 1500);

                btn.disabled = false;
                btn.innerHTML = '🎤 音频转文本 (Whisper)';
            } else if (status.status === 'error') {
                messageEl.textContent = '❌ ' + (status.message || '转写失败');
                btn.disabled = false;
                btn.innerHTML = '🎤 音频转文本 (Whisper)';
            } else {
                setTimeout(poll, 1000);
            }
        } catch (error) {
            console.error('获取转写状态失败:', error);
            setTimeout(poll, 2000);
        }
    };
    poll();
}

function displayTranscriptResult(data) {
    currentTranscriptData = data;

    const duration = data.duration || 0;
    const minutes = Math.floor(duration / 60);
    const seconds = Math.floor(duration % 60);
    document.getElementById('statDuration').textContent = `${minutes}分${seconds}秒`;
    document.getElementById('statSegments').textContent = (data.segments || []).length;
    document.getElementById('statChars').textContent = (data.text || '').length;

    document.getElementById('transcriptText').textContent = data.text || '';
    document.getElementById('transcriptTimestamped').textContent = data.timestamped_text || '';

    const segmentsHtml = (data.segments || []).map(seg => `
        <div class="segment-item">
            <div class="segment-time">${seg.start_formatted} → ${seg.end_formatted}</div>
            <div class="segment-text">${escapeHtml(seg.text)}</div>
        </div>
    `).join('');
    document.getElementById('transcriptSegments').innerHTML = segmentsHtml;

    const srtContent = generateSRT(data.segments || []);
    document.getElementById('transcriptSrt').textContent = srtContent;

    document.getElementById('transcriptResult').classList.remove('hidden');
    switchFormat('plain');
}

function generateSRT(segments) {
    return segments.map((seg, i) => {
        const startSrt = formatSrtTime(seg.start);
        const endSrt = formatSrtTime(seg.end);
        return `${i + 1}\n${startSrt} --> ${endSrt}\n${seg.text}\n`;
    }).join('\n');
}

function formatSrtTime(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    const ms = Math.floor((seconds % 1) * 1000);
    return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')},${ms.toString().padStart(3, '0')}`;
}

function switchFormat(format) {
    document.querySelectorAll('.format-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.format === format);
    });

    document.getElementById('viewPlain').classList.add('hidden');
    document.getElementById('viewTimestamped').classList.add('hidden');
    document.getElementById('viewSegments').classList.add('hidden');
    document.getElementById('viewSrt').classList.add('hidden');

    if (format === 'plain') {
        document.getElementById('viewPlain').classList.remove('hidden');
    } else if (format === 'timestamped') {
        document.getElementById('viewTimestamped').classList.remove('hidden');
    } else if (format === 'segments') {
        document.getElementById('viewSegments').classList.remove('hidden');
    } else if (format === 'srt') {
        document.getElementById('viewSrt').classList.remove('hidden');
    }
}

function copyTranscript() {
    const activeTab = document.querySelector('.format-tab.active');
    const format = activeTab ? activeTab.dataset.format : 'plain';

    let text = '';
    if (format === 'plain') {
        text = document.getElementById('transcriptText').textContent;
    } else if (format === 'timestamped') {
        text = document.getElementById('transcriptTimestamped').textContent;
    } else if (format === 'segments' && currentTranscriptData) {
        text = currentTranscriptData.segments.map(s =>
            `[${s.start_formatted}] ${s.text}`
        ).join('\n');
    } else if (format === 'srt') {
        text = document.getElementById('transcriptSrt').textContent;
    }

    navigator.clipboard.writeText(text).then(() => {
        const btn = document.getElementById('copyBtn');
        btn.textContent = '✅ 已复制';
        btn.classList.add('copied');
        setTimeout(() => {
            btn.textContent = '📋 复制';
            btn.classList.remove('copied');
        }, 2000);
    });
}

// ========== AI总结功能 ==========
async function summarizeText() {
    if (!currentTranscriptData) {
        alert('请先进行音频转文本');
        return;
    }

    const baseUrl = document.getElementById('baseUrl').value;
    const apiKey = document.getElementById('apiKey').value;
    const prompt = document.getElementById('summaryPrompt').value;
    const model = document.getElementById('modelName').value;
    const includeTimestamps = document.getElementById('includeTimestamps').checked;

    if (!apiKey) {
        alert('请输入API Key');
        return;
    }

    let textToSummarize;
    if (includeTimestamps) {
        textToSummarize = currentTranscriptData.timestamped_text ||
            currentTranscriptData.segments.map(s =>
                `[${s.start_formatted}] ${s.text}`
            ).join('\n');
    } else {
        textToSummarize = currentTranscriptData.text;
    }

    const btn = document.getElementById('summarizeBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="loading"></span> 总结中...';

    try {
        const response = await fetch(`${API_BASE}/api/summarize`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                text: textToSummarize,
                include_timestamps: includeTimestamps,
                base_url: baseUrl,
                api_key: apiKey,
                prompt,
                model
            })
        });
        const data = await response.json();

        if (data.error) {
            alert('总结失败: ' + data.error);
            return;
        }

        document.getElementById('summaryText').textContent = data.summary;
        document.getElementById('summaryResult').classList.remove('hidden');
    } catch (error) {
        alert('总结请求失败: ' + error.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '🤖 AI总结';
    }
}

// ========== 工具函数 ==========
function formatFileSize(bytes) {
    if (bytes > 1024 * 1024 * 1024) {
        return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
    } else if (bytes > 1024 * 1024) {
        return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
    } else if (bytes > 1024) {
        return `${(bytes / 1024).toFixed(2)} KB`;
    }
    return `${bytes} B`;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 暴露给 onclick 的全局函数（用于任务列表中的按钮）
window.loadFilesForTask = loadFilesForTask;
window.selectForProcess = selectForProcess;
