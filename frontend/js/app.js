// frontend/js/app.js
/**
 * B站视频工具箱 - 前端交互逻辑
 * 重构版本：卡片式展示 + 批量选择
 */


// ========== 全局变量 ==========
const API_BASE = '';


// 数据存储
let allVideos = [];                    // 所有视频数据
let selectedVideos = new Set();        // 选中的视频BVID
let videoDetails = {};                 // 视频详情缓存 {bvid: {files, transcript, summary}}
let manualKeywords = [];               // 手动添加的关键词


// 状态控制
let isBatchMode = false;               // 是否批量选择模式
let expandedCard = null;               // 当前展开的卡片BVID
let currentPage = 1;                   // 当前页码
let videosPerPage = 50;                // 每页数量


// 轮询
let crawlerPollingInterval = null;


// 转写队列
let transcribeQueue = [];              // 转写任务队列
let isTranscribing = false;            // 是否正在转写


// ========== 初始化 ==========
document.addEventListener('DOMContentLoaded', function() {
    initEventListeners();
    loadSavedData();
    loadDownloadedInfo();
});


function initEventListeners() {
    // 批量模式切换
    document.getElementById('batch-mode-switch').addEventListener('change', toggleBatchMode);

    // 搜索面板
    document.getElementById('search-toggle-btn').addEventListener('click', () => togglePanel('search'));
    document.getElementById('search-panel-close').addEventListener('click', () => closePanel('search'));

    // 设置面板
    document.getElementById('settings-toggle-btn').addEventListener('click', () => togglePanel('settings'));
    document.getElementById('settings-panel-close').addEventListener('click', () => closePanel('settings'));

    // 遮罩层
    document.getElementById('overlay').addEventListener('click', closeAllPanels);

    // 点击主内容区空白处关闭展开的卡片
    document.getElementById('video-list').addEventListener('click', handleVideoListClick);

    // 搜索相关
    document.getElementById('keyword-upload').addEventListener('click', () => {
        document.getElementById('keyword-file').click();
    });
    document.getElementById('keyword-file').addEventListener('change', handleFileSelect);
    document.getElementById('add-keyword-btn').addEventListener('click', addKeyword);
    document.getElementById('new-keyword').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            addKeyword();
        }
    });
    document.getElementById('start-crawl').addEventListener('click', startCrawl);
    document.getElementById('pause-crawl').addEventListener('click', pauseCrawl);
    document.getElementById('resume-crawl').addEventListener('click', resumeCrawl);
    document.getElementById('stop-crawl').addEventListener('click', stopCrawl);

    // 批量操作
    document.getElementById('batch-download-btn').addEventListener('click', batchDownload);
    document.getElementById('batch-transcribe-btn').addEventListener('click', batchTranscribe);
    document.getElementById('batch-summary-btn').addEventListener('click', batchSummary);

    // 设置相关
    document.getElementById('export-data-btn').addEventListener('click', exportData);
    document.getElementById('clear-all-btn').addEventListener('click', clearAllData);

    // 下载模态框
    document.getElementById('download-modal-close').addEventListener('click', () => closeModal('download-modal'));
    document.getElementById('confirm-download-btn').addEventListener('click', confirmDownload);

    // 详情模态框
    document.getElementById('detail-modal-close').addEventListener('click', () => closeModal('video-detail-modal'));

    // 任务浮窗
    document.getElementById('task-float-toggle').addEventListener('click', toggleTaskFloat);

    // 加载更多
    document.getElementById('load-more-btn').addEventListener('click', loadMoreVideos);

    // 拖拽上传
    const uploadArea = document.getElementById('keyword-upload');
    uploadArea.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadArea.style.borderColor = 'var(--primary)';
    });
    uploadArea.addEventListener('dragleave', () => {
        uploadArea.style.borderColor = '';
    });
    uploadArea.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadArea.style.borderColor = '';
        if (e.dataTransfer.files.length) {
            document.getElementById('keyword-file').files = e.dataTransfer.files;
            handleFileSelect();
        }
    });

    // 点击页面其他区域关闭展开的卡片
    document.querySelector('.main-content').addEventListener('click', handleMainContentClick);
}


