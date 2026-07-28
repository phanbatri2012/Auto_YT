import { useState, useEffect } from 'react'
import './App.css'
import AutoLogin from './AutoLogin'
import Settings from './Settings'

function App() {
  const [activeView, setActiveView] = useState('fetcher') // 'fetcher' or 'dashboard'
  const [url, setUrl] = useState('')
  const [isFetching, setIsFetching] = useState(false)
  const [showResult, setShowResult] = useState(false)
  const [resultText, setResultText] = useState('')
  const [fullTranscript, setFullTranscript] = useState('')
  const [chatUrl, setChatUrl] = useState('')
  const [errorMsg, setErrorMsg] = useState('')
  const [activeTab, setActiveTab] = useState('summary')
  const [savedVideos, setSavedVideos] = useState({ items: [], total: 0 })
  const [currentPage, setCurrentPage] = useState(1)
  const [generatingThumbnailType, setGeneratingThumbnailType] = useState(null)
  const [isGenAudio, setIsGenAudio] = useState(false)
  const [audioStatus, setAudioStatus] = useState('not_started')
  const [progressMsg, setProgressMsg] = useState('')
  const [currentVideoId, setCurrentVideoId] = useState(null)
  const [videoTitle, setVideoTitle] = useState('')
  const [isCurrentVideoPublished, setIsCurrentVideoPublished] = useState(false)
  
  const [promptVersions, setPromptVersions] = useState([])
  const [selectedPromptVersion, setSelectedPromptVersion] = useState('default')
  const [publishFilter, setPublishFilter] = useState('all') // 'all' | 'published' | 'unpublished'
  
  const PAGE_SIZE = 10

  const fetchSavedVideos = async (page = currentPage, filter = publishFilter) => {
    try {
      const offset = (page - 1) * PAGE_SIZE;
      let url = `http://127.0.0.1:8080/api/videos?limit=${PAGE_SIZE}&offset=${offset}`;
      if (filter === 'published') url += '&is_published=1';
      else if (filter === 'unpublished') url += '&is_published=0';
      const response = await fetch(url);
      const data = await response.json();
      setSavedVideos(data);
    } catch (err) {
      console.error("Failed to fetch videos", err);
    }
  };

  const handlePageChange = (newPage) => {
    setCurrentPage(newPage);
    fetchSavedVideos(newPage);
  }

  const toggleCurrentVideoPublish = async () => {
    if (!currentVideoId) return;
    const newStatus = isCurrentVideoPublished ? 0 : 1;
    try {
      const response = await fetch(`http://127.0.0.1:8080/api/videos/${currentVideoId}/publish?is_published=${newStatus}`, { method: 'PUT' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setIsCurrentVideoPublished(newStatus === 1);
      await fetchSavedVideos(currentPage, publishFilter);
    } catch (err) {
      console.error('Failed to toggle publish status', err);
    }
  }

  const togglePublish = async (videoId, currentStatus) => {
    const newStatus = currentStatus ? 0 : 1;
    try {
      const response = await fetch(`http://127.0.0.1:8080/api/videos/${videoId}/publish?is_published=${newStatus}`, {
        method: 'PUT'
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      if (videoId === currentVideoId) {
        setIsCurrentVideoPublished(newStatus === 1);
      }
      await fetchSavedVideos(currentPage, publishFilter);
    } catch (err) {
      console.error('Failed to toggle publish status', err);
    }
  }

  useEffect(() => {
    fetchPromptVersions(); // Load mapping unconditionally for Dashboard
  }, [])

  useEffect(() => {
    if (activeView === 'dashboard') {
      fetchSavedVideos(1);
      setCurrentPage(1);
    }
  }, [activeView])

  useEffect(() => {
    if (!currentVideoId) return;

    let stopped = false;
    let intervalId = null;
    const syncAudio = async () => {
      try {
        const response = await fetch(
          `http://127.0.0.1:8080/api/videos/${currentVideoId}/audio-status`
        );
        const data = await response.json();
        if (stopped || !data.success) return;

        const status = data.audio_task?.status || 'not_started';
        setAudioStatus(status);
        setIsGenAudio(status === 'pending' || status === 'processing');

        if (status === 'completed') {
          const videoResponse = await fetch(
            `http://127.0.0.1:8080/api/videos/${currentVideoId}`
          );
          const video = await videoResponse.json();
          if (!stopped) setResultText(video.generated_script);
          if (intervalId) clearInterval(intervalId);
        } else if (status === 'failed' && intervalId) {
          clearInterval(intervalId);
        }
      } catch (error) {
        console.error('Failed to sync audio status', error);
      }
    };

    syncAudio();
    intervalId = setInterval(syncAudio, 5000);
    return () => {
      stopped = true;
      if (intervalId) clearInterval(intervalId);
    };
  }, [currentVideoId])
  
  const fetchPromptVersions = async () => {
    try {
      const res = await fetch('http://127.0.0.1:8080/api/prompts');
      const data = await res.json();
      if (data.versions) {
        const versionsList = Object.entries(data.versions).map(([key, version]) => ({
          key: key,
          name: version.name
        }));
        setPromptVersions(versionsList);
        setSelectedPromptVersion(data.active_version || 'default');
      }
    } catch (err) {
      console.error('Failed to fetch prompt versions', err);
    }
  }

  const handleRun = async () => {
    if (!url.trim()) return
    setIsFetching(true)
    setShowResult(false)
    setErrorMsg('')
    setResultText('')
    setVideoTitle('')
    setAudioStatus('not_started')
    setChatUrl('')
    setProgressMsg('⏳ Đang khởi động...')
    
    try {
      // Start job - returns immediately with job_id
      const response = await fetch('http://127.0.0.1:8080/api/process-video', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, prompt_version: selectedPromptVersion })
      });
      const { job_id } = await response.json();
      if (!job_id) throw new Error('Backend did not return a job_id');

      // Poll every 3 seconds until done or error
      await new Promise((resolve) => {
        const interval = setInterval(async () => {
          try {
            const res = await fetch(`http://127.0.0.1:8080/api/jobs/${job_id}`);
            const job = await res.json();
            setProgressMsg(job.progress || '...');
            if (job.status === 'done') {
              clearInterval(interval);
              const data = job.result;
              setResultText(data.summary);
              setFullTranscript(data.full_transcript);
              setChatUrl(data.chat_url || '');
              setCurrentVideoId(data.video_id);
              setVideoTitle(data.title || '');
              setIsCurrentVideoPublished(false);
              setAudioStatus(data.audio_task?.status || 'not_started');
              if (data.audio_error) {
                alert('Audio chưa được gửi: ' + data.audio_error);
              }
              resolve();
            } else if (job.status === 'error') {
              clearInterval(interval);
              setErrorMsg(job.error || 'Đã xảy ra lỗi không xác định.');
              resolve();
            }
          } catch (e) {
            // ignore transient fetch errors, keep polling
          }
        }, 3000);
      });

    } catch (err) {
      setErrorMsg('Cannot connect to the Python backend. Is it running?');
    } finally {
      setIsFetching(false)
      setShowResult(true)
    }
  }

  const getCleanText = (text) => {
    if (!text) return '';
    let clean = text.replace(/### \[IMAGE\][\s\S]*/, ''); // Remove image section entirely
    clean = clean.replace(/### \[AUDIO\][\s\S]*/, ''); // Remove audio section entirely
    clean = clean.replace(/### \[[^\]]+\]/g, ''); // Remove all ### [TITLE] markers
    clean = clean.replace(/\n\s*\n\s*\n/g, '\n\n').trim(); // Collapse excess newlines
    return clean;
  };

  const handleCopy = () => {
    const textToCopy = activeTab === 'summary' ? getCleanText(resultText) : fullTranscript;
    navigator.clipboard.writeText(textToCopy).then(() => {
      const btn = document.getElementById('copy-btn');
      if (btn) {
        const originalText = btn.innerText;
        btn.innerText = 'Copied! ✅';
        setTimeout(() => { btn.innerText = originalText; }, 2000);
      }
    });
  };

  const handleExportTxt = () => {
    const textToCopy = activeTab === 'summary' ? getCleanText(resultText) : fullTranscript;
    const blob = new Blob([textToCopy], { type: 'text/plain' });
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = activeTab === 'summary' ? 'Kich_Ban_Auto_YT.txt' : 'Phu_De_Goc.txt';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(blobUrl);
  };

  const viewSavedVideo = async (id) => {
    try {
      const response = await fetch(`http://127.0.0.1:8080/api/videos/${id}`);
      const data = await response.json();
      setUrl(data.url);
      setResultText(data.generated_script);
      setFullTranscript(data.transcript);
      setChatUrl(data.chat_url || '');
      setVideoTitle(data.title || '');
      setShowResult(true);
      setActiveView('fetcher');
      setCurrentVideoId(id);  // track which video is loaded
      setIsCurrentVideoPublished(Boolean(data.is_published));
      setAudioStatus('not_started');
    } catch (err) {
      console.error("Failed to load video", err);
      alert("Failed to load video from database.");
    }
  };

  const deleteSavedVideo = async (id) => {
    if (!confirm("Are you sure you want to delete this video?")) return;
    try {
      await fetch(`http://127.0.0.1:8080/api/videos/${id}`, { method: 'DELETE' });
      fetchSavedVideos(); // Refresh list
    } catch (err) {
      console.error("Failed to delete", err);
    }
  };

  const handleCopySection = (content, btnId) => {
    navigator.clipboard.writeText(content).then(() => {
      const btn = document.getElementById(btnId);
      if (btn) {
        const originalText = btn.innerText;
        btn.innerText = 'Copied! ✅';
        setTimeout(() => { btn.innerText = originalText; }, 2000);
      }
    });
  };

  const handleDownloadAudio = async () => {
    if (!audioUrl) return;
    try {
      const response = await fetch(audioUrl);
      const blob = await response.blob();
      const blobUrl = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = blobUrl;
      a.download = `voice_over_${Date.now()}.mp3`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(blobUrl);
    } catch (error) {
      console.error('Download failed, opening in new tab', error);
      window.open(audioUrl, '_blank');
    }
  };

  const handleDownloadImage = async (url, defaultFilename, e) => {
    e.preventDefault();
    try {
      // Add a cache-busting parameter to prevent using opaque responses cached by <img> tag
      const fetchUrl = url.includes('?') ? `${url}&_cb=${Date.now()}` : `${url}?_cb=${Date.now()}`;
      const response = await fetch(fetchUrl, { mode: 'cors' });
      if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
      const blob = await response.blob();
      
      if (window.showSaveFilePicker) {
        const handle = await window.showSaveFilePicker({
          suggestedName: defaultFilename,
          types: [{
            description: 'PNG Image',
            accept: { 'image/png': ['.png'] },
          }],
        });
        const writable = await handle.createWritable();
        await writable.write(blob);
        await writable.close();
      } else {
        const blobUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = blobUrl;
        a.download = defaultFilename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(blobUrl);
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        console.error('Download failed', err);
        alert('Lỗi tải ảnh: ' + err.message);
      }
    }
  };

  const handleGenerateThumbnail = async (thumbnailType) => {
    if (!resultText) return;
    setGeneratingThumbnailType(thumbnailType);
    try {
      const res = await fetch('http://127.0.0.1:8080/api/generate-thumbnails', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          script: resultText,
          video_id: currentVideoId,
          thumbnail_type: thumbnailType
        })
      });
      const data = await res.json();
      if (data.success) {
        // Patch resultText with new image URLs
        let newScript = resultText;
        if (data.image1_url) {
          newScript = newScript.replace(
            /(### \[THUMBNAIL CÓ CHỮ\][\s\S]*?)(?=\n### |$)/,
            (m) => m.replace(/\[IMAGE_URL:.*?\]/g, '').trimEnd() + `\n\n[IMAGE_URL:${data.image1_url}]`
          );
        }
        if (data.image2_url) {
          newScript = newScript.replace(
            /(### \[THUMBNAIL KHÔNG CHỮ\][\s\S]*?)(?=\n### |$)/,
            (m) => m.replace(/\[IMAGE_URL:.*?\]/g, '').trimEnd() + `\n\n[IMAGE_URL:${data.image2_url}]`
          );
        }
        setResultText(newScript);
      } else {
        alert('Lỗi tạo thumbnail: ' + (data.error || 'Unknown error'));
      }
    } catch (err) {
      alert('Không thể kết nối Backend.');
    } finally {
      setGeneratingThumbnailType(null);
    }
  };
  const handleGenerateAudio = async () => {
    if (!currentVideoId) return;
    setIsGenAudio(true);
    let keepPolling = false;
    try {
      const res = await fetch(`http://127.0.0.1:8080/api/videos/${currentVideoId}/generate-audio`, {
        method: 'POST',
      });
      const data = await res.json();
      if (!data.success) {
        setAudioStatus(data.audio_task?.status || 'failed');
        alert('Lỗi: ' + (data.error || 'Không thể tạo audio.'));
        return;
      }

      const status = data.audio_task?.status || 'pending';
      setAudioStatus(status);
      keepPolling = status === 'pending' || status === 'processing';
      if (status === 'completed') {
        const videoResponse = await fetch(
          `http://127.0.0.1:8080/api/videos/${currentVideoId}`
        );
        const video = await videoResponse.json();
        setResultText(video.generated_script);
      }
    } catch (err) {
      alert('Không thể kết nối Backend.');
    } finally {
      setIsGenAudio(keepPolling);
    }
  };

  const handleRetryAudio = async () => {
    if (!currentVideoId) return;
    const confirmed = window.confirm(
      'Genmax sẽ trừ credit thêm một lần. Bạn có chắc muốn retry audio?'
    );
    if (!confirmed) return;

    setIsGenAudio(true);
    try {
      const response = await fetch(
        `http://127.0.0.1:8080/api/videos/${currentVideoId}/retry-audio`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirm_credit_charge: true })
        }
      );
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.detail || data.error || 'Không thể retry audio.');
      }
      setAudioStatus(data.audio_task?.status || 'pending');
    } catch (error) {
      setIsGenAudio(false);
      alert('Lỗi: ' + error.message);
    }
  };


  const parseSections = (text) => {
    if (!text) return [];
    
    const sections = [];
    const parts = text.split(/### \[(.*?)\]/g);
    
    let mainScriptContent = '';

    const splitMetadataContent = (text) => {
      const subs = [];
      const lines = text.split('\n');
      let currentTitle = 'METADATA';
      let currentBody = '';
      
      const flush = () => {
        if (currentBody.trim()) {
          subs.push({ title: currentTitle, content: currentBody.trim() });
        }
        currentBody = '';
      };

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        // Remove leading markdown asterisks, dashes, numbers, etc for matching
        const cleanLower = line.toLowerCase().replace(/^[\*\-\d\.\s]+/, '').trim();
        
        if (cleanLower.startsWith('tiêu đề') && cleanLower.includes(':')) {
          flush(); currentTitle = 'TIÊU ĐỀ VIDEO'; currentBody += line.split(':').slice(1).join(':').trim() + '\n';
        } else if (cleanLower.startsWith('url slug') && cleanLower.includes(':')) {
          flush(); currentTitle = 'URL SLUG'; currentBody += line.split(':').slice(1).join(':').trim() + '\n';
        } else if (cleanLower.startsWith('mô tả') && cleanLower.includes(':')) {
          flush(); currentTitle = 'MÔ TẢ VIDEO'; currentBody += line.split(':').slice(1).join(':').trim() + '\n';
        } else if (cleanLower.startsWith('bình luận ghim') && cleanLower.includes(':')) {
          flush(); currentTitle = 'BÌNH LUẬN GHIM'; currentBody += line.split(':').slice(1).join(':').trim() + '\n';
        } else if (cleanLower.includes('câu hỏi:') || cleanLower.startsWith('theo các bạn')) {
          flush(); currentTitle = 'QUIZ TƯƠNG TÁC'; currentBody += line + '\n';
        } else {
          // Lines like hashtags (#) will just fall in here and be appended to the current block
          currentBody += line + '\n';
        }
      }
      flush();
      return subs;
    };

    for (let i = 1; i < parts.length; i += 2) {
      const tag = parts[i];
      const content = parts[i+1] ? parts[i+1].trim() : '';
      
      if (tag === 'INTRO' || tag === 'BODY' || tag === 'OUTRO') {
        if (mainScriptContent) mainScriptContent += '\n\n';
        mainScriptContent += content;
      } else if (tag === 'METADATA & QUIZ') {
        const parsedMeta = splitMetadataContent(content);
        sections.push(...parsedMeta);
      } else if (tag !== 'IMAGE' && tag !== 'AUDIO') {
        sections.push({
          title: tag,
          content: content
        });
      }
    }
    
    if (mainScriptContent) {
      sections.unshift({
        title: 'NỘI DUNG KỊCH BẢN',
        content: mainScriptContent
      });
    }

    // --- NEW LOGIC: Combine MÔ TẢ VIDEO, HASHTAG, and CHAPTERS ---
    let finalSections = [];
    let moTaIndex = -1;
    let chaptersContent = '';

    // First pass to extract chapters content
    sections.forEach((sec, idx) => {
      if (sec.title === 'CHAPTERS') {
        chaptersContent = sec.content;
      }
    });

    // Second pass to build final sections
    sections.forEach(sec => {
      if (sec.title === 'CHAPTERS') {
        // Skip it, we merge it into MÔ TẢ VIDEO
        return;
      }
      if (sec.title === 'MÔ TẢ VIDEO') {
        let combinedContent = sec.content;
        if (chaptersContent) {
          combinedContent += '\n\n' + chaptersContent;
        }
        finalSections.push({
          title: 'MÔ TẢ VIDEO & CHAPTERS',
          content: combinedContent
        });
      } else {
        finalSections.push(sec);
      }
    });

    if (finalSections.length === 0 && text.trim()) {
      finalSections.push({
        title: 'KẾT QUẢ TRẢ VỀ',
        content: text.trim()
      });
    }

    return finalSections;
  };

  const displayTranscript = fullTranscript.length > 1000 
    ? fullTranscript.substring(0, 1000) + '...\n\n(Nội dung đã được thu gọn để dễ nhìn)'
    : fullTranscript;

  let parsedSections = [];
  let imageUrl = '';
  let audioUrl = '';
  
  if (resultText) {
    if (resultText.includes('### [IMAGE]')) {
      const parts = resultText.split('### [IMAGE]');
      imageUrl = parts[1].split('###')[0].trim();
    }
    if (resultText.includes('### [AUDIO]')) {
      const parts = resultText.split('### [AUDIO]');
      audioUrl = parts[1].split('###')[0].trim();
    }
    parsedSections = parseSections(resultText);
  }

  return (
    <div className="app-container">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-icon">YT</div>
          <div className="brand-name">Auto_YT</div>
        </div>
        <ul className="nav-menu">
          <li className={`nav-item ${activeView === 'dashboard' ? 'active' : ''}`} onClick={() => setActiveView('dashboard')}>Dashboard</li>
          <li className={`nav-item ${activeView === 'fetcher' ? 'active' : ''}`} onClick={() => setActiveView('fetcher')}>Video Fetcher</li>
          <li className={`nav-item ${activeView === 'autologin' ? 'active' : ''}`} onClick={() => setActiveView('autologin')}>Auto Login</li>
          <li className={`nav-item ${activeView === 'settings' ? 'active' : ''}`} onClick={() => setActiveView('settings')}>Settings</li>
        </ul>
      </aside>

      {/* Main Content Area */}
      <main className="main-content">
        <header className="header">
          <div className="status-badge">
            <span className="status-dot"></span>
            Ready
          </div>
        </header>

        <div className="view-container">
          {activeView === 'autologin' ? (
            <AutoLogin />
          ) : activeView === 'settings' ? (
            <Settings />
          ) : activeView === 'dashboard' ? (
            <>
              <h1 className="hero-title">Video Library</h1>
              <p className="hero-subtitle">All your automatically saved video scripts are here.</p>
              
              {/* Filter bar + stats */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '20px', marginBottom: '20px', flexWrap: 'wrap', gap: '12px' }}>
                {/* Stats */}
                <div style={{ display: 'flex', gap: '16px', alignItems: 'center' }}>
                  <span style={{ color: '#aaa', fontSize: '0.9em' }}>
                    📦 Tổng: <strong style={{color:'white'}}>{(savedVideos.count_published || 0) + (savedVideos.count_unpublished || 0)}</strong> video
                  </span>
                  <span style={{ color: '#aaa', fontSize: '0.9em' }}>
                    ✅ Đã đăng: <strong style={{color:'#4caf50'}}>{savedVideos.count_published || 0}</strong>
                  </span>
                  <span style={{ color: '#aaa', fontSize: '0.9em' }}>
                    ⏳ Chưa đăng: <strong style={{color:'#f39c12'}}>{savedVideos.count_unpublished || 0}</strong>
                  </span>
                </div>
                {/* Filter buttons */}
                <div style={{ display: 'flex', gap: '8px' }}>
                  {[['all', '🗂️ Tất cả'], ['published', '✅ Đã đăng'], ['unpublished', '⏳ Chưa đăng']].map(([val, label]) => (
                    <button
                      key={val}
                      onClick={() => { setPublishFilter(val); setCurrentPage(1); fetchSavedVideos(1, val); }}
                      style={{
                        padding: '6px 14px', borderRadius: '20px', fontSize: '0.85em', cursor: 'pointer', fontWeight: '600',
                        border: publishFilter === val
                          ? (val === 'published' ? '1px solid #4caf50' : val === 'unpublished' ? '1px solid #f39c12' : '1px solid var(--accent)')
                          : '1px solid #444',
                        background: publishFilter === val
                          ? (val === 'published' ? 'rgba(76,175,80,0.2)' : val === 'unpublished' ? 'rgba(243,156,18,0.2)' : 'rgba(155,89,182,0.2)')
                          : 'transparent',
                        color: publishFilter === val
                          ? (val === 'published' ? '#4caf50' : val === 'unpublished' ? '#f39c12' : 'var(--accent)')
                          : '#888',
                        transition: 'all 0.2s'
                      }}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="video-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '20px' }}>
                {savedVideos.items.length === 0 ? (
                  <p style={{ color: '#888' }}>No saved videos yet. Fetch a video first!</p>
                ) : (
                  savedVideos.items.map(video => {
                    let cleanSnippet = "";
                    if (video.snippet) {
                       cleanSnippet = video.snippet.replace(/### \[[^\]]+\]/g, '').replace(/\n/g, ' ').trim();
                       if (cleanSnippet.length > 80) cleanSnippet = cleanSnippet.substring(0, 80) + '...';
                    }
                    const isPublished = Boolean(video.is_published);
                    return (
                      <div key={video.id} className="result-panel" style={{ padding: '20px', display: 'flex', flexDirection: 'column', borderTop: `3px solid ${isPublished ? '#4caf50' : '#f39c12'}` }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '8px' }}>
                          <h4 style={{ margin: 0, color: 'white', flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', paddingRight: '8px' }}>{video.title}</h4>
                          <span style={{ 
                            fontSize: '0.75em', fontWeight: 'bold', padding: '3px 8px', borderRadius: '12px', whiteSpace: 'nowrap',
                            backgroundColor: isPublished ? 'rgba(76,175,80,0.15)' : 'rgba(243,156,18,0.15)',
                            color: isPublished ? '#4caf50' : '#f39c12',
                            border: `1px solid ${isPublished ? '#4caf50' : '#f39c12'}`
                          }}>
                            {isPublished ? '✅ Đã đăng' : '⏳ Chưa đăng'}
                          </span>
                        </div>
                        <p style={{ color: '#888', fontSize: '0.9em', marginBottom: '8px' }}>{new Date(video.created_at).toLocaleString('vi-VN')}</p>
                        <div style={{ display: 'flex', gap: '15px', marginBottom: '10px', alignItems: 'center' }}>
                          <a href={video.url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)', fontSize: '0.9em', textDecoration: 'none' }}>▶ Xem YouTube</a>
                          {video.chat_url && (
                            <a href={video.chat_url} target="_blank" rel="noreferrer" style={{ color: '#2ecc71', fontSize: '0.9em', textDecoration: 'none' }}>💬 Chat Gốc</a>
                          )}
                          {video.prompt_version && (
                            <span style={{
                              marginLeft: 'auto',
                              fontSize: '0.75em',
                              backgroundColor: 'rgba(155, 89, 182, 0.15)',
                              color: '#c39bd3',
                              padding: '2px 8px',
                              borderRadius: '4px',
                              border: '1px solid rgba(155, 89, 182, 0.4)'
                            }} title="Bộ Prompt sử dụng">
                              🤖 {promptVersions.find(p => p.key === video.prompt_version)?.name || video.prompt_version}
                            </span>
                          )}
                        </div>
                        
                        <div style={{ backgroundColor: '#1a1a1a', padding: '10px', borderRadius: '6px', marginBottom: '15px', fontSize: '0.85em', color: '#ccc', fontStyle: 'italic' }}>
                          {cleanSnippet || 'Không có nội dung...'}
                        </div>
                        
                        <div style={{ marginTop: 'auto', display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                          <button className="btn-run" style={{ padding: '8px', flex: 1, fontSize: '0.9em' }} onClick={() => viewSavedVideo(video.id)}>📄 Xem Script</button>
                          <button 
                            style={{ 
                              padding: '8px 12px', fontSize: '0.85em', border: 'none', borderRadius: '6px', cursor: 'pointer', fontWeight: 'bold',
                              backgroundColor: isPublished ? 'rgba(76,175,80,0.2)' : 'rgba(243,156,18,0.2)',
                              color: isPublished ? '#4caf50' : '#f39c12',
                            }}
                            onClick={() => togglePublish(video.id, isPublished)}
                          >
                            {isPublished ? '↩ Bỏ đăng' : '📤 Đánh dấu đã đăng'}
                          </button>
                          <button className="btn-secondary" style={{ padding: '8px', fontSize: '0.9em' }} onClick={() => deleteSavedVideo(video.id)}>🗑️</button>
                        </div>
                      </div>
                    )
                  })
                )}
              </div>

              {/* Pagination */}
              {savedVideos.total > PAGE_SIZE && (
                <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '8px', marginTop: '30px' }}>
                  <button 
                    className="btn-secondary"
                    style={{ padding: '8px 16px', opacity: currentPage === 1 ? 0.4 : 1 }}
                    disabled={currentPage === 1}
                    onClick={() => handlePageChange(currentPage - 1)}
                  >← Trước</button>
                  
                  {Array.from({ length: Math.ceil(savedVideos.total / PAGE_SIZE) }, (_, i) => i + 1).map(page => (
                    <button
                      key={page}
                      onClick={() => handlePageChange(page)}
                      style={{
                        padding: '8px 14px', borderRadius: '6px', border: '1px solid #444', cursor: 'pointer', fontWeight: 'bold',
                        backgroundColor: currentPage === page ? 'var(--accent)' : 'transparent',
                        color: currentPage === page ? '#000' : '#fff'
                      }}
                    >{page}</button>
                  ))}

                  <button 
                    className="btn-secondary"
                    style={{ padding: '8px 16px', opacity: currentPage === Math.ceil(savedVideos.total / PAGE_SIZE) ? 0.4 : 1 }}
                    disabled={currentPage === Math.ceil(savedVideos.total / PAGE_SIZE)}
                    onClick={() => handlePageChange(currentPage + 1)}
                  >Sau →</button>
                </div>
              )}
            </>
          ) : (
            <>
              <h1 className="hero-title">Video Content Fetcher</h1>
              <p className="hero-subtitle">Paste any YouTube URL to extract its core content and transcripts automatically.</p>

              <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '24px' }}>
                <div style={{ 
                  display: 'flex', alignItems: 'center', gap: '12px', 
                  background: 'rgba(255, 255, 255, 0.03)', 
                  padding: '12px 24px', 
                  borderRadius: '12px', 
                  border: '1px solid rgba(255, 255, 255, 0.1)',
                  boxShadow: '0 4px 6px rgba(0, 0, 0, 0.1)',
                  backdropFilter: 'blur(10px)'
                }}>
                  <label htmlFor="prompt-version-select" style={{ 
                    color: '#e0e0e0', 
                    fontSize: '15px',
                    fontWeight: '500',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px'
                  }}>
                    <span style={{ fontSize: '18px' }}>🤖</span> Bộ Prompt:
                  </label>
                  <div style={{ position: 'relative' }}>
                    <select 
                      id="prompt-version-select"
                      value={selectedPromptVersion}
                      onChange={(e) => setSelectedPromptVersion(e.target.value)}
                      style={{
                        appearance: 'none',
                        background: 'rgba(155, 89, 182, 0.15)', 
                        color: 'white', 
                        border: '1px solid rgba(155, 89, 182, 0.4)', 
                        padding: '8px 36px 8px 16px', 
                        borderRadius: '8px', 
                        outline: 'none',
                        fontSize: '15px',
                        fontWeight: '600',
                        cursor: 'pointer',
                        transition: 'all 0.2s ease',
                      }}
                      onMouseOver={(e) => {
                        e.target.style.background = 'rgba(155, 89, 182, 0.25)';
                        e.target.style.borderColor = 'rgba(155, 89, 182, 0.6)';
                      }}
                      onMouseOut={(e) => {
                        e.target.style.background = 'rgba(155, 89, 182, 0.15)';
                        e.target.style.borderColor = 'rgba(155, 89, 182, 0.4)';
                      }}
                    >
                      {promptVersions.map(v => (
                        <option key={v.key} value={v.key} style={{ background: '#1a1a1a', color: 'white' }}>
                          {v.name}
                        </option>
                      ))}
                    </select>
                    <div style={{
                      position: 'absolute', right: '12px', top: '50%', transform: 'translateY(-50%)',
                      pointerEvents: 'none', color: '#c39bd3', fontSize: '12px'
                    }}>▼</div>
                  </div>
                </div>
              </div>

              <div className="input-group">
                <input 
                  type="text" 
                  className="hero-input" 
                  placeholder="https://www.youtube.com/watch?v=..."
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleRun()}
                />
                <button className="btn-run" onClick={handleRun} disabled={isFetching}>
                  {isFetching ? 'Generating (5-10 mins)...' : 'Lên Kịch Bản & Audio ⚡'}
                </button>
              </div>

              {isFetching && !showResult && (
                <div className="result-panel" style={{ textAlign: 'center', padding: '40px' }}>
                  <h3 style={{ color: 'var(--accent)' }}>Executing Multi-Step AI Workflow...</h3>
                  <p style={{ color: '#aaa', marginTop: '12px', fontSize: '1.05em', fontWeight: '500' }}>
                    {progressMsg || '...'}
                  </p>
                  <p style={{ color: '#666', marginTop: '8px', fontSize: '0.85em' }}>
                    Quá trình có thể mất <strong>5-15 phút</strong>. Bạn có thể mở tab khác trong lúc chờ.
                  </p>
                  <div style={{ marginTop: '20px', display: 'flex', justifyContent: 'center', gap: '8px' }}>
                    <div className="status-dot" style={{ animation: 'pulse 1.5s infinite' }}></div>
                    <div className="status-dot" style={{ animation: 'pulse 1.5s infinite 0.2s' }}></div>
                    <div className="status-dot" style={{ animation: 'pulse 1.5s infinite 0.4s' }}></div>
                  </div>
                </div>
              )}

              {showResult && (
                <div className="result-panel">
                  <div className="result-header">
                    <div className="thumbnail-placeholder">▶</div>
                    <div className="video-info">
                      <h3 title={videoTitle}>
                        {videoTitle || 'Auto_YT Extraction'}
                      </h3>
                      <div style={{display: 'flex', gap: '10px', marginTop: '8px', flexWrap: 'wrap', alignItems: 'center'}}>
                        <button 
                          onClick={() => setActiveTab('summary')}
                          style={{padding: '4px 12px', borderRadius: '4px', border: '1px solid var(--accent)', background: activeTab === 'summary' ? 'var(--accent)' : 'transparent', color: 'white', cursor: 'pointer'}}
                        >
                          Generated Script
                        </button>
                        <button 
                          onClick={() => setActiveTab('transcript')}
                          style={{padding: '4px 12px', borderRadius: '4px', border: '1px solid var(--accent)', background: activeTab === 'transcript' ? 'var(--accent)' : 'transparent', color: 'white', cursor: 'pointer'}}
                        >
                          Raw Transcript
                        </button>
                        <button
                          onClick={() => handleGenerateThumbnail('with_text')}
                          disabled={generatingThumbnailType !== null}
                          style={{
                            padding: '4px 14px', borderRadius: '4px', cursor: generatingThumbnailType ? 'not-allowed' : 'pointer',
                            border: '1px solid #9b59b6', background: generatingThumbnailType ? '#333' : 'rgba(155,89,182,0.2)',
                            color: generatingThumbnailType ? '#888' : '#c39bd3', fontWeight: 'bold'
                          }}
                        >
                          {generatingThumbnailType === 'with_text' ? '⏳ Đang tạo có chữ...' : '🎨 Tạo lại thumbnail có chữ'}
                        </button>
                        <button
                          onClick={() => handleGenerateThumbnail('without_text')}
                          disabled={generatingThumbnailType !== null}
                          style={{
                            padding: '4px 14px', borderRadius: '4px', cursor: generatingThumbnailType ? 'not-allowed' : 'pointer',
                            border: '1px solid #3498db', background: generatingThumbnailType ? '#333' : 'rgba(52,152,219,0.2)',
                            color: generatingThumbnailType ? '#888' : '#85c1e9', fontWeight: 'bold'
                          }}
                        >
                          {generatingThumbnailType === 'without_text' ? '⏳ Đang tạo không chữ...' : '🖼️ Tạo lại thumbnail không chữ'}
                        </button>
                        {currentVideoId && (
                          <button 
                            style={{ 
                              padding: '4px 14px', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold',
                              border: `1px solid ${isCurrentVideoPublished ? '#4caf50' : '#f39c12'}`,
                              backgroundColor: isCurrentVideoPublished ? 'rgba(76,175,80,0.2)' : 'rgba(243,156,18,0.2)',
                              color: isCurrentVideoPublished ? '#4caf50' : '#f39c12',
                            }}
                            onClick={toggleCurrentVideoPublish}
                          >
                            {isCurrentVideoPublished ? '✅ Đã đăng' : '⏳ Chưa đăng'}
                          </button>
                        )}
                        {currentVideoId && !audioUrl && (
                          <button
                            onClick={
                              audioStatus === 'failed'
                                ? handleRetryAudio
                                : handleGenerateAudio
                            }
                            disabled={isGenAudio}
                            style={{
                              padding: '4px 14px', borderRadius: '4px',
                              cursor: isGenAudio ? 'not-allowed' : 'pointer',
                              border: '1px solid #1abc9c',
                              background: isGenAudio ? '#333' : 'rgba(26,188,156,0.2)',
                              color: isGenAudio ? '#888' : '#1abc9c',
                              fontWeight: 'bold'
                            }}
                          >
                            {isGenAudio
                              ? '⏳ Đang chờ Genmax...'
                              : audioStatus === 'failed'
                                ? '⚠️ Retry Audio (sẽ tốn credit)'
                                : '🎵 Tạo Audio'}
                          </button>
                        )}
                        {chatUrl && (
                          <button
                            onClick={() => window.open(chatUrl, '_blank')}
                            style={{
                              padding: '4px 14px', borderRadius: '4px', cursor: 'pointer',
                              border: '1px solid #2ecc71', background: 'rgba(46, 204, 113, 0.2)',
                              color: '#2ecc71', fontWeight: 'bold'
                            }}
                            title="Mở phiên chat gốc trên ChatGPT"
                          >
                            💬 Mở Chat Gốc ↗
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                  
                  <div className="content-blocks" style={{ display: 'flex', flexDirection: 'column', gap: '20px', marginTop: '20px' }}>
                    {errorMsg ? (
                      <span style={{color: '#ff4b4b'}}>{errorMsg}</span>
                    ) : (
                      <>
                        {activeTab === 'summary' && audioUrl && (
                          <div className="result-panel" style={{ padding: '20px' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
                              <h4 style={{color: 'var(--accent)', margin: 0}}>🔊 AI Voice-over:</h4>
                              <button 
                                className="btn-secondary" 
                                style={{ padding: '4px 12px', fontSize: '0.8em', display: 'flex', alignItems: 'center', gap: '5px' }}
                                onClick={handleDownloadAudio}
                              >
                                📥 Tải Audio MP3
                              </button>
                            </div>
                            <audio controls src={audioUrl} style={{width: '100%'}} />
                          </div>
                        )}
                        
                        {activeTab === 'summary' ? (
                          parsedSections.map((section, idx) => {
                            const isMainContent = section.title === 'NỘI DUNG KỊCH BẢN' || section.title === 'KỊCH BẢN CHÍNH';
                            const isThumbnail = section.title.toUpperCase().includes('THUMBNAIL');

                            // Parse [IMAGE_URL:...] from content
                            let displayContent = section.content;
                            let extractedImages = [];
                            const imgRegex = /\[IMAGE_URL:(.*?)\]/g;
                            let match;
                            while ((match = imgRegex.exec(section.content)) !== null) {
                              extractedImages.push(match[1]);
                            }
                            displayContent = displayContent.replace(/\[IMAGE_URL:.*?\]/g, '').trim();

                            if (isThumbnail) {
                              return (
                                <div key={idx} className="result-panel" style={{ padding: '20px', margin: 0 }}>
                                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px', borderBottom: '1px solid #333', paddingBottom: '10px' }}>
                                    <h4 style={{ color: 'var(--accent)', margin: 0 }}>🖼️ {section.title}</h4>
                                    <button
                                      id={`copy-btn-${idx}`}
                                      className="btn-secondary"
                                      style={{ padding: '4px 12px', fontSize: '0.8em' }}
                                      onClick={() => handleCopySection(displayContent, `copy-btn-${idx}`)}
                                    >Copy Prompt</button>
                                  </div>

                                  {extractedImages.length > 0 ? (
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', marginBottom: '15px' }}>
                                      {extractedImages.map((img, i) => {
                                        const imgSrc = img.startsWith('/api/') ? `http://127.0.0.1:8080${img}` : img;
                                        return (
                                        <div key={i} style={{ position: 'relative' }}>
                                          <img
                                            src={imgSrc}
                                            alt={section.title}
                                            crossOrigin="anonymous"
                                            style={{ width: '100%', borderRadius: '10px', border: '2px solid var(--accent)', display: 'block' }}
                                          />
                                          <a
                                            href={imgSrc}
                                            target="_blank"
                                            rel="noreferrer"
                                            style={{
                                              position: 'absolute', bottom: '10px', right: '10px',
                                              background: 'rgba(0,0,0,0.7)', color: 'white',
                                              padding: '5px 10px', borderRadius: '6px', fontSize: '0.8em',
                                              textDecoration: 'none'
                                            }}
                                          >↗ Mở ảnh gốc</a>
                                          <button
                                            onClick={(e) => {
                                              const fileName = section.title.toUpperCase().includes('KHÔNG CHỮ') ? 'thumbnail 1.png' : 'thumbnail.png';
                                              handleDownloadImage(imgSrc, fileName, e);
                                            }}
                                            style={{
                                              position: 'absolute', bottom: '10px', left: '10px',
                                              background: 'rgba(155,89,182,0.85)', color: 'white',
                                              padding: '5px 10px', borderRadius: '6px', fontSize: '0.8em',
                                              textDecoration: 'none',
                                              border: 'none', cursor: 'pointer'
                                            }}
                                          >⬇ Tải về</button>
                                        </div>
                                        );
                                      })}
                                    </div>
                                  ) : (
                                    <div style={{ background: '#1a1a1a', borderRadius: '10px', padding: '40px', textAlign: 'center', color: '#666', marginBottom: '15px' }}>
                                      🎨 Chưa có ảnh thumbnail
                                    </div>
                                  )}

                                  <details style={{ marginTop: '8px' }}>
                                    <summary style={{ cursor: 'pointer', color: '#888', fontSize: '0.85em', userSelect: 'none' }}>
                                      📝 Xem prompt chi tiết...
                                    </summary>
                                    <div style={{ whiteSpace: 'pre-wrap', color: '#aaa', lineHeight: '1.5', marginTop: '10px', fontSize: '0.85em', background: '#111', padding: '12px', borderRadius: '6px' }}>
                                      {displayContent}
                                    </div>
                                  </details>
                                </div>
                              );
                            }

                            return (
                              <div key={idx} className={isMainContent ? "transcript-area" : "result-panel"} style={{ padding: '20px', position: 'relative', margin: 0, backgroundColor: isMainContent ? 'rgba(0,0,0,0.3)' : '' }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px', borderBottom: '1px solid #333', paddingBottom: '10px' }}>
                                  <h4 style={{ color: 'var(--accent)', margin: 0 }}>{section.title}</h4>
                                  <button 
                                    id={`copy-btn-${idx}`}
                                    className="btn-secondary" 
                                    style={{ padding: '4px 12px', fontSize: '0.8em' }}
                                    onClick={() => handleCopySection(displayContent, `copy-btn-${idx}`)}
                                  >
                                    Copy
                                  </button>
                                </div>
                                <div style={{ whiteSpace: 'pre-wrap', color: '#e0e0e0', lineHeight: '1.6' }}>
                                  {displayContent}
                                </div>
                                {extractedImages.length > 0 && (
                                  <div style={{ marginTop: '15px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                    {extractedImages.map((img, i) => (
                                      <img key={i} src={img} alt="Generated Thumbnail" style={{maxWidth: '100%', borderRadius: '8px', border: '1px solid #444'}} />
                                    ))}
                                  </div>
                                )}
                              </div>
                            );
                          })
                        ) : (
                          <div className="transcript-area" style={{ position: 'relative' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px', borderBottom: '1px solid #333', paddingBottom: '10px' }}>
                              <h4 style={{ color: 'var(--accent)', margin: 0 }}>RAW TRANSCRIPT</h4>
                              <button 
                                id="copy-btn-transcript"
                                className="btn-secondary" 
                                style={{ padding: '4px 12px', fontSize: '0.8em' }}
                                onClick={() => handleCopySection(fullTranscript, 'copy-btn-transcript')}
                              >
                                Copy All
                              </button>
                            </div>
                            <div style={{ whiteSpace: 'pre-wrap', color: '#e0e0e0', lineHeight: '1.6' }}>
                              {displayTranscript}
                            </div>
                          </div>
                        )}

                        {activeTab === 'summary' && imageUrl && (
                          <div className="result-panel" style={{ padding: '20px' }}>
                            <h4 style={{color: 'var(--accent)', marginBottom: '15px'}}>🖼️ AI Generated Thumbnail</h4>
                            <img src={imageUrl} alt="DALL-E Thumbnail" style={{maxWidth: '100%', borderRadius: '8px', border: '1px solid #444'}} />
                          </div>
                        )}
                      </>
                    )}
                  </div>
                  
                  {activeTab === 'summary' && (
                    <div className="actions" style={{ marginTop: '20px', justifyContent: 'center' }}>
                      <button className="btn-run" onClick={handleExportTxt} style={{ width: '100%' }}>Download All (TXT)</button>
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </main>
    </div>
  )
}

export default App
