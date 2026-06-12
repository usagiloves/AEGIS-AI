/* ==========================================================================
   Aegis AI Employee System - Frontend JavaScript (Workflow Node-Graph Engine)
   ========================================================================== */

// Dynamic URL configuration to prevent cross-origin/sandbox WebSocket blocks
const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
const WS_URL = window.location.host ? `${protocol}//${window.location.host}/ws` : "ws://127.0.0.1:8000/ws";
const API_URL = window.location.host ? `${window.location.protocol}//${window.location.host}` : "http://127.0.0.1:8000";

// Robust localStorage helper functions to prevent SecurityError script crashes
function getLocalStorageItem(key) {
    try {
        return localStorage.getItem(key);
    } catch (e) {
        console.warn("localStorage is not accessible:", e);
        return null;
    }
}

function setLocalStorageItem(key, value) {
    try {
        localStorage.setItem(key, value);
    } catch (e) {
        console.warn("localStorage is not accessible:", e);
    }
}

// Schema version - khi nâng cấp schema nodes (thêm node mới), tăng số này để auto-reset cache
const SCHEMA_VERSION = "v4.0";
const storedVersion = getLocalStorageItem("aegis_schema_version");
if (storedVersion !== SCHEMA_VERSION) {
    // Phiên bản không khớp → Xóa cache cũ để load lại vị trí node mới mặc định
    try {
        localStorage.removeItem("aegis_node_graph_config");
    } catch (e) {}
    setLocalStorageItem("aegis_schema_version", SCHEMA_VERSION);
    console.log(`[Aegis] Schema nâng cấp lên ${SCHEMA_VERSION} - Đặt lại vị trí Node về mặc định.`);
}

let socket = null;
let reconnectTimer = null;
let currentPlan = [];
let lastSrtContent = "";

// --- KHỞI TẠO DỮ LIỆU SƠ ĐỒ NODE-GRAPH (Aegis Workflow) ---
let nodes = {
    downloader: { 
        id: "downloader", name: "Video Downloader", icon: "📥", 
        x: 30, y: 220, type: "downloader", desc: "Tải Video từ URL", 
        status: "idle", bypassed: false, inputs: [], outputs: ["video"], 
        config: { quality: "best" } 
    },
    audio: { 
        id: "audio", name: "Audio Extract", icon: "🔊", 
        x: 230, y: 220, type: "audio", desc: "Tách & Khử nhiễu AI", 
        status: "idle", bypassed: false, inputs: ["video"], outputs: ["audio"], 
        config: { enhance: true } 
    },
    subtitle: { 
        id: "subtitle", name: "Subtitle ASR", icon: "🎙️", 
        x: 430, y: 220, type: "subtitle", desc: "Whisper Nhận diện giọng", 
        status: "idle", bypassed: false, inputs: ["audio"], outputs: ["srt"], 
        config: { offset: 0.0, model: "large-v3", device: "cuda" } 
    },
    translator: { 
        id: "translator", name: "LLM Translator", icon: "🌐", 
        x: 630, y: 220, type: "translator", desc: "Dịch thuật bằng AI", 
        status: "idle", bypassed: false, inputs: ["srt"], outputs: ["srt_trans"], 
        config: { target_lang: "Tiếng Việt", provider: "ollama", model: "deepseek-r1:8b", api_base: "http://localhost:11434", api_key: "" } 
    },
    voiceover: {
        id: "voiceover", name: "Voiceover TTS", icon: "🗣️",
        x: 830, y: 390, type: "voiceover", desc: "Lồng tiếng AI TTS",
        status: "idle", bypassed: true, inputs: ["srt_trans"], outputs: ["audio"],
        config: { 
            engine: "edge", 
            voice: "vi-VN-HoaiMyNeural", 
            emotion: "neutral", 
            api_key_openai: "", 
            api_key_elevenlabs: "", 
            mix_ratio: 70 
        }
    },
    exporter: { 
        id: "exporter", name: "Exporter Burn", icon: "🎬", 
        x: 830, y: 220, type: "exporter", desc: "Nhúng phụ đề ASS", 
        status: "idle", bypassed: false, inputs: ["video", "srt_trans"], outputs: ["subbed_video"], 
        config: { fontname: "Outfit", fontsize: 16, color: "#00FFFF", outline_color: "#000000", borderstyle: "1", dual_subtitles: false } 
    },
    seo: {
        id: "seo", name: "SEO & Marketing", icon: "📝",
        x: 830, y: 50, type: "seo", desc: "AI SEO & Social Post",
        status: "idle", bypassed: false, inputs: ["srt_trans"], outputs: ["seo_post"],
        config: { platform: "youtube", tone: "engaging" }
    },
    clipper: {
        id: "clipper", name: "Shorts Clipper", icon: "✂️",
        x: 1030, y: 220, type: "clipper", desc: "Cắt Highlights Shorts",
        status: "idle", bypassed: true, inputs: ["subbed_video"], outputs: ["shorts_video"],
        config: { duration: 60, aspect_ratio: "9:16" }
    },
    uploader: {
        id: "uploader", name: "Cloud Publisher", icon: "📤",
        x: 1230, y: 220, type: "uploader", desc: "Đăng tải Google/Telegram/YouTube",
        status: "idle", bypassed: false, inputs: ["subbed_video", "audio", "seo_post", "shorts_video"], outputs: [],
        config: { gdrive: false, telegram: false, youtube: false, privacy_status: "private" }
    }
};

let connections = [
    { from: "downloader", to: "audio" },
    { from: "audio", to: "subtitle" },
    { from: "subtitle", to: "translator" },
    { from: "translator", to: "exporter" },
    { from: "translator", to: "voiceover" },
    { from: "translator", to: "seo" },
    { from: "exporter", to: "clipper" },
    { from: "exporter", to: "uploader" },
    { from: "voiceover", to: "uploader" },
    { from: "seo", to: "uploader" },
    { from: "clipper", to: "uploader" }
];

let selectedNodeId = null;

// UI Elements
const goalInput = document.getElementById("goal-input");
const launchBtn = document.getElementById("launch-btn");
const agentStatusBadge = document.getElementById("agent-status-badge");
const agentStatusText = document.getElementById("agent-status-text");
const terminalBody = document.getElementById("terminal-body");
const thinkingBody = document.getElementById("thinking-body");

// Live Monitor is replaced by high-end Modal Overlays and Approval screenshots:
const modalBrowserContainer = document.getElementById("modal-browser-container");
const modalBrowserScreenshot = document.getElementById("modal-browser-screenshot");
const modalBrowserStepBadge = document.getElementById("modal-browser-step-badge");

const videoPlayerModal = document.getElementById("video-player-modal");
const videoPlayer = document.getElementById("video-player");
const closeVideoBtn = document.getElementById("close-video-btn");

// Metrics elements
const cpuCircle = document.getElementById("cpu-circle");
const ramCircle = document.getElementById("ram-circle");
const gpuCircle = document.getElementById("gpu-circle-chart");
const cpuValText = document.getElementById("cpu-val-text");
const ramValText = document.getElementById("ram-val-text");
const gpuValText = document.getElementById("gpu-val-text");
const gpuNameText = document.getElementById("gpu-name-text");

// Downloader elements
const dlProgressContainer = document.getElementById("downloader-progress-container");
const dlFilename = document.getElementById("dl-filename");
const dlPercentage = document.getElementById("dl-percentage");
const dlFillBar = document.getElementById("dl-fill-bar");
const dlSpeed = document.getElementById("dl-speed");
const dlEta = document.getElementById("dl-eta");

// Tab elements
const logTabs = document.querySelectorAll(".log-tab");
const terminalLog = document.getElementById("terminal-log");
const thinkingLog = document.getElementById("thinking-log");

// Modal elements
const approvalModal = document.getElementById("approval-modal");
const approvalStepTitle = document.getElementById("approval-step-title");
const approvalStepDesc = document.getElementById("approval-step-desc");
const rejectComment = document.getElementById("reject-comment");
const modalApproveBtn = document.getElementById("modal-approve-btn");
const modalRejectBtn = document.getElementById("modal-reject-btn");

// Subtitle Editor Modal elements
const subEditorModal = document.getElementById("subtitle-editor-modal");
const subEditorList = document.getElementById("subtitle-editor-list");
const subEditorSaveBtn = document.getElementById("editor-save-btn");
const editorCancelBtn = document.getElementById("editor-cancel-btn");

// Sidebar Cấu Hình Node Elements
const nodeConfigSidebar = document.getElementById("node-config-sidebar");
const sidebarNodeIcon = document.getElementById("sidebar-node-icon");
const sidebarNodeTitle = document.getElementById("sidebar-node-title");
const sidebarConfigFields = document.getElementById("sidebar-config-fields");
const sidebarCloseBtn = document.getElementById("sidebar-close-btn");
const resetLayoutBtn = document.getElementById("reset-layout-btn");

const canvasContainer = document.getElementById("workflow-canvas-container");
const canvasInner = document.getElementById("workflow-inner-canvas");
const canvasNodes = document.getElementById("workflow-nodes");
const canvasSvg = document.getElementById("workflow-svg");
const canvasScrollHint = document.getElementById("canvas-scroll-hint");

// Connect to WebSocket
function connectWebSocket() {
    try {
        appendLog("Đang kết nối đến máy chủ Aegis AI...", "info");
        
        socket = new WebSocket(WS_URL);
        
        socket.onopen = () => {
            appendLog("Đã kết nối thành công với máy chủ!", "success");
            clearTimeout(reconnectTimer);
            // Gửi cấu hình hiện tại lên server để đồng bộ lưu trữ
            socket.send(JSON.stringify({
                type: "save_webui_config",
                data: { nodes: nodes, connections: connections }
            }));
        };
        
        socket.onmessage = (event) => {
            const message = JSON.parse(event.data);
            handleServerMessage(message.type, message.data);
        };
        
        socket.onclose = () => {
            appendLog("Mất kết nối với máy chủ. Tự động kết nối lại sau 3 giây...", "warning");
            updateAgentStatus("failed");
            reconnectTimer = setTimeout(connectWebSocket, 3000);
        };
        
        socket.onerror = (err) => {
            console.error("WebSocket Error:", err);
        };
    } catch (e) {
        console.error("Lỗi khởi tạo WebSocket:", e);
        appendLog(`Không thể kết nối đến máy chủ: ${e.message}`, "error");
        updateAgentStatus("failed");
        reconnectTimer = setTimeout(connectWebSocket, 3000);
    }
}