// ========== 工具函数 ==========
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
    if (!bytes) return '0 B';
    if (bytes > 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
    if (bytes > 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
    if (bytes > 1024) return `${(bytes / 1024).toFixed(2)} KB`;
    return `${bytes} B`;
}


function formatNumber(num) {
    if (!num) return '0';
    if (num >= 10000) return (num / 10000).toFixed(1) + '万';
    return num.toLocaleString();
}


function showNotification(message, type = 'info') {
    const notification = document.getElementById('notification');
    const icon = document.getElementById('notification-icon');
    const msg = document.getElementById('notification-message');

    const icons = { success: '✅', error: '❌', warning: '⚠️', info: 'ℹ️' };
    icon.textContent = icons[type] || icons.info;
    msg.textContent = message;

    notification.className = `notification ${type} show`;

    setTimeout(() => {
        notification.classList.remove('show');
    }, 3000);
}


// ========== 点击处理 ==========
function handleVideoListClick(event) {
    // 这个函数留空，实际处理在卡片点击中
}


function handleMainContentClick(event) {
    // 如果没有展开的卡片或在批量模式，不处理
    if (!expandedCard || isBatchMode) return;

    // 检查点击是否在视频卡片内
    const clickedCard = event.target.closest('.video-card');

    // 如果点击的不是任何卡片（点击空白区域），折叠当前展开的卡片
    if (!clickedCard) {
        expandedCard = null;
        renderVideoList();
    }
}


// ========== 面板控制 ==========
function togglePanel(panelType) {
    const panel = document.getElementById(`${panelType}-panel`);
    const overlay = document.getElementById('overlay');
    const btn = document.getElementById(`${panelType}-toggle-btn`);

    if (panel.classList.contains('open')) {
        closePanel(panelType);
    } else {
        closeAllPanels();
        panel.classList.add('open');
        overlay.classList.remove('hidden');
        setTimeout(() => overlay.classList.add('show'), 10);
        btn.classList.add('active');
    }
}


function closePanel(panelType) {
    const panel = document.getElementById(`${panelType}-panel`);
    const overlay = document.getElementById('overlay');
    const btn = document.getElementById(`${panelType}-toggle-btn`);

    panel.classList.remove('open');
    overlay.classList.remove('show');
    setTimeout(() => overlay.classList.add('hidden'), 300);
    btn.classList.remove('active');
}


function closeAllPanels() {
    closePanel('search');
    closePanel('settings');
}


// ========== 模态框控制 ==========
function openModal(modalId) {
    const modal = document.getElementById(modalId);
    modal.classList.remove('hidden');
    setTimeout(() => modal.classList.add('show'), 10);
}


function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    modal.classList.remove('show');
    setTimeout(() => modal.classList.add('hidden'), 300);
}


// ========== 批量模式 ==========
function toggleBatchMode() {
    isBatchMode = document.getElementById('batch-mode-switch').checked;
    const batchActions = document.getElementById('batch-actions');

    if (isBatchMode) {
        batchActions.classList.add('active');
        // 进入批量模式时，折叠已展开的卡片
        expandedCard = null;
    } else {
        batchActions.classList.remove('active');
        selectedVideos.clear();
        updateSelectedCount();
    }

    renderVideoList();
}


function updateSelectedCount() {
    document.getElementById('selected-count').textContent = `已选 ${selectedVideos.size} 个`;
}


// ========== 关键词管理 ==========
function handleFileSelect() {
    const fileInput = document.getElementById('keyword-file');
    const fileInfo = document.getElementById('file-name');

    if (fileInput.files.length > 0) {
        fileInfo.textContent = `已选择: ${fileInput.files[0].name}`;
        fileInfo.classList.remove('hidden');
        showNotification(`文件已选择`, 'success');
    }
}


function addKeyword() {
    const input = document.getElementById('new-keyword');
    const keyword = input.value.trim();

    if (!keyword) return;
    if (manualKeywords.includes(keyword)) {
        showNotification('关键词已存在', 'warning');
        return;
    }

    manualKeywords.push(keyword);
    renderKeywords();
    input.value = '';
}


function removeKeyword(keyword) {
    manualKeywords = manualKeywords.filter(k => k !== keyword);
    renderKeywords();
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


// ========== 搜索爬取 ==========
async function startCrawl() {
    const fileInput = document.getElementById('keyword-file');
    const hasFile = fileInput.files.length > 0;
    const hasKeywords = manualKeywords.length > 0;

    if (!hasFile && !hasKeywords) {
        showNotification('请上传关键词文件或添加关键词', 'error');
        return;
    }

    // 显示进度
    document.getElementById('search-progress').classList.remove('hidden');
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
            showNotification(`开始搜索，共 ${data.keywords_count} 个关键词`, 'success');
            startCrawlerPolling();
        } else {
            showNotification('错误: ' + data.error, 'error');
            document.getElementById('search-progress').classList.add('hidden');
            document.getElementById('start-crawl').disabled = false;
        }
    } catch (error) {
        showNotification('请求失败: ' + error.message, 'error');
        document.getElementById('search-progress').classList.add('hidden');
        document.getElementById('start-crawl').disabled = false;
    }
}


