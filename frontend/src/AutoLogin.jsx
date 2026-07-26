import { useState, useEffect } from 'react';
import './AutoLogin.css';

export default function AutoLogin() {
  const [account, setAccount] = useState({
    email: '',
    password: '',
    totp_secret: '',
    headless: true
  });
  const [loadingMsg, setLoadingMsg] = useState('');
  const [resultMsg, setResultMsg] = useState('');

  useEffect(() => {
    fetch('http://127.0.0.1:8080/api/account')
      .then(res => res.json())
      .then(data => setAccount(data))
      .catch(err => console.error("Lỗi khi lấy account", err));
  }, []);

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;
    setAccount(prev => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value
    }));
  };

  const handleSave = async () => {
    try {
      setLoadingMsg('Đang lưu cấu hình...');
      await fetch('http://127.0.0.1:8080/api/account', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(account)
      });
      setResultMsg('✅ Đã lưu cấu hình thành công!');
    } catch (err) {
      setResultMsg('❌ Lỗi khi lưu cấu hình.');
    } finally {
      setLoadingMsg('');
    }
  };

  const handleClear = async () => {
    if (!confirm("Bạn có chắc chắn muốn xóa toàn bộ thông tin đăng nhập và Profile trình duyệt?")) return;
    try {
      setLoadingMsg('Đang xóa thông tin...');
      await fetch('http://127.0.0.1:8080/api/clear-account', { method: 'POST' });
      setAccount({ email: '', password: '', totp_secret: '', headless: true });
      setResultMsg('✅ Đã xóa thông tin thành công!');
    } catch (err) {
      setResultMsg('❌ Lỗi khi xóa thông tin.');
    } finally {
      setLoadingMsg('');
    }
  };

  const handleOpenProfile = async () => {
    try {
      setLoadingMsg('Đang mở trình duyệt...');
      await fetch('http://127.0.0.1:8080/api/open-profile', { method: 'POST' });
      setResultMsg('🌐 Đang mở cửa sổ Chrome (Sẽ tự đóng sau 10 phút hoặc khi bạn tắt).');
    } catch (err) {
      setResultMsg('❌ Lỗi khi mở trình duyệt.');
    } finally {
      setLoadingMsg('');
    }
  };

  const handleAutoLogin = async () => {
    try {
      setLoadingMsg('🤖 Đang chạy kịch bản Auto Login... (Sẽ tốn 30s - 1 phút, vui lòng chờ)');
      setResultMsg('');
      const response = await fetch('http://127.0.0.1:8080/api/login-chatgpt', { method: 'POST' });
      const data = await response.json();
      if (data.success) {
        setResultMsg(`✅ Auto Login thành công! ${data.message || ''}`);
      } else {
        setResultMsg(`❌ Lỗi Auto Login: ${data.error || data.message}`);
      }
    } catch (err) {
      setResultMsg('❌ Lỗi kết nối tới Backend.');
    } finally {
      setLoadingMsg('');
    }
  };

  return (
    <div className="auto-login-container">
      <h1 className="hero-title">ChatGPT Auto Login</h1>
      <p className="hero-subtitle">Tự động cấu hình phiên đăng nhập cho ChatGPT để dùng làm công cụ chạy nền.</p>
      
      <div className="login-panel">
        <div className="form-group">
          <label>Email OpenAI:</label>
          <input 
            type="email" 
            name="email" 
            value={account.email} 
            onChange={handleChange} 
            placeholder="example@gmail.com" 
          />
        </div>
        
        <div className="form-group">
          <label>Mật khẩu:</label>
          <input 
            type="password" 
            name="password" 
            value={account.password} 
            onChange={handleChange} 
            placeholder="Nhập mật khẩu..." 
          />
        </div>

        <div className="form-group">
          <label>Mã bảo mật 2FA (TOTP Secret) - Tùy chọn:</label>
          <input 
            type="text" 
            name="totp_secret" 
            value={account.totp_secret} 
            onChange={handleChange} 
            placeholder="ABC123XYZ..." 
          />
        </div>

        <div className="form-group checkbox-group">
          <label className="switch">
            <input 
              type="checkbox" 
              name="headless" 
              checked={account.headless} 
              onChange={handleChange} 
            />
            <span className="slider round"></span>
          </label>
          <span className="checkbox-label">Chạy Ẩn (Headless Mode)</span>
          <p className="help-text" style={{margin: '5px 0 0 50px', fontSize: '0.85em', color: '#888'}}>
            Tắt đi nếu bạn muốn trình duyệt mở lên để tự giải CAPTCHA hoặc xác minh Cloudflare.
          </p>
        </div>
        
        <div className="action-buttons">
          <button className="btn-save" onClick={handleSave}>💾 Save Settings</button>
          <button className="btn-primary" onClick={handleAutoLogin}>🚀 Auto Login</button>
          <button className="btn-secondary" onClick={handleOpenProfile}>🌐 Open Profile</button>
          <button className="btn-danger" onClick={handleClear}>🗑️ Clear Account</button>
        </div>
        
        {(loadingMsg || resultMsg) && (
          <div className="status-box">
            {loadingMsg && <p className="loading">{loadingMsg}</p>}
            {resultMsg && <p className="result">{resultMsg}</p>}
          </div>
        )}
      </div>
    </div>
  );
}