// Handle Server Messages
function handleServerMessage(type, data) {
    switch (type) {
        case "init_state":
            updateAgentStatus(data.status);
            if (data.goal) {
                goalInput.value = data.goal;
            }
            if (data.nodes_data && Object.keys(data.nodes_data).length > 0) {
                Object.keys(data.nodes_data).forEach(key => {
                    if (nodes[key]) {
                        nodes[key].bypassed = !!data.nodes_data[key].bypassed;
                        if (data.nodes_data[key].config) {
                            nodes[key].config = Object.assign({}, nodes[key].config, data.nodes_data[key].config);
                        }
                    }
                });
                syncToolbarCheckboxes();
            }
            if (data.plan && data.plan.length > 0) {
                syncNodesStateWithPlan(data.plan, data.step_index);
            } else {
                renderNodes();
                drawConnections();
            }
            loadMediaLibrary();
            break;
            
        case "sync_nodes":
            if (data.nodes_data && Object.keys(data.nodes_data).length > 0) {
                Object.keys(data.nodes_data).forEach(key => {
                    if (nodes[key]) {
                        nodes[key].bypassed = !!data.nodes_data[key].bypassed;
                        if (data.nodes_data[key].config) {
                            nodes[key].config = Object.assign({}, nodes[key].config, data.nodes_data[key].config);
                        }
                    }
                });
                renderNodes();
                drawConnections();
                syncToolbarCheckboxes();
            }
            break;
            
        case "status_change":
            updateAgentStatus(data.status);
            if (data.status === "completed") {
                loadMediaLibrary();
                // Kích hoạt sáng bừng tất cả node hoàn thành thành công
                Object.keys(nodes).forEach(k => {
                    if (!nodes[k].bypassed) {
                        nodes[k].status = "success";
                    }
                });
                renderNodes();
                drawConnections();
            }
            break;
            
        case "log":
            appendLog(data.message, data.level);
            break;
            
        case "thinking":
            thinkingBody.innerText = data.thinking;
            thinkingBody.scrollTop = thinkingBody.scrollHeight;
            break;
            
        case "plan_created":
            syncNodesStateWithPlan(data.steps, 0);
            break;
            
        case "step_start":
            highlightActiveNode(data.index);
            break;
            
        case "step_complete":
            markNodeStatus(data.index, "success");
            break;
            
        case "step_fail":
            markNodeStatus(data.index, "failed");
            break;
            
        case "downloader_progress":
            updateDownloaderProgress(data);
            break;
            
        case "browser_screenshot":
            showBrowserStream(data.image, data.step);
            break;
            
        case "approval_required":
            showApprovalModal(data);
            break;
            
        case "metrics":
            updateSystemMetrics(data);
            break;
            
        case "srt_ready":
            loadSrtIntoEditor(data.srt_content);
            break;
            
        case "seo_ready":
            document.getElementById("seo-body").innerText = data.seo_content;
            switchLogTab("seo");
            break;
            
        default:
            console.log("Unknown event:", type, data);
    }
}

// Sync node states on client with current orchestrator pipeline steps
function syncNodesStateWithPlan(steps, activeIndex) {
    currentPlan = steps;
    
    // Đặt lại tất cả node về mặc định
    Object.keys(nodes).forEach(k => {
        nodes[k].status = "idle";
    });
    
    steps.forEach((step, idx) => {
        let nodeId = getMappedNodeId(step.module, step.action);
        if (!nodeId) return;
        
        if (idx === activeIndex) {
            nodes[nodeId].status = "running";
        } else if (idx < activeIndex) {
            nodes[nodeId].status = "success";
        } else {
            nodes[nodeId].status = "idle";
        }
    });
    
    renderNodes();
    drawConnections();
}

function getMappedNodeId(moduleName, actionName) {
    if (moduleName === "downloader") return "downloader";
    if (moduleName === "audio") return "audio";
    if (moduleName === "subtitle") {
        if (actionName === "transcribe") return "subtitle";
        if (actionName === "translate") return "translator";
        if (actionName === "burn") return "exporter";
    }
    if (moduleName === "voiceover") return "voiceover";
    if (moduleName === "clipper") return "clipper";
    if (moduleName === "seo") return "seo";
    if (moduleName === "uploader") return "uploader";
    return null;
}

// Highlight running Node on the SVG Graph
function highlightActiveNode(stepIndex) {
    if (!currentPlan[stepIndex]) return;
    let step = currentPlan[stepIndex];
    let nodeId = getMappedNodeId(step.module, step.action);
    
    currentPlan.forEach((s, idx) => {
        let nId = getMappedNodeId(s.module, s.action);
        if (!nId) return;
        
        if (idx === stepIndex) {
            nodes[nId].status = "running";
        } else if (idx < stepIndex) {
            nodes[nId].status = "success";
        } else {
            nodes[nId].status = "idle";
        }
    });
    
    renderNodes();
    drawConnections();
}

// Mark Node execution status
function markNodeStatus(stepIndex, status) {
    if (!currentPlan[stepIndex]) return;
    let step = currentPlan[stepIndex];
    let nodeId = getMappedNodeId(step.module, step.action);
    if (nodeId) {
        nodes[nodeId].status = status;
        renderNodes();
        drawConnections();
    }
}

// Update system metrics animated SVG circles
function updateSystemMetrics(metrics) {
    if (metrics.cpu !== undefined) {
        cpuCircle.setAttribute("stroke-dasharray", `${metrics.cpu}, 100`);
        cpuValText.innerText = `${Math.round(metrics.cpu)}%`;
    }
    if (metrics.ram !== undefined) {
        ramCircle.setAttribute("stroke-dasharray", `${metrics.ram}, 100`);
        ramValText.innerText = `${Math.round(metrics.ram)}%`;
    }
    if (metrics.gpu !== undefined) {
        gpuCircle.setAttribute("stroke-dasharray", `${metrics.gpu}, 100`);
        gpuValText.innerText = `${Math.round(metrics.gpu)}%`;
    }
    if (metrics.gpu_name) {
        gpuNameText.innerText = `${metrics.gpu_name} (${metrics.gpu_vram})`;
    }
}

// Update Agent Status UI
function updateAgentStatus(status) {
    agentStatusBadge.className = "status-indicator";
    
    let text = "ĐANG CHỜ";
    let statusClass = "status-idle";
    
    switch (status) {
        case "idle":
            text = "ĐANG CHỜ";
            statusClass = "status-idle";
            break;
        case "queued":
            text = "ĐANG XẾP HÀNG";
            statusClass = "status-planning";
            break;
        case "planning":
            text = "LẬP KẾ HOẠCH";
            statusClass = "status-planning";
            break;
        case "running":
            text = "ĐANG VẬN HÀNH";
            statusClass = "status-running";
            break;
        case "waiting_approval":
            text = "KIỂM DUYỆT";
            statusClass = "status-waiting";
            break;
        case "completed":
            text = "HOÀN THÀNH";
            statusClass = "status-completed";
            break;
        case "failed":
            text = "LỖI HỆ THỐNG";
            statusClass = "status-failed";
            break;
    }
    
    agentStatusBadge.classList.add(statusClass);
    agentStatusText.innerText = text;
}

// Play Subtitle-Embedded Video
function playResultVideo(filepath) {
    const filename = filepath.split(/[\\/]/).pop();
    const videoUrl = `${API_URL}/output/${encodeURIComponent(filename)}`;
    
    appendLog(`Phát hiện video đầu ra: ${filename}. Đang nạp vào Trình phát video...`, "success");
    
    videoPlayerModal.classList.remove("hidden");
    videoPlayer.src = videoUrl;
    videoPlayer.load();
    videoPlayer.play().catch(e => {
        console.warn("Tự động phát bị chặn bởi chính sách bảo mật trình duyệt, người dùng có thể nhấp Phát thủ công.", e);
    });
}

// Show Browser Agent Screenshots inside the HITL Approval Modal
function showBrowserStream(base64Image, step) {
    modalBrowserContainer.classList.remove("hidden");
    modalBrowserScreenshot.src = `data:image/png;base64,${base64Image}`;
    modalBrowserStepBadge.innerText = `Bước ${step}`;
}

// Subtitle parser
function parseSrt(srtText) {
    const segments = [];
    const blocks = srtText.trim().split(/\n\s*\n/);
    blocks.forEach(block => {
        const lines = block.trim().split('\n');
        if (lines.length >= 3) {
            const index = lines[0].trim();
            const timeLine = lines[1].trim();
            const text = lines.slice(2).join('\n').trim();
            
            const times = timeLine.split('-->');
            if (times.length === 2) {
                segments.push({
                    index: index,
                    start: times[0].trim(),
                    end: times[1].trim(),
                    text: text
                });
            }
        }
    });
    return segments;
}

// Rebuild SRT text from interactive editor fields
function rebuildSrt() {
    const rows = document.querySelectorAll(".editor-row");
    let srtContent = "";
    rows.forEach(row => {
        const idx = row.querySelector(".editor-index").innerText;
        const start = row.querySelector(".editor-start-time").value;
        const end = row.querySelector(".editor-end-time").value;
        const text = row.querySelector(".editor-text-val").value;
        
        srtContent += `${idx}\n${start} --> ${end}\n${text}\n\n`;
    });
    return srtContent.trim();
}

// Load SRT data and render inside the Subtitle Editor Panel Modal
function loadSrtIntoEditor(srtContent) {
    lastSrtContent = srtContent;
    setLocalStorageItem("aegis_last_srt", srtContent);
    const segments = parseSrt(srtContent);
    subEditorList.innerHTML = "";
    
    if (segments.length === 0) {
        subEditorList.innerHTML = '<div class="empty-state">Không có nội dung phụ đề nào được tạo.</div>';
        return;
    }
    
    segments.forEach(seg => {
        const row = document.createElement("div");
        row.className = "editor-row";
        row.innerHTML = `
            <div class="editor-index">${seg.index}</div>
            <div class="editor-time-inputs">
                <input type="text" class="editor-start-time" value="${seg.start}">
                <input type="text" class="editor-end-time" value="${seg.end}">
            </div>
            <div class="editor-text-input">
                <input type="text" class="editor-text-val" value="${seg.text}">
            </div>
        `;
        subEditorList.appendChild(row);
    });
    
    subEditorModal.classList.remove("hidden");
    appendLog("Phụ đề đã sẵn sàng! Đang mở Trình Biên Tập Phụ Đề Tương Tác cho bạn chỉnh sửa.", "info");
}