function startCrawlerPolling() {
    if (crawlerPollingInterval) clearInterval(crawlerPollingInterval);
    crawlerPollingInterval = setInterval(updateCrawlerStatus, 1000);
}


async function updateCrawlerStatus() {
    try {
        const response = await fetch('/api/crawler/status');
        const status = await response.json();

        // 更新进度
        document.getElementById('crawl-progress-bar').style.width = `${status.progress}%`;
        document.getElementById('crawl-progress-text').textContent = `${Math.round(status.progress)}%`;

        // 更新状态
        let statusText = status.current_task || '处理中...';
        if (status.current_keyword) {
            statusText = `${status.current_keyword} (${status.processed_keywords + 1}/${status.total_keywords})`;
        }
        document.getElementById('crawl-status').textContent = statusText;

        // 更新按钮
        if (status.is_paused) {
            document.getElementById('pause-crawl').classList.add('hidden');
            document.getElementById('resume-crawl').classList.remove('hidden');
        } else {
            document.getElementById('pause-crawl').classList.remove('hidden');
            document.getElementById('resume-crawl').classList.add('hidden');
        }

        // 实时更新视频列表
        if (status.videos && status.videos.length > allVideos.length) {
            allVideos = status.videos;
            saveData();
            renderVideoList();
        }

        // 完成检查
        if (!status.is_running) {
            clearInterval(crawlerPollingInterval);
            document.getElementById('search-progress').classList.add('hidden');
            document.getElementById('start-crawl').disabled = false;

            if (status.progress === 100 && !status.error) {
                showNotification(`搜索完成，共获取 ${status.videos.length} 个视频`, 'success');
                allVideos = status.videos;
                saveData();
                renderVideoList();
                closePanel('search');
            } else if (status.error) {
                showNotification('搜索出错: ' + status.error, 'error');
            }
        }
    } catch (error) {
        console.error('获取状态失败:', error);
    }
}


async function pauseCrawl() {
    await fetch('/api/crawler/pause', { method: 'POST' });
    showNotification('已暂停', 'warning');
}


async function resumeCrawl() {
    await fetch('/api/crawler/resume', { method: 'POST' });
    showNotification('继续搜索', 'success');
}


async function stopCrawl() {
    await fetch('/api/crawler/stop', { method: 'POST' });
    clearInterval(crawlerPollingInterval);
    document.getElementById('search-progress').classList.add('hidden');
    document.getElementById('start-crawl').disabled = false;
    showNotification('已停止', 'warning');
}


// ========== 视频列表渲染 ==========
function renderVideoList() {
    const container = document.getElementById('video-list');
    const emptyState = document.getElementById('empty-state');
    const loadMore = document.getElementById('load-more');

    if (allVideos.length === 0) {
        container.innerHTML = '';
        emptyState.classList.remove('hidden');
        loadMore.classList.add('hidden');
        return;
    }

    emptyState.classList.add('hidden');

    const displayVideos = allVideos.slice(0, currentPage * videosPerPage);

    container.innerHTML = displayVideos.map(video => renderVideoCard(video)).join('');

    // 加载更多
    if (displayVideos.length < allVideos.length) {
        loadMore.classList.remove('hidden');
        document.getElementById('load-info').textContent =
            `显示 ${displayVideos.length} / ${allVideos.length} 个视频`;
    } else {
        loadMore.classList.add('hidden');
    }
}


