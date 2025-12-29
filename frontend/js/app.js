// frontend/js/app.js
/**
 * B站视频工具箱 - 前端交互逻辑
 */

// ========== 全局变量 ==========
const API_BASE = '';
let manualKeywords = [];
let crawlerPollingInterval = null;
let downloadTasks = {};
let transcribeTaskId = null;
let currentTranscriptData = null;
let allCrawledVideos = [];
let currentPage = 1;
let videosPerPage = 20;

// ========== 初始化 ==========
document.addEventListener('DOMContentLoaded', function() {
    initNavigation();
    initCrawler();
    initDownloader();
    initTranscriber();
    initSummarizer();
    loadDownloads();
});

// ========== 通用工具函数 ==========
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function truncate(str, maxLength) {
    if (!str) return '';
    return str.length > maxLength ? str.substring(0, maxLength) + '...' : str;
}

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

function showNotification(message, type = 'info') {
    const notification = document.getElementById('notification');
    const icon = document.getElementById('notification-icon');
    const msg = document.getElementById('notification-message');

    // 设置图标
    const icons = {
        success: '✅',
        error: '❌',
        warning: '⚠️',
        info: 'ℹ️'
    };
    icon.textContent = icons[type] || icons.info;
    msg.textContent = message;

    // 设置样式
    notification.className = `notification ${type} show`;

    // 3秒后隐藏
    setTimeout(() => {
        notification.classList.remove('show');
    }, 3000);
}

// ========== 导航切换 ==========
function initNavigation() {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', function() {
            const tabId = this.dataset.tab;

            // 更新导航状态
            document.querySelectorAll('.nav-item').forEach(nav => nav.classList.remove('active'));
            this.classList.add('active');

            // 切换内容
            document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
            document.getElementById(`tab-${tabId}`).classList.add('active');
        });
    });
}

// ========== 爬虫模块 ==========
function initCrawler() {
    // 文件上传
    const fileUpload = document.getElementById('keyword-upload');
    const fileInput = document.getElementById('keyword-file');

    fileUpload.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', handleFileSelect);

    // 拖拽上传
    fileUpload.addEventListener('dragover', (e) => {
        e.preventDefault();
        fileUpload.style.borderColor = 'var(--primary)';
        fileUpload.style.background = 'rgba(251, 114, 153, 0.05)';
    });

    fileUpload.addEventListener('dragleave', (e) => {
        e.preventDefault();
        fileUpload.style.borderColor = '';
        fileUpload.style.background = '';
    });

    fileUpload.addEventListener('drop', (e) => {
        e.preventDefault();
        fileUpload.style.borderColor = '';
        fileUpload.style.background = '';
        if (e.dataTransfer.files.length) {
            fileInput.files = e.dataTransfer.files;
            handleFileSelect();
        }
    });

    // 手动添加关键词
    document.getElementById('add-keyword-btn').addEventListener('click', addKeyword);
    document.getElementById('new-keyword').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            addKeyword();
        }
    });

    // 控制按钮
    document.getElementById('start-crawl').addEventListener('click', startCrawl);
    document.getElementById('pause-crawl').addEventListener('click', pauseCrawl);
    document.getElementById('resume-crawl').addEventListener('click', resumeCrawl);
    document.getElementById('stop-crawl').addEventListener('click', stopCrawl);
    document.getElementById('download-results').addEventListener('click', downloadResults);
}

function handleFileSelect() {
    const fileInput = document.getElementById('keyword-file');
    const fileInfo = document.getElementById('file-name');

    if (fileInput.files.length > 0) {
        fileInfo.textContent = `已选择: ${fileInput.files[0].name}`;
        fileInfo.classList.remove('hidden');
        showNotification(`文件 ${fileInput.files[0].name} 已选择`, 'success');
    }
}