// Update Downloader Progress UI
function updateDownloaderProgress(data) {
    if (data.status === "downloading") {
        dlProgressContainer.classList.remove("hidden");
        dlFilename.innerText = data.filename;
        dlPercentage.innerText = `${data.percentage}%`;
        dlFillBar.style.width = `${data.percentage}%`;
        dlSpeed.innerText = `${data.speed_mb} MB/s`;
        dlEta.innerText = `Còn lại: ${data.eta}s`;
    } else if (data.status === "finished") {
        dlPercentage.innerText = "100%";
        dlFillBar.style.width = "100%";
        appendLog(`Đã tải xong file video: ${data.filename}`, "success");
        
        setTimeout(() => {
            dlProgressContainer.classList.add("hidden");
        }, 2000);
    }
}

// Append logs to terminal
function appendLog(message, level = "normal", isReconstructed = false, customTime = null) {
    const line = document.createElement("div");
    line.className = `log-line ${level}`;
    
    const time = customTime || new Date().toLocaleTimeString();
    line.innerHTML = `<span style="color: #6b7280;">[${time}]</span> ${message}`;
    
    terminalBody.appendChild(line);
    terminalBody.scrollTop = terminalBody.scrollHeight;
    
    if (!isReconstructed) {
        try {
            let logs = JSON.parse(sessionStorage.getItem("aegis_session_logs") || "[]");
            logs.push({ message: message, level: level, time: time });
            if (logs.length > 500) logs.shift();
            sessionStorage.setItem("aegis_session_logs", JSON.stringify(logs));
        } catch (e) {}
    }
}

// Human-in-the-loop Security Approval Modal
function showApprovalModal(data) {
    approvalStepTitle.innerText = data.title;
    approvalStepDesc.innerText = data.description;
    rejectComment.value = "";
    
    // Tự động kiểm tra hiển thị phần Review SEO & Tiêu đề
    const seoReviewBox = document.getElementById("seo-review-box");
    if (seoReviewBox) {
        if (data.is_seo_review) {
            seoReviewBox.classList.remove("hidden");
            document.getElementById("seo-review-title").value = data.seo_title || "";
            document.getElementById("seo-review-desc").value = data.seo_desc || "";
        } else {
            seoReviewBox.classList.add("hidden");
        }
    }
    
    approvalModal.classList.remove("hidden");
}

function hideApprovalModal() {
    approvalModal.classList.add("hidden");
}

// --- Dynamic Subtitle Style Generator Helpers ---
function hexToAssColor(hexStr) {
    const r = hexStr.substring(1, 3);
    const g = hexStr.substring(3, 5);
    const b = hexStr.substring(5, 7);
    return `&H00${b}${g}${r}`;
}

// --- VANILLA NODE-GRAPH ENGINE (Aegis style) ---

// Sắp xếp lại vị trí các Node về mặc định
function resetLayout() {
    const defaults = {
        downloader: { x: 30, y: 220, bypassed: false },
        audio: { x: 230, y: 220, bypassed: false },
        subtitle: { x: 430, y: 220, bypassed: false },
        translator: { x: 630, y: 220, bypassed: false },
        seo: { x: 830, y: 50, bypassed: false },
        exporter: { x: 830, y: 220, bypassed: false },
        voiceover: { x: 830, y: 390, bypassed: false },
        clipper: { x: 1030, y: 220, bypassed: false },
        uploader: { x: 1230, y: 220, bypassed: false }
    };
    Object.keys(defaults).forEach(key => {
        if (nodes[key]) {
            nodes[key].x = defaults[key].x;
            nodes[key].y = defaults[key].y;
            nodes[key].bypassed = defaults[key].bypassed;
        }
    });
    renderNodes();
    drawConnections();
    saveConfigToStorage();
}

// Vẽ HTML Nodes
function renderNodes() {
    canvasNodes.innerHTML = "";
    
    Object.keys(nodes).forEach(key => {
        const node = nodes[key];
        const nodeEl = document.createElement("div");
        nodeEl.className = `workflow-node ${node.status || 'idle'} ${node.bypassed ? 'bypassed' : ''} ${selectedNodeId === node.id ? 'selected' : ''}`;
        nodeEl.id = `node-${node.id}`;
        nodeEl.style.left = `${node.x}px`;
        nodeEl.style.top = `${node.y}px`;
        
        let statusBadge = "Đang chờ ⏳";
        if (node.status === "running") statusBadge = "Đang chạy ⚡";
        if (node.status === "success") statusBadge = "Hoàn thành ✅";
        if (node.status === "failed") statusBadge = "Lỗi ❌";
        if (node.bypassed) statusBadge = "Bỏ qua 👁️";

        nodeEl.innerHTML = `
            <!-- Bypass Toggle button -->
            <button class="node-bypass-btn" title="Bật/Tắt module này" onclick="event.stopPropagation(); toggleNodeBypass('${node.id}')">
                ${node.bypassed ? '👁️' : '✕'}
            </button>
            
            <div class="node-header ${node.type}">
                <span class="node-icon">${node.icon}</span>
                <span>${node.name}</span>
            </div>
            <div class="node-body">
                <div>${node.desc}</div>
                <div class="node-status-text">${statusBadge}</div>
            </div>
        `;
        
        // Thêm Handles cổng kết nối nếu không bị bypassed
        if (!node.bypassed) {
            if (node.inputs.length > 0) {
                const inPort = document.createElement("div");
                inPort.className = "node-handle node-handle-input";
                inPort.id = `handle-input-${node.id}`;
                nodeEl.appendChild(inPort);
            }
            if (node.outputs.length > 0) {
                const outPort = document.createElement("div");
                outPort.className = "node-handle node-handle-output";
                outPort.id = `handle-output-${node.id}`;
                nodeEl.appendChild(outPort);
            }
        }

        // Lập sự kiện Click chọn Node -> hiển thị Panel cấu hình
        nodeEl.addEventListener("click", (e) => {
            e.stopPropagation();
            selectNode(node.id);
        });

        // Kéo thả Node (Drag & Drop)
        nodeEl.addEventListener("mousedown", (e) => {
            if (e.target.classList.contains("node-bypass-btn") || e.target.classList.contains("node-handle")) return;
            e.preventDefault();
            e.stopPropagation();
            
            nodeEl.style.cursor = "grabbing";
            
            // Ghi nhớ vị trí chuột BAN ĐẦU - tính theo cả scroll offset của canvas
            const startX = e.clientX + (canvasContainer ? canvasContainer.scrollLeft : 0);
            const startY = e.clientY + (canvasContainer ? canvasContainer.scrollTop : 0);
            const originalX = node.x;
            const originalY = node.y;
            
            function onMouseMove(moveEvent) {
                // Tính toạ độ chuột hiện tại có tính scroll offset
                const currentX = moveEvent.clientX + (canvasContainer ? canvasContainer.scrollLeft : 0);
                const currentY = moveEvent.clientY + (canvasContainer ? canvasContainer.scrollTop : 0);
                const dx = currentX - startX;
                const dy = currentY - startY;
                
                // Đảm bảo node nằm trong biên inner Canvas
                const innerW = canvasInner ? canvasInner.scrollWidth : 1600;
                const innerH = canvasInner ? canvasInner.scrollHeight : 600;
                node.x = Math.max(10, Math.min(innerW - 185, originalX + dx));
                node.y = Math.max(10, Math.min(innerH - 115, originalY + dy));
                
                nodeEl.style.left = `${node.x}px`;
                nodeEl.style.top = `${node.y}px`;
                
                drawConnections();
            }
            
            function onMouseUp() {
                nodeEl.style.cursor = "grab";
                document.removeEventListener("mousemove", onMouseMove);
                document.removeEventListener("mouseup", onMouseUp);
                saveConfigToStorage();
            }
            
            document.addEventListener("mousemove", onMouseMove);
            document.addEventListener("mouseup", onMouseUp);
        });

        canvasNodes.appendChild(nodeEl);
    });
}

// Bật/tắt chế độ Bypass Node
window.toggleNodeBypass = function(nodeId) {
    nodes[nodeId].bypassed = !nodes[nodeId].bypassed;
    appendLog(`Đã ${nodes[nodeId].bypassed ? 'TẮT (Bypass)' : 'BẬT'} module ${nodes[nodeId].name}.`, "info");
    
    // Nếu Sidebar đang mở chính node này, đóng lại hoặc load lại
    if (selectedNodeId === nodeId) {
        closeSidebar();
    }
    
    renderNodes();
    drawConnections();
    saveConfigToStorage();
};