function renderVideoCard(video) {
    const bvid = video.bvid;
    const isSelected = selectedVideos.has(bvid);
    const isExpanded = expandedCard === bvid;
    const detail = videoDetails[bvid] || {};

    // 检查队列状态
    const queueIndex = transcribeQueue.findIndex(item => item.bvid === bvid);
    const isInQueue = queueIndex !== -1;
    const isCurrentTranscribing = isInQueue && queueIndex === 0 && isTranscribing;

    // 标签
    let tags = '';
    if (detail.files && detail.files.length > 0) {
        tags += '<span class="card-tag tag-downloaded">已下载</span>';
    }
    if (detail.transcript) {
        tags += '<span class="card-tag tag-transcribed">已转写</span>';
    } else if (isCurrentTranscribing) {
        tags += '<span class="card-tag" style="background:#fef3c7;color:#d97706;">转写中...</span>';
    } else if (isInQueue) {
        tags += `<span class="card-tag" style="background:#e0e7ff;color:#4338ca;">队列 #${queueIndex + 1}</span>`;
    }
    if (detail.summary) {
        tags += '<span class="card-tag tag-summarized">已总结</span>';
    }

    // 卡片主体
    let cardHtml = `
        <div class="video-card ${isSelected ? 'selected' : ''} ${isExpanded ? 'expanded' : ''}" data-bvid="${bvid}">
            ${isBatchMode ? `<div class="card-checkbox" onclick="handleCheckboxClick('${bvid}', event)"></div>` : ''}
            <div class="card-main" onclick="handleCardClick('${bvid}', event)">
                <div class="card-info">
                    <div class="card-title" title="${escapeHtml(video.title)}">${escapeHtml(video.title)}</div>
                    <div class="card-meta">
                        <span class="card-meta-item">👤 ${escapeHtml(video.author) || '未知'}</span>
                        <span class="card-meta-item">▶️ ${formatNumber(video.play)}</span>
                        <span class="card-meta-item">💬 ${formatNumber(video.review)}</span>
                        <span class="card-meta-item">📅 ${video.pubdate || video.uploadDate || '-'}</span>
                    </div>
                </div>
                <div class="card-tags">${tags}</div>
                <div class="card-actions" onclick="event.stopPropagation()">
                    <button class="btn btn-secondary btn-small" onclick="quickDownload('${bvid}')" title="下载">📥</button>
                    <a href="https://www.bilibili.com/video/${bvid}" target="_blank" class="btn btn-secondary btn-small" title="打开B站">🔗</a>
                </div>
            </div>
    `;

    // 展开详情
    if (isExpanded && !isBatchMode) {
        cardHtml += renderCardDetail(video, detail);
    }

    cardHtml += '</div>';
    return cardHtml;
}


function renderCardDetail(video, detail) {
    const bvid = video.bvid;

    // 检查队列状态
    const queueIndex = transcribeQueue.findIndex(item => item.bvid === bvid);
    const isInQueue = queueIndex !== -1;
    const isCurrentTranscribing = isInQueue && queueIndex === 0 && isTranscribing;

    let html = '<div class="card-detail" onclick="event.stopPropagation()">';

    // 基本信息
    html += `
        <div class="detail-section">
            <div class="detail-section-title">📋 基本信息</div>
            <div style="font-size: 13px; color: var(--gray-600); line-height: 1.6;">
                <div><strong>BV号:</strong> ${bvid}</div>
                <div><strong>时长:</strong> ${video.duration || '-'}</div>
                <div><strong>标签:</strong> ${escapeHtml(video.tag) || '-'}</div>
                ${video.description ? `<div><strong>简介:</strong> ${escapeHtml(truncate(video.description, 100))}</div>` : ''}
            </div>
        </div>
    `;

    // 已下载文件
    html += '<div class="detail-section">';
    html += '<div class="detail-section-title">📁 已下载文件</div>';
    if (detail.files && detail.files.length > 0) {
        html += '<div class="detail-files">';
        detail.files.forEach(file => {
            html += `<span class="file-tag">📄 ${escapeHtml(file.name)} <span class="file-size">(${formatFileSize(file.size)})</span></span>`;
        });
        html += '</div>';
    } else {
        html += `
            <div class="content-bubble">
                <div class="bubble-empty">
                    <div class="bubble-empty-icon">📭</div>
                    <div>暂无下载文件</div>
                    <div class="bubble-actions">
                        <button class="btn btn-primary btn-small" onclick="quickDownload('${bvid}')">📥 下载</button>
                    </div>
                </div>
            </div>
        `;
    }
    html += '</div>';

    // 转写内容
    html += '<div class="detail-section">';
    html += '<div class="detail-section-title">📝 转写内容</div>';
    if (detail.transcript) {
        html += `
            <div class="content-bubble">
                <div class="bubble-content">${escapeHtml(detail.transcript)}</div>
            </div>
        `;
    } else {
        const hasAudio = detail.files && detail.files.some(f =>
            f.name.endsWith('.m4a') || f.name.endsWith('.mp3') || f.name.endsWith('.mp4')
        );

        let transcribeButton = '';
        if (isCurrentTranscribing) {
            transcribeButton = `<button class="btn btn-secondary btn-small" disabled>🎤 转写中...</button>`;
        } else if (isInQueue) {
            transcribeButton = `<button class="btn btn-secondary btn-small" onclick="removeFromTranscribeQueue('${bvid}')">❌ 取消排队 (#${queueIndex + 1})</button>`;
        } else if (hasAudio) {
            transcribeButton = `<button class="btn btn-primary btn-small" onclick="addToTranscribeQueue('${bvid}')">🎤 加入转写队列</button>`;
        } else {
            transcribeButton = `<button class="btn btn-secondary btn-small" disabled>需先下载音频</button>`;
        }

        html += `
            <div class="content-bubble">
                <div class="bubble-empty">
                    <div class="bubble-empty-icon">🎤</div>
                    <div>${isCurrentTranscribing ? '正在转写中...' : (isInQueue ? `排队中 (第 ${queueIndex + 1} 位)` : '暂无转写内容')}</div>
                    <div class="bubble-actions">
                        ${transcribeButton}
                    </div>
                </div>
            </div>
        `;
    }
    html += '</div>';

    // AI总结
    html += '<div class="detail-section">';
    html += '<div class="detail-section-title">🤖 AI总结</div>';
    if (detail.summary) {
        html += `
            <div class="content-bubble">
                <div class="bubble-content">${escapeHtml(detail.summary)}</div>
            </div>
        `;
    } else {
        html += `
            <div class="content-bubble">
                <div class="bubble-empty">
                    <div class="bubble-empty-icon">🤖</div>
                    <div>暂无AI总结</div>
                    <div class="bubble-actions">
                        ${detail.transcript ?
                            `<button class="btn btn-primary btn-small" onclick="singleSummary('${bvid}')">🤖 生成总结</button>` :
                            `<button class="btn btn-secondary btn-small" disabled>需先转写</button>`
                        }
                    </div>
                </div>
            </div>
        `;
    }
    html += '</div>';

    html += '</div>';
    return html;
}


