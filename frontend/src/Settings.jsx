import { useState, useEffect } from 'react';
import './Settings.css';

export default function Settings() {
  const [promptsData, setPromptsData] = useState(null);
  const [voicesData, setVoicesData] = useState(null);
  const [activeVersion, setActiveVersion] = useState('');
  const [loadingMsg, setLoadingMsg] = useState('');
  const [resultMsg, setResultMsg] = useState('');

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
    setPromptsData(prev => {
      const newData = { ...prev };
      newData.versions[activeVersion].prompts[key] = value;
      return newData;
    });
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
        prompts: currentPrompts
      };
      
      newData.active_version = versionId;
      setActiveVersion(versionId);
      return newData;
    });
  };

  const handleDeleteVersion = () => {
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

  const handleSave = async () => {
    try {
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
        throw new Error('Không thể lưu cấu hình prompt.');
      }
      setResultMsg('✅ Đã lưu cấu hình Prompt và giọng đọc thành công!');
    } catch (err) {
      setResultMsg('❌ ' + err.message);
    } finally {
      setLoadingMsg('');
    }
  };

  if (!promptsData || !voicesData || !promptsData.versions[activeVersion]) {
    return <div style={{padding: '20px', color: 'white'}}>Loading Settings...</div>;
  }

  const currentVersion = promptsData.versions[activeVersion];

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

  return (
    <div className="settings-container">
      <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
        <div>
          <h1 className="hero-title">Prompt Management</h1>
          <p className="hero-subtitle">Quản lý và chỉnh sửa các lệnh AI hệ thống sử dụng.</p>
        </div>
        <button className="btn-save btn-large" onClick={handleSave} style={{padding: '15px 30px', fontSize: '1.1em'}}>💾 Lưu Tất Cả</button>
      </div>
      
      <div className="version-control">
        <div style={{display: 'flex', alignItems: 'center', gap: '10px'}}>
          <label style={{color: '#fff', fontWeight: 'bold'}}>Phiên bản hiện tại:</label>
          <select value={activeVersion} onChange={handleVersionChange} className="version-select">
            {Object.entries(promptsData.versions).map(([key, version]) => (
              <option key={key} value={key}>{version.name}</option>
            ))}
          </select>
        </div>
        
        <div style={{display: 'flex', gap: '10px'}}>
          <button className="btn-secondary" onClick={handleDuplicateVersion}>➕ Tạo Bản Sao</button>
          <button className="btn-danger" onClick={handleDeleteVersion}>🗑️ Xóa Bản Này</button>
        </div>
      </div>

      <div className="prompt-item" style={{ marginBottom: '20px' }}>
        <div className="prompt-header" style={{ alignItems: 'center' }}>
          <div>
            <label>🎙️ Quản lý giọng đọc Genmax</label>
            <div className="help-text" style={{ marginTop: '5px' }}>
              Đặt tên dễ nhớ và nhập đúng Voice ID từ Genmax.
            </div>
          </div>
          <button className="btn-secondary" onClick={handleAddVoice}>
            ➕ Thêm giọng
          </button>
        </div>

        <div style={{
          display: 'flex', alignItems: 'center', gap: '10px',
          margin: '15px 0', flexWrap: 'wrap'
        }}>
          <label style={{ color: '#fff', fontWeight: 'bold' }}>
            Giọng mặc định:
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
                placeholder="Voice ID (UUID)"
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
              {field.help && <span className="help-text">{field.help}</span>}
            </div>
            <textarea
              className="prompt-textarea"
              value={currentVersion.prompts[field.key] || ''}
              onChange={(e) => handlePromptChange(field.key, e.target.value)}
              rows={6}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
