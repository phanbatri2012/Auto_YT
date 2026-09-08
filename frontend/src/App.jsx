import { useState, useEffect, useRef } from 'react'
import './App.css'
import AutoLogin from './AutoLogin'
import AudioReviewPanel from './AudioReviewPanel'
import JobCenter from './JobCenter'
import Settings from './Settings'
import VideoQueuePanel from './VideoQueuePanel'
import YouTubeDownloader from './YouTubeDownloader'

const SECONDS_PER_MINUTE = 60
const SECONDS_PER_HOUR = 60 * SECONDS_PER_MINUTE

function formatAudioDuration(durationSeconds) {
  const totalSeconds = Math.floor(durationSeconds)
  const hours = Math.floor(totalSeconds / SECONDS_PER_HOUR)
  const minutes = Math.floor((totalSeconds % SECONDS_PER_HOUR) / SECONDS_PER_MINUTE)
  const seconds = totalSeconds % SECONDS_PER_MINUTE
  const paddedSeconds = String(seconds).padStart(2, '0')

  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${paddedSeconds}`
  }
  return `${minutes}:${paddedSeconds}`
}

async function saveAudioDuration(videoId, durationSeconds) {
  const response = await fetch(
    `http://127.0.0.1:8080/api/videos/${videoId}/audio-duration`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ duration_seconds: durationSeconds })
    }
  )
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`)
  }
}

function AudioDurationBadge({ videoId, audioUrl, savedDurationSeconds, audioReviewStatus }) {
  const [durationSeconds, setDurationSeconds] = useState(savedDurationSeconds || null)
  const [loadFailed, setLoadFailed] = useState(false)

  useEffect(() => {
    setDurationSeconds(savedDurationSeconds || null)
    setLoadFailed(false)
    if (!audioUrl || savedDurationSeconds) return undefined

    const audio = new Audio()
    const handleLoadedMetadata = () => {
      if (Number.isFinite(audio.duration) && audio.duration > 0) {
        setDurationSeconds(audio.duration)
        saveAudioDuration(videoId, audio.duration)
          .catch(error => console.error('Failed to save audio duration', error))
      } else {
        setLoadFailed(true)
      }
    }
    const handleError = () => setLoadFailed(true)

    audio.preload = 'metadata'
    audio.addEventListener('loadedmetadata', handleLoadedMetadata)
    audio.addEventListener('error', handleError)
    audio.src = audioUrl

    return () => {
      audio.removeEventListener('loadedmetadata', handleLoadedMetadata)
      audio.removeEventListener('error', handleError)
      audio.removeAttribute('src')
      audio.load()
    }
  }, [videoId, audioUrl, savedDurationSeconds])

  let label = 'Chưa có audio'
  if (audioUrl) {
    label = durationSeconds !== null
      ? formatAudioDuration(durationSeconds)
      : loadFailed ? 'Audio không khả dụng' : 'Đang tải...'
  } else if (audioReviewStatus === 'pending') {
    label = 'Chưa tự động kiểm tra'
  } else if (audioReviewStatus === 'blocked') {
    label = 'Kịch bản cần sửa'
  }

  return (
    <span
      title={audioUrl || 'Video chưa có audio'}
      style={{
        color: durationSeconds !== null ? '#4dd0e1' : '#888',
        fontSize: '0.82em',
        fontWeight: '600',
        whiteSpace: 'nowrap'
      }}
    >
      🎵 {label}
    </span>
  )
}

function App() {
  const [activeView, setActiveView] = useState('dashboard') // 'fetcher' or 'dashboard'
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
  const [isGeneratingChapters, setIsGeneratingChapters] = useState(false)
  const [isGeneratingMetadata, setIsGeneratingMetadata] = useState(false)
  const [isGenAudio, setIsGenAudio] = useState(false)
  const [audioStatus, setAudioStatus] = useState('not_started')
  const [audioMissingSegments, setAudioMissingSegments] = useState(0)
  const [progressMsg, setProgressMsg] = useState('')
  const [queueRefreshKey, setQueueRefreshKey] = useState(0)
  const [currentVideoId, setCurrentVideoId] = useState(null)
  const currentVideoIdRef = useRef(null)
  const [videoTitle, setVideoTitle] = useState('')
  const [currentVideoPromptVersion, setCurrentVideoPromptVersion] = useState('')
  const [isCurrentVideoPublished, setIsCurrentVideoPublished] = useState(false)
  const [currentVideoHasCheckpoint, setCurrentVideoHasCheckpoint] = useState(false)
  const [chatGptStatus, setChatGptStatus] = useState({
    busy: false,
    operation: '',
    promptVersion: ''
  })
  
  const [promptVersions, setPromptVersions] = useState([])
  const [selectedPromptVersion, setSelectedPromptVersion] = useState('default')
  const [voiceOptions, setVoiceOptions] = useState([])
  const [globalDefaultVoiceId, setGlobalDefaultVoiceId] = useState('')
  const [selectedVoiceId, setSelectedVoiceId] = useState('')
  const [currentVideoVoiceId, setCurrentVideoVoiceId] = useState('')
  const [currentVideoVoiceName, setCurrentVideoVoiceName] = useState('')
  const [audioTaskVoiceName, setAudioTaskVoiceName] = useState('')
  const [audioReview, setAudioReview] = useState(null)
  const [isLoadingAudioReview, setIsLoadingAudioReview] = useState(false)
  const [regenerateVoiceId, setRegenerateVoiceId] = useState('')
  const [publishFilter, setPublishFilter] = useState('unpublished') // 'all' | 'published' | 'unpublished'
  const [promptVersionFilter, setPromptVersionFilter] = useState('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('')
  const dashboardFetchRequestRef = useRef(0)
  
  const PAGE_SIZE = 10
  const chatGptControlsDisabled =
    isFetching ||
    chatGptStatus.busy ||
    generatingThumbnailType !== null ||
    isGeneratingChapters ||
    isGeneratingMetadata
  const getPromptVersionName = (versionKey) =>
    promptVersions.find(version => version.key === versionKey)?.name ||
    versionKey ||
    'Không xác định'
  const getVoiceName = (voiceId, savedName = '') =>
    savedName ||
    voiceOptions.find(voice => voice.id === voiceId)?.name ||
    'Không xác định'

  const fetchSavedVideos = async (
    page = currentPage,
    filter = publishFilter,
    versionFilter = promptVersionFilter,
    search = debouncedSearchQuery
  ) => {
    const requestId = dashboardFetchRequestRef.current + 1
    dashboardFetchRequestRef.current = requestId
    try {
      const offset = (page - 1) * PAGE_SIZE;
      const params = new URLSearchParams({ limit: PAGE_SIZE, offset });
      if (filter === 'published') params.set('is_published', '1');
      else if (filter === 'unpublished') params.set('is_published', '0');
      if (versionFilter !== 'all') params.set('prompt_version', versionFilter);
      if (search) params.set('search', search);
      const response = await fetch(`http://127.0.0.1:8080/api/videos?${params}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (dashboardFetchRequestRef.current === requestId) {
        setSavedVideos(data);
      }
    } catch (err) {
      console.error("Failed to fetch videos", err);
    }
  };

  const handlePageChange = (newPage) => {
    setCurrentPage(newPage);
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
    fetchVoices();
  }, [])

  useEffect(() => {
    if (activeView === 'fetcher') {
      // Settings may have changed the prompt-specific default voice.
      fetchPromptVersions()
      fetchVoices()
    }
  }, [activeView])

  useEffect(() => {
    if (!promptVersions.length || !voiceOptions.length) return
    const selectedVersion = promptVersions.find(
      version => version.key === selectedPromptVersion
    )
    const promptVoiceId = selectedVersion?.defaultVoiceId || ''
    const promptVoiceExists = voiceOptions.some(
      voice => voice.id === promptVoiceId
    )
    const globalVoiceExists = voiceOptions.some(
      voice => voice.id === globalDefaultVoiceId
    )
    setSelectedVoiceId(
      promptVoiceExists
        ? promptVoiceId
        : globalVoiceExists
          ? globalDefaultVoiceId
          : voiceOptions[0].id
    )
  }, [
    selectedPromptVersion,
    promptVersions,
    voiceOptions,
    globalDefaultVoiceId
  ])

  useEffect(() => {
    const timeoutId = setTimeout(() => {
      setDebouncedSearchQuery(searchQuery.trim())
      setCurrentPage(1)
    }, 300)
    return () => clearTimeout(timeoutId)
  }, [searchQuery])

  useEffect(() => {
    if (activeView === 'dashboard') {
      fetchSavedVideos(
        currentPage,
        publishFilter,
        promptVersionFilter,
        debouncedSearchQuery
      )
    }
  }, [
    activeView,
    currentPage,
    publishFilter,
    promptVersionFilter,
    debouncedSearchQuery
  ])

  useEffect(() => {
    currentVideoIdRef.current = currentVideoId
  }, [currentVideoId])

  useEffect(() => {
    let stopped = false
    const syncChatGptStatus = async () => {
      try {
        const response = await fetch('http://127.0.0.1:8080/api/chatgpt-status')
        const data = await response.json()
        if (!stopped) {
          setChatGptStatus({
            busy: Boolean(data.busy),
            operation: data.operation || '',
            promptVersion: data.prompt_version || ''
          })
        }
      } catch {
        // Keep the last known state during transient backend errors.
      }
    }

    syncChatGptStatus()
    const intervalId = setInterval(syncChatGptStatus, 2000)
    return () => {
      stopped = true
      clearInterval(intervalId)
    }
  }, [])

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
        setAudioMissingSegments(data.audio_task?.missing_segments || 0);
        setAudioTaskVoiceName(data.audio_task?.voice_name || '');
        setIsGenAudio(status === 'pending' || status === 'processing');

        if (status === 'completed') {
          const videoResponse = await fetch(
            `http://127.0.0.1:8080/api/videos/${currentVideoId}`
          );
          const video = await videoResponse.json();
          if (!stopped) {
            setResultText(video.generated_script);
            setCurrentVideoVoiceId(video.voice_id || '');
            setCurrentVideoVoiceName(video.voice_name || '');
            setRegenerateVoiceId(previousVoiceId =>
              video.voice_id || previousVoiceId
            );
          }
          if (intervalId) clearInterval(intervalId);
        } else if ((status === 'failed' || status === 'interrupted') && intervalId) {
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

  useEffect(() => {
    if (!currentVideoId) {
      setAudioReview(null)
      return undefined
    }

    let stopped = false
    setIsLoadingAudioReview(true)
    fetch(`http://127.0.0.1:8080/api/videos/${currentVideoId}/audio-review`)
      .then(async response => {
        const data = await response.json()
        if (!response.ok || !data.success) {
          throw new Error(data.detail || data.error || `HTTP ${response.status}`)
        }
        if (!stopped) setAudioReview(data.audio_review || null)
      })
      .catch(error => {
        if (!stopped) console.error('Failed to load audio review', error)
      })
      .finally(() => {
        if (!stopped) setIsLoadingAudioReview(false)
      })

    return () => {
      stopped = true
    }
  }, [currentVideoId])
  
  const fetchPromptVersions = async () => {
    try {
      const res = await fetch('http://127.0.0.1:8080/api/prompts');
      const data = await res.json();
      if (data.versions) {
        const versionsList = Object.entries(data.versions).map(([key, version]) => ({
          key: key,
          name: version.name,
          defaultVoiceId: version.default_voice_id || ''
        }));
        setPromptVersions(versionsList);
        setSelectedPromptVersion(data.active_version || 'default');
      }
    } catch (err) {
      console.error('Failed to fetch prompt versions', err);
    }
  }

  const fetchVoices = async () => {
    try {
      const response = await fetch('http://127.0.0.1:8080/api/voices')
      const data = await response.json()
      if (!response.ok || !Array.isArray(data.voices)) {
        throw new Error(data.detail || 'Invalid voice configuration')
      }
      setVoiceOptions(data.voices)
      setGlobalDefaultVoiceId(data.active_voice_id || data.voices[0]?.id || '')
      setSelectedVoiceId(data.active_voice_id || data.voices[0]?.id || '')
      setRegenerateVoiceId(previousVoiceId =>
        previousVoiceId || data.active_voice_id || data.voices[0]?.id || ''
      )
    } catch (error) {
      console.error('Failed to fetch voices', error)
    }
  }

  const handleRun = async () => {
    if (!url.trim() || isFetching) return
    const submittedUrl = url.trim()
    setIsFetching(true)
    setErrorMsg('')
    setProgressMsg('Đang thêm video vào hàng đợi...')
    
    try {
      const response = await fetch('http://127.0.0.1:8080/api/process-video', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: submittedUrl,
          prompt_version: selectedPromptVersion,
          voice_id: selectedVoiceId || null
        })
      })
      const data = await response.json()
      if (!response.ok || !data.job_id) {
        throw new Error(data.detail || 'Backend không trả về mã job.')
      }
      setUrl('')
      setProgressMsg(
        data.duplicate
          ? 'Video này đã có trong hàng đợi; hệ thống không tạo job trùng.'
          : data.status === 'running'
          ? 'Video đã bắt đầu xử lý.'
          : `Đã thêm vào hàng đợi${data.queue_position ? ` ở vị trí #${data.queue_position}` : ''}.`
      )
      setQueueRefreshKey(value => value + 1)
    } catch (error) {
      setErrorMsg(error.message || 'Không thể kết nối tới backend Python.')
    } finally {
      setIsFetching(false)
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
      setCurrentVideoPromptVersion(data.prompt_version || '');
      setCurrentVideoVoiceId(data.voice_id || '');
      setCurrentVideoVoiceName(data.voice_name || '');
      setAudioTaskVoiceName('');
      setRegenerateVoiceId(data.voice_id || selectedVoiceId);
      setShowResult(true);
      setActiveView('fetcher');
      setCurrentVideoId(id);  // track which video is loaded
      setAudioReview(null);
      setIsCurrentVideoPublished(Boolean(data.is_published));
      setCurrentVideoHasCheckpoint(Boolean(data.has_checkpoint));
      setAudioStatus('not_started');
      setAudioMissingSegments(0);
      setErrorMsg('');
      setProgressMsg('');
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

  const handleContinueGeneration = async () => {
    if (!currentVideoId || chatGptControlsDisabled) return;
    setIsFetching(true);
    setProgressMsg('⏳ Đang tiếp tục tạo...');
    setErrorMsg('');
    setShowResult(false);

    try {
      const response = await fetch(`http://127.0.0.1:8080/api/videos/${currentVideoId}/continue-generation`, {
        method: 'POST',
      });
      const data = await response.json();
      if (!response.ok || !data.job_id) {
        throw new Error(data.detail || data.error || 'Lỗi gọi API tiếp tục.');
      }

      const job_id = data.job_id;
      // Poll every 3 seconds until done or error
      await new Promise((resolve) => {
        const interval = setInterval(async () => {
          try {
            const res = await fetch(`http://127.0.0.1:8080/api/jobs/${job_id}`);
            const job = await res.json();
            setProgressMsg(job.progress || '...');
            if (job.status === 'done') {
              clearInterval(interval);
              const resultData = job.result;
              if (resultData) {
                setResultText(resultData.summary);
                setFullTranscript(resultData.full_transcript);
                setChatUrl(resultData.chat_url || '');
                setCurrentVideoId(resultData.video_id);
                setAudioReview(resultData.audio_review || null);
                setVideoTitle(resultData.title || '');
                setCurrentVideoHasCheckpoint(Boolean(resultData.failed_step));
                if (resultData.generation_warning) {
                  setErrorMsg(
                    'Đã lưu phần nội dung hoàn tất. Bước cần tạo lại: ' +
                    resultData.generation_warning
                  );
                }
              }
              resolve();
            } else if (job.status === 'error') {
              clearInterval(interval);
              setErrorMsg(job.error || 'Đã xảy ra lỗi không xác định.');
              resolve();
            }
          } catch {
            // ignore transient fetch errors
          }
        }, 3000);
      });
    } catch {
      setErrorMsg('Lỗi tiếp tục: ' + err.message);
    } finally {
      setIsFetching(false);
      setShowResult(true);
      await fetchSavedVideos(currentPage, publishFilter);
    }
  };

  const handleGenerateThumbnail = async (thumbnailType) => {
    if (!resultText || chatGptControlsDisabled) return;
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
        const normalizeThumbnailUrls = (urls, fallbackUrl) => {
          const candidates = Array.isArray(urls) && urls.length > 0
            ? urls
            : (fallbackUrl ? [fallbackUrl] : []);
          return [...new Set(candidates.filter(Boolean))].slice(0, 2);
        };
        const patchThumbnailImages = (script, sectionTitle, imageUrls) => {
          if (imageUrls.length === 0) return script;
          const imageMarkers = imageUrls
            .map((imageUrl) => `[IMAGE_URL:${imageUrl}]`)
            .join('\n\n');
          return script.replace(
            new RegExp(`(### \\[${sectionTitle}\\][\\s\\S]*?)(?=\\n### |$)`),
            (section) => section.replace(/\[IMAGE_URL:.*?\]/g, '').trimEnd() + `\n\n${imageMarkers}`
          );
        };

        let newScript = resultText;
        newScript = patchThumbnailImages(
          newScript,
          'THUMBNAIL CÓ CHỮ',
          normalizeThumbnailUrls(data.image1_urls, data.image1_url)
        );
        newScript = patchThumbnailImages(
          newScript,
          'THUMBNAIL KHÔNG CHỮ',
          normalizeThumbnailUrls(data.image2_urls, data.image2_url)
        );
        setResultText(newScript);
      } else {
        alert('Lỗi tạo thumbnail: ' + (data.error || 'Unknown error'));
      }
    } catch {
      alert('Không thể kết nối Backend.');
    } finally {
      setGeneratingThumbnailType(null);
    }
  };

  const handleGenerateChapters = async () => {
    if (!currentVideoId || chatGptControlsDisabled) return;
    const requestedVideoId = currentVideoId;
    setIsGeneratingChapters(true);
    try {
      const response = await fetch('http://127.0.0.1:8080/api/generate-chapters', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video_id: requestedVideoId })
      });
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.detail || data.error || 'Không thể tạo lại chapter.');
      }
      if (data.video_id !== requestedVideoId) {
        throw new Error('Backend trả về sai video. Giao diện chưa được cập nhật.');
      }
      if (currentVideoIdRef.current === requestedVideoId) {
        setResultText(data.script);
        setErrorMsg(previousError =>
          previousError.toLowerCase().includes('chapters:')
            ? ''
            : previousError
        );
      } else {
        alert('Chapter đã được cập nhật. Hãy mở lại đúng video để xem kết quả.');
      }
      await fetchSavedVideos(currentPage, publishFilter);
    } catch (error) {
      alert('Lỗi tạo chapter: ' + error.message);
    } finally {
      setIsGeneratingChapters(false);
    }
  };

  const handleGenerateMetadata = async () => {
    if (!currentVideoId || chatGptControlsDisabled) return;
    const requestedVideoId = currentVideoId;
    setIsGeneratingMetadata(true);
    try {
      const response = await fetch('http://127.0.0.1:8080/api/generate-metadata', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video_id: requestedVideoId })
      });
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.detail || data.error || 'Không thể tạo lại tiêu đề.');
      }
      if (data.video_id !== requestedVideoId) {
        throw new Error('Backend trả về sai video. Giao diện chưa được cập nhật.');
      }

      const refreshedResponse = await fetch(
        `http://127.0.0.1:8080/api/videos/${requestedVideoId}?_=${Date.now()}`,
        { cache: 'no-store' }
      );
      if (!refreshedResponse.ok) {
        throw new Error('Không thể tải metadata mới từ database.');
      }
      const refreshedVideo = await refreshedResponse.json();
      if (currentVideoIdRef.current === requestedVideoId) {
        setResultText(refreshedVideo.generated_script);
      } else {
        alert('Metadata đã được cập nhật. Hãy mở lại đúng video để xem kết quả.');
      }
      await fetchSavedVideos(currentPage, publishFilter);
    } catch (error) {
      alert('Lỗi tạo metadata: ' + error.message);
    } finally {
      setIsGeneratingMetadata(false);
    }
  };

  const handleGenerateAudio = async () => {
    if (!currentVideoId) return;
    if (audioReview && !audioReview.can_approve) {
      alert('Kịch bản không đạt kiểm tra tự động. Hãy sửa nội dung trước khi tạo audio.');
      return;
    }

    setIsGenAudio(true);
    let keepPolling = false;
    try {
      const res = await fetch(
        `http://127.0.0.1:8080/api/videos/${currentVideoId}/generate-audio`,
        { method: 'POST' }
      );
      const data = await res.json();
      if (data.audio_review) setAudioReview(data.audio_review);
      if (!data.success) {
        setAudioStatus(data.audio_task?.status || 'not_started');
        setAudioMissingSegments(data.audio_task?.missing_segments || 0);
        alert('Lỗi: ' + (data.detail || data.error || 'Không thể tạo audio.'));
        return;
      }

      const status = data.audio_task?.status || 'pending';
      setAudioStatus(status);
      setAudioMissingSegments(data.audio_task?.missing_segments || 0);
      setAudioTaskVoiceName(data.audio_task?.voice_name || '');
      keepPolling = status === 'pending' || status === 'processing';
      if (status === 'completed') {
        const videoResponse = await fetch(
          `http://127.0.0.1:8080/api/videos/${currentVideoId}`
        );
        const video = await videoResponse.json();
        setResultText(video.generated_script);
        setCurrentVideoVoiceId(video.voice_id || '');
        setCurrentVideoVoiceName(video.voice_name || '');
      }
    } catch {
      alert('Không thể kết nối Backend.');
    } finally {
      setIsGenAudio(keepPolling);
    }
  };

  const handleRetryAudio = async () => {
    if (!currentVideoId) return;
    if (audioReview?.status === 'blocked') {
      alert('Kịch bản không đạt kiểm tra tự động nên chưa thể retry audio.');
      return;
    }
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
      setAudioMissingSegments(data.audio_task?.missing_segments || 0);
      setAudioTaskVoiceName(data.audio_task?.voice_name || '');
    } catch (error) {
      setIsGenAudio(false);
      alert('Lỗi: ' + error.message);
    }
  };

  const handleRegenerateAudio = async () => {
    if (!currentVideoId || !regenerateVoiceId || isGenAudio) return;
    if (audioReview?.status === 'blocked') {
      alert('Kịch bản không đạt kiểm tra tự động nên chưa thể tạo lại audio.');
      return;
    }
    const voiceName = getVoiceName(regenerateVoiceId);
    const confirmed = window.confirm(
      `Tạo lại toàn bộ audio bằng giọng "${voiceName}" sẽ tốn credit Genmax. ` +
      'Audio cũ được giữ cho đến khi audio mới hoàn thành. Bạn có tiếp tục không?'
    );
    if (!confirmed) return;

    setIsGenAudio(true);
    try {
      const response = await fetch(
        `http://127.0.0.1:8080/api/videos/${currentVideoId}/regenerate-audio`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            voice_id: regenerateVoiceId,
            confirm_credit_charge: true
          })
        }
      );
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.detail || data.error || 'Không thể tạo lại audio.');
      }
      const status = data.audio_task?.status || 'pending';
      setAudioStatus(status);
      setAudioMissingSegments(data.audio_task?.missing_segments || 0);
      setAudioTaskVoiceName(data.audio_task?.voice_name || voiceName);
      setIsGenAudio(status === 'pending' || status === 'processing');
      if (status === 'completed') {
        const videoResponse = await fetch(
          `http://127.0.0.1:8080/api/videos/${currentVideoId}`
        );
        const video = await videoResponse.json();
        setResultText(video.generated_script);
        setCurrentVideoVoiceId(video.voice_id || '');
        setCurrentVideoVoiceName(video.voice_name || '');
      }
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
        const cleanLower = line.toLowerCase().replace(/^[*\-\d.\s]+/, '').trim();
        
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
    const finalSections = [];
    let chaptersContent = '';

    // First pass to extract chapters content
    sections.forEach((sec) => {
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
          <li className={`nav-item ${activeView === 'jobs' ? 'active' : ''}`} onClick={() => setActiveView('jobs')}>Trung tâm Job</li>
          <li className={`nav-item ${activeView === 'fetcher' ? 'active' : ''}`} onClick={() => setActiveView('fetcher')}>Video Fetcher</li>
          <li
            className={`nav-item ${activeView === 'downloader' ? 'active' : ''}`}
            onClick={() => setActiveView('downloader')}
          >YouTube Downloader</li>
          <li
            className={`nav-item ${activeView === 'autologin' ? 'active' : ''}`}
            aria-disabled={chatGptControlsDisabled}
            onClick={() => !chatGptControlsDisabled && setActiveView('autologin')}
            style={chatGptControlsDisabled ? { opacity: 0.45, cursor: 'not-allowed' } : undefined}
          >Auto Login</li>
          <li
            className={`nav-item ${activeView === 'settings' ? 'active' : ''}`}
            onClick={() => setActiveView('settings')}
          >Settings</li>
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
            <Settings
              lockedPromptVersion={chatGptStatus.promptVersion}
              chatGptOperation={chatGptStatus.operation}
            />
          ) : activeView === 'jobs' ? (
            <JobCenter onOpenVideo={viewSavedVideo} />
          ) : activeView === 'downloader' ? (
            <YouTubeDownloader />
          ) : activeView === 'dashboard' ? (
            <>
              <h1 className="hero-title">Video Library</h1>
              <p className="hero-subtitle">All your automatically saved video scripts are here.</p>

              <div style={{ marginTop: '20px' }}>
                <input
                  type="search"
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="🔎 Tìm theo link gốc, tiêu đề gốc, tiêu đề video hoặc mô tả..."
                  aria-label="Tìm kiếm video"
                  style={{
                    width: '100%',
                    padding: '12px 16px',
                    borderRadius: '10px',
                    border: '1px solid rgba(155, 89, 182, 0.55)',
                    background: '#17131d',
                    color: '#eee',
                    fontSize: '0.95em',
                    outline: 'none'
                  }}
                />
              </div>
              
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
                {/* Version + publication filters */}
                <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
                  <label
                    htmlFor="dashboard-version-filter"
                    style={{ color: '#aaa', fontSize: '0.85em', fontWeight: '600' }}
                  >
                    Phiên bản:
                  </label>
                  <select
                    id="dashboard-version-filter"
                    value={promptVersionFilter}
                    onChange={(event) => {
                      const nextVersion = event.target.value;
                      setPromptVersionFilter(nextVersion);
                      setCurrentPage(1);
                    }}
                    style={{
                      minWidth: '190px', padding: '7px 12px', borderRadius: '8px',
                      border: '1px solid rgba(155, 89, 182, 0.55)',
                      background: '#17131d', color: '#eee', cursor: 'pointer', fontWeight: '600'
                    }}
                  >
                    <option value="all">🤖 Tất cả phiên bản</option>
                    {promptVersions.map(version => (
                      <option key={version.key} value={version.key}>{version.name}</option>
                    ))}
                  </select>
                  {[['all', '🗂️ Tất cả'], ['published', '✅ Đã đăng'], ['unpublished', '⏳ Chưa đăng']].map(([val, label]) => (
                    <button
                      key={val}
                      onClick={() => { setPublishFilter(val); setCurrentPage(1); }}
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
                  <p style={{ color: '#888' }}>
                    {debouncedSearchQuery
                      ? 'Không tìm thấy video phù hợp.'
                      : 'No saved videos yet. Fetch a video first!'}
                  </p>
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
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
                          <p style={{ color: '#888', fontSize: '0.9em', margin: 0 }}>{new Date(video.created_at).toLocaleString('vi-VN')}</p>
                          <AudioDurationBadge
                            videoId={video.id}
                            audioUrl={video.audio_url}
                            savedDurationSeconds={video.audio_duration_seconds}
                            audioReviewStatus={video.audio_review_status}
                          />
                        </div>
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
                              🤖 {getPromptVersionName(video.prompt_version)}
                            </span>
                          )}
                          <span style={{
                            marginLeft: video.prompt_version ? 0 : 'auto',
                            fontSize: '0.75em',
                            backgroundColor: 'rgba(26, 188, 156, 0.12)',
                            color: '#76d7c4', padding: '2px 8px',
                            borderRadius: '4px',
                            border: '1px solid rgba(26,188,156,0.4)',
                            whiteSpace: 'nowrap'
                          }} title={video.voice_id || 'Video cũ chưa lưu Voice ID'}>
                            🎙️ {getVoiceName(video.voice_id, video.voice_name)}
                          </span>
                        </div>
                        
                        <div style={{ backgroundColor: '#1a1a1a', padding: '10px', borderRadius: '6px', marginBottom: '15px', fontSize: '0.85em', color: '#ccc', fontStyle: 'italic' }}>
                          {cleanSnippet || 'Không có nội dung...'}
                        </div>
                        
                        <div style={{ marginTop: 'auto', display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                          <button
                            className="btn-run"
                            style={{ padding: '8px', flex: 1, fontSize: '0.9em' }}
                            onClick={() => viewSavedVideo(video.id)}
                          >📄 Xem Script</button>
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

              <div style={{
                display: 'flex', justifyContent: 'center', marginBottom: '24px',
                gap: '12px', flexWrap: 'wrap'
              }}>
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
                <div style={{
                  display: 'flex', alignItems: 'center', gap: '12px',
                  background: 'rgba(255, 255, 255, 0.03)',
                  padding: '12px 24px', borderRadius: '12px',
                  border: '1px solid rgba(255, 255, 255, 0.1)'
                }}>
                  <label htmlFor="voice-select" style={{
                    color: '#e0e0e0', fontSize: '15px', fontWeight: '500'
                  }}>
                    🎙️ Giọng đọc:
                  </label>
                  <select
                    id="voice-select"
                    value={selectedVoiceId}
                    onChange={(event) => setSelectedVoiceId(event.target.value)}
                    disabled={voiceOptions.length === 0}
                    style={{
                      background: 'rgba(26, 188, 156, 0.15)', color: 'white',
                      border: '1px solid rgba(26, 188, 156, 0.5)',
                      padding: '8px 14px', borderRadius: '8px', outline: 'none',
                      fontSize: '15px', fontWeight: '600', cursor: 'pointer'
                    }}
                  >
                    {voiceOptions.map(voice => (
                      <option
                        key={voice.id}
                        value={voice.id}
                        style={{ background: '#1a1a1a', color: 'white' }}
                      >
                        {voice.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="input-group">
                <input 
                  type="text" 
                  className="hero-input" 
                  placeholder="https://www.youtube.com/watch?v=..."
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !isFetching) handleRun()
                  }}
                />
                <button className="btn-run" onClick={handleRun} disabled={isFetching || !url.trim()}>
                  {isFetching
                    ? 'Đang thêm vào hàng đợi...'
                    : chatGptStatus.busy
                      ? 'Thêm vào hàng đợi ＋'
                      : 'Lên Kịch Bản ⚡'}
                </button>
              </div>

              {progressMsg && (
                <div style={{ color: '#76d7c4', textAlign: 'center', marginTop: '12px' }}>
                  {progressMsg}
                </div>
              )}

              <VideoQueuePanel
                refreshKey={queueRefreshKey}
                onOpenJobCenter={() => setActiveView('jobs')}
                onOpenVideo={viewSavedVideo}
              />

              {showResult && (
                <div className="result-panel">
                  <div className="result-header">
                    <div className="thumbnail-placeholder">▶</div>
                    <div className="video-info">
                      <h3 title={videoTitle}>
                        {videoTitle || 'Auto_YT Extraction'}
                      </h3>
                      {currentVideoId && (
                        <div style={{
                          display: 'inline-flex', alignItems: 'center', gap: '6px',
                          marginTop: '6px', padding: '3px 10px', borderRadius: '5px',
                          border: '1px solid rgba(155, 89, 182, 0.5)',
                          background: 'rgba(155, 89, 182, 0.15)',
                          color: '#c39bd3', fontSize: '0.8em', fontWeight: '600'
                        }} title="Bộ prompt đã được lưu khi tạo video này">
                          🤖 Bộ prompt của video: {getPromptVersionName(currentVideoPromptVersion)}
                        </div>
                      )}
                      {currentVideoId && (
                        <div style={{
                          display: 'inline-flex', alignItems: 'center', gap: '6px',
                          marginTop: '6px', marginLeft: '8px', padding: '3px 10px',
                          borderRadius: '5px', border: '1px solid rgba(26,188,156,0.5)',
                          background: 'rgba(26,188,156,0.12)', color: '#76d7c4',
                          fontSize: '0.8em', fontWeight: '600'
                        }} title={currentVideoVoiceId || 'Video cũ chưa lưu Voice ID'}>
                          🎙️ Giọng đọc: {getVoiceName(
                            currentVideoVoiceId,
                            currentVideoVoiceName
                          )}
                        </div>
                      )}
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
                          disabled={chatGptControlsDisabled}
                          style={{
                            padding: '4px 14px', borderRadius: '4px', cursor: chatGptControlsDisabled ? 'not-allowed' : 'pointer',
                            border: '1px solid #9b59b6', background: chatGptControlsDisabled ? '#333' : 'rgba(155,89,182,0.2)',
                            color: chatGptControlsDisabled ? '#888' : '#c39bd3', fontWeight: 'bold'
                          }}
                        >
                          {generatingThumbnailType === 'with_text' ? '⏳ Đang tạo có chữ...' : '🎨 Tạo lại thumbnail có chữ'}
                        </button>
                        <button
                          onClick={() => handleGenerateThumbnail('without_text')}
                          disabled={chatGptControlsDisabled}
                          style={{
                            padding: '4px 14px', borderRadius: '4px', cursor: chatGptControlsDisabled ? 'not-allowed' : 'pointer',
                            border: '1px solid #3498db', background: chatGptControlsDisabled ? '#333' : 'rgba(52,152,219,0.2)',
                            color: chatGptControlsDisabled ? '#888' : '#85c1e9', fontWeight: 'bold'
                          }}
                        >
                          {generatingThumbnailType === 'without_text' ? '⏳ Đang tạo không chữ...' : '🖼️ Tạo lại thumbnail không chữ'}
                        </button>
                        <button
                          onClick={() => handleGenerateThumbnail('both')}
                          disabled={chatGptControlsDisabled}
                          style={{
                            padding: '4px 14px', borderRadius: '4px', cursor: chatGptControlsDisabled ? 'not-allowed' : 'pointer',
                            border: '1px solid #e67e22', background: chatGptControlsDisabled ? '#333' : 'rgba(230,126,34,0.2)',
                            color: chatGptControlsDisabled ? '#888' : '#f5b041', fontWeight: 'bold'
                          }}
                        >
                          {generatingThumbnailType === 'both' ? '⏳ Đang tạo cả 2...' : '🎨 Tạo lại cả 2 thumbnail'}
                        </button>
                        <button
                          onClick={handleGenerateChapters}
                          disabled={chatGptControlsDisabled}
                          style={{
                            padding: '4px 14px', borderRadius: '4px',
                            cursor: chatGptControlsDisabled ? 'not-allowed' : 'pointer',
                            border: '1px solid #16a085',
                            background: chatGptControlsDisabled ? '#333' : 'rgba(22,160,133,0.2)',
                            color: chatGptControlsDisabled ? '#888' : '#48c9b0',
                            fontWeight: 'bold'
                          }}
                        >
                          {isGeneratingChapters ? '⏳ Đang tạo chapter...' : '🕒 Tạo lại chapter'}
                        </button>
                        <button
                          onClick={handleGenerateMetadata}
                          disabled={chatGptControlsDisabled}
                          title="Tạo lại mục 5: Tiêu đề, URL slug, mô tả và quiz trong cùng chat của video"
                          style={{
                            padding: '4px 14px', borderRadius: '4px',
                            cursor: chatGptControlsDisabled ? 'not-allowed' : 'pointer',
                            border: '1px solid #d4ac0d',
                            background: chatGptControlsDisabled ? '#333' : 'rgba(212,172,13,0.2)',
                            color: chatGptControlsDisabled ? '#888' : '#f7dc6f',
                            fontWeight: 'bold'
                          }}
                        >
                          {isGeneratingMetadata ? '⏳ Đang tạo metadata...' : '✍️ Tạo lại TIÊU ĐỀ'}
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
                        {currentVideoId && (!audioUrl || audioStatus === 'failed') && (
                          <button
                            onClick={
                              audioStatus === 'failed'
                                ? handleRetryAudio
                                : handleGenerateAudio
                            }
                            disabled={
                              isGenAudio ||
                              isLoadingAudioReview ||
                              (audioStatus === 'failed'
                                ? audioReview?.status !== 'approved'
                                : Boolean(audioReview && !audioReview.can_approve))
                            }
                            style={{
                              padding: '4px 14px', borderRadius: '4px',
                              cursor: (isGenAudio || isLoadingAudioReview || Boolean(audioReview && !audioReview.can_approve)) ? 'not-allowed' : 'pointer',
                              border: '1px solid #1abc9c',
                              background: (isGenAudio || isLoadingAudioReview) ? '#333' : 'rgba(26,188,156,0.2)',
                              color: (isGenAudio || isLoadingAudioReview) ? '#888' : '#1abc9c',
                              fontWeight: 'bold'
                            }}
                          >
                            {isGenAudio
                              ? audioStatus === 'interrupted'
                                ? '⏳ Đang tiếp tục Audio...'
                                : '⏳ Đang chờ Genmax...'
                              : audioStatus === 'interrupted'
                                ? `🔄 Tiếp tục Audio${audioMissingSegments ? ` (còn ${audioMissingSegments} phần)` : ''}`
                              : audioStatus === 'failed'
                                ? '⚠️ Retry Audio (sẽ tốn credit)'
                                : audioReview?.status === 'blocked'
                                  ? '⛔ Kịch bản cần sửa'
                                  : '🔎 Kiểm tra tự động & tạo Audio'}
                          </button>
                        )}
                        {chatUrl && currentVideoHasCheckpoint && (
                          <button
                            onClick={handleContinueGeneration}
                            disabled={chatGptControlsDisabled}
                            style={{
                              padding: '4px 14px', borderRadius: '4px', cursor: chatGptControlsDisabled ? 'not-allowed' : 'pointer',
                              border: '1px solid #e74c3c', background: chatGptControlsDisabled ? '#333' : 'rgba(231, 76, 60, 0.2)',
                              color: chatGptControlsDisabled ? '#888' : '#e74c3c', fontWeight: 'bold'
                            }}
                            title="Tiếp tục tiến trình nếu bị lỗi giữa chừng"
                          >
                            ▶️ Tiếp tục tạo
                          </button>
                        )}
                        {chatUrl && !currentVideoHasCheckpoint && resultText && audioUrl && resultText.includes('THUMBNAIL KHÔNG CHỮ') && resultText.includes('CHAPTERS') && (
                          <button
                            disabled={true}
                            style={{
                              padding: '4px 14px', borderRadius: '4px', cursor: 'not-allowed',
                              border: '1px solid #27ae60', background: 'rgba(39, 174, 96, 0.2)',
                              color: '#27ae60', fontWeight: 'bold'
                            }}
                            title="Video này đã được tạo xong toàn bộ."
                          >
                            ✅ Đã Hoàn thành
                          </button>
                        )}
                        {chatUrl && !currentVideoHasCheckpoint && resultText && (!resultText.includes('THUMBNAIL KHÔNG CHỮ') || !resultText.includes('CHAPTERS')) && (
                          <button
                            disabled={true}
                            style={{
                              padding: '4px 14px', borderRadius: '4px', cursor: 'not-allowed',
                              border: '1px solid #c0392b', background: 'rgba(192, 57, 43, 0.2)',
                              color: '#c0392b', fontWeight: 'bold'
                            }}
                            title="Lỗi: Video bị lỗi ngầm từ trước và đã mất bản nháp. Bạn phải tạo lại từ đầu."
                          >
                            ❌ Lỗi (Mất dữ liệu)
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
                    {activeTab === 'summary' && currentVideoId && (
                      <AudioReviewPanel
                        review={audioReview}
                        loading={isLoadingAudioReview}
                        submitting={isGenAudio}
                        voiceName={getVoiceName(currentVideoVoiceId, currentVideoVoiceName)}
                        hasAudio={Boolean(audioUrl)}
                      />
                    )}
                    {errorMsg ? (
                      <span style={{color: '#ff4b4b'}}>{errorMsg}</span>
                    ) : (
                      <>
                        {activeTab === 'summary' && audioUrl && (
                          <div className="result-panel" style={{ padding: '20px' }}>
                            <div style={{
                              display: 'flex', justifyContent: 'space-between',
                              alignItems: 'center', marginBottom: '10px',
                              gap: '12px', flexWrap: 'wrap'
                            }}>
                              <div>
                                <h4 style={{color: 'var(--accent)', margin: 0}}>🔊 AI Voice-over:</h4>
                                <div style={{ color: '#76d7c4', fontSize: '0.8em', marginTop: '5px' }}>
                                  🎙️ Đang sử dụng: {getVoiceName(
                                    currentVideoVoiceId,
                                    currentVideoVoiceName
                                  )}
                                </div>
                                {audioTaskVoiceName &&
                                  ['pending', 'processing', 'interrupted', 'failed'].includes(audioStatus) &&
                                  audioTaskVoiceName !== currentVideoVoiceName && (
                                    <div style={{ color: '#f5b041', fontSize: '0.78em', marginTop: '3px' }}>
                                      ⏳ Audio mới: {audioTaskVoiceName} ({audioStatus})
                                    </div>
                                  )}
                              </div>
                              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                                <select
                                  value={regenerateVoiceId}
                                  onChange={(event) => setRegenerateVoiceId(event.target.value)}
                                  disabled={isGenAudio || voiceOptions.length === 0}
                                  aria-label="Giọng tạo lại audio"
                                  style={{
                                    background: '#17131d', color: '#eee',
                                    border: '1px solid rgba(26,188,156,0.5)',
                                    borderRadius: '6px', padding: '5px 9px'
                                  }}
                                >
                                  {voiceOptions.map(voice => (
                                    <option key={voice.id} value={voice.id}>{voice.name}</option>
                                  ))}
                                </select>
                                <button
                                  className="btn-secondary"
                                  style={{ padding: '4px 12px', fontSize: '0.8em' }}
                                  onClick={handleRegenerateAudio}
                                  disabled={isGenAudio || !regenerateVoiceId}
                                >
                                  {isGenAudio ? '⏳ Audio đang chạy...' : '🎙️ Tạo lại toàn bộ'}
                                </button>
                                <button
                                  className="btn-secondary"
                                  style={{ padding: '4px 12px', fontSize: '0.8em', display: 'flex', alignItems: 'center', gap: '5px' }}
                                  onClick={handleDownloadAudio}
                                >
                                  📥 Tải Audio MP3
                                </button>
                              </div>
                            </div>
                            <audio
                              controls
                              src={audioUrl}
                              style={{width: '100%'}}
                              onLoadedMetadata={(event) => {
                                const durationSeconds = event.currentTarget.duration
                                if (
                                  currentVideoId &&
                                  Number.isFinite(durationSeconds) &&
                                  durationSeconds > 0
                                ) {
                                  saveAudioDuration(currentVideoId, durationSeconds)
                                    .catch(error => console.error('Failed to save audio duration', error))
                                }
                              }}
                            />
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
                                        const isWithoutText = section.title.toUpperCase().includes('KHÔNG CHỮ');
                                        const fileName = `${isWithoutText ? 'thumbnail-khong-chu' : 'thumbnail-co-chu'}-${i + 1}.png`;
                                        return (
                                        <div key={i} style={{ position: 'relative' }}>
                                          {extractedImages.length > 1 && (
                                            <div style={{ color: '#bbb', fontWeight: 600, marginBottom: '7px' }}>
                                              Phương án {i + 1}
                                            </div>
                                          )}
                                          <img
                                            src={imgSrc}
                                            alt={`${section.title} - Phương án ${i + 1}`}
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