// 处理复选框点击（批量模式）
function handleCheckboxClick(bvid, event) {
    event.stopPropagation();

    if (selectedVideos.has(bvid)) {
        selectedVideos.delete(bvid);
    } else {
        selectedVideos.add(bvid);
    }
    updateSelectedCount();
    renderVideoList();
}


// 处理卡片点击
function handleCardClick(bvid, event) {
    // 如果点击的是按钮或链接，不处理（已经在card-actions上阻止冒泡了）
    if (event.target.closest('button') || event.target.closest('a')) {
        return;
    }

    if (isBatchMode) {
        // 批量模式：切换选中状态
        if (selectedVideos.has(bvid)) {
            selectedVideos.delete(bvid);
        } else {
            selectedVideos.add(bvid);
        }
        updateSelectedCount();
        renderVideoList();
    } else {
        // 普通模式：展开/折叠详情
        if (expandedCard === bvid) {
            // 点击已展开的卡片头部，不做任何操作（让详情区域保持可交互）
            // 折叠通过点击卡片外部实现
        } else {
            // 展开新卡片
            expandedCard = bvid;
            loadVideoDetail(bvid);
            renderVideoList();
        }
    }
}


function loadMoreVideos() {
    currentPage++;
    renderVideoList();
}


// ========== 视频详情加载 ==========
async function loadVideoDetail(bvid) {
    if (videoDetails[bvid] && videoDetails[bvid].loaded) {
        return;
    }

    try {
        // 获取文件列表
        const filesResponse = await fetch(`/api/files/${bvid}`);
        const filesData = await filesResponse.json();

        if (!videoDetails[bvid]) {
            videoDetails[bvid] = {};
        }

        videoDetails[bvid].files = filesData.files || [];

        // 尝试获取转写内容
        try {
            const transcriptResponse = await fetch(`/api/transcript/${bvid}`);
            if (transcriptResponse.ok) {
                const transcriptData = await transcriptResponse.json();
                if (transcriptData.text) {
                    videoDetails[bvid].transcript = transcriptData.text;
                }
            }
        } catch (e) {
            // 忽略转写文件不存在的错误
        }

        videoDetails[bvid].loaded = true;
        saveData();
        renderVideoList();
    } catch (error) {
        console.error('加载视频详情失败:', error);
    }
}


// ========== 下载功能 ==========
let pendingDownloadBvids = [];


function quickDownload(bvid) {
    pendingDownloadBvids = [bvid];
    openModal('download-modal');
}


function batchDownload() {
    if (selectedVideos.size === 0) {
        showNotification('请先选择视频', 'warning');
        return;
    }
    pendingDownloadBvids = Array.from(selectedVideos);
    openModal('download-modal');
}


async function confirmDownload() {
    const types = [];
    if (document.getElementById('dl-type-audio').checked) types.push('audio');
    if (document.getElementById('dl-type-video').checked) types.push('video_only');
    if (document.getElementById('dl-type-merged').checked) types.push('merged');
    if (document.getElementById('dl-type-danmaku').checked) types.push('danmaku');

    if (types.length === 0) {
        showNotification('请选择下载类型', 'warning');
        return;
    }

    closeModal('download-modal');

    // 显示任务浮窗
    document.getElementById('task-float').classList.remove('hidden');

    for (const type of types) {
        try {
            const response = await fetch('/api/download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ bvids: pendingDownloadBvids, type })
            });
            const data = await response.json();

            if (data.task_ids) {
                data.task_ids.forEach(taskId => {
                    addTaskToFloat(taskId, 'download', type);
                    pollDownloadStatus(taskId);
                });
            }
        } catch (error) {
            showNotification('下载请求失败: ' + error.message, 'error');
        }
    }

    showNotification(`开始下载 ${pendingDownloadBvids.length} 个视频`, 'success');
    pendingDownloadBvids = [];
}