function addKeyword() {
    const input = document.getElementById('new-keyword');
    const keyword = input.value.trim();

    if (!keyword) {
        showNotification('请输入关键词', 'warning');
        return;
    }

    if (manualKeywords.includes(keyword)) {
        showNotification('关键词已存在', 'warning');
        return;
    }

    manualKeywords.push(keyword);
    renderKeywords();
    input.value = '';
    showNotification(`已添加关键词: ${keyword}`, 'success');
}

function removeKeyword(keyword) {
    manualKeywords = manualKeywords.filter(k => k !== keyword);
    renderKeywords();
    showNotification(`已删除关键词: ${keyword}`, 'info');
}

function renderKeywords() {
    const container = document.getElementById('keywords-container');

    if (manualKeywords.length === 0) {
        container.innerHTML = '<span class="empty-hint">暂无关键词</span>';
        return;
    }

    container.innerHTML = manualKeywords.map(keyword => `
        <span class="keyword-tag">
            ${escapeHtml(keyword)}
            <span class="remove" onclick="removeKeyword('${escapeHtml(keyword).replace(/'/g, "\\'")}')">&times;</span>
        </span>
    `).join('');
}

async function startCrawl() {
    const fileInput = document.getElementById('keyword-file');
    const hasFile = fileInput.files.length > 0;
    const hasKeywords = manualKeywords.length > 0;

    if (!hasFile && !hasKeywords) {
        showNotification('请上传关键词文件或添加关键词', 'error');
        return;
    }

    // 显示进度区域，隐藏结果
    document.getElementById('crawl-progress-card').classList.remove('hidden');
    document.getElementById('crawl-results-card').classList.add('hidden');

    // 清空日志
    document.getElementById('crawl-log').innerHTML = '';
    document.getElementById('crawl-progress-bar').style.width = '0%';
    document.getElementById('crawl-progress-text').textContent = '0%';
    document.getElementById('crawl-status').textContent = '准备开始...';

    // 禁用开始按钮
    document.getElementById('start-crawl').disabled = true;

    try {
        let formData = new FormData();
        let endpoint;

        if (hasFile) {
            formData.append('file', fileInput.files[0]);
            endpoint = '/api/crawler/upload';
        } else {
            formData.append('keywords', JSON.stringify(manualKeywords));
            endpoint = '/api/crawler/start-with-keywords';
        }

        formData.append('pages', document.getElementById('pages-to-crawl').value);
        formData.append('enable_detailed_info', document.getElementById('enable-detailed-info').checked);
        formData.append('remove_duplicates', document.getElementById('remove-duplicates').checked);

        const response = await fetch(endpoint, {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        if (response.ok) {
            showNotification(`开始爬取，共 ${data.keywords_count} 个关键词`, 'success');
            startCrawlerPolling();
        } else {
            showNotification('错误: ' + data.error, 'error');
            document.getElementById('start-crawl').disabled = false;
        }
    } catch (error) {
        showNotification('请求失败: ' + error.message, 'error');
        document.getElementById('start-crawl').disabled = false;
    }
}

function startCrawlerPolling() {
    // 清除之前的轮询
    if (crawlerPollingInterval) {
        clearInterval(crawlerPollingInterval);
    }
    crawlerPollingInterval = setInterval(updateCrawlerStatus, 1000);
}

function stopCrawlerPolling() {
    if (crawlerPollingInterval) {
        clearInterval(crawlerPollingInterval);
        crawlerPollingInterval = null;
    }
}

async function updateCrawlerStatus() {
    try {
        const response = await fetch('/api/crawler/status');
        const status = await response.json();

        // 更新进度条
        document.getElementById('crawl-progress-bar').style.width = `${status.progress}%`;
        document.getElementById('crawl-progress-text').textContent = `${Math.round(status.progress)}%`;

        // 更新状态文本
        let statusText = status.current_task || '处理中...';
        if (status.current_keyword) {
            statusText = `正在处理: ${status.current_keyword} (${status.processed_keywords + 1}/${status.total_keywords})`;
        }
        if (status.total_videos > 0) {
            statusText += ` | 已获取 ${status.total_videos} 个视频`;
        }
        document.getElementById('crawl-status').textContent = statusText;

        // 更新日志
        const logBox = document.getElementById('crawl-log');
        if (status.logs && status.logs.length > 0) {
            logBox.innerHTML = status.logs.map(log => `
                <div class="log-entry ${log.is_error ? 'error' : ''}">
                    <span class="log-time">[${log.timestamp}]</span> ${escapeHtml(log.message)}
                </div>
            `).join('');
            logBox.scrollTop = logBox.scrollHeight;
        }

        // 更新按钮状态
        if (status.is_paused) {
            document.getElementById('pause-crawl').classList.add('hidden');
            document.getElementById('resume-crawl').classList.remove('hidden');
        } else {
            document.getElementById('pause-crawl').classList.remove('hidden');
            document.getElementById('resume-crawl').classList.add('hidden');
        }

        // 检查是否完成
        if (!status.is_running) {
            stopCrawlerPolling();
            document.getElementById('start-crawl').disabled = false;

            if (status.progress === 100 && !status.error) {
                showNotification('爬取完成！', 'success');

                if (status.videos && status.videos.length > 0) {
                    allCrawledVideos = status.videos;
                    displayCrawlResults(status.videos);
                }
            } else if (status.error) {
                showNotification('爬取出错: ' + status.error, 'error');
            }
        }
    } catch (error) {
        console.error('获取爬虫状态失败:', error);
    }
}

async function pauseCrawl() {
    try {
        await fetch('/api/crawler/pause', { method: 'POST' });
        showNotification('已暂停爬取', 'warning');
    } catch (error) {
        showNotification('暂停失败: ' + error.message, 'error');
    }
}

async function resumeCrawl() {
    try {
        await fetch('/api/crawler/resume', { method: 'POST' });
        showNotification('继续爬取', 'success');
    } catch (error) {
        showNotification('继续失败: ' + error.message, 'error');
    }
}

async function stopCrawl() {
    if (!confirm('确定要停止当前爬取任务吗？')) {
        return;
    }

    try {
        await fetch('/api/crawler/stop', { method: 'POST' });
        stopCrawlerPolling();
        document.getElementById('start-crawl').disabled = false;
        showNotification('已停止爬取', 'warning');
    } catch (error) {
        showNotification('停止失败: ' + error.message, 'error');
    }
}

function displayCrawlResults(videos) {
    document.getElementById('crawl-results-card').classList.remove('hidden');

    // 计算分页
    const totalPages = Math.ceil(videos.length / videosPerPage);
    const startIndex = (currentPage - 1) * videosPerPage;
    const endIndex = startIndex + videosPerPage;
    const currentVideos = videos.slice(startIndex, endIndex);

    // 渲染表格
    const tbody = document.getElementById('results-table-body');
    tbody.innerHTML = currentVideos.map(video => `
        <tr>
            <td>
                <a href="https://www.bilibili.com/video/${video.bvid}" target="_blank" style="color: var(--primary);">
                    ${video.bvid || ''}
                </a>
            </td>
            <td title="${escapeHtml(video.title || '')}">
                ${escapeHtml(truncate(video.title || '', 40))}
            </td>
            <td>${escapeHtml(video.author || '-')}</td>
            <td>${(video.play || 0).toLocaleString()}</td>
            <td>${video.pubdate || video.uploadDate || '-'}</td>
            <td>
                <button class="btn btn-secondary btn-small" onclick="selectForDownload('${video.bvid}')">
                    📥 下载
                </button>
                <button class="btn btn-secondary btn-small" onclick="selectForTranscribe('${video.bvid}')">
                    🎤 转写
                </button>
            </td>
        </tr>
    `).join('');

    // 渲染分页
    renderPagination(videos.length, totalPages);
}

function renderPagination(totalVideos, totalPages) {
    const pagination = document.getElementById('pagination');

    if (totalPages <= 1) {
        pagination.innerHTML = `<span style="color: var(--gray-500);">共 ${totalVideos} 条结果</span>`;
        return;
    }

    let html = `<span style="color: var(--gray-500); margin-right: 16px;">共 ${totalVideos} 条结果</span>`;

    // 上一页
    html += `<button class="btn btn-secondary btn-small" onclick="goToPage(${currentPage - 1})" ${currentPage === 1 ? 'disabled' : ''}>上一页</button>`;

    // 页码
    html += `<span style="margin: 0 12px;">第 ${currentPage} / ${totalPages} 页</span>`;

    // 下一页
    html += `<button class="btn btn-secondary btn-small" onclick="goToPage(${currentPage + 1})" ${currentPage === totalPages ? 'disabled' : ''}>下一页</button>`;

    pagination.innerHTML = html;
}

function goToPage(page) {
    const totalPages = Math.ceil(allCrawledVideos.length / videosPerPage);
    if (page < 1 || page > totalPages) return;

    currentPage = page;
    displayCrawlResults(allCrawledVideos);

    // 滚动到表格顶部
    document.getElementById('crawl-results-card').scrollIntoView({ behavior: 'smooth' });
}

function downloadResults() {
    showNotification('正在下载Excel文件...', 'info');
    window.location.href = '/api/crawler/download';
}

function selectForDownload(bvid) {
    // 切换到下载页签
    document.querySelectorAll('.nav-item').forEach(nav => nav.classList.remove('active'));
    document.querySelector('.nav-item[data-tab="download"]').classList.add('active');

    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
    document.getElementById('tab-download').classList.add('active');

    // 填入BV号
    document.getElementById('bvid-input').value = bvid;

    showNotification(`已选择 ${bvid}，请选择下载类型后开始下载`, 'success');
}

function selectForTranscribe(bvid) {
    // 切换到转写页签
    document.querySelectorAll('.nav-item').forEach(nav => nav.classList.remove('active'));
    document.querySelector('.nav-item[data-tab="transcribe"]').classList.add('active');

    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
    document.getElementById('tab-transcribe').classList.add('active');

    // 填入BV号
    document.getElementById('transcribe-bvid').value = bvid;

    showNotification(`已选择 ${bvid}，请先下载音频后进行转写`, 'info');
}

// ========== 下载模块 ==========
function initDownloader() {
    document.getElementById('start-download').addEventListener('click', startDownload);
    document.getElementById('clear-tasks').addEventListener('click', clearDownloadTasks);
    document.getElementById('refresh-downloads').addEventListener('click', loadDownloads);
}

function getSelectedDownloadTypes() {
    const types = [];
    if (document.getElementById('type-audio').checked) types.push('audio');
    if (document.getElementById('type-video').checked) types.push('video_only');
    if (document.getElementById('type-merged').checked) types.push('merged');
    if (document.getElementById('type-danmaku').checked) types.push('danmaku');
    return types;
}

async function startDownload() {
    const bvidText = document.getElementById('bvid-input').value;
    const bvids = bvidText.split('\n')
        .map(b => b.trim())
        .filter(b => b)
        .map(b => {
            // 从URL中提取BV号
            const match = b.match(/(BV[\w]+)/i);
            return match ? match[1] : b;
        });

    const types = getSelectedDownloadTypes();

    if (bvids.length === 0) {
        showNotification('请输入至少一个BV号', 'error');
        return;
    }

    if (types.length === 0) {
        showNotification('请选择至少一种下载类型', 'error');
        return;
    }

    showNotification(`开始下载 ${bvids.length} 个视频`, 'success');

    for (const type of types) {
        try {
            const response = await fetch('/api/download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ bvids, type })
            });
            const data = await response.json();

            if (data.task_ids) {
                for (const taskId of data.task_ids) {
                    addDownloadTask(taskId, type);
                    pollDownloadStatus(taskId);
                }
            }
        } catch (error) {
            showNotification('下载请求失败: ' + error.message, 'error');
        }
    }
}

function addDownloadTask(taskId, type) {
    const taskList = document.getElementById('download-task-list');
    const emptyState = taskList.querySelector('.empty-state');
    if (emptyState) emptyState.remove();

    const bvid = taskId.split('_')[0];
    const typeLabels = {
        'audio': '🎵 音频',
        'video_only': '🎥 纯视频',
        'merged': '📹 合成视频',
        'danmaku': '💬 弹幕'
    };

    const taskHtml = `
        <div class="task-item" id="task-${taskId}">
            <div class="task-header">
                <span><strong>${bvid}</strong> (${typeLabels[type] || type})</span>
                <span class="task-status status-downloading" id="status-${taskId}">下载中</span>
            </div>
            <div class="progress-container">
                <div class="progress-bar">
                    <div class="progress-fill" id="progress-${taskId}" style="width: 0%"></div>
                </div>
                <span class="progress-text" id="progress-text-${taskId}">0%</span>
            </div>
            <p id="message-${taskId}" style="font-size: 12px; color: var(--gray-500); margin-top: 8px;">准备中...</p>
        </div>
    `;

    taskList.insertAdjacentHTML('beforeend', taskHtml);
    downloadTasks[taskId] = { bvid, type };
}

async function pollDownloadStatus(taskId) {
    const poll = async () => {
        try {
            const response = await fetch(`/api/download/status/${taskId}`);
            const status = await response.json();

            const statusEl = document.getElementById(`status-${taskId}`);
            const progressEl = document.getElementById(`progress-${taskId}`);
            const progressTextEl = document.getElementById(`progress-text-${taskId}`);
            const messageEl = document.getElementById(`message-${taskId}`);

            if (!statusEl) return; // 任务已被清除

            if (status.status === 'downloading') {
                const progress = Math.round(status.progress || 0);
                progressEl.style.width = `${progress}%`;
                progressTextEl.textContent = `${progress}%`;
                messageEl.textContent = status.message || '下载中...';
                setTimeout(poll, 1000);
            } else if (status.status === 'completed') {
                statusEl.textContent = '完成';
                statusEl.className = 'task-status status-completed';
                progressEl.style.width = '100%';
                progressTextEl.textContent = '100%';
                messageEl.textContent = status.message || '下载完成！';

                // 刷新已下载列表
                loadDownloads();
                showNotification(`${taskId.split('_')[0]} 下载完成`, 'success');
            } else if (status.status === 'error') {
                statusEl.textContent = '失败';
                statusEl.className = 'task-status status-error';
                messageEl.textContent = status.message || '下载失败';
                showNotification(`${taskId.split('_')[0]} 下载失败: ${status.message}`, 'error');
            } else {
                setTimeout(poll, 1000);
            }
        } catch (error) {
            console.error('获取下载状态失败:', error);
            setTimeout(poll, 2000);
        }
    };
    poll();
}

function clearDownloadTasks() {
    document.getElementById('download-task-list').innerHTML = '<div class="empty-state">暂无下载任务</div>';
    downloadTasks = {};
}

async function loadDownloads() {
    const container = document.getElementById('download-list');
    container.innerHTML = '<div class="empty-state">正在加载...</div>';

    try {
        const response = await fetch('/api/downloads');
        const data = await response.json();

        if (!data.downloads || data.downloads.length === 0) {
            container.innerHTML = '<div class="empty-state">📭 暂无已下载内容<br><small>在上方输入BV号开始下载</small></div>';
            return;
        }

        container.innerHTML = data.downloads.map(item => {
            const tags = [];
            if (item.has_audio) tags.push('<span class="tag tag-audio">🎵 音频</span>');
            if (item.has_video) tags.push('<span class="tag tag-video">🎥 视频</span>');
            if (item.has_transcript) tags.push('<span class="tag tag-transcript">📝 转写</span>');

            return `
                <div class="download-item" data-bvid="${item.bvid}">
                    <div class="download-info">
                        <h3 title="${escapeHtml(item.title)}">${escapeHtml(truncate(item.title, 40))}</h3>
                        <div class="download-meta">
                            <span>${item.bvid}</span> ·
                            <span>${item.file_count} 个文件</span> ·
                            <span>${formatFileSize(item.total_size)}</span>
                        </div>
                        <div class="download-tags">${tags.join('')}</div>
                    </div>
                    <div class="download-actions">
                        <button class="btn btn-secondary btn-small" onclick="viewFiles('${item.bvid}')">📂 文件</button>
                        <button class="btn btn-primary btn-small" onclick="selectDownloadForProcess('${item.bvid}')">✅ 选择</button>
                        <button class="btn btn-danger btn-small" onclick="deleteDownload('${item.bvid}')">🗑️</button>
                    </div>
                </div>
            `;
        }).join('');
    } catch (error) {
        container.innerHTML = `<div class="empty-state">❌ 加载失败: ${error.message}</div>`;
    }
}

async function viewFiles(bvid) {
    try {
        const response = await fetch(`/api/files/${bvid}`);
        const data = await response.json();

        if (data.files && data.files.length > 0) {
            const fileList = data.files.map(f => `📄 ${f.name} (${formatFileSize(f.size)})`).join('\n');
            alert(`📁 ${bvid} 的文件:\n\n${fileList}`);
        } else {
            alert('暂无文件');
        }
    } catch (error) {
        showNotification('获取文件列表失败: ' + error.message, 'error');
    }
}

function selectDownloadForProcess(bvid) {
    document.getElementById('transcribe-bvid').value = bvid;

    // 切换到转写页签
    document.querySelectorAll('.nav-item').forEach(nav => nav.classList.remove('active'));
    document.querySelector('.nav-item[data-tab="transcribe"]').classList.add('active');

    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
    document.getElementById('tab-transcribe').classList.add('active');

    showNotification(`已选择 ${bvid}，可以开始转写`, 'success');
}

async function deleteDownload(bvid) {
    if (!confirm(`确定要删除 ${bvid} 及其所有文件吗？`)) {
        return;
    }

    try {
        const response = await fetch(`/api/delete/${bvid}`, {
            method: 'DELETE'
        });
        const data = await response.json();

        if (data.success) {
            showNotification(`已删除 ${bvid}`, 'success');
            loadDownloads();
        } else {
            showNotification('删除失败: ' + data.error, 'error');
        }
    } catch (error) {
        showNotification('删除失败: ' + error.message, 'error');
    }
}

// ========== 转写模块 ==========
function initTranscriber() {
    document.getElementById('start-transcribe').addEventListener('click', startTranscribe);
    document.getElementById('copy-transcript').addEventListener('click', copyTranscript);

    // 格式切换标签
    document.querySelectorAll('.format-tabs .tab-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const format = this.dataset.format;
            switchTranscriptFormat(format);
        });
    });
}

