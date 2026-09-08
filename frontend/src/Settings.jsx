import { useState, useEffect } from 'react';
import './Settings.css';

const DEFAULT_PIPELINE = {
  metadata: true,
  chapters: true,
  thumbnail_with_text: true,
  thumbnail_without_text: true,
  audio: true
};

const PIPELINE_STEPS = [
  {
    key: 'metadata',
    label: 'Metadata & Quiz',
    description: 'Tự động tạo tiêu đề, slug, mô tả, hashtag, bình luận ghim và quiz.'
  },
  {
    key: 'chapters',
    label: 'Chapters',
    description: 'Tự động tạo các mốc chapter sau khi kịch bản lõi hoàn tất.'
  },
  {
    key: 'thumbnail_with_text',
    label: 'Thumbnail có chữ',
    description: 'Tự động gửi prompt và lấy ảnh thumbnail có chữ.'
  },
  {
    key: 'thumbnail_without_text',
    label: 'Thumbnail không chữ',
    description: 'Tự động gửi prompt và lấy ảnh thumbnail không chữ.'
  },
  {
    key: 'audio',
    label: 'Tự động tạo audio',
    description: 'Tự kiểm duyệt kịch bản và gửi sang Genmax bằng giọng đã chọn.'
  }
];

export default function Settings({
  lockedPromptVersion = '',
  chatGptOperation = ''
}) {
  const [promptsData, setPromptsData] = useState(null);
  const [voicesData, setVoicesData] = useState(null);
  const [activeVersion, setActiveVersion] = useState('');
  const [loadingMsg, setLoadingMsg] = useState('');
  const [resultMsg, setResultMsg] = useState('');
  const [savingSection, setSavingSection] = useState('');

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    try {
      const [promptsResponse, voicesResponse] = await Promise.all([
        fetch('http://127.0.0.1:8080/api/prompts'),
        fetch('http://127.0.0.1:8080/api/voices')
      ]);
      const prompts = await promptsResponse.json();
      const voices = await voicesResponse.json();
      setPromptsData(prompts);
      setVoicesData(voices);
      setActiveVersion(prompts.active_version);
    } catch (err) {
      console.error("Lỗi khi lấy prompts:", err);
    }
  };

  const handleVoiceChange = (index, field, value) => {
    setVoicesData(prev => {
      const previousVoice = prev.voices[index];
      return {
        ...prev,
        active_voice_id:
          field === 'id' && prev.active_voice_id === previousVoice.id
            ? value
            : prev.active_voice_id,
        voices: prev.voices.map((voice, voiceIndex) =>
          voiceIndex === index ? { ...voice, [field]: value } : voice
        )
      };
    });
  };

  const handleAddVoice = () => {
    setVoicesData(prev => ({
      ...prev,
      voices: [...prev.voices, { id: '', name: '' }]
    }));
  };

  const handleRemoveVoice = (index) => {
    setVoicesData(prev => {
      if (prev.voices.length <= 1) {
        alert('Phải giữ lại ít nhất một giọng đọc.');
        return prev;
      }
      const voices = prev.voices.filter((_, voiceIndex) => voiceIndex !== index);
      const activeVoiceExists = voices.some(
        voice => voice.id === prev.active_voice_id
      );
      return {
        ...prev,
        voices,
        active_voice_id: activeVoiceExists
          ? prev.active_voice_id
          : voices[0].id
      };
    });
  };

  const handlePromptChange = (key, value) => {
    setPromptsData(prev => ({
      ...prev,
      versions: {
        ...prev.versions,
        [activeVersion]: {
          ...prev.versions[activeVersion],
          prompts: {
            ...prev.versions[activeVersion].prompts,
            [key]: value
          }
        }
      }
    }));
  };

  const handleVersionNameChange = (value) => {
    setPromptsData(prev => ({
      ...prev,
      versions: {
        ...prev.versions,
        [activeVersion]: {
          ...prev.versions[activeVersion],
          name: value
        }
      }
    }));
  };

  const handleProjectUrlChange = (value) => {
    setPromptsData(prev => ({
      ...prev,
      versions: {
        ...prev.versions,
        [activeVersion]: {
          ...prev.versions[activeVersion],
          project_url: value
        }
      }
    }));
  };

  const handlePromptDefaultVoiceChange = (voiceId) => {
    setPromptsData(prev => ({
      ...prev,
      versions: {
        ...prev.versions,
        [activeVersion]: {
          ...prev.versions[activeVersion],
          default_voice_id: voiceId
        }
      }
    }));
  };

  const handlePipelineChange = (stepKey, enabled) => {
    setPromptsData(prev => ({
      ...prev,
      versions: {
        ...prev.versions,
        [activeVersion]: {
          ...prev.versions[activeVersion],
          pipeline: {
            ...DEFAULT_PIPELINE,
            ...prev.versions[activeVersion].pipeline,
            [stepKey]: enabled
          }
        }
      }
    }));
  };

  const handleVersionChange = (e) => {
    const newVersion = e.target.value;
    setActiveVersion(newVersion);
    setPromptsData(prev => ({ ...prev, active_version: newVersion }));
  };

  const handleDuplicateVersion = () => {
    const newName = prompt("Nhập tên cho phiên bản mới:");
    if (!newName) return;
    
    const versionId = "v_" + Date.now();
    
    setPromptsData(prev => {
      const newData = { ...prev };
      const currentPrompts = { ...newData.versions[activeVersion].prompts };
      
      newData.versions[versionId] = {
        name: newName,
        project_url: newData.versions[activeVersion].project_url,
        default_voice_id:
          newData.versions[activeVersion].default_voice_id || '',
        pipeline: {
          ...DEFAULT_PIPELINE,
          ...(newData.versions[activeVersion].pipeline || {})
        },
        prompts: currentPrompts
      };
      
      newData.active_version = versionId;
      setActiveVersion(versionId);
      return newData;
    });
  };

  const handleDeleteVersion = () => {
    if (activeVersion === lockedPromptVersion) {
      alert('Bộ prompt này đang được job sử dụng nên chưa thể xóa.');
      return;
    }
    if (Object.keys(promptsData.versions).length <= 1) {
      alert("Không thể xóa phiên bản duy nhất!");
      return;
    }
    
    if (!confirm("Bạn có chắc muốn xóa phiên bản này?")) return;
    
    setPromptsData(prev => {
      const newData = { ...prev };
      delete newData.versions[activeVersion];
      
      const remainingVersions = Object.keys(newData.versions);
      const nextVersion = remainingVersions[0];
      
      newData.active_version = nextVersion;
      setActiveVersion(nextVersion);
      return newData;
    });
  };

  const saveSection = async (sectionKey, loadingText, successText, request) => {
    try {
      setSavingSection(sectionKey);
      setLoadingMsg(loadingText);
      setResultMsg('');
      const response = await request();
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail || 'Không thể lưu thiết lập.');
      }
      setResultMsg(`✅ ${successText}`);
      return result;
    } catch (err) {
      setResultMsg('❌ ' + err.message);
      return null;
    } finally {
      setSavingSection('');
      setLoadingMsg('');
    }
  };

  const handleSaveVersionName = async () => {
    const versionId = activeVersion;
    const name = promptsData.versions[versionId].name;
    const result = await saveSection(
      'version-name',
      'Đang lưu tên bộ prompt...',
      'Đã lưu tên bộ prompt.',
      () => fetch(`http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/name`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
      })
    );
    if (result && activeVersion === versionId) {
      setPromptsData(prev => ({
        ...prev,
        versions: {
          ...prev.versions,
          [versionId]: {
            ...prev.versions[versionId],
            name: result.version.name
          }
        }
      }));
    }
  };

  const handleSaveProject = () => {
    const versionId = activeVersion;
    const projectUrl = promptsData.versions[versionId].project_url;
    return saveSection(
      'project',
      'Đang lưu ChatGPT Project...',
      'Đã lưu ChatGPT Project cho bộ prompt này.',
      () => fetch(`http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/project`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_url: projectUrl })
      })
    );
  };

  const handleSavePromptDefaultVoice = () => {
    const versionId = activeVersion;
    const voiceId = promptsData.versions[versionId].default_voice_id || '';
    return saveSection(
      'prompt-default-voice',
      'Đang lưu giọng mặc định của bộ prompt...',
      'Đã lưu giọng mặc định của bộ prompt.',
      () => fetch(
        `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/default-voice`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ voice_id: voiceId })
        }
      )
    );
  };

  const handleSavePipeline = async () => {
    const versionId = activeVersion;
    const pipeline = {
      ...DEFAULT_PIPELINE,
      ...promptsData.versions[versionId].pipeline
    };
    const result = await saveSection(
      'pipeline',
      'Đang lưu pipeline của bộ prompt...',
      'Đã lưu pipeline cho bộ prompt này.',
      () => fetch(
        `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/pipeline`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(pipeline)
        }
      )
    );
    if (result && activeVersion === versionId) {
      setPromptsData(prev => ({
        ...prev,
        versions: {
          ...prev.versions,
          [versionId]: {
            ...prev.versions[versionId],
            pipeline: result.pipeline
          }
        }
      }));
    }
  };

  const handleSaveVoices = async () => {
    const result = await saveSection(
      'voices',
      'Đang lưu danh sách giọng...',
      'Đã lưu danh sách và giọng mặc định.',
      () => fetch('http://127.0.0.1:8080/api/voices', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(voicesData)
      })
    );
    if (result) setVoicesData(result);
  };

  const handleSavePrompt = (promptKey, promptLabel) => {
    const versionId = activeVersion;
    const value = promptsData.versions[versionId].prompts[promptKey] || '';
    return saveSection(
      `prompt-${promptKey}`,
      `Đang lưu ${promptLabel}...`,
      `Đã lưu ${promptLabel}.`,
      async () => {
        const res = await fetch(
          `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/fields/${encodeURIComponent(promptKey)}`,
          {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ value })
          }
        );
        if (!res.ok) throw new Error("Failed to save text");

        if (promptKey === 'thumb_text' || promptKey === 'thumb_notext') {
          const imageKey = `${promptKey}_image_base64`;
          const imageVal = promptsData.versions[versionId].prompts[imageKey] || '';
          const res2 = await fetch(
            `http://127.0.0.1:8080/api/prompts/${encodeURIComponent(versionId)}/fields/${encodeURIComponent(imageKey)}`,
            {
              method: 'PATCH',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ value: imageVal })
            }
          );
          if (!res2.ok) throw new Error("Failed to save image");
        }
        return res;
      }
    );
  };

  const handleSave = async () => {
    try {
      setSavingSection('all');
      setLoadingMsg('Đang lưu thiết lập...');
      setResultMsg('');
      const voicesResponse = await fetch('http://127.0.0.1:8080/api/voices', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(voicesData)
      });
      const voicesResult = await voicesResponse.json();
      if (!voicesResponse.ok) {
        throw new Error(voicesResult.detail || 'Không thể lưu danh sách giọng.');
      }
      setVoicesData(voicesResult);

      const promptsResponse = await fetch('http://127.0.0.1:8080/api/prompts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(promptsData)
      });
      if (!promptsResponse.ok) {
        const promptsResult = await promptsResponse.json();
        throw new Error(promptsResult.detail || 'Không thể lưu cấu hình prompt.');
      }
      setResultMsg(
        lockedPromptVersion
          ? '✅ Đã lưu các thiết lập khác. Bộ prompt đang chạy được giữ nguyên.'
          : '✅ Đã lưu cấu hình Prompt và giọng đọc thành công!'
      );
    } catch (err) {
      setResultMsg('❌ ' + err.message);
    } finally {
      setSavingSection('');
      setLoadingMsg('');
    }
  };

  if (!promptsData || !voicesData || !promptsData.versions[activeVersion]) {
    return <div style={{padding: '20px', color: 'white'}}>Loading Settings...</div>;
  }

  const currentVersion = promptsData.versions[activeVersion];
  const activeVersionLocked = Boolean(
    lockedPromptVersion && activeVersion === lockedPromptVersion
  );
  const lockedVersionName = lockedPromptVersion
    ? promptsData.versions[lockedPromptVersion]?.name || lockedPromptVersion
    : '';
  const globalDefaultVoice = voicesData.voices.find(
    voice => voice.id === voicesData.active_voice_id
  );
  const promptDefaultVoiceId = currentVersion.default_voice_id || '';
  const promptDefaultVoiceMissing = Boolean(
    promptDefaultVoiceId &&
    !voicesData.voices.some(voice => voice.id === promptDefaultVoiceId)
  );
  const currentPipeline = {
    ...DEFAULT_PIPELINE,
    ...currentVersion.pipeline
  };

  const promptFields = [
    { key: 'outline', label: '1. Dàn ý (Outline)', help: 'Biến có sẵn: {transcript}' },
    { key: 'intro', label: '2. Mở đầu (Intro)' },
    { key: 'body', label: '3. Nội dung chính (Body)', help: 'Biến có sẵn: {part}' },
    { key: 'outro', label: '4. Kết thúc (Outro)' },
    { key: 'metadata', label: '5. Tiêu đề, Mô tả & Quiz (Metadata)' },
    { key: 'chapters', label: '6. Phân đoạn (Chapters)' },
    { key: 'thumb_text', label: '7. Thumbnail (Có chữ)' },
    { key: 'thumb_notext', label: '8. Thumbnail (Không chữ)' },
  ];


  const handleImageUpload = (e, imageKey) => {
    const files = Array.from(e.target.files);
    if (!files.length) return;

    let currentArray = [];
    try {
      const existing = currentVersion.prompts[imageKey];
      if (existing) {
        if (existing.startsWith('[')) {
          currentArray = JSON.parse(existing);
        } else {
          currentArray = [existing];
        }
      }
    } catch {}

    let processedCount = 0;
    const newBase64s = [];

    files.forEach(file => {
      const reader = new FileReader();
      reader.onload = (event) => {
        const base64 = event.target.result.split(',')[1];
        newBase64s.push(base64);
        processedCount++;

        if (processedCount === files.length) {
          const finalArray = [...currentArray, ...newBase64s];
          handlePromptChange(imageKey, JSON.stringify(finalArray));
        }
      };
      reader.readAsDataURL(file);
    });
  };

  const handleRemoveImage = (imageKey, indexToRemove) => {
    let currentArray = [];
    try {
      const existing = currentVersion.prompts[imageKey];
      if (existing) {
        if (existing.startsWith('[')) {
          currentArray = JSON.parse(existing);
        } else {
          currentArray = [existing];
        }
      }
    } catch {}

    if (indexToRemove === -1) {
      handlePromptChange(imageKey, '');
    } else {
      currentArray.splice(indexToRemove, 1);
      if (currentArray.length === 0) {
        handlePromptChange(imageKey, '');
      } else {
        handlePromptChange(imageKey, JSON.stringify(currentArray));
      }
    }
  };

  const renderImagePreviews = (fieldKey) => {
    const imageKey = `${fieldKey}_image_base64`;
    const existing = currentVersion.prompts[imageKey];
    let images = [];
    if (existing) {
      if (existing.startsWith('[')) {
        try {
          images = JSON.parse(existing);
        } catch {
          images = [existing];
        }
      } else {
        images = [existing];
      }
    }

    if (images.length === 0) {
      return (
        <label style={{ cursor: activeVersionLocked ? 'not-allowed' : 'pointer', textAlign: 'center', color: '#888', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '5px', opacity: activeVersionLocked ? 0.5 : 1, margin: 0 }}>
          <span style={{ fontSize: '2em' }}>🖼️</span>
          <span style={{ fontSize: '0.8em' }}>Tải ảnh mẫu lên</span>
          <input type="file" multiple accept="image/png, image/jpeg, image/webp" style={{ display: 'none' }} disabled={activeVersionLocked} onChange={(e) => handleImageUpload(e, imageKey)} />
        </label>
      );
    }

    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', width: '100%' }}>
        <div style={{ display: 'flex', gap: '5px', flexWrap: 'wrap', justifyContent: 'center' }}>
          {images.map((b64, idx) => (
            <div key={idx} style={{ position: 'relative' }}>
              <img
                src={`data:image/png;base64,${b64}`}
                alt="Reference"
                style={{ width: '45px', height: '45px', objectFit: 'cover', borderRadius: '4px', border: '1px solid #444' }}
              />
              <button
                onClick={() => handleRemoveImage(imageKey, idx)}
                disabled={activeVersionLocked}
                style={{ position: 'absolute', top: '-5px', right: '-5px', background: '#e74c3c', border: 'none', color: 'white', borderRadius: '50%', width: '16px', height: '16px', fontSize: '10px', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: activeVersionLocked ? 'not-allowed' : 'pointer', padding: 0 }}
                title="Xóa ảnh này"
              >
                ✕
              </button>
            </div>
          ))}
        </div>

        <div style={{ display: 'flex', gap: '5px', justifyContent: 'center', marginTop: '4px' }}>
          <label style={{ cursor: activeVersionLocked ? 'not-allowed' : 'pointer', background: 'var(--accent)', color: 'white', padding: '2px 8px', borderRadius: '4px', fontSize: '0.7em', opacity: activeVersionLocked ? 0.5 : 1, margin: 0 }}>
            + Thêm
            <input type="file" multiple accept="image/png, image/jpeg, image/webp" style={{ display: 'none' }} disabled={activeVersionLocked} onChange={(e) => handleImageUpload(e, imageKey)} />
          </label>
          <button onClick={() => handleRemoveImage(imageKey, -1)} disabled={activeVersionLocked} style={{ background: '#e74c3c', border: 'none', color: 'white', padding: '2px 8px', borderRadius: '4px', fontSize: '0.7em', cursor: activeVersionLocked ? 'not-allowed' : 'pointer', opacity: activeVersionLocked ? 0.5 : 1, margin: 0 }}>
            Xóa hết
          </button>
        </div>
      </div>
    );
  };

  return (
    <div className="settings-container">
      <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
        <div>
          <h1 className="hero-title">Prompt Management</h1>
          <p className="hero-subtitle">Quản lý và chỉnh sửa các lệnh AI hệ thống sử dụng.</p>
        </div>
        <button
          className="btn-save btn-large"
          onClick={handleSave}
          disabled={Boolean(savingSection)}
          style={{padding: '15px 30px', fontSize: '1.1em'}}
        >
          💾 Lưu Tất Cả
        </button>
      </div>

      {lockedPromptVersion && (
        <div className="prompt-lock-notice" role="status">
          🔒 Job {chatGptOperation || 'ChatGPT'} đang dùng bộ prompt
          <strong> {lockedVersionName}</strong>. Chỉ bộ này tạm khóa; bạn vẫn có
          thể quản lý các bộ prompt khác và danh sách giọng đọc.
        </div>
      )}
      
      <div className="version-control">
        <div className="version-editor">
          <label style={{color: '#fff', fontWeight: 'bold'}}>Phiên bản hiện tại:</label>
          <select value={activeVersion} onChange={handleVersionChange} className="version-select">
            {Object.entries(promptsData.versions).map(([key, version]) => (
              <option key={key} value={key}>{version.name}</option>
            ))}
          </select>
          <input
            value={currentVersion.name}
            onChange={(event) => handleVersionNameChange(event.target.value)}
            placeholder="Tên bộ prompt"
            className="version-select version-name-input"
            maxLength={100}
            disabled={activeVersionLocked}
          />
          <button
            className="btn-save section-save-button"
            onClick={handleSaveVersionName}
            disabled={Boolean(savingSection) || activeVersionLocked}
          >
            💾 Lưu tên
          </button>
        </div>
        
        <div style={{display: 'flex', gap: '10px'}}>
          <button className="btn-secondary" onClick={handleDuplicateVersion}>➕ Tạo Bản Sao</button>
          <button
            className="btn-danger"
            onClick={handleDeleteVersion}
            disabled={activeVersionLocked}
            title={
              activeVersionLocked
                ? 'Bộ prompt này đang được job sử dụng'
                : 'Xóa bộ prompt hiện tại'
            }
          >
            🗑️ Xóa Bản Này
          </button>
        </div>
      </div>

      <div className="prompt-item" style={{ marginBottom: '20px' }}>
        <div className="prompt-header">
          <div>
            <label>🎙️ Giọng mặc định của bộ prompt</label>
            <div className="help-text" style={{ marginTop: '5px' }}>
              Video Fetcher sẽ tự chọn giọng này khi bạn chọn bộ prompt.
              Bạn vẫn có thể đổi giọng thủ công trước khi tạo từng video.
            </div>
          </div>
          <button
            className="btn-save section-save-button"
            onClick={handleSavePromptDefaultVoice}
            disabled={Boolean(savingSection) || activeVersionLocked}
          >
            💾 Lưu
          </button>
        </div>
        <select
          value={promptDefaultVoiceId}
          onChange={(event) => handlePromptDefaultVoiceChange(event.target.value)}
          className="version-select"
          style={{ width: '100%', marginTop: '12px' }}
          disabled={activeVersionLocked}
        >
          <option value="">
            Dùng giọng mặc định chung
            {globalDefaultVoice ? ` — ${globalDefaultVoice.name}` : ''}
          </option>
          {promptDefaultVoiceMissing && (
            <option value={promptDefaultVoiceId}>
              ⚠️ Giọng đã bị xóa — sẽ dùng giọng mặc định chung
            </option>
          )}
          {voicesData.voices.map(voice => (
            <option key={voice.id} value={voice.id}>
              {voice.name}
            </option>
          ))}
        </select>
      </div>

      <div className="prompt-item" style={{ marginBottom: '20px' }}>
        <div className="prompt-header">
          <div>
            <label>🌐 ChatGPT Project viết kịch bản</label>
            <div className="help-text" style={{ marginTop: '5px' }}>
              Mỗi bộ prompt có thể lưu kịch bản vào một ChatGPT Project riêng.
            </div>
          </div>
          <button
            className="btn-save section-save-button"
            onClick={handleSaveProject}
            disabled={Boolean(savingSection) || activeVersionLocked}
          >
            💾 Lưu
          </button>
        </div>
        <input
          value={currentVersion.project_url || ''}
          onChange={(event) => handleProjectUrlChange(event.target.value)}
          placeholder="https://chatgpt.com/g/g-p-.../project"
          className="version-select"
          style={{ width: '100%', marginTop: '12px' }}
          disabled={activeVersionLocked}
        />
      </div>

      <div className="prompt-item" style={{ marginBottom: '20px' }}>
        <div className="prompt-header">
          <div>
            <label>⚙️ Pipeline tự động của bộ prompt</label>
            <div className="help-text" style={{ marginTop: '5px' }}>
              Bốn bước lõi Dàn ý → Intro → Body → Outro luôn bắt buộc. Các bước
              dưới đây được áp dụng độc lập cho video mới của riêng bộ prompt này.
            </div>
          </div>
          <button
            className="btn-save section-save-button"
            onClick={handleSavePipeline}
            disabled={Boolean(savingSection) || activeVersionLocked}
          >
            💾 Lưu pipeline
          </button>
        </div>
        <div className="pipeline-core-flow" aria-label="Các bước lõi bắt buộc">
          <span>Dàn ý</span><b>→</b><span>Intro</span><b>→</b><span>Body</span><b>→</b><span>Outro</span>
        </div>
        <div className="pipeline-options">
          {PIPELINE_STEPS.map((step, index) => (
            <label key={step.key} className="pipeline-option">
              <input
                type="checkbox"
                checked={currentPipeline[step.key]}
                onChange={(event) => handlePipelineChange(step.key, event.target.checked)}
                disabled={activeVersionLocked}
              />
              <span className="pipeline-step-number">{index + 6}</span>
              <span>
                <strong>{step.label}</strong>
                <small>{step.description}</small>
              </span>
            </label>
          ))}
        </div>
        <div className="help-text pipeline-snapshot-help">
          Mỗi job lưu một bản chụp pipeline khi được thêm vào hàng đợi. Sửa cấu
          hình tại đây không thay đổi job đã xếp hàng hoặc đang phục hồi.
        </div>
      </div>

      <div className="prompt-item" style={{ marginBottom: '20px' }}>
        <div className="prompt-header" style={{ alignItems: 'center' }}>
          <div>
            <label>🎙️ Quản lý giọng đọc Genmax</label>
            <div className="help-text" style={{ marginTop: '5px' }}>
              Nhập UUID của giọng tùy chỉnh hoặc mã giọng hệ thống từ Genmax.
            </div>
          </div>
          <div className="prompt-header-actions">
            <button
              className="btn-save section-save-button"
              onClick={handleSaveVoices}
              disabled={Boolean(savingSection)}
            >
              💾 Lưu
            </button>
            <button className="btn-secondary" onClick={handleAddVoice}>
              ➕ Thêm giọng
            </button>
          </div>
        </div>

        <div style={{
          display: 'flex', alignItems: 'center', gap: '10px',
          margin: '15px 0', flexWrap: 'wrap'
        }}>
          <label style={{ color: '#fff', fontWeight: 'bold' }}>
            Giọng mặc định chung (dự phòng):
          </label>
          <select
            value={voicesData.active_voice_id}
            onChange={(event) => setVoicesData(prev => ({
              ...prev,
              active_voice_id: event.target.value
            }))}
            className="version-select"
          >
            {voicesData.voices.map((voice, index) => (
              <option key={`${voice.id}-${index}`} value={voice.id}>
                {voice.name || `Giọng ${index + 1}`}
              </option>
            ))}
          </select>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {voicesData.voices.map((voice, index) => (
            <div
              key={index}
              style={{
                display: 'grid',
                gridTemplateColumns: 'minmax(180px, 0.8fr) minmax(300px, 1.5fr) auto',
                gap: '10px',
                alignItems: 'center'
              }}
            >
              <input
                value={voice.name}
                onChange={(event) => handleVoiceChange(index, 'name', event.target.value)}
                placeholder="Tên giọng"
                className="version-select"
                style={{ width: '100%' }}
              />
              <input
                value={voice.id}
                onChange={(event) => handleVoiceChange(index, 'id', event.target.value)}
                placeholder="Voice ID (UUID hoặc mã giọng hệ thống)"
                className="version-select"
                style={{ width: '100%' }}
              />
              <button
                className="btn-danger"
                onClick={() => handleRemoveVoice(index)}
                title="Xóa giọng đọc"
              >
                🗑️
              </button>
            </div>
          ))}
        </div>
      </div>

      {(loadingMsg || resultMsg) && (
        <div className="status-box" style={{marginBottom: '20px'}}>
          {loadingMsg && <p className="loading">{loadingMsg}</p>}
          {resultMsg && <p className="result">{resultMsg}</p>}
        </div>
      )}

      <div className="prompts-list">
        {promptFields.map(field => (
          <div key={field.key} className="prompt-item">
            <div className="prompt-header">
              <label>{field.label}</label>
              <div className="prompt-header-actions">
                {field.help && <span className="help-text">{field.help}</span>}
                <button
                  className="btn-save section-save-button"
                  onClick={() => handleSavePrompt(field.key, field.label)}
                  disabled={Boolean(savingSection) || activeVersionLocked}
                >
                  💾 Lưu
                </button>
              </div>
            </div>
            <div style={{ display: 'flex', gap: '10px' }}>
              <textarea
                className="prompt-textarea"
                style={{ flex: 1 }}
                value={currentVersion.prompts[field.key] || ''}
                onChange={(e) => handlePromptChange(field.key, e.target.value)}
                rows={6}
                disabled={activeVersionLocked}
              />
              {(field.key === 'thumb_text' || field.key === 'thumb_notext') && (
                <div style={{ width: '150px', border: '1px dashed #666', borderRadius: '4px', padding: '10px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.2)' }}>
                  {renderImagePreviews(field.key)}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