// ========== 转写队列功能 ==========
function addToTranscribeQueue(bvid) {
    // 检查是否已在队列中
    if (transcribeQueue.find(item => item.bvid === bvid)) {
        showNotification('该视频已在转写队列中', 'warning');
        return;
    }

    // 检查是否已有转写
    if (videoDetails[bvid]?.transcript) {
        showNotification('该视频已有转写内容', 'warning');
        return;
    }

    // 添加到队列
    transcribeQueue.push({ bvid });
    showNotification(`已加入转写队列 (第 ${transcribeQueue.length} 位)`, 'success');

    // 显示任务浮窗
    document.getElementById('task-float').classList.remove('hidden');

    // 更新队列显示
    updateTranscribeQueueDisplay();
    renderVideoList();

    // 如果没有正在转写的任务，开始处理队列
    if (!isTranscribing) {
        processTranscribeQueue();
    }
}


function removeFromTranscribeQueue(bvid) {
    const index = transcribeQueue.findIndex(item => item.bvid === bvid);
    if (index > 0) { // 不能移除正在处理的（index 0）
        transcribeQueue.splice(index, 1);
        showNotification('已从队列中移除', 'info');
        updateTranscribeQueueDisplay();
        renderVideoList();
    } else if (index === 0 && isTranscribing) {
        showNotification('正在转写中，无法取消', 'warning');
    }
}


function updateTranscribeQueueDisplay() {
    // 更新任务浮窗中的队列显示
    const container = document.getElementById('task-float-body');

    // 移除旧的队列显示
    const oldQueueDisplay = document.getElementById('transcribe-queue-display');
    if (oldQueueDisplay) {
        oldQueueDisplay.remove();
    }

    if (transcribeQueue.length === 0) return;

    let queueHtml = `
        <div id="transcribe-queue-display" class="task-item" style="background: var(--gray-50);">
            <div class="task-item-header">
                <span class="task-item-title">🎤 转写队列 (${transcribeQueue.length})</span>
            </div>
            <div style="font-size: 12px; color: var(--gray-600); margin-top: 8px;">
    `;

    transcribeQueue.forEach((item, index) => {
        const video = allVideos.find(v => v.bvid === item.bvid);
        const title = video ? truncate(video.title, 20) : item.bvid;
        const status = index === 0 && isTranscribing ? '🔄 转写中...' : `#${index + 1}`;
        queueHtml += `<div style="margin-bottom: 4px;">${status} ${escapeHtml(title)}</div>`;
    });

    queueHtml += '</div></div>';

    container.insertAdjacentHTML('afterbegin', queueHtml);
}


async function processTranscribeQueue() {
    if (isTranscribing || transcribeQueue.length === 0) {
        return;
    }

    isTranscribing = true;

    while (transcribeQueue.length > 0) {
        const current = transcribeQueue[0];
        const bvid = current.bvid;

        // 更新显示
        updateTranscribeQueueDisplay();
        renderVideoList();

        // 添加任务到浮窗
        const taskId = `transcribe_${bvid}`;
        addTaskToFloat(taskId, 'transcribe', bvid);

        try {
            // 发起转写请求
            const response = await fetch('/api/transcribe', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ bvid, formats: ['txt', 'srt'] })
            });

            const data = await response.json();

            if (data.error) {
                updateTaskInFloat(taskId, 'error', 0, data.error);
                showNotification(`${bvid} 转写失败: ${data.error}`, 'error');
            } else if (data.status === 'completed') {
                // 已完成（缓存）
                videoDetails[bvid] = videoDetails[bvid] || {};
                videoDetails[bvid].transcript = data.text;
                saveData();
                updateTaskInFloat(taskId, 'completed', 100, '转写完成');
                showNotification(`${bvid} 转写完成`, 'success');
            } else {
                // 等待转写完成
                await waitForTranscribeComplete(data.task_id, bvid);
            }
        } catch (error) {
            updateTaskInFloat(taskId, 'error', 0, error.message);
            showNotification(`${bvid} 转写失败: ${error.message}`, 'error');
        }

        // 从队列中移除已处理的
        transcribeQueue.shift();
        updateTranscribeQueueDisplay();
        renderVideoList();

        // 短暂延迟，避免请求过快
        await new Promise(r => setTimeout(r, 1000));
    }

    isTranscribing = false;
    updateTranscribeQueueDisplay();
    renderVideoList();
}