// Vẽ các đường cong Bezier SVG cho connections
function drawConnections() {
    // Xóa chỉ các path cũ (giữ nguyên <defs> chứa bộ lọc glow)
    Array.from(canvasSvg.querySelectorAll("path, circle")).forEach(el => el.remove());
    
    const rawConns = [
        { from: "downloader", to: "audio" },
        { from: "audio", to: "subtitle" },
        { from: "subtitle", to: "translator" },
        { from: "translator", to: "exporter" },
        { from: "translator", to: "voiceover" },
        { from: "translator", to: "seo" },
        { from: "exporter", to: "clipper" },
        { from: "exporter", to: "uploader" },
        { from: "voiceover", to: "uploader" },
        { from: "seo", to: "uploader" },
        { from: "clipper", to: "uploader" }
    ];
    
    // Tạo map nhanh: nodeId -> danh sách đầu ra trực tiếp
    const directOutputs = {};
    rawConns.forEach(c => {
        if (!directOutputs[c.from]) directOutputs[c.from] = [];
        directOutputs[c.from].push(c.to);
    });
    
    // Tìm node hoạt động đầu tiên không bị bypass tới được từ nodeId
    function resolveTarget(nodeId, visited = new Set()) {
        if (visited.has(nodeId)) return [];
        visited.add(nodeId);
        const node = nodes[nodeId];
        if (!node) return [];
        if (!node.bypassed) return [nodeId];  // Node này hoạt động
        // Node bị bypass: đi xuống các con
        const result = [];
        const outs = directOutputs[nodeId] || [];
        for (const out of outs) {
            result.push(...resolveTarget(out, new Set(visited)));
        }
        return result;
    }
    
    // Tạo danh sách các cạnh cần vẽ (dùng Set để tránh trùng lặp)
    const edgeSet = new Set();
    const activeConnections = [];
    
    rawConns.forEach(conn => {
        const fromNode = nodes[conn.from];
        if (!fromNode || fromNode.bypassed) return;
        
        // Tìm các đích đến thực tế (bao gồm skip qua bypass)
        const targets = resolveTarget(conn.to);
        targets.forEach(toId => {
            const edgeKey = `${conn.from}->${toId}`;
            if (!edgeSet.has(edgeKey)) {
                edgeSet.add(edgeKey);
                activeConnections.push({ from: conn.from, to: toId });
            }
        });
    });

    activeConnections.forEach(conn => {
        const fromNode = nodes[conn.from];
        const toNode = nodes[conn.to];
        
        if (!fromNode || !toNode) return;
        
        // Tính toạ độ từ handle output sang handle input
        const NODE_W = 185;
        const NODE_H = 90;
        const fromX = fromNode.x + NODE_W;
        const fromY = fromNode.y + NODE_H / 2;
        
        const toX = toNode.x;
        const toY = toNode.y + NODE_H / 2;
        
        // Vẽ đường cong Bezier
        const dist = Math.abs(toX - fromX);
        const controlOffset = Math.max(50, Math.min(150, dist * 0.45));
        const pathData = `M ${fromX} ${fromY} C ${fromX + controlOffset} ${fromY}, ${toX - controlOffset} ${toY}, ${toX} ${toY}`;
        
        const pathEl = document.createElementNS("http://www.w3.org/2000/svg", "path");
        pathEl.setAttribute("d", pathData);
        
        // Xác định class động dựa theo trạng thái node trước sau
        let stateClass = "idle";
        if (fromNode.status === "running") {
            stateClass = "running";
        } else if (fromNode.status === "success" && toNode.status === "running") {
            stateClass = "running";
        } else if (fromNode.status === "success" && toNode.status === "success") {
            stateClass = "success";
        } else if (fromNode.status === "success") {
            stateClass = "active";
        }
        
        pathEl.setAttribute("class", `edge-path ${stateClass}`);
        canvasSvg.appendChild(pathEl);
    });
}

// Chọn Node và mở Sidebar cấu hình
function selectNode(nodeId) {
    selectedNodeId = nodeId;
    renderNodes(); // Update selected style border
    
    const node = nodes[nodeId];
    sidebarNodeIcon.innerText = node.icon;
    sidebarNodeTitle.innerText = `Cấu hình: ${node.name}`;
    
    // Sinh các trường config động theo loại Node
    renderSidebarConfigFields(node);
    
    nodeConfigSidebar.classList.remove("hidden");
}

function closeSidebar() {
    selectedNodeId = null;
    nodeConfigSidebar.classList.add("hidden");
    renderNodes();
}

sidebarCloseBtn.addEventListener("click", closeSidebar);
resetLayoutBtn.addEventListener("click", resetLayout);