function getSelectedTranscribeFormats() {
    const formats = ['txt']; // 默认纯文本
    if (document.getElementById('format-timestamped').checked) formats.push('timestamped');
    if (document.getElementById('format-srt').checked) formats.push('srt');
    if (document.getElementById('format-json').checked) formats.push('json');
    return formats;
}

async function startTranscribe() {
    const bvid = document.getElementById('transcribe-bvid').value.trim();

    if (!bvid) {
        showNotification('请输入BV号', 'error');
        return;
    }

    const formats = getSelectedTranscribeFormats();

    // 显示进度，隐藏结果
    document.getElementById('transcribe-progress-card').classList.remove('hidden');
    document.getElementById('transcribe-result-card').classList.add('hidden');

    // 重置进度
    document.getElementById('transcribe-progress-bar').style.width = '0%';
    document.getElementById('transcribe-progress-text').textContent = '0%';
    document.getElementById('transcribe-message').textContent = '正在启动转写任务...';

    // 禁用按钮
    const btn = document.getElementById('start-transcribe');
    btn.disabled = true;
    btn.innerHTML = '<span class="btn-icon">⏳</span> 转写中...';

    try {
        const response = await fetch('/api/transcribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ bvid, formats })
        });

        const data = await response.json();

        if (data.error) {
            showNotification('转写失败: ' + data.error, 'error');
            resetTranscribeButton();
            document.getElementById('transcribe-progress-card').classList.add('hidden');
            return;
        }

        // 如果已完成（缓存）
        if (data.status === 'completed' && data.text) {
            displayTranscriptResult(data);
            resetTranscribeButton();
            document.getElementById('transcribe-progress-card').classList.add('hidden');
            showNotification('转写完成（使用缓存）', 'success');
            return;
        }

        // 开始轮询状态
        transcribeTaskId = data.task_id;
        pollTranscribeStatus(transcribeTaskId);

    } catch (error) {
        showNotification('转写请求失败: ' + error.message, 'error');
        resetTranscribeButton();
        document.getElementById('transcribe-progress-card').classList.add('hidden');
    }
}