function waitForTranscribeComplete(taskId, bvid) {
    return new Promise((resolve) => {
        const poll = async () => {
            try {
                const response = await fetch(`/api/transcribe/status/${taskId}`);
                const status = await response.json();

                const displayTaskId = `transcribe_${bvid}`;
                const progress = Math.round(status.progress || 0);

                updateTaskInFloat(displayTaskId, status.status, progress, status.message);

                if (status.status === 'completed') {
                    videoDetails[bvid] = videoDetails[bvid] || {};
                    videoDetails[bvid].transcript = status.text;
                    saveData();
                    renderVideoList();
                    loadDownloadedInfo();
                    resolve();
                } else if (status.status === 'error') {
                    resolve();
                } else {
                    setTimeout(poll, 2000);
                }
            } catch (error) {
                console.error('获取转写状态失败:', error);
                setTimeout(poll, 3000);
            }
        };
        poll();
    });
}


// 批量转写
function batchTranscribe() {
    if (selectedVideos.size === 0) {
        showNotification('请先选择视频', 'warning');
        return;
    }

    let addedCount = 0;
    for (const bvid of selectedVideos) {
        const detail = videoDetails[bvid] || {};

        // 检查是否已有转写
        if (detail.transcript) continue;

        // 检查是否有音频文件
        const hasAudio = detail.files && detail.files.some(f =>
            f.name.endsWith('.m4a') || f.name.endsWith('.mp3') || f.name.endsWith('.mp4')
        );

        if (!hasAudio) continue;

        // 检查是否已在队列中
        if (transcribeQueue.find(item => item.bvid === bvid)) continue;

        transcribeQueue.push({ bvid });
        addedCount++;
    }

    if (addedCount > 0) {
        showNotification(`已添加 ${addedCount} 个视频到转写队列`, 'success');
        document.getElementById('task-float').classList.remove('hidden');
        updateTranscribeQueueDisplay();
        renderVideoList();

        if (!isTranscribing) {
            processTranscribeQueue();
        }
    } else {
        showNotification('没有可转写的视频（需先下载音频）', 'warning');
    }
}


// ========== AI总结功能 ==========
async function singleSummary(bvid) {
    const detail = videoDetails[bvid];
    if (!detail || !detail.transcript) {
        showNotification('请先转写视频', 'warning');
        return;
    }

    const apiKey = document.getElementById('api-key').value.trim();
    if (!apiKey) {
        showNotification('请在设置中配置API Key', 'warning');
        togglePanel('settings');
        return;
    }

    showNotification('正在生成总结...', 'info');

    try {
        const response = await fetch('/api/summarize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                text: detail.transcript,
                base_url: document.getElementById('api-base-url').value,
                api_key: apiKey,
                model: document.getElementById('api-model').value,
                prompt: document.getElementById('summary-prompt').value,
                include_timestamps: document.getElementById('include-timestamps').checked
            })
        });

        const data = await response.json();

        if (data.error) {
            showNotification('总结失败: ' + data.error, 'error');
            return;
        }

        videoDetails[bvid].summary = data.summary;
        saveData();
        renderVideoList();
        showNotification('总结完成', 'success');

    } catch (error) {
        showNotification('总结请求失败: ' + error.message, 'error');
    }
}


async function batchSummary() {
    if (selectedVideos.size === 0) {
        showNotification('请先选择视频', 'warning');
        return;
    }

    const apiKey = document.getElementById('api-key').value.trim();
    if (!apiKey) {
        showNotification('请在设置中配置API Key', 'warning');
        togglePanel('settings');
        return;
    }

    let count = 0;
    for (const bvid of selectedVideos) {
        const detail = videoDetails[bvid];
        if (detail && detail.transcript && !detail.summary) {
            await singleSummary(bvid);
            count++;
            await new Promise(r => setTimeout(r, 1000));
        }
    }

    if (count === 0) {
        showNotification('没有可总结的视频（需先转写）', 'warning');
    }
}


// ========== 任务浮窗 ==========
function addTaskToFloat(taskId, type, info) {
    const container = document.getElementById('task-float-body');

    // 检查是否已存在
    if (document.getElementById(`task-${taskId}`)) {
        return;
    }

    const typeLabels = {
        'download': '📥 下载',
        'transcribe': '🎤 转写'
    };

    const taskHtml = `
        <div class="task-item" id="task-${taskId}">
            <div class="task-item-header">
                <span class="task-item-title">${typeLabels[type] || type} - ${escapeHtml(String(info))}</span>
                <span class="task-item-status status-running" id="status-${taskId}">进行中</span>
            </div>
            <div class="task-item-progress">
                <div class="task-item-progress-fill" id="progress-${taskId}" style="width: 0%"></div>
            </div>
            <div class="task-item-message" id="message-${taskId}">准备中...</div>
        </div>
    `;

    container.insertAdjacentHTML('beforeend', taskHtml);
}