// Render các trường cấu hình động trong Sidebar dựa trên loại Node
function renderSidebarConfigFields(node) {
    sidebarConfigFields.innerHTML = "";
    
    if (node.bypassed) {
        sidebarConfigFields.innerHTML = `
            <div style="background: rgba(225, 29, 72, 0.08); border: 1px dashed var(--neon-red); border-radius: 8px; padding: 12px; font-size: 12px; color: var(--neon-red); font-weight: 700; text-align: center;">
                ⚠️ Module này đang ở trạng thái Tắt (Bypass). Hãy bật module bằng nút [👁️] ở góc node để cấu hình!
            </div>
        `;
        return;
    }
    
    const config = node.config;
    
    if (node.type === "downloader") {
        sidebarConfigFields.innerHTML = `
            <div class="form-group">
                <label for="cfg-quality">Chất lượng tải Video</label>
                <select id="cfg-quality">
                    <option value="720p" ${config.quality === "720p" ? 'selected' : ''}>Tối ưu tốc độ (720p HD)</option>
                    <option value="best" ${config.quality === "best" ? 'selected' : ''}>Chất lượng cao nhất (Best quality)</option>
                </select>
            </div>
        `;
        
        document.getElementById("cfg-quality").addEventListener("change", (e) => {
            config.quality = e.target.value;
            saveConfigToStorage();
        });
        
    } else if (node.type === "audio") {
        sidebarConfigFields.innerHTML = `
            <div class="form-group" style="flex-direction: row; align-items: center; gap: 8px;">
                <input type="checkbox" id="cfg-enhance" ${config.enhance ? 'checked' : ''} style="width: auto; cursor: pointer;">
                <label for="cfg-enhance" style="cursor: pointer; margin: 0;">Bật Khử Nhiễu & Tăng Cường Giọng Nói (AI Enhanced)</label>
            </div>
        `;
        
        document.getElementById("cfg-enhance").addEventListener("change", (e) => {
            config.enhance = e.target.checked;
            saveConfigToStorage();
        });
        
    } else if (node.type === "subtitle") {
        sidebarConfigFields.innerHTML = `
            <div class="form-group">
                <label for="cfg-offset">Độ lệch thời gian: <span id="cfg-offset-val" style="color: var(--neon-blue); font-weight: 700;">${parseFloat(config.offset).toFixed(1)}</span>s</label>
                <input type="range" id="cfg-offset" min="-2.0" max="2.0" step="0.1" value="${config.offset}">
                <span style="font-size: 10px; color: var(--text-muted);">Kéo sang phải để trì hoãn phụ đề nếu phụ đề hiện quá sớm.</span>
            </div>
            
            <div class="form-group">
                <label for="cfg-whisper-model">Mô hình Whisper</label>
                <select id="cfg-whisper-model">
                    <option value="large-v3" ${config.model === "large-v3" ? 'selected' : ''}>large-v3 (Độ chính xác tuyệt đối - Quadro 6000)</option>
                    <option value="medium" ${config.model === "medium" ? 'selected' : ''}>medium (Tốt)</option>
                    <option value="small" ${config.model === "small" ? 'selected' : ''}>small (Nhanh)</option>
                </select>
            </div>
        `;
        
        const offsetSlider = document.getElementById("cfg-offset");
        const offsetVal = document.getElementById("cfg-offset-val");
        offsetSlider.addEventListener("input", (e) => {
            config.offset = parseFloat(e.target.value);
            offsetVal.innerText = config.offset.toFixed(1);
            saveConfigToStorage();
        });
        
        document.getElementById("cfg-whisper-model").addEventListener("change", (e) => {
            config.model = e.target.value;
            saveConfigToStorage();
        });
        
    } else if (node.type === "translator") {
        sidebarConfigFields.innerHTML = `
            <div class="form-group">
                <label for="cfg-target-lang">Ngôn ngữ dịch mục tiêu</label>
                <input type="text" id="cfg-target-lang" value="${config.target_lang}" placeholder="Ví dụ: Tiếng Việt, English, Japanese...">
            </div>
            
            <div class="form-group">
                <label for="cfg-provider">LLM Provider</label>
                <select id="cfg-provider">
                    <option value="ollama" ${config.provider === "ollama" ? 'selected' : ''}>Ollama (Chạy local GPU)</option>
                    <option value="deepseek" ${config.provider === "deepseek" ? 'selected' : ''}>DeepSeek Cloud API</option>
                    <option value="openrouter" ${config.provider === "openrouter" ? 'selected' : ''}>OpenRouter (GPT/Claude)</option>
                </select>
            </div>
            
            <div class="form-group" id="cfg-base-group">
                <label for="cfg-api-base">API Base / Host URL</label>
                <input type="text" id="cfg-api-base" value="${config.api_base}">
            </div>
            
            <div class="form-group" id="cfg-key-group">
                <label for="cfg-api-key">API Key (Xác thực)</label>
                <input type="password" id="cfg-api-key" value="${config.api_key}" placeholder="Nhập API Key nếu dùng cloud...">
            </div>
            
            <div class="form-group">
                <label for="cfg-model">Tên mô hình (Model Name)</label>
                <input type="text" id="cfg-model" value="${config.model}">
            </div>
        `;
        
        const providerSelect = document.getElementById("cfg-provider");
        const keyGroup = document.getElementById("cfg-key-group");
        const baseGroup = document.getElementById("cfg-base-group");
        
        function syncProviderFields() {
            const provider = providerSelect.value;
            if (provider === "ollama") {
                keyGroup.classList.add("hidden");
                baseGroup.classList.remove("hidden");
            } else if (provider === "deepseek") {
                keyGroup.classList.remove("hidden");
                baseGroup.classList.add("hidden");
            } else {
                keyGroup.classList.remove("hidden");
                baseGroup.classList.remove("hidden");
            }
        }
        
        syncProviderFields();
        
        providerSelect.addEventListener("change", (e) => {
            config.provider = e.target.value;
            if (config.provider === "ollama") {
                config.api_base = "http://localhost:11434";
                config.model = "deepseek-r1:8b";
            } else if (config.provider === "deepseek") {
                config.api_base = "https://api.deepseek.com/v1";
                config.model = "deepseek-chat";
            } else {
                config.api_base = "https://openrouter.ai/api/v1";
                config.model = "deepseek/deepseek-r1";
            }
            
            document.getElementById("cfg-api-base").value = config.api_base;
            document.getElementById("cfg-model").value = config.model;
            
            syncProviderFields();
            saveConfigToStorage();
        });
        
        document.getElementById("cfg-target-lang").addEventListener("input", (e) => {
            config.target_lang = e.target.value;
            saveConfigToStorage();
        });
        document.getElementById("cfg-api-base").addEventListener("input", (e) => {
            config.api_base = e.target.value;
            saveConfigToStorage();
        });
        document.getElementById("cfg-api-key").addEventListener("input", (e) => {
            config.api_key = e.target.value;
            saveConfigToStorage();
        });
        document.getElementById("cfg-model").addEventListener("input", (e) => {
            config.model = e.target.value;
            saveConfigToStorage();
        });
        
    } else if (node.type === "exporter") {
        sidebarConfigFields.innerHTML = `
            <div class="form-group">
                <label for="cfg-fontname">Font Chữ</label>
                <select id="cfg-fontname">
                    <option value="Outfit" ${config.fontname === "Outfit" ? 'selected' : ''}>Outfit (Hiện đại)</option>
                    <option value="Arial" ${config.fontname === "Arial" ? 'selected' : ''}>Arial (Cơ bản)</option>
                    <option value="Montserrat" ${config.fontname === "Montserrat" ? 'selected' : ''}>Montserrat</option>
                    <option value="JetBrains Mono" ${config.fontname === "JetBrains Mono" ? 'selected' : ''}>JetBrains Mono (Đẹp)</option>
                </select>
            </div>
            
            <div class="form-group">
                <label for="cfg-fontsize">Cỡ Chữ: <span id="cfg-fontsize-val" style="color: var(--neon-blue); font-weight: 700;">${config.fontsize}</span>px</label>
                <input type="range" id="cfg-fontsize" min="10" max="32" value="${config.fontsize}">
            </div>
            
            <div class="form-group">
                <label for="cfg-color">Màu Chữ chính</label>
                <div class="color-picker-wrapper">
                    <input type="color" id="cfg-color" value="${config.color}">
                    <span id="cfg-color-lbl" class="font-mono" style="font-size: 11px; font-weight: 700;">${config.color}</span>
                </div>
            </div>
            
            <div class="form-group">
                <label for="cfg-outline">Màu viền chữ</label>
                <div class="color-picker-wrapper">
                    <input type="color" id="cfg-outline" value="${config.outline_color}">
                    <span id="cfg-outline-lbl" class="font-mono" style="font-size: 11px; font-weight: 700;">${config.outline_color}</span>
                </div>
            </div>
            
            <div class="form-group">
                <label for="cfg-borderstyle">Kiểu hiển thị viền</label>
                <select id="cfg-borderstyle">
                    <option value="1" ${config.borderstyle === "1" ? 'selected' : ''}>Outline + Shadow (Viền nét mỏng)</option>
                    <option value="3" ${config.borderstyle === "3" ? 'selected' : ''}>Opaque Box (Nền đen bao quanh chữ)</option>
                </select>
            </div>
            
            <div class="form-group" style="flex-direction: row; align-items: center; gap: 8px; margin-top: 6px;">
                <input type="checkbox" id="cfg-dual" ${config.dual_subtitles ? 'checked' : ''} style="width: auto; cursor: pointer;">
                <label for="cfg-dual" style="cursor: pointer; margin: 0;">Bật phụ đề song ngữ (Dual Subtitles)</label>
            </div>
        `;
        
        document.getElementById("cfg-fontname").addEventListener("change", (e) => {
            config.fontname = e.target.value;
            saveConfigToStorage();
        });
        
        const sizeSlider = document.getElementById("cfg-fontsize");
        const sizeVal = document.getElementById("cfg-fontsize-val");
        sizeSlider.addEventListener("input", (e) => {
            config.fontsize = parseInt(e.target.value);
            sizeVal.innerText = config.fontsize;
            saveConfigToStorage();
        });
        
        const colorPicker = document.getElementById("cfg-color");
        const colorLbl = document.getElementById("cfg-color-lbl");
        colorPicker.addEventListener("input", (e) => {
            config.color = e.target.value;
            colorLbl.innerText = config.color.toUpperCase();
            saveConfigToStorage();
        });
        
        const outlinePicker = document.getElementById("cfg-outline");
        const outlineLbl = document.getElementById("cfg-outline-lbl");
        outlinePicker.addEventListener("input", (e) => {
            config.outline_color = e.target.value;
            outlineLbl.innerText = config.outline_color.toUpperCase();
            saveConfigToStorage();
        });
        
        document.getElementById("cfg-borderstyle").addEventListener("change", (e) => {
            config.borderstyle = e.target.value;
            saveConfigToStorage();
        });
        
        document.getElementById("cfg-dual").addEventListener("change", (e) => {
            config.dual_subtitles = e.target.checked;
            saveConfigToStorage();
        });
    } else if (node.type === "voiceover") {
        // Đảm bảo khởi tạo các trường mới nếu chưa có
        if (config.engine === undefined) config.engine = "edge";
        if (config.emotion === undefined) config.emotion = "neutral";
        if (config.api_key_openai === undefined) config.api_key_openai = "";
        if (config.api_key_elevenlabs === undefined) config.api_key_elevenlabs = "";

        sidebarConfigFields.innerHTML = `
            <div class="form-group">
                <label for="cfg-voiceover-engine">Động cơ lồng tiếng (AI Engine)</label>
                <select id="cfg-voiceover-engine">
                    <option value="edge" ${config.engine === "edge" ? 'selected' : ''}>Edge-TTS (Tốc độ, miễn phí)</option>
                    <option value="openai" ${config.engine === "openai" ? 'selected' : ''}>OpenAI TTS API (Giọng tự nhiên)</option>
                    <option value="elevenlabs" ${config.engine === "elevenlabs" ? 'selected' : ''}>ElevenLabs (Premium Cinematic)</option>
                    <option value="piper" ${config.engine === "piper" ? 'selected' : ''}>Piper TTS (Offline CPU Fallback)</option>
                    <option value="auto" ${config.engine === "auto" ? 'selected' : ''}>Tự động chuyển mạch (AI Router)</option>
                </select>
            </div>

            <!-- Cấu hình riêng cho Edge-TTS / Tự động -->
            <div class="form-group group-voiceover-engine" id="group-voiceover-edge">
                <label for="cfg-voiceover-voice-edge">Chọn giọng nói (Edge-TTS)</label>
                <select id="cfg-voiceover-voice-edge">
                    <option value="vi-VN-HoaiMyNeural" ${config.voice === "vi-VN-HoaiMyNeural" ? 'selected' : ''}>vi-VN-HoaiMyNeural (Nữ Nam)</option>
                    <option value="vi-VN-NamMinhNeural" ${config.voice === "vi-VN-NamMinhNeural" ? 'selected' : ''}>vi-VN-NamMinhNeural (Nam Bắc)</option>
                    <option value="en-US-AriaNeural" ${config.voice === "en-US-AriaNeural" ? 'selected' : ''}>en-US-AriaNeural (Nữ Mỹ)</option>
                    <option value="en-US-GuyNeural" ${config.voice === "en-US-GuyNeural" ? 'selected' : ''}>en-US-GuyNeural (Nam Mỹ)</option>
                </select>
            </div>

            <!-- Cấu hình riêng cho OpenAI Cloud -->
            <div class="form-group group-voiceover-engine hidden" id="group-voiceover-openai">
                <label for="cfg-voiceover-openai-key">OpenAI API Key</label>
                <input type="password" id="cfg-voiceover-openai-key" value="${config.api_key_openai}" placeholder="sk-...">
                
                <label for="cfg-voiceover-voice-openai" style="margin-top: 8px;">Chọn giọng đọc OpenAI</label>
                <select id="cfg-voiceover-voice-openai">
                    <option value="alloy" ${config.voice === "alloy" ? 'selected' : ''}>alloy</option>
                    <option value="echo" ${config.voice === "echo" ? 'selected' : ''}>echo</option>
                    <option value="fable" ${config.voice === "fable" ? 'selected' : ''}>fable</option>
                    <option value="onyx" ${config.voice === "onyx" ? 'selected' : ''}>onyx</option>
                    <option value="nova" ${config.voice === "nova" ? 'selected' : ''}>nova</option>
                    <option value="shimmer" ${config.voice === "shimmer" ? 'selected' : ''}>shimmer</option>
                </select>
            </div>

            <!-- Cấu hình riêng cho ElevenLabs Cinematic -->
            <div class="form-group group-voiceover-engine hidden" id="group-voiceover-elevenlabs">
                <label for="cfg-voiceover-elevenlabs-key">ElevenLabs API Key</label>
                <input type="password" id="cfg-voiceover-elevenlabs-key" value="${config.api_key_elevenlabs}" placeholder="Nhập API Key ElevenLabs...">

                <label for="cfg-voiceover-voice-elevenlabs" style="margin-top: 8px;">ElevenLabs Voice ID</label>
                <input type="text" id="cfg-voiceover-voice-elevenlabs" value="${(config.voice && !config.voice.includes('-') && config.voice !== 'alloy' && config.voice !== 'echo' && config.voice !== 'fable' && config.voice !== 'onyx' && config.voice !== 'nova' && config.voice !== 'shimmer') ? config.voice : '21m00Tcm4TlvDq8ikWAM'}" placeholder="VD: 21m00Tcm4TlvDq8ikWAM">

                <label for="cfg-voiceover-emotion" style="margin-top: 8px;">Cảm xúc / Phong cách</label>
                <select id="cfg-voiceover-emotion">
                    <option value="neutral" ${config.emotion === "neutral" ? 'selected' : ''}>neutral (Mặc định)</option>
                    <option value="happy" ${config.emotion === "happy" ? 'selected' : ''}>happy (Hạnh phúc)</option>
                    <option value="sad" ${config.emotion === "sad" ? 'selected' : ''}>sad (Buồn bã)</option>
                    <option value="angry" ${config.emotion === "angry" ? 'selected' : ''}>angry (Giận dữ)</option>
                    <option value="whisper" ${config.emotion === "whisper" ? 'selected' : ''}>whisper (Thì thầm)</option>
                    <option value="cinematic" ${config.emotion === "cinematic" ? 'selected' : ''}>cinematic (Điện ảnh)</option>
                </select>
            </div>

            <!-- Cấu hình riêng cho Piper Offline -->
            <div class="form-group group-voiceover-engine hidden" id="group-voiceover-piper">
                <label for="cfg-voiceover-voice-piper">Giọng offline (Piper)</label>
                <select id="cfg-voiceover-voice-piper">
                    <option value="vi_VN-vivos-x_low" ${config.voice === "vi_VN-vivos-x_low" ? 'selected' : ''}>vi_VN-vivos-x_low (Tiếng Việt vivos)</option>
                    <option value="en_US-lessac-medium" ${config.voice === "en_US-lessac-medium" ? 'selected' : ''}>en_US-lessac-medium (Tiếng Anh lessac)</option>
                </select>
            </div>

            <!-- Âm lượng mix -->
            <div class="form-group">
                <label for="cfg-mix-ratio">Âm lượng giọng đọc AI: <span id="cfg-mix-ratio-val" style="color: var(--neon-blue); font-weight: 700;">${config.mix_ratio}</span>%</label>
                <input type="range" id="cfg-mix-ratio" min="10" max="100" step="5" value="${config.mix_ratio}">
                <span style="font-size: 10px; color: var(--text-muted);">Tỷ lệ âm lượng giọng nói đè lên âm thanh gốc của video.</span>
            </div>
        `;

        const engineSelect = document.getElementById("cfg-voiceover-engine");
        
        function syncVoiceoverFields() {
            const engine = engineSelect.value;
            config.engine = engine;
            
            // Ẩn tất cả các engine groups trước
            document.querySelectorAll(".group-voiceover-engine").forEach(el => el.classList.add("hidden"));
            
            if (engine === "edge" || engine === "auto") {
                document.getElementById("group-voiceover-edge").classList.remove("hidden");
                // Cập nhật voice sang edge tương ứng
                config.voice = document.getElementById("cfg-voiceover-voice-edge").value;
            } else if (engine === "openai") {
                document.getElementById("group-voiceover-openai").classList.remove("hidden");
                config.voice = document.getElementById("cfg-voiceover-voice-openai").value;
            } else if (engine === "elevenlabs") {
                document.getElementById("group-voiceover-elevenlabs").classList.remove("hidden");
                config.voice = document.getElementById("cfg-voiceover-voice-elevenlabs").value;
            } else if (engine === "piper") {
                document.getElementById("group-voiceover-piper").classList.remove("hidden");
                config.voice = document.getElementById("cfg-voiceover-voice-piper").value;
            }
            saveConfigToStorage();
        }

        // Đăng ký sự kiện thay đổi Engine
        engineSelect.addEventListener("change", syncVoiceoverFields);
        
        // Khởi động đồng bộ ban đầu
        syncVoiceoverFields();

        // Đăng ký sự kiện thay đổi giọng cho từng engine
        document.getElementById("cfg-voiceover-voice-edge").addEventListener("change", (e) => {
            if (config.engine === "edge" || config.engine === "auto") {
                config.voice = e.target.value;
                saveConfigToStorage();
            }
        });
        document.getElementById("cfg-voiceover-voice-openai").addEventListener("change", (e) => {
            if (config.engine === "openai") {
                config.voice = e.target.value;
                saveConfigToStorage();
            }
        });
        document.getElementById("cfg-voiceover-voice-elevenlabs").addEventListener("input", (e) => {
            if (config.engine === "elevenlabs") {
                config.voice = e.target.value;
                saveConfigToStorage();
            }
        });
        document.getElementById("cfg-voiceover-voice-piper").addEventListener("change", (e) => {
            if (config.engine === "piper") {
                config.voice = e.target.value;
                saveConfigToStorage();
            }
        });

        // Đăng ký sự kiện nhập API Key
        document.getElementById("cfg-voiceover-openai-key").addEventListener("input", (e) => {
            config.api_key_openai = e.target.value.trim();
            saveConfigToStorage();
        });
        document.getElementById("cfg-voiceover-elevenlabs-key").addEventListener("input", (e) => {
            config.api_key_elevenlabs = e.target.value.trim();
            saveConfigToStorage();
        });

        // Đăng ký sự kiện cảm xúc
        document.getElementById("cfg-voiceover-emotion").addEventListener("change", (e) => {
            config.emotion = e.target.value;
            saveConfigToStorage();
        });

        // Đăng ký sự kiện mix_ratio
        const mixSlider = document.getElementById("cfg-mix-ratio");
        const mixVal = document.getElementById("cfg-mix-ratio-val");
        mixSlider.addEventListener("input", (e) => {
            config.mix_ratio = parseInt(e.target.value);
            mixVal.innerText = config.mix_ratio;
            saveConfigToStorage();
        });
        
    } else if (node.type === "clipper") {
        sidebarConfigFields.innerHTML = `
            <div class="form-group">
                <label for="cfg-aspect-ratio">Khung hình Shorts/TikTok</label>
                <select id="cfg-aspect-ratio">
                    <option value="9:16" ${config.aspect_ratio === "9:16" ? 'selected' : ''}>9:16 đứng (TikTok, Shorts, Reels)</option>
                    <option value="1:1" ${config.aspect_ratio === "1:1" ? 'selected' : ''}>1:1 vuông (Instagram, FB Post)</option>
                    <option value="16:9" ${config.aspect_ratio === "16:9" ? 'selected' : ''}>16:9 ngang (Bản gốc giữ nguyên)</option>
                </select>
            </div>
            
            <div class="form-group">
                <label for="cfg-duration">Độ dài Highlights: <span id="cfg-duration-val" style="color: var(--neon-blue); font-weight: 700;">${config.duration}</span>s</label>
                <input type="range" id="cfg-duration" min="15" max="120" step="5" value="${config.duration}">
            </div>
        `;
        
        document.getElementById("cfg-aspect-ratio").addEventListener("change", (e) => {
            config.aspect_ratio = e.target.value;
            saveConfigToStorage();
        });
        
        const durSlider = document.getElementById("cfg-duration");
        const durVal = document.getElementById("cfg-duration-val");
        durSlider.addEventListener("input", (e) => {
            config.duration = parseInt(e.target.value);
            durVal.innerText = config.duration;
            saveConfigToStorage();
        });
        
    } else if (node.type === "seo") {
        sidebarConfigFields.innerHTML = `
            <div class="form-group">
                <label for="cfg-platform">Nền tảng mạng xã hội</label>
                <select id="cfg-platform">
                    <option value="youtube" ${config.platform === "youtube" ? 'selected' : ''}>YouTube (Mô tả, tags, chapters)</option>
                    <option value="tiktok" ${config.platform === "tiktok" ? 'selected' : ''}>TikTok / Reels (Tiêu đề giật gân, ngắn)</option>
                    <option value="facebook" ${config.platform === "facebook" ? 'selected' : ''}>Facebook (Dài, có emoji cuốn hút)</option>
                    <option value="reddit" ${config.platform === "reddit" ? 'selected' : ''}>Reddit (Tóm tắt nghiêm túc)</option>
                </select>
            </div>
            
            <div class="form-group">
                <label for="cfg-tone">Giọng điệu văn phong AI</label>
                <select id="cfg-tone">
                    <option value="engaging" ${config.tone === "engaging" ? 'selected' : ''}>Giật gân, cuốn hút (Clickbait)</option>
                    <option value="professional" ${config.tone === "professional" ? 'selected' : ''}>Trang trọng, chuyên gia (Professional)</option>
                    <option value="funny" ${config.tone === "funny" ? 'selected' : ''}>Hài hước, gần gũi (Funny)</option>
                </select>
            </div>
        `;
        
        document.getElementById("cfg-platform").addEventListener("change", (e) => {
            config.platform = e.target.value;
            saveConfigToStorage();
        });
        
        document.getElementById("cfg-tone").addEventListener("change", (e) => {
            config.tone = e.target.value;
            saveConfigToStorage();
        });
        
    } else if (node.type === "uploader") {
        if (config.youtube === undefined) config.youtube = false;
        if (config.privacy_status === undefined) config.privacy_status = "private";

        sidebarConfigFields.innerHTML = `
            <div class="form-group" style="flex-direction: row; align-items: center; gap: 8px; margin-bottom: 12px;">
                <input type="checkbox" id="cfg-gdrive" ${config.gdrive ? 'checked' : ''} style="width: auto; cursor: pointer;">
                <label for="cfg-gdrive" style="cursor: pointer; margin: 0;">Tự động tải lên Google Drive</label>
            </div>
            
            <div class="form-group" style="flex-direction: row; align-items: center; gap: 8px; margin-bottom: 12px;">
                <input type="checkbox" id="cfg-telegram" ${config.telegram ? 'checked' : ''} style="width: auto; cursor: pointer;">
                <label for="cfg-telegram" style="cursor: pointer; margin: 0;">Gửi video lồng tiếng và phụ đề qua Telegram</label>
            </div>

            <div class="form-group" style="flex-direction: row; align-items: center; gap: 8px; margin-bottom: 12px;">
                <input type="checkbox" id="cfg-youtube" ${config.youtube ? 'checked' : ''} style="width: auto; cursor: pointer;">
                <label for="cfg-youtube" style="cursor: pointer; margin: 0; font-weight: 700; color: var(--neon-blue);">Tự động đăng tải lên YouTube 🎥</label>
            </div>

            <div class="form-group" id="cfg-youtube-privacy-group" style="margin-left: 20px; display: ${config.youtube ? 'flex' : 'none'};">
                <label for="cfg-youtube-privacy" style="font-size: 11px;">Chế độ riêng tư (Privacy Status)</label>
                <select id="cfg-youtube-privacy" style="font-size: 11px; padding: 6px 10px; border-radius: 6px; background: #ffffff;">
                    <option value="private" ${config.privacy_status === "private" ? 'selected' : ''}>Riêng tư (Private)</option>
                    <option value="unlisted" ${config.privacy_status === "unlisted" ? 'selected' : ''}>Không công khai (Unlisted)</option>
                    <option value="public" ${config.privacy_status === "public" ? 'selected' : ''}>Công khai (Public)</option>
                </select>
            </div>
        `;
        
        document.getElementById("cfg-gdrive").addEventListener("change", (e) => {
            config.gdrive = e.target.checked;
            document.getElementById("sync-gdrive").checked = config.gdrive;
            saveConfigToStorage();
        });
        
        document.getElementById("cfg-telegram").addEventListener("change", (e) => {
            config.telegram = e.target.checked;
            document.getElementById("sync-telegram").checked = config.telegram;
            saveConfigToStorage();
        });

        const ytCheckbox = document.getElementById("cfg-youtube");
        const ytPrivacyGroup = document.getElementById("cfg-youtube-privacy-group");
        ytCheckbox.addEventListener("change", (e) => {
            config.youtube = e.target.checked;
            document.getElementById("sync-youtube").checked = config.youtube;
            ytPrivacyGroup.style.display = config.youtube ? 'flex' : 'none';
            saveConfigToStorage();
        });

        document.getElementById("cfg-youtube-privacy").addEventListener("change", (e) => {
            config.privacy_status = e.target.value;
            saveConfigToStorage();
        });
    }

    // Nút Biên tập thủ công & AI Căn câu tự động
    if (["subtitle", "translator", "exporter"].includes(node.type)) {
        const subActionGroup = document.createElement("div");
        subActionGroup.className = "form-group";
        subActionGroup.style.marginTop = "20px";
        subActionGroup.style.borderTop = "1px solid var(--border-light)";
        subActionGroup.style.paddingTop = "16px";
        subActionGroup.style.display = "flex";
        subActionGroup.style.flexDirection = "column";
        subActionGroup.style.gap = "10px";

        subActionGroup.innerHTML = `
            <label style="font-weight: 700; color: var(--text-primary); margin-bottom: 2px;">Biên tập & Làm đẹp phụ đề</label>
            <button id="btn-open-editor-manual" class="btn btn-primary" style="background: var(--grad-primary); border: none; font-weight: 700; border-radius: 8px; padding: 10px; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 6px;">
                Biên tập phụ đề thủ công ✍️
            </button>
            <button id="btn-ai-wrap-sub" class="btn" style="background: rgba(14, 165, 233, 0.1); color: var(--neon-blue); border: 1px solid rgba(14, 165, 233, 0.2); font-weight: 700; border-radius: 8px; padding: 10px; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 6px;">
                AI Tự động căn dòng phụ đề 🤖
            </button>
            <span style="font-size: 10px; color: var(--text-muted); line-height: 1.4; text-align: center;">
                Chỉnh sửa thủ công hoặc để AI tự căn chỉnh câu vừa khít khung hình video.
            </span>
        `;
        sidebarConfigFields.appendChild(subActionGroup);

        // Click Biên tập thủ công
        document.getElementById("btn-open-editor-manual").addEventListener("click", () => {
            const activeSrt = getLocalStorageItem("aegis_edited_srt") || lastSrtContent || getLocalStorageItem("aegis_last_srt") || "";
            if (!activeSrt.trim()) {
                alert("Chưa có phụ đề nào được tạo. Hãy chạy luồng hoạt động hoặc dịch thuật để tạo phụ đề trước!");
                return;
            }
            loadSrtIntoEditor(activeSrt);
        });

        // Click AI Căn dòng tự động
        document.getElementById("btn-ai-wrap-sub").addEventListener("click", () => {
            const activeSrt = getLocalStorageItem("aegis_edited_srt") || lastSrtContent || getLocalStorageItem("aegis_last_srt") || "";
            if (!activeSrt.trim()) {
                alert("Chưa có phụ đề nào để AI tự động căn dòng!");
                return;
            }
            if (!socket || socket.readyState !== WebSocket.OPEN) {
                alert("Mất kết nối với máy chủ AI. Vui lòng kết nối lại!");
                return;
            }
            appendLog("Yêu cầu AI tự động ngắt dòng thông minh cân đối phụ đề...", "info");
            socket.send(JSON.stringify({
                type: "auto_wrap_subtitle",
                data: {
                    srt_content: activeSrt
                }
            }));
        });
    }
}