function resetTranscribeButton() {
    const btn = document.getElementById('start-transcribe');
    btn.disabled = false;
    btn.innerHTML = '<span class="btn-icon">🎤</span> 开始转写';
}

async function pollTranscribeStatus(taskId) {
    const poll = async () => {
        try {
            const response = await fetch(`/api/transcribe/status/${taskId}`);
            const status = await response.json();

            const progressBar = document.getElementById('transcribe-progress-bar');
            const progressText = document.getElementById('transcribe-progress-text');
            const messageEl = document.getElementById('transcribe-message');

            const progress = Math.round(status.progress || 0);
            progressBar.style.width = `${progress}%`;
            progressText.textContent = `${progress}%`;

            // 根据状态显示不同消息
            if (status.status === 'starting' || status.status === 'loading_model') {
                messageEl.textContent = '⏳ ' + (status.message || '正在加载 Whisper 模型...');
                setTimeout(poll, 1000);
            } else if (status.status === 'transcribing') {
                messageEl.textContent = '🎤 ' + (status.message || '正在转写...');
                setTimeout(poll, 2000);
            } else if (status.status === 'completed') {
                progressBar.style.width = '100%';
                progressText.textContent = '100%';
                messageEl.textContent = '✅ 转写完成！';

                displayTranscriptResult(status);
                resetTranscribeButton();

                setTimeout(() => {
                    document.getElementById('transcribe-progress-card').classList.add('hidden');
                }, 1500);

                showNotification('转写完成！', 'success');
                loadDownloads(); // 刷新下载列表
            } else if (status.status === 'error') {
                messageEl.textContent = '❌ ' + (status.message || '转写失败');
                resetTranscribeButton();
                showNotification('转写失败: ' + status.message, 'error');
            } else {
                messageEl.textContent = status.message || '处理中...';
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

    // 显示结果卡片
    document.getElementById('transcribe-result-card').classList.remove('hidden');

    // 统计信息
    const duration = data.duration || 0;
    const minutes = Math.floor(duration / 60);
    const seconds = Math.floor(duration % 60);
    document.getElementById('stat-duration').textContent = `${minutes}分${seconds}秒`;
    document.getElementById('stat-segments').textContent = (data.segments || []).length;
    document.getElementById('stat-chars').textContent = (data.text || '').length;

    // 纯文本
    document.getElementById('transcript-text').textContent = data.text || '';

    // 时间戳文本
    document.getElementById('transcript-timestamped').textContent = data.timestamped_text || '';

    // 分段视图
    const segmentsHtml = (data.segments || []).map(seg => `
        <div class="segment-item">
            <div class="segment-time">${seg.start_formatted} → ${seg.end_formatted}</div>
            <div class="segment-text">${escapeHtml(seg.text)}</div>
        </div>
    `).join('');
    document.getElementById('transcript-segments').innerHTML = segmentsHtml || '<p>无分段数据</p>';

    // SRT格式
    const srtContent = generateSRT(data.segments || []);
    document.getElementById('transcript-srt').textContent = srtContent;

    // 默认显示纯文本
    switchTranscriptFormat('plain');
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

function switchTranscriptFormat(format) {
    // 更新标签状态
    document.querySelectorAll('.format-tabs .tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.format === format);
    });

    // 隐藏所有视图
    document.getElementById('view-plain').classList.add('hidden');
    document.getElementById('view-timestamped').classList.add('hidden');
    document.getElementById('view-segments').classList.add('hidden');
    document.getElementById('view-srt').classList.add('hidden');

    // 显示对应视图
    document.getElementById(`view-${format}`).classList.remove('hidden');
}

function copyTranscript() {
    if (!currentTranscriptData) {
        showNotification('没有可复制的内容', 'warning');
        return;
    }

    // 获取当前选中的格式
    const activeTab = document.querySelector('.format-tabs .tab-btn.active');
    const format = activeTab ? activeTab.dataset.format : 'plain';

    let text = '';
    switch (format) {
        case 'plain':
            text = currentTranscriptData.text || '';
            break;
        case 'timestamped':
            text = currentTranscriptData.timestamped_text || '';
            break;
        case 'segments':
            text = (currentTranscriptData.segments || []).map(s =>
                `[${s.start_formatted}] ${s.text}`
            ).join('\n');
            break;
        case 'srt':
            text = document.getElementById('transcript-srt').textContent;
            break;
    }

    navigator.clipboard.writeText(text).then(() => {
        const btn = document.getElementById('copy-transcript');
        btn.textContent = '✅ 已复制';
        setTimeout(() => {
            btn.textContent = '📋 复制';
        }, 2000);
        showNotification('已复制到剪贴板', 'success');
    }).catch(err => {
        showNotification('复制失败: ' + err.message, 'error');
    });
}

// ========== AI总结模块 ==========
function initSummarizer() {
    document.getElementById('start-summary').addEventListener('click', startSummary);
}

async function startSummary() {
    if (!currentTranscriptData || !currentTranscriptData.text) {
        showNotification('请先进行音频转写', 'error');
        return;
    }

    const baseUrl = document.getElementById('api-base-url').value.trim();
    const apiKey = document.getElementById('api-key').value.trim();
    const model = document.getElementById('api-model').value.trim();
    const prompt = document.getElementById('summary-prompt').value.trim();
    const includeTimestamps = document.getElementById('include-timestamps').checked;

    if (!apiKey) {
        showNotification('请输入API Key', 'error');
        return;
    }

    // 准备文本
    let textToSummarize;
    if (includeTimestamps && currentTranscriptData.timestamped_text) {
        textToSummarize = currentTranscriptData.timestamped_text;
    } else {
        textToSummarize = currentTranscriptData.text;
    }

    // 禁用按钮
    const btn = document.getElementById('start-summary');
    btn.disabled = true;
    btn.innerHTML = '<span class="btn-icon">⏳</span> 总结中...';

    try {
        const response = await fetch('/api/summarize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                text: textToSummarize,
                base_url: baseUrl,
                api_key: apiKey,
                model: model,
                prompt: prompt,
                include_timestamps: includeTimestamps
            })
        });

        const data = await response.json();

        if (data.error) {
            showNotification('总结失败: ' + data.error, 'error');
        } else {
            document.getElementById('summary-text').textContent = data.summary || '';
            document.getElementById('summary-result-card').classList.remove('hidden');
            showNotification('AI总结完成！', 'success');
        }
    } catch (error) {
        showNotification('总结请求失败: ' + error.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<span class="btn-icon">🤖</span> 开始总结';
    }
}

// ========== 全局函数暴露 ==========
// 这些函数需要在HTML的onclick中调用
window.removeKeyword = removeKeyword;
window.goToPage = goToPage;
window.selectForDownload = selectForDownload;
window.selectForTranscribe = selectForTranscribe;
window.viewFiles = viewFiles;
window.selectDownloadForProcess = selectDownloadForProcess;
window.deleteDownload = deleteDownload;

