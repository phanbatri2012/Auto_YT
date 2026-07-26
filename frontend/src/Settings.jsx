import { useState, useEffect } from 'react';
import './Settings.css';

export default function Settings() {
  const [promptsData, setPromptsData] = useState(null);
  const [activeVersion, setActiveVersion] = useState('');
  const [loadingMsg, setLoadingMsg] = useState('');
  const [resultMsg, setResultMsg] = useState('');

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    try {
      const res = await fetch('http://127.0.0.1:8080/api/prompts');
      const data = await res.json();
      setPromptsData(data);
      setActiveVersion(data.active_version);
    } catch (err) {
      console.error("Lỗi khi lấy prompts:", err);
    }
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
      await fetch('http://127.0.0.1:8080/api/prompts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(promptsData)
      });
      setResultMsg('✅ Đã lưu cấu hình Prompts thành công!');
    } catch (err) {
      setResultMsg('❌ Lỗi khi lưu cấu hình.');
    } finally {
      setLoadingMsg('');
    }
  };

  if (!promptsData || !promptsData.versions[activeVersion]) {
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