// Click ra ngoài canvas (inner area) để đóng sidebar
canvasContainer.addEventListener("click", (e) => {
    // Chỉ đóng sidebar khi click trực tiếp vào background (không phải vào node)
    if (e.target === canvasContainer || e.target.id === "workflow-inner-canvas" || 
        e.target.classList.contains("workflow-grid-bg")) {
        closeSidebar();
    }
});

// Hiển thị / ẩn badge gợi ý cuộn ngang khi canvas rộng hơn khung nhìn
function updateScrollHint() {
    if (!canvasScrollHint || !canvasContainer || !canvasInner) return;
    const needsScroll = canvasInner.scrollWidth > canvasContainer.clientWidth;
    canvasScrollHint.style.display = needsScroll ? "flex" : "none";
}

// Ẩn hint khi người dùng đã bắt đầu cuộn
canvasContainer.addEventListener("scroll", () => {
    if (canvasScrollHint) {
        if (canvasContainer.scrollLeft > 30) {
            canvasScrollHint.style.display = "none";
        }
    }
});

// Kiểm tra trạng thái cuộn sau khi resize màn hình
window.addEventListener("resize", updateScrollHint);

// Event Listeners for Run Workflow UI
launchBtn.addEventListener("click", () => {
    const goal = goalInput.value.trim();
    if (!goal) {
        appendLog("Vui lòng nhập mục tiêu vận hành cho AI Employee!", "error");
        return;
    }
    
    // Kiểm tra trạng thái kết nối WebSocket để tránh lỗi gửi khi ngắt kết nối
    if (!socket || socket.readyState !== WebSocket.OPEN) {
        appendLog("Không thể vận hành: Mất kết nối đến máy chủ Aegis AI. Đang thử kết nối lại, vui lòng bấm lại sau vài giây!", "error");
        connectWebSocket();
        return;
    }
    
    thinkingBody.innerText = "Đang chờ AI lập luận...";
    terminalBody.innerHTML = '<div class="log-line info">> Khởi tạo tiến trình xử lý tự động (Workflow Pipeline)...</div>';
    
    // Tải thông số phụ đề từ Exporter node config để sinh ASS style string
    const expNode = nodes.exporter.config;
    const colorASS = hexToAssColor(expNode.color);
    const outlineColorASS = hexToAssColor(expNode.outline_color);
    const styleString = `FontSize=${expNode.fontsize},PrimaryColour=${colorASS},OutlineColour=${outlineColorASS},BorderStyle=${expNode.borderstyle},Fontname=${expNode.fontname}`;
    
    const syncGdrive = document.getElementById("sync-gdrive").checked;
    const syncTelegram = document.getElementById("sync-telegram").checked;
    const syncYoutube = document.getElementById("sync-youtube").checked;
    
    // Cập nhật trạng thái các node về Chờ
    Object.keys(nodes).forEach(k => {
        if (!nodes[k].bypassed) {
            nodes[k].status = "idle";
        }
    });
    renderNodes();
    drawConnections();
    
    socket.send(JSON.stringify({
        type: "start_workflow",
        data: { 
            goal: goal,
            nodes: nodes,
            connections: connections,
            upload_gdrive: syncGdrive,
            upload_telegram: syncTelegram,
            upload_youtube: syncYoutube,
            subtitle_style: styleString,
            edited_srt_content: getLocalStorageItem("aegis_edited_srt") || ""
        }
    }));
});