function updateTaskInFloat(taskId, status, progress, message) {
    const statusEl = document.getElementById(`status-${taskId}`);
    const progressEl = document.getElementById(`progress-${taskId}`);
    const messageEl = document.getElementById(`message-${taskId}`);

    if (!statusEl) return;

    progressEl.style.width = `${progress}%`;
    messageEl.textContent = message || '';

    if (status === 'completed') {
        statusEl.textContent = '完成';
        statusEl.className = 'task-item-status status-completed';
    } else if (status === 'error') {
        statusEl.textContent = '失败';
        statusEl.className = 'task-item-status status-error';
    } else {
        statusEl.textContent = '进行中';
        statusEl.className = 'task-item-status status-running';
    }
}


function toggleTaskFloat() {
    const body = document.getElementById('task-float-body');
    const btn = document.getElementById('task-float-toggle');

    if (body.classList.contains('collapsed')) {
        body.classList.remove('collapsed');
        btn.textContent = '−';
    } else {
        body.classList.add('collapsed');
        btn.textContent = '+';
    }
}


async function pollDownloadStatus(taskId) {
    const poll = async () => {
        try {
            const response = await fetch(`/api/download/status/${taskId}`);
            const status = await response.json();

            const progress = Math.round(status.progress || 0);
            updateTaskInFloat(taskId, status.status, progress, status.message);

            if (status.status === 'completed') {
                const bvid = taskId.split('_')[0];
                // 重新加载该视频的详情
                if (videoDetails[bvid]) {
                    videoDetails[bvid].loaded = false;
                }
                loadVideoDetail(bvid);
                loadDownloadedInfo();
            } else if (status.status !== 'error') {
                setTimeout(poll, 1000);
            }
        } catch (error) {
            console.error('获取下载状态失败:', error);
            setTimeout(poll, 2000);
        }
    };
    poll();
}


// ========== 数据管理 ==========
function saveData() {
    try {
        localStorage.setItem('bilibili_tool_videos', JSON.stringify(allVideos));
        localStorage.setItem('bilibili_tool_details', JSON.stringify(videoDetails));
    } catch (e) {
        console.error('保存数据失败:', e);
    }
}


function loadSavedData() {
    try {
        const savedVideos = localStorage.getItem('bilibili_tool_videos');
        const savedDetails = localStorage.getItem('bilibili_tool_details');

        if (savedVideos) {
            allVideos = JSON.parse(savedVideos);
        }
        if (savedDetails) {
            videoDetails = JSON.parse(savedDetails);
        }

        renderVideoList();
    } catch (error) {
        console.error('加载保存的数据失败:', error);
    }
}


async function loadDownloadedInfo() {
    try {
        const response = await fetch('/api/downloads');
        const data = await response.json();

        if (data.downloads) {
            data.downloads.forEach(item => {
                if (!videoDetails[item.bvid]) {
                    videoDetails[item.bvid] = {};
                }
                videoDetails[item.bvid].files = item.files;
                videoDetails[item.bvid].hasAudio = item.has_audio;
                videoDetails[item.bvid].hasVideo = item.has_video;
                videoDetails[item.bvid].hasTranscript = item.has_transcript;
            });
            saveData();
            renderVideoList();
        }
    } catch (error) {
        console.error('加载下载信息失败:', error);
    }
}


function exportData() {
    if (allVideos.length === 0) {
        showNotification('暂无数据可导出', 'warning');
        return;
    }

    window.location.href = '/api/crawler/download';
    showNotification('正在导出Excel...', 'info');
}


function clearAllData() {
    if (!confirm('确定要清空所有数据吗？这将清除搜索结果和缓存，但不会删除已下载的文件。')) {
        return;
    }

    allVideos = [];
    videoDetails = {};
    selectedVideos.clear();
    expandedCard = null;
    currentPage = 1;
    transcribeQueue = [];
    isTranscribing = false;

    localStorage.removeItem('bilibili_tool_videos');
    localStorage.removeItem('bilibili_tool_details');

    renderVideoList();
    updateSelectedCount();
    updateTranscribeQueueDisplay();
    showNotification('数据已清空', 'success');
}


// ========== 全局函数暴露 ==========
window.removeKeyword = removeKeyword;
window.handleCheckboxClick = handleCheckboxClick;
window.handleCardClick = handleCardClick;
window.quickDownload = quickDownload;
window.addToTranscribeQueue = addToTranscribeQueue;
window.removeFromTranscribeQueue = removeFromTranscribeQueue;
window.singleSummary = singleSummary;