modalApproveBtn.addEventListener("click", () => {
    const seoReviewBox = document.getElementById("seo-review-box");
    let seoTitle = "";
    let seoDesc = "";
    if (seoReviewBox && !seoReviewBox.classList.contains("hidden")) {
        seoTitle = document.getElementById("seo-review-title").value;
        seoDesc = document.getElementById("seo-review-desc").value;
    }

    socket.send(JSON.stringify({
        type: "approve",
        data: {
            seo_title: seoTitle,
            seo_desc: seoDesc
        }
    }));
    hideApprovalModal();
});

modalRejectBtn.addEventListener("click", () => {
    const comment = rejectComment.value.trim() || "Người dùng từ chối thao tác này.";
    socket.send(JSON.stringify({
        type: "reject",
        data: { comment: comment }
    }));
    hideApprovalModal();
});

// Subtitle Editor Save Button click listener
subEditorSaveBtn.addEventListener("click", () => {
    const editedSrt = rebuildSrt();
    
    // Lưu vào cả biến tạm và localStorage
    lastSrtContent = editedSrt;
    setLocalStorageItem("aegis_edited_srt", editedSrt);
    setLocalStorageItem("aegis_last_srt", editedSrt);

    socket.send(JSON.stringify({
        type: "approve",
        data: {
            srt_content: editedSrt
        }
    }));
    
    subEditorModal.classList.add("hidden");
    appendLog("Đã lưu và gửi phụ đề hiệu chỉnh thành công lên máy chủ!", "success");
});

// Subtitle Editor Cancel Button click listener
editorCancelBtn.addEventListener("click", () => {
    subEditorModal.classList.add("hidden");
    appendLog("Đã đóng Trình Biên Tập Phụ Đề Tương Tác mà không lưu thay đổi.", "warning");
});

// Video Lightbox Close Button click listener
closeVideoBtn.addEventListener("click", () => {
    videoPlayer.pause();
    videoPlayer.src = "";
    videoPlayerModal.classList.add("hidden");
    appendLog("Đã đóng trình phát video.", "info");
});

// Helper function to switch log tabs programmatically or via click
function switchLogTab(tabType) {
    logTabs.forEach(t => {
        if (t.getAttribute("data-tab") === tabType) {
            t.classList.add("active");
        } else {
            t.classList.remove("active");
        }
    });
    
    terminalLog.classList.remove("active");
    thinkingLog.classList.remove("active");
    document.getElementById("seo-log").classList.remove("active");
    
    if (tabType === "terminal") {
        terminalLog.classList.add("active");
    } else if (tabType === "thinking") {
        thinkingLog.classList.add("active");
    } else if (tabType === "seo") {
        document.getElementById("seo-log").classList.add("active");
    }
}

// Logs Tab Switch logic
logTabs.forEach(tab => {
    tab.addEventListener("click", () => {
        const tabType = tab.getAttribute("data-tab");
        switchLogTab(tabType);
    });
});

// Lưu cấu hình toàn bộ Nodes vào localStorage
function saveConfigToStorage() {
    const serializedNodes = {};
    Object.keys(nodes).forEach(key => {
        serializedNodes[key] = {
            x: nodes[key].x,
            y: nodes[key].y,
            bypassed: nodes[key].bypassed,
            config: nodes[key].config
        };
    });
    setLocalStorageItem("aegis_node_graph_config", JSON.stringify(serializedNodes));
    
    // Gửi đồng bộ lên máy chủ
    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({
            type: "save_webui_config",
            data: { nodes: nodes, connections: connections }
        }));
    }
}

// Nạp cấu hình từ localStorage
function loadConfigFromStorage() {
    const saved = getLocalStorageItem("aegis_node_graph_config");
    if (saved) {
        try {
            const data = JSON.parse(saved);
            Object.keys(data).forEach(key => {
                if (nodes[key]) {
                    let valid = true;
                    // Đảm bảo toạ độ x, y là những số hợp lệ và dương để tránh bị ẩn khỏi màn hình
                    if (data[key].x !== undefined && !isNaN(data[key].x) && data[key].x >= 0) {
                        nodes[key].x = data[key].x;
                    } else {
                        valid = false;
                    }
                    if (data[key].y !== undefined && !isNaN(data[key].y) && data[key].y >= 0) {
                        nodes[key].y = data[key].y;
                    } else {
                        valid = false;
                    }
                    
                    if (!valid) {
                        const defaults = {
                            downloader: { x: 30, y: 220 },
                            audio: { x: 230, y: 220 },
                            subtitle: { x: 430, y: 220 },
                            translator: { x: 630, y: 220 },
                            exporter: { x: 830, y: 220 },
                            voiceover: { x: 1030, y: 220 },
                            seo: { x: 1030, y: 50 },
                            clipper: { x: 1030, y: 390 },
                            uploader: { x: 1250, y: 220 }
                        };
                        if (defaults[key]) {
                            nodes[key].x = defaults[key].x;
                            nodes[key].y = defaults[key].y;
                        }
                    }
                    
                    if (data[key].bypassed !== undefined) nodes[key].bypassed = data[key].bypassed;
                    if (data[key].config !== undefined) {
                        // Tránh ghi đè mất các trường mặc định nếu schema có update
                        nodes[key].config = Object.assign({}, nodes[key].config, data[key].config);
                    }
                }
            });
        } catch (e) {
            console.error("Lỗi khi load cấu hình Node-Graph từ localStorage:", e);
        }
    }
    renderNodes();
    drawConnections();
    // Chạy sau một tick để DOM đã cập nhật kích thước
    setTimeout(updateScrollHint, 100);
}

// Fetch and render the media library output files
async function loadMediaLibrary() {
    const listEl = document.getElementById("media-library-list");
    if (!listEl) return;
    
    try {
        const resp = await fetch(`${API_URL}/api/media-library`);
        if (!resp.ok) throw new Error("HTTP error");
        const files = await resp.json();
        
        if (files.length === 0) {
            listEl.innerHTML = '<div class="empty-state">Không có tệp thành phẩm nào. Hãy chạy AI để tạo video đầu tiên!</div>';
            return;
        }
        
        listEl.innerHTML = "";
        files.forEach(f => {
            const row = document.createElement("div");
            row.style.background = "rgba(255,255,255,0.6)";
            row.style.border = "1px solid var(--border-light)";
            row.style.borderRadius = "8px";
            row.style.padding = "8px 12px";
            row.style.display = "flex";
            row.style.justifyContent = "space-between";
            row.style.alignItems = "center";
            row.style.gap = "8px";
            
            const isVideo = f.suffix.toLowerCase() === ".mp4";
            
            row.innerHTML = `
                <div style="display: flex; flex-direction: column; gap: 2px; flex: 1; min-width: 0;">
                    <span class="font-sans" style="font-size: 12px; font-weight: 700; color: var(--text-primary); text-overflow: ellipsis; overflow: hidden; white-space: nowrap;">
                        ${isVideo ? "🎬" : "📝"} ${f.name}
                    </span>
                    <span class="font-mono" style="font-size: 10px; color: var(--text-muted);">
                        ${f.size_mb} MB • ${new Date(f.modified * 1000).toLocaleString("vi-VN")}
                    </span>
                </div>
                <div style="display: flex; gap: 6px;">
                    ${isVideo ? `
                        <button class="btn btn-primary play-media-btn" data-url="${f.path}" style="padding: 4px 8px; font-size: 11px; border-radius: 4px;"> phát </button>
                    ` : ""}
                    <a href="${API_URL}${f.path}" download class="btn" style="padding: 4px 8px; font-size: 11px; border-radius: 4px; background: rgba(2, 132, 199, 0.1); color: var(--neon-blue); font-weight: 700; text-decoration: none;"> tải </a>
                    <button class="btn delete-media-btn" data-name="${f.name}" style="padding: 4px 8px; font-size: 11px; border-radius: 4px; background: rgba(225, 29, 72, 0.1); color: var(--neon-red); font-weight: 700; border: none;"> xóa </button>
                </div>
            `;
            listEl.appendChild(row);
        });
        
        listEl.querySelectorAll(".play-media-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                const videoUrl = btn.getAttribute("data-url");
                playVideo(videoUrl);
            });
        });
        
        listEl.querySelectorAll(".delete-media-btn").forEach(btn => {
            btn.addEventListener("click", async () => {
                const name = btn.getAttribute("data-name");
                if (confirm(`Bạn có chắc chắn muốn xóa tệp ${name}?`)) {
                    try {
                        const delResp = await fetch(`${API_URL}/api/media-library/${name}`, { method: "DELETE" });
                        if (delResp.ok) {
                            appendLog(`Đã xóa tệp ${name} khỏi thư mục output.`, "success");
                            loadMediaLibrary();
                        }
                    } catch (err) {
                        console.error(err);
                    }
                }
            });
        });
    } catch (e) {
        console.error("Lỗi khi load Media Library:", e);
    }
}

function playVideo(url) {
    videoPlayerModal.classList.remove("hidden");
    videoPlayer.src = API_URL + url;
    videoPlayer.load();
    videoPlayer.play().catch(e => {
        console.warn("Tự động phát bị chặn bởi chính sách bảo mật trình duyệt, người dùng có thể nhấp Phát thủ công.", e);
    });
    appendLog(`Đang phát trực tiếp video thành phẩm: ${url.split('/').pop()}`, "info");
}

document.getElementById("copy-seo-btn").addEventListener("click", () => {
    const seoText = document.getElementById("seo-body").innerText;
    if (seoText && seoText !== "Đang chờ video hoàn thành để sinh nội dung SEO...") {
        navigator.clipboard.writeText(seoText)
            .then(() => appendLog("Đã sao chép nội dung SEO thành công vào bộ nhớ tạm (Clipboard)!", "success"))
            .catch(err => console.error("Không thể sao chép:", err));
    } else {
        appendLog("Chưa có nội dung SEO để sao chép!", "warning");
    }
});

// Khôi phục lịch sử logs từ sessionStorage
function loadLogsFromStorage() {
    try {
        const saved = sessionStorage.getItem("aegis_session_logs");
        if (saved) {
            const logs = JSON.parse(saved);
            terminalBody.innerHTML = "";
            logs.forEach(l => {
                appendLog(l.message, l.level, true, l.time);
            });
        }
    } catch (e) {
        console.error("Lỗi khôi phục logs:", e);
    }
}

// Khởi tạo tính năng kéo thả Node từ Thư Viện sang Canvas
function initDragAndDrop() {
    const libItems = document.querySelectorAll(".library-node-item");
    
    libItems.forEach(item => {
        item.addEventListener("dragstart", (e) => {
            const nodeId = item.getAttribute("data-node-id");
            e.dataTransfer.setData("text/plain", nodeId);
            e.dataTransfer.effectAllowed = "move";
            item.style.opacity = "0.4";
        });
        
        item.addEventListener("dragend", () => {
            item.style.opacity = "1";
        });
    });
    
    if (canvasContainer) {
        canvasContainer.addEventListener("dragover", (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = "move";
        });
        
        canvasContainer.addEventListener("drop", (e) => {
            e.preventDefault();
            
            const nodeId = e.dataTransfer.getData("text/plain");
            if (!nodeId || !nodes[nodeId]) return;
            
            const rect = canvasContainer.getBoundingClientRect();
            const dropX = e.clientX - rect.left + canvasContainer.scrollLeft - 92;
            const dropY = e.clientY - rect.top + canvasContainer.scrollTop - 45;
            
            const innerW = canvasInner ? canvasInner.scrollWidth : 1600;
            const innerH = canvasInner ? canvasInner.scrollHeight : 600;
            
            const finalX = Math.max(10, Math.min(innerW - 195, dropX));
            const finalY = Math.max(10, Math.min(innerH - 110, dropY));
            
            const wasBypassed = nodes[nodeId].bypassed;
            nodes[nodeId].bypassed = false;
            nodes[nodeId].x = finalX;
            nodes[nodeId].y = finalY;
            
            renderNodes();
            drawConnections();
            saveConfigToStorage();
            
            if (wasBypassed) {
                appendLog(`Đã kích hoạt và thêm thành công node: <b>${nodes[nodeId].name}</b> vào sơ đồ`, "success");
            } else {
                appendLog(`Đã di chuyển vị trí node: <b>${nodes[nodeId].name}</b> trên sơ đồ.`, "info");
            }
        });
    }
}

// Toolbar checkboxes synchronization and initial sync
document.getElementById("sync-gdrive").addEventListener("change", (e) => {
    nodes.uploader.config.gdrive = e.target.checked;
    saveConfigToStorage();
});
document.getElementById("sync-telegram").addEventListener("change", (e) => {
    nodes.uploader.config.telegram = e.target.checked;
    saveConfigToStorage();
});
document.getElementById("sync-youtube").addEventListener("change", (e) => {
    nodes.uploader.config.youtube = e.target.checked;
    saveConfigToStorage();
});

// Update toolbar checkboxes from loaded config
function syncToolbarCheckboxes() {
    if (nodes.uploader && nodes.uploader.config) {
        document.getElementById("sync-gdrive").checked = !!nodes.uploader.config.gdrive;
        document.getElementById("sync-telegram").checked = !!nodes.uploader.config.telegram;
        document.getElementById("sync-youtube").checked = !!nodes.uploader.config.youtube;
    }
}

// Start application
loadConfigFromStorage();
loadLogsFromStorage();
initDragAndDrop();
loadMediaLibrary();
syncToolbarCheckboxes();
connectWebSocket();
